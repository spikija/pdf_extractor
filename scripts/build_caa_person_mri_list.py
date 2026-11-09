"""Current person-level CAA minus saved MRI evidence; local deterministic data only.

Fallnummer is used to retrieve source records, never to match an MRI to a person.
Existing DICOM identity CSVs are reused; only file existence is checked. Saved
radiology records outside that inventory remain evidence too. Unidentified saved
records get opaque identity keys and stay ambiguous, not silently discarded.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
from openpyxl import load_workbook

import audit_parser_caa_mri as caa
import relink_mri_patient_identity as identity
from caa_pdf_extractor import person_identity as base
from caa_pdf_extractor.identity_resolution import full_name

ROOT = Path(__file__).resolve().parents[1]
RANK = {'CAA_REVIEW_CANDIDATE': 1, 'CAA_SUSPECTED': 2, 'CAA_ESTABLISHED': 3}
EXACT = 'MRI_PERSON_EXACT_MATCH'
NORMALIZED = 'MRI_PERSON_SAFE_NORMALIZED_MATCH'
AMBIGUOUS = 'MRI_PERSON_AMBIGUOUS'
NO_MATCH = 'MRI_PERSON_NO_MATCH'


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def positive(value):
    return value is True or str(value).strip().lower() in {'t', 'true'}


def records_from_private(rows):
    records = defaultdict(set)
    for row in rows:
        births = {identity.dob(value) for value in row['dob'].split(';') if value}
        if len(births) != 1 or None in births:
            raise ValueError('CURRENT_PERSON_BIRTH_METADATA_INCOMPLETE')
        for surname in row['surname_raw'].split(';'):
            for given in row['given_name_raw'].split(';'):
                person = identity.identity(surname, given, next(iter(births)))
                if person:
                    records[row['person_id']].add(person)
    return records


def full_match(left, right):
    if left[2] != right[2]:
        return None
    if not all(full_name(value[0]) and full_name(value[1], given=True) for value in (left, right)):
        return None
    if left[:2] == right[:2]:
        return EXACT
    if (identity.name_forms(left[0]) & identity.name_forms(right[0])
            and identity.name_forms(left[1]) & identity.name_forms(right[1])):
        return NORMALIZED
    return None


def resolve_mri(entry, records):
    """Keep uncertain middle names, conflicting DOB and partial names in review."""
    source = entry['identity']
    if source is None:
        candidates = {person for person, variants in records.items()
                      if any(value[2] in entry['possible_dobs']
                             or any(identity.name_match_method(value, old) for old in entry.get('possible_identities', set()))
                             for value in variants)}
        return AMBIGUOUS, candidates, 'Saved flag lacks a validated full patient identity'
    exact = {}; compatible = set(); possible = set()
    for person, variants in records.items():
        for value in variants:
            method = full_match(source, value)
            if method and (person not in exact or method == EXACT):
                exact[person] = method
            if identity.match_method(source, value):
                compatible.add(person)
            if (identity.name_match_method(source, value)
                    or (source[2] == value[2] and identity.name_forms(source[0]) & identity.name_forms(value[0]))):
                possible.add(person)
    if entry.get('identity_conflict'):
        return AMBIGUOUS, possible | set(exact), 'Saved source has conflicting identity metadata'
    if len(exact) == 1 and not compatible - set(exact):
        person = next(iter(exact))
        return exact[person], {person}, 'Full surname and full given name with exact DOB'
    if exact or possible:
        return AMBIGUOUS, possible | set(exact), 'Incomplete name agreement, DOB conflict or multiple possible people'
    return NO_MATCH, set(), 'No deterministic current-person candidate'


def split_target_statuses(classification, folder_people, pdf_statuses, confirmed):
    """Never transfer a mixed folder's diagnosis to another PDF's owner."""
    source_status = {}
    for folder, people in folder_people.items():
        if folder in pdf_statuses:
            for person in people:
                source_status[person, folder] = pdf_statuses[folder].get(person, 'CAA_NONE')
        else:
            if len(people) != 1:
                raise ValueError('MULTIPERSON_FOLDER_HAS_NO_PDF_OWNERSHIP_EVIDENCE')
            source_status[next(iter(people)), folder] = classification[folder]
    for person, folder in confirmed:
        prior = source_status.get((person, folder), 'CAA_NONE')
        if prior not in RANK:
            source_status[person, folder] = 'CAA_REVIEW_CANDIDATE'
    target = {}
    for (person, folder), status in source_status.items():
        if status in RANK and RANK[status] > RANK.get(target.get(person), 0):
            target[person] = status
    return target, source_status


def partition_target(target, available, uncertain):
    with_mri = set(target) & available
    review = (set(target) & uncertain) - available
    missing = set(target) - available - uncertain
    assert not with_mri & missing and not review & missing and not with_mri & review
    assert set(target) == with_mri | review | missing
    return with_mri, review, missing


def historical_identities(path):
    result = defaultdict(set)
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in ('CAA', 'orbis_amyloidangiopat'):
            iterator = workbook[sheet].iter_rows(values_only=True)
            headers = next(iterator)
            index = {key: headers.index(key) for key in ('Fallnummer', 'Nachname', 'Vorname', 'Geburtsdatum')}
            for row in iterator:
                value = identity.identity(row[index['Nachname']], row[index['Vorname']], row[index['Geburtsdatum']])
                if value:
                    result[identity.fallnr(row[index['Fallnummer']])].add(value)
    finally:
        workbook.close()
    return result


def saved_mri_sources(inventory, cases, radiology, historical, secret):
    groups = defaultdict(list)
    for row in inventory:
        if row['modality'].strip().upper() == 'MR':
            groups[identity.private_key(row)].append(row)
    entries = {}
    for key, rows in groups.items():
        exists = any(int(row['dicom_instance_count'] or 0) > 0
                     and row['dicom_representative_path'].strip()
                     and Path(row['dicom_representative_path']).is_file() for row in rows)
        if not exists:
            continue
        variants = {identity.dicom_identity(row) for row in rows}
        variants.discard(None)
        person = next(iter(variants)) if len(variants) == 1 else None
        entries[key] = dict(identity=person, possible_dobs={value[2] for value in variants},
            identity_conflict=len(variants) != 1 or not any(not row['identity_qc'].strip() for row in rows),
            evidence='DICOM_MR_FILES_ON_HDD', case_ids=set())
    by_case = {identity.fallnr(row['id']): row for row in cases}
    if len(by_case) != len(cases):
        raise ValueError('DUPLICATE_OLD_CASE_PRIMARY_KEY')
    positive_cases = {identity.fallnr(row['case_id']) for row in radiology if positive(row['saved_on_hdd'])}
    if not positive_cases <= set(by_case):
        raise ValueError('SAVED_RADIOLOGY_ORPHAN_CASE')
    # Full old name/DOB are obtained through the explicit case record. MRI-to-
    # current matching below still uses only person demographics, never Fallnr.
    for case_id in sorted(positive_cases):
        case = by_case[case_id]
        old = historical[identity.fallnr(case['fallnr'])]
        covered = {key for key, entry in entries.items() if entry['identity']
                   and any(identity.match_method(value, entry['identity']) for value in old)}
        for key in covered:
            entries[key]['case_ids'].add(case_id)
        if covered:
            continue
        birth = identity.dob(case['date_of_birth'])
        source = next(iter(old)) if len(old) == 1 and {value[2] for value in old} == {birth} else None
        # Known full identities deduplicate across old encounters. Unknown saved
        # identities retain one opaque evidence key per case, explicitly reviewed.
        token = ('KNOWN', *source) if source else ('UNRESOLVED_SAVED_CASE', case_id)
        key = 'M_' + hmac.new(secret, b'pdf-extractor.mri.saved-identity.v1\0'
            + json.dumps(token, ensure_ascii=False, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()[:24]
        if key not in entries:
            entries[key] = dict(identity=source, possible_dobs={birth} if birth else set(),
                possible_identities=old,
                identity_conflict=source is None, evidence='RADIOLOGY_SAVED_ON_HDD_TRUE', case_ids=set())
        entries[key]['case_ids'].add(case_id)
    assert set().union(*(entry['case_ids'] for entry in entries.values())) == positive_cases
    return entries


def run():
    data = ROOT / 'data'; output = ROOT / 'output'; private = data / 'private_identity'
    input_paths = [data / 'patient_identity_crosswalk.csv', data / 'pdf_person_identity_crosswalk.csv',
        private / 'person_identity_crosswalk.csv', private / 'pdf_identity_adjudications.csv',
        data / 'person_caa_physician_overrides.csv', output / 'pdf_db_caa_classification.csv',
        ROOT / 'caa_private_linkage/mri_identity_series_PRIVATE.csv',
        data / 'old_retool_db/case.csv', data / 'old_retool_db/radiology.csv',
        data / 'old_retool_db/orbis_01012015_01062023_amyloidangiopat.xlsx',
        ROOT / 'caa_private_linkage/mri_exam_key_PRIVATE.key']
    protected = {path: base.utils.get_sha256(path) for path in input_paths}
    target_path = output / 'caa_patients_mri_to_extract_final.csv'
    review_path = output / 'mri_person_linkage_review.csv'
    protected.update({path: base.utils.get_sha256(path) for path in output.glob('*.csv')
                      if path not in {target_path, review_path}})
    safe = read(input_paths[0]); pdf_rows = read(input_paths[1]); private_rows = read(input_paths[2])
    ledger = read(input_paths[3]); overrides = read(input_paths[4]); classes = read(input_paths[5])
    folders = {path.name for path in (ROOT / 'pdf_db').iterdir() if path.is_dir()}
    people = {row['person_id'] for row in safe}
    assert '' not in people and len(people) == 378 and len(folders) == 390
    assert len({(row['person_id'], row['fallnr']) for row in safe}) == len(safe)
    assert not any(row['identity_type'] == 'UNRESOLVED' for row in safe)
    assert not read(output / 'person_identity_review.csv')
    assert {(row['person_id'], row['fallnr']) for row in private_rows} == {(row['person_id'], row['fallnr']) for row in safe}
    classification = {row['fallnr']: row['CAA_status'] for row in classes}
    assert len(classification) == len(classes) and set(classification) == folders
    records = records_from_private(private_rows)
    assert set(records) == people
    provenance = defaultdict(set); folder_people = defaultdict(set)
    for row in safe:
        if row['fallnr'] in folders:
            provenance[row['person_id']].add(row['fallnr'])
            folder_people[row['fallnr']].add(row['person_id'])
    assert set(folder_people) == folders
    hashes = {(row['source_fallnr'], row['filename']): row['sha256'] for row in ledger}
    assert len(hashes) == len(ledger) == len(pdf_rows)
    cache = data / 'debug/review_app/parsed_pdf_cache' / (pymupdf.__version__ + '-'
            + base.utils.get_sha256(Path(base.utils.__file__))[:16])
    owned_assertions = defaultdict(lambda: defaultdict(list)); source_snapshots = {}
    affected = {row['source_fallnr'] for row in pdf_rows}
    expected = {(folder, path.relative_to(ROOT / 'pdf_db' / folder).as_posix())
                for folder in affected for path in (ROOT / 'pdf_db' / folder).rglob('*')
                if path.is_file() and path.suffix.lower() == '.pdf'}
    assert set(hashes) == expected
    for row in pdf_rows:
        folder = row['source_fallnr']; person = row['person_id']; filename = row['filename']
        assert person in people and row['assignment_status'] != 'AMBIGUOUS'
        path = ROOT / 'pdf_db' / folder / filename; digest = hashes[folder, filename]
        assert base.utils.get_sha256(path) == digest
        source_snapshots[path] = digest
        payload = base.read_payload(cache / (digest + '.json'), digest, allow_empty=True)
        if payload is None:
            raise ValueError('CURRENT_CACHED_PDF_ARTIFACT_REQUIRED_NO_REPARSE')
        owned_assertions[folder][person].extend(caa.caa_text(payload))
        provenance[person].add(folder); folder_people[folder].add(person)
    pdf_statuses = {folder: {person: caa.strongest(values) or 'CAA_NONE' for person, values in owners.items()}
                    for folder, owners in owned_assertions.items()}
    confirmed = {(row['person_id'], row['source_fallnr']) for row in overrides if positive(row['CAA_PHYSICIAN_CONFIRMED'])}
    assert len(confirmed) == 14 and len({folder for person, folder in confirmed}) == 7
    for folder in {folder for person, folder in confirmed}:
        assert {person for person, source in confirmed if source == folder} == folder_people[folder]
        assert len(folder_people[folder]) == 2
    target, source_status = split_target_statuses(classification, folder_people, pdf_statuses, confirmed)
    assert {person for person, folder in confirmed} <= set(target)
    secret = base.load_secret(input_paths[-1])
    if secret is None:
        raise ValueError('EXISTING_PRIVATE_HMAC_KEY_REQUIRED')
    history = historical_identities(input_paths[9])
    saved = saved_mri_sources(read(input_paths[6]), read(input_paths[7]), read(input_paths[8]), history, secret)
    historical_path = Path(r'E:\cdk.bckp.2024\Sci\Stroke\CAA\Register\data_dump')
    historical_folders = {path.name for path in historical_path.iterdir()
                          if path.is_dir() and path.name.isdigit() and len(path.name) == 10}
    assert len(historical_folders) == 98 and historical_folders <= folders
    available = set(); uncertain = set(); reviews = []; audit = []; statuses = Counter()
    for key, entry in sorted(saved.items()):
        status, candidates, reason = resolve_mri(entry, records)
        statuses[status] += 1
        if status in {EXACT, NORMALIZED}:
            assert len(candidates) == 1
            available.update(candidates)
        elif status == AMBIGUOUS:
            uncertain.update(candidates)
        if status in {AMBIGUOUS, NO_MATCH}:
            reviews.append(dict(MRI_identity_key=key, match_status=status,
                                candidate_count=str(len(candidates)), short_reason=reason))
        audit.append(dict(MRI_identity_key=key, evidence_source=entry['evidence'], match_status=status,
            person_ids=';'.join(sorted(candidates)), saved_old_case_ids=';'.join(sorted(entry['case_ids'])),
            historical_source_trace='TRUE' if any(provenance[person] & historical_folders for person in candidates) else 'FALSE'))
    with_mri, uncertain_caa, missing = partition_target(target, available, uncertain)
    final = []
    for person in missing:
        sources = provenance[person]
        assert sources and sources <= folders
        # Retrieval preference: a current source containing the strongest CAA
        # evidence. Fallnr is only a deterministic tie-break, not a date proxy.
        representative = max(sources, key=lambda folder: (RANK.get(source_status.get((person, folder)), 0), folder))
        final.append(dict(person_id=person, fallnr=representative, CAA_status=target[person],
                          all_fallnr=';'.join(sorted(sources))))
    final.sort(key=lambda row: (-RANK[row['CAA_status']], row['fallnr'], row['person_id']))
    assert len(final) == len({row['person_id'] for row in final}) == len(missing)
    assert all(row['CAA_status'] in RANK and row['person_id'] not in available | uncertain for row in final)
    assert sum(statuses.values()) == len(saved)
    assert len(target) == len(with_mri) + len(uncertain_caa) + len(missing)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    assert all(base.utils.get_sha256(path) == digest for path, digest in source_snapshots.items())
    base.write_csv(target_path, ['person_id', 'fallnr', 'CAA_status', 'all_fallnr'], final)
    if reviews or review_path.exists():
        # A header-only replacement prevents a stale review file if later all resolve.
        base.write_csv(review_path, ['MRI_identity_key', 'match_status', 'candidate_count', 'short_reason'], reviews)
    base.write_csv(private / 'mri_person_linkage_audit.csv',
        ['MRI_identity_key', 'evidence_source', 'match_status', 'person_ids', 'saved_old_case_ids', 'historical_source_trace'], audit, private=True)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    counts = Counter(row['CAA_status'] for row in final)
    result = [('CURRENT_UNIQUE_PERSONS', len(people)), ('TARGET_CAA_PERSONS', len(target)),
        ('MRI_IDENTITIES_SAVED', len(saved)),
        ('MRI_IDENTITIES_MATCHED_TO_CURRENT_PERSON', statuses[EXACT] + statuses[NORMALIZED]),
        ('MRI_IDENTITIES_AMBIGUOUS', statuses[AMBIGUOUS]),
        ('MRI_IDENTITIES_NOT_IN_CURRENT_COHORT', statuses[NO_MATCH]),
        ('CAA_PERSONS_WITH_MRI', len(with_mri)), ('CAA_PERSONS_WITHOUT_MRI', len(missing)),
        ('CAA_PERSONS_MRI_STATUS_AMBIGUOUS', len(uncertain_caa)),
        ('CAA_ESTABLISHED_WITHOUT_MRI', counts['CAA_ESTABLISHED']),
        ('CAA_SUSPECTED_WITHOUT_MRI', counts['CAA_SUSPECTED']),
        ('CAA_REVIEW_CANDIDATE_WITHOUT_MRI', counts['CAA_REVIEW_CANDIDATE']),
        ('FINAL_FILE', 'output/caa_patients_mri_to_extract_final.csv'), ('PRIVATE_FILE', 'NONE'),
        ('MRI_REVIEW_FILE', 'output/mri_person_linkage_review.csv' if reviews else 'NONE'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]
    for label, value in result:
        print(f'{label} = {value}', flush=True)
    return dict(result)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        print('CAA_PERSON_MRI_EXPORT_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)
