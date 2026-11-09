"""Unified person-level CAA download list using the validated old MRI/Excel bridge.

Only existing CSV/XLSX/parsed JSON artifacts are read. No original PDF/DICOM is
opened, no E: folders are copied, and no database connection is created.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

import build_caa_person_mri_list as cohort
import link_mri_via_caa_excel as bridge
from caa_pdf_extractor import person_identity as base

ROOT = Path(__file__).resolve().parents[1]
EXACT = 'EXACT_PERSON_MATCH'
SAFE = 'SAFE_NORMALIZED_PERSON_MATCH'
AMBIGUOUS = 'AMBIGUOUS'
ABSENT = 'NOT_IN_CURRENT_UNIFIED_COHORT'
SAFE_FIELDS = ['person_id', 'preferred_fallnr', 'all_fallnr', 'CAA_status']
PRIVATE_FIELDS = ['person_id', 'preferred_fallnr', 'all_fallnr', 'name', 'dob', 'SVNR_or_foreign_id', 'CAA_status']
REVIEW_FIELDS = ['person_id', 'preferred_fallnr', 'all_fallnr', 'name', 'dob', 'CAA_status',
                 'MRI_identity_keys', 'issue']
TITLE = re.compile(r'^(?:(?:Dr|Prof|Mag)\.?\s+)+', re.I)


def without_title(value):
    return TITLE.sub('', str(value).strip()).strip()


def person_method(left, right):
    method = bridge.matching.method(left, right)
    if method:
        return EXACT if method == bridge.matching.EXACT else SAFE
    # An explicit academic title in a parsed given-name field is not a middle
    # name. Preserve the original private metadata and existing person ID.
    clean_left = without_title(left[0]), without_title(left[1]), left[2]
    clean_right = without_title(right[0]), without_title(right[1]), right[2]
    return SAFE if bridge.matching.method(clean_left, clean_right) else None


def resolve_person(variants, people, supporting_ids=()):
    candidates = {}; possible = set(supporting_ids)
    for person, current in people.items():
        methods = {person_method(left, right) for left in variants for right in current}
        if EXACT in methods:
            candidates[person] = EXACT
        elif SAFE in methods:
            candidates[person] = SAFE
        # Name/DOB disagreements are review evidence only. They cannot put a
        # potential owner of an existing MRI straight into a download list.
        for left in variants:
            for right in current:
                l = bridge.matching.previous.identity(without_title(left[0]), without_title(left[1]), left[2])
                r = bridge.matching.previous.identity(without_title(right[0]), without_title(right[1]), right[2])
                if l and r and (bridge.matching.previous.name_match_method(l, r)
                    or (l[2] == r[2] and bridge.matching.previous.name_forms(l[0]) & bridge.matching.previous.name_forms(r[0]))):
                    possible.add(person)
    if len(candidates) == 1:
        person = next(iter(candidates))
        # A competing validated identity is material even when one name matches.
        if set(supporting_ids) - {person}:
            return AMBIGUOUS, possible | set(candidates)
        return candidates[person], {person}
    if candidates or possible:
        return AMBIGUOUS, possible | set(candidates)
    return ABSENT, set()


def load_target(safe, pdf_rows, ledger, classes, overrides, folders):
    classification = {row['fallnr']: row['CAA_status'] for row in classes}
    assert len(classification) == len(classes) and set(classification) == folders
    provenance = defaultdict(set); folder_people = defaultdict(set)
    for row in safe:
        if row['fallnr'] in folders:
            provenance[row['person_id']].add(row['fallnr'])
            folder_people[row['fallnr']].add(row['person_id'])
    assert set(folder_people) == folders
    hashes = {(row['source_fallnr'], row['filename']): row['sha256'] for row in ledger}
    assert len(hashes) == len(ledger) == len(pdf_rows)
    cache = ROOT / 'data/debug/review_app/parsed_pdf_cache' / (pymupdf.__version__ + '-'
        + base.utils.get_sha256(Path(base.utils.__file__))[:16])
    owned = defaultdict(lambda: defaultdict(list))
    # Corrected/mixed source folders cannot safely transfer their old aggregate
    # CAA diagnosis to every owner. Reuse the established deterministic rules
    # over their existing parsed artifacts, with the physician overrides intact.
    for row in pdf_rows:
        folder = row['source_fallnr']; person = row['person_id']
        assert person and row['assignment_status'] != 'AMBIGUOUS'
        digest = hashes[folder, row['filename']]
        payload = base.read_payload(cache / (digest + '.json'), digest, allow_empty=True)
        if payload is None:
            raise ValueError('CURRENT_PARSED_ARTIFACT_REQUIRED_NO_PDF_REPARSE')
        owned[folder][person].extend(cohort.caa.caa_text(payload))
        provenance[person].add(folder); folder_people[folder].add(person)
    pdf_statuses = {folder: {person: cohort.caa.strongest(values) or 'CAA_NONE'
                    for person, values in owners.items()} for folder, owners in owned.items()}
    confirmed = {(row['person_id'], row['source_fallnr']) for row in overrides
                 if cohort.positive(row['CAA_PHYSICIAN_CONFIRMED'])}
    assert len(confirmed) == 14 and len({folder for person, folder in confirmed}) == 7
    for folder in {folder for person, folder in confirmed}:
        assert {person for person, source in confirmed if source == folder} == folder_people[folder]
        assert len(folder_people[folder]) == 2
    target, source_status = cohort.split_target_statuses(classification, folder_people, pdf_statuses, confirmed)
    assert {person for person, folder in confirmed} <= set(target)
    return target, source_status, provenance


def preferred_source(person, provenance, source_status):
    # Prefer the source containing the strongest owned CAA evidence. The folder
    # number is a stable tie-break, not an inferred encounter date or person ID.
    return max(provenance[person], key=lambda folder: (cohort.RANK.get(source_status.get((person, folder)), 0), folder))


def operational_identity(person, preferred, private_rows):
    rows = [row for row in private_rows if row['person_id'] == person]
    preferred_rows = [row for row in rows if row['fallnr'] == preferred] or rows
    variants = bridge.matching.current_index(preferred_rows)[person]
    cleaned = {(without_title(surname), without_title(given), birth) for surname, given, birth in variants}
    surname, given, birth = max(cleaned, key=lambda value: (len(value[1].split()), len(value[1]), len(value[0]), value))
    identifiers = set()
    for row in rows:
        if row['identity_type'] == 'AT_SVNR':
            for value in row['svnr_normalized'].split(';'):
                if base.valid_svnr(value):
                    identifiers.add(value)
        elif row['identity_type'] == 'FOREIGN_ID' and row['identity_value_private']:
            identifiers.add(row['identity_value_private'])
    return dict(name=given + ' ' + surname, dob=birth, SVNR_or_foreign_id=';'.join(sorted(identifiers)))


def run():
    data = ROOT / 'data'; output = ROOT / 'output'; private_dir = data / 'private_identity'
    mri_dir = ROOT / 'caa_private_linkage'
    workbook = next((path for path in [ROOT / 'old_db_definition/orbis_01012015_01062023_amyloidangiopat.xlsx',
        data / 'old_retool_db/orbis_01012015_01062023_amyloidangiopat.xlsx'] if path.is_file()), None)
    if workbook is None:
        raise ValueError('VALIDATED_CAA_EXCEL_WORKBOOK_REQUIRED')
    safe_path = output / 'caa_patients_for_andreea_mri_download.csv'
    private_path = private_dir / 'caa_patients_for_andreea_mri_download_PRIVATE.csv'
    review_path = private_dir / 'caa_mri_status_review_PRIVATE.csv'
    source_paths = [workbook, mri_dir / 'mri_identity_series_PRIVATE.csv', mri_dir / 'mri_exam_key_PRIVATE.key']
    protected_paths = set(source_paths)
    protected_paths.update(path for path in output.glob('*.csv') if path != safe_path)
    protected_paths.update(path for path in data.glob('*.csv'))
    protected_paths.update(path for path in private_dir.glob('*.csv') if path not in {private_path, review_path})
    protected_paths.update(mri_dir.glob('*.csv'))
    protected = {path: base.utils.get_sha256(path) for path in protected_paths}
    safe = cohort.read(data / 'patient_identity_crosswalk.csv')
    private_current = cohort.read(private_dir / 'person_identity_crosswalk.csv')
    pdf_rows = cohort.read(data / 'pdf_person_identity_crosswalk.csv')
    ledger = cohort.read(private_dir / 'pdf_identity_adjudications.csv')
    overrides = cohort.read(data / 'person_caa_physician_overrides.csv')
    classes = cohort.read(output / 'pdf_db_caa_classification.csv')
    assert not cohort.read(output / 'person_identity_review.csv')
    assert all(row['person_id'] and row['identity_type'] != 'UNRESOLVED' for row in safe)
    people = bridge.matching.current_index(private_current)
    assert set(people) == {row['person_id'] for row in safe} and len(people) == 378
    assert {(row['person_id'], row['fallnr']) for row in safe} == {(row['person_id'], row['fallnr']) for row in private_current}
    folders = {path.name for path in (ROOT / 'pdf_db').iterdir() if path.is_dir()}
    assert len(folders) == 390
    target, source_status, provenance = load_target(safe, pdf_rows, ledger, classes, overrides, folders)
    secret = base.load_secret(source_paths[2])
    if secret is None:
        raise ValueError('EXISTING_LOCAL_HMAC_KEY_REQUIRED')
    mri = bridge.matching.collapse_mri(cohort.read(source_paths[1]), secret)
    assert len(mri) == 99
    excel = bridge.excel_index(workbook)
    validated = cohort.read(mri_dir / 'mri_via_caa_excel_to_current_person_PRIVATE.csv')
    validated_fallnr = defaultdict(set)
    for row in validated:
        if row['CAA_excel_fallnr']:
            validated_fallnr[row['MRI_identity_key']].add(row['CAA_excel_fallnr'])
    assert len(validated_fallnr) == 97
    svnr = bridge.current_svnr_index(private_current)
    statuses = Counter(); available = set(); uncertain = set(); review_keys = defaultdict(set)
    for group in mri.values():
        source = group['variants']; supporting = set()
        if group['key'] in validated_fallnr:
            matched_excel, _ = bridge.match_excel(group, excel)
            if matched_excel is None or matched_excel['fallnr'] != validated_fallnr[group['key']]:
                raise ValueError('VALIDATED_MRI_EXCEL_LINKAGE_CHANGED')
            source = matched_excel['variants']
            supporting = set().union(*(svnr.get(value, set()) for value in matched_excel['svnr']))
        status, candidates = resolve_person(source, people, supporting)
        statuses[status] += 1
        if status in {EXACT, SAFE}:
            assert len(candidates) == 1
            available.update(candidates)
        elif status == AMBIGUOUS:
            uncertain.update(candidates)
            for person in candidates:
                review_keys[person].add(group['key'])
    with_mri, needs_review, missing = cohort.partition_target(target, available, uncertain)
    final = []; operational = []; review = []
    for person in sorted(missing | needs_review):
        preferred = preferred_source(person, provenance, source_status)
        common = dict(person_id=person, preferred_fallnr=preferred,
                      all_fallnr=';'.join(sorted(provenance[person])), CAA_status=target[person])
        personal = operational_identity(person, preferred, private_current)
        if person in missing:
            final.append(common)
            operational.append(dict(common, **personal))
        else:
            review.append(dict(common, name=personal['name'], dob=personal['dob'],
                MRI_identity_keys=';'.join(sorted(review_keys[person])), issue='MRI_STATUS_REVIEW'))
    ordering = lambda row: (-cohort.RANK[row['CAA_status']], row['preferred_fallnr'], row['person_id'])
    final.sort(key=ordering); operational.sort(key=ordering); review.sort(key=ordering)
    assert len(final) == len({row['person_id'] for row in final}) == len(operational) == len(missing)
    assert len(review) == len(needs_review)
    assert {row['person_id'] for row in final}.isdisjoint(available | uncertain)
    assert all(row['CAA_status'] in cohort.RANK and row['preferred_fallnr'] in folders for row in final)
    assert all({key: row[key] for key in SAFE_FIELDS} == public for row, public in zip(operational, final))
    assert len(target) == len(with_mri) + len(missing) + len(needs_review)
    assert sum(statuses.values()) == 99
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    base.write_csv(private_path, PRIVATE_FIELDS, operational, private=True)
    if review or review_path.exists():
        base.write_csv(review_path, REVIEW_FIELDS, review, private=True)
    base.write_csv(safe_path, SAFE_FIELDS, final)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    result = [('UNIFIED_PERSONS', len(people)), ('CAA_TARGET_PERSONS', len(target)), ('OLD_MRI_IDENTITIES', len(mri)),
        ('OLD_MRI_MATCHED_TO_CURRENT_PERSON', statuses[EXACT] + statuses[SAFE]),
        ('OLD_MRI_NOT_IN_CURRENT_UNIFIED_COHORT', statuses[ABSENT]), ('OLD_MRI_AMBIGUOUS', statuses[AMBIGUOUS]),
        ('CAA_WITH_MRI_ALREADY_AVAILABLE', len(with_mri)), ('CAA_MRI_TO_DOWNLOAD', len(missing)),
        ('CAA_MRI_STATUS_REVIEW', len(needs_review)), ('FINAL_FILE', 'output/caa_patients_for_andreea_mri_download.csv'),
        ('PRIVATE_FILE', 'data/private_identity/caa_patients_for_andreea_mri_download_PRIVATE.csv'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]
    for label, value in result:
        print(f'{label} = {value}', flush=True)
    return dict(result)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        print('ANDREEA_MRI_EXPORT_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)
