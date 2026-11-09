"""Physician-authorized PDF ownership overrides, with immutable PDF provenance.

The PDF crosswalk covers the adjudicated folders only. Other folders inherit
their single person from the encounter crosswalk. A two-person source relation
is explicitly physician-confirmed; it does not assert that both people have
the same clinical encounter number. Misfiled PDFs never create that relation.
Private hash-bound decisions and the identity registry survive later rebuilds.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import identity_resolution as r
from . import person_identity as base

GROUP_A = frozenset({'2469660915', '2545338617', '2580065218', '2947015425', '2980158925'})
GROUP_B = frozenset({'2509414116', '2546006917', '2611010718', '2693909420',
                     '2694967920', '2767125621', '2898744824'})
TARGETS = GROUP_A | GROUP_B
PRIMARY = 'PHYSICIAN_CONFIRMED_PRIMARY'
SECOND = 'PHYSICIAN_CONFIRMED_SECOND_PATIENT'
MISFILED = 'MISFILED_PDF_REASSIGNED'
AMBIGUOUS = 'AMBIGUOUS'
SAFE_FIELDS = ['person_id', 'fallnr', 'identity_type', 'identity_status', 'identity_confidence']
PDF_FIELDS = ['source_fallnr', 'filename', 'person_id', 'assignment_status']
LEDGER_FIELDS = PDF_FIELDS + ['sha256', 'identity_type', 'identity_status', 'token_private', 'evidence']
ENCOUNTER = re.compile(r'\b(?:AZ|Fallnummer|Fall[\s.-]*Nr\.?|Aufnahmezahl)\s*[:.#]?\s*([0-9]{10})(?![0-9])', re.I)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def encode(token):
    return json.dumps(token, ensure_ascii=False, separators=(',', ':'))


def text_profile(text, digest):
    profile = r.new_profile()
    r.collect(dict(pages=[dict(text=text)]), profile, digest)
    return profile


def merge_profile(destination, source):
    for field, value in source.items():
        if isinstance(value, list):
            destination[field].extend(value)
        else:
            destination[field].update(value)


def full_name_occurs(wanted, text):
    """Whole tokens only: Anna must never match Hannah; retain all name parts."""
    words = [r.normalized_name(word) for word in re.findall(r'[^\W\d_]+', text, re.UNICODE)]
    for start in range(len(words)):
        joined = ''
        for word in words[start:]:
            joined += word
            if joined == wanted:
                return True
            if len(joined) >= len(wanted):
                break
    return False


def exact_dob_occurs(birth, text):
    year, month, day = map(int, birth.split('-'))
    # No century inference: accept the complete four-digit DOB only.
    pattern = rf'(?<!\d)0?{day}[\s./-]+0?{month}[\s./-]+{year}(?!\d)'
    return bool(re.search(pattern, text) or re.search(r'(?<!\d)' + re.escape(birth) + r'(?!\d)', text))


def owner_from_text(profile, text, candidates, *, physician_two_person=False):
    """Validated ID first, then exact demographics, then physician closed set.

    The last fallback is restricted to a physician-confirmed TWO-person folder,
    requires a unique complete full name, and rejects contradictory birth data.
    It cannot assign unlabelled/missing evidence by folder membership or majority.
    """
    result = r.initial(profile)
    if result['token'] and result['identity_type'] in {r.AT, r.FOREIGN}:
        token = result['token']
        return (token, 'VALIDATED_OFFICIAL_IDENTIFIER') if token in candidates else (None, 'UNEXPECTED_OFFICIAL_IDENTIFIER')
    at, foreign = r.official(profile)
    if at or foreign:
        return None, 'CONTRADICTORY_OFFICIAL_IDENTIFIER'
    key, issue = r.demographic_key(profile)
    if issue == r.DEMO_CONFLICT:
        return None, 'CONTRADICTORY_DEMOGRAPHICS'
    matches = {token for token, keys in candidates.items() if key and key in keys}
    if len(matches) == 1:
        return next(iter(matches)), 'EXACT_FULL_NAME_AND_DOB'
    matches = set()
    name_only = set()
    for token, keys in candidates.items():
        for surname, given, birth in keys:
            if profile['dobs'] and profile['dobs'] != {birth}:
                continue
            if full_name_occurs(surname, text) and full_name_occurs(given, text):
                name_only.add(token)
                if exact_dob_occurs(birth, text):
                    matches.add(token)
    if len(matches) == 1 and len(name_only) == 1:
        return next(iter(matches)), 'EXACT_FULL_NAME_AND_DOB_HEADER'
    if physician_two_person and len(candidates) == 2 and len(name_only) == 1:
        return next(iter(name_only)), 'PHYSICIAN_TWO_PERSON_SCOPE_EXACT_FULL_NAME'
    return None, 'INSUFFICIENT_OR_AMBIGUOUS_PDF_IDENTITY'


def source_relationships(folder, group, primary, owner, encounters):
    if owner == primary or group == 'B':
        return {folder}
    # Group A secondary: an actual documented encounter, never storage provenance.
    return set(encounters) - {folder}


def private_row(common, profile, result):
    value = ''; country = ''
    if result['identity_type'] == r.AT:
        value = next(iter(result['at'])); country = 'AT'
    elif result['identity_type'] == r.FOREIGN:
        country, value = next(iter(result['foreign']))
    elif result['identity_type'] == r.DEMO:
        value = encode(result['demo'])
    return dict(common, identity_value_private=value, identity_country=country,
        svnr_normalized=';'.join(sorted(result['at'])),
        surname_raw=';'.join(sorted({item[0] for item in profile['demographics']})),
        given_name_raw=';'.join(sorted({item[1] for item in profile['demographics']})),
        dob=';'.join(sorted(profile['dobs'] or {item[2] for item in profile['demographics']})),
        dob_raw=';'.join(sorted({item[3] for item in profile['demographics']})),
        sex_raw=';'.join(sorted(profile['sex_raw'])), nationality_raw=';'.join(sorted(profile['country_raw'])),
        identifiers_raw=';'.join(sorted({item['label'] + '=' + item['raw'] for item in profile['identifiers']})))


def registry_from_private(rows):
    registry = {}; demographic_people = defaultdict(set); person_at = defaultdict(set)
    for row in rows:
        person = row['person_id']
        if not person:
            continue
        kind = row['identity_type']; value = row.get('identity_value_private', '')
        if kind == r.AT and base.valid_svnr(value):
            token = (r.AT, value); person_at[person].add(value)
        elif kind == r.FOREIGN and value:
            token = (r.FOREIGN, row['identity_country'], value)
        elif kind == r.DEMO and value:
            token = (r.DEMO, *json.loads(value))
        else:
            continue
        if token in registry and registry[token] != person:
            raise ValueError('INCONSISTENT_EXISTING_PERSON_REGISTRY')
        registry[token] = person
        # Only complete, single-valued private demographics may preserve an old
        # demographic/foreign person's ID when a new official identifier appears.
        fields = [row.get(key, '') for key in ('surname_raw', 'given_name_raw', 'dob')]
        if all(fields) and all(';' not in value for value in fields):
            surname, given, birth = fields
            if r.full_name(surname) and r.full_name(given, given=True) and base.canonical_dob(birth):
                demographic_people[(r.normalized_name(surname), r.normalized_name(given), birth)].add(person)
    return registry, demographic_people, person_at


def resolve_person(secret, token, profile, registry, demographic_people, person_at):
    if token in registry:
        return registry[token]
    key, _ = r.demographic_key(profile)
    matches = demographic_people.get(key, set())
    if token[0] == r.AT and len(matches) == 1:
        person = next(iter(matches))
        if not person_at[person] or person_at[person] == {token[1]}:
            registry[token] = person; person_at[person].add(token[1])
            return person
    person = r.pseudonym(secret, token)
    if token[0] == r.AT:
        if person_at[person] - {token[1]}:
            raise ValueError('DIFFERENT_AUSTRIAN_IDENTITIES_COLLAPSED')
        person_at[person].add(token[1])
    registry[token] = person
    return person


def run(key_file=None):
    root = base.ROOT; directory = root / 'data/private_identity'
    directory.mkdir(parents=True, exist_ok=True); base.protect(directory)
    private_path = directory / 'person_identity_crosswalk.csv'
    ledger_path = directory / 'pdf_identity_adjudications.csv'
    registry_path = directory / 'person_identity_registry.csv'
    review_path = root / 'output/person_identity_review.csv'
    protected = {path: base.utils.get_sha256(path) for path in (root / 'output').glob('*.csv') if path != review_path}
    secret_path = Path(key_file or os.getenv('PERSON_ID_HMAC_KEY_FILE') or root / 'caa_private_linkage/mri_exam_key_PRIVATE.key')
    secret = base.load_secret(secret_path)
    if secret is None:
        raise RuntimeError('LOCAL_HMAC_KEY_REQUIRED')
    protected[secret_path] = base.utils.get_sha256(secret_path)
    old_private = read_csv(private_path)
    registry, demographic_people, person_at = registry_from_private(old_private)
    for row in read_csv(registry_path):
        token = tuple(json.loads(row['identity_token_private']))
        if token in registry and registry[token] != row['person_id']:
            raise ValueError('PERSISTED_PERSON_ID_CHANGED')
        registry[token] = row['person_id']
        if token[0] == r.AT:
            person_at[row['person_id']].add(token[1])
    if any(len(values) > 1 for values in person_at.values()):
        raise ValueError('DIFFERENT_AUSTRIAN_IDENTITIES_COLLAPSED')
    saved = {(row['source_fallnr'], row['filename'], row['sha256']): row for row in read_csv(ledger_path)}
    patients, rows = base.inventory()
    if not TARGETS <= set(patients):
        raise ValueError('ADJUDICATED_SOURCE_FOLDER_MISSING')
    parsed, reparsed, unavailable = base.payloads(rows)
    if reparsed:
        raise RuntimeError('ADJUDICATION_REQUIRES_EXISTING_PARSED_ARTIFACTS')
    profiles = {pid: r.new_profile() for pid in patients if pid not in TARGETS}
    documents = []; candidates = defaultdict(lambda: defaultdict(set))
    for row in rows:
        payload = parsed.get(row['digest'])
        if payload is None:
            raise ValueError('PDF_ARTIFACT_UNAVAILABLE')
        if row['fallnr'] not in TARGETS:
            r.collect(payload, profiles[row['fallnr']], row['digest'])
            continue
        profile = r.new_profile(); r.collect(payload, profile, row['digest'])
        header = '\n'.join(payload['pages'][0]['text'].splitlines()[:60])
        result = r.initial(profile); token = result['token']; key, _ = r.demographic_key(profile)
        if token and result['identity_type'] in {r.AT, r.FOREIGN}:
            candidates[row['fallnr']][token]  # retain official ID even without demographics
            if key:
                candidates[row['fallnr']][token].add(key)
        filename = row['path'].relative_to(root / 'pdf_db' / row['fallnr']).as_posix()
        documents.append(dict(row=row, profile=profile, header=header, filename=filename,
                              encounters=set(ENCOUNTER.findall(header))))
    if any(len(candidates[folder]) != 2 for folder in GROUP_B):
        raise ValueError('PHYSICIAN_TWO_PERSON_FOLDER_DOES_NOT_HAVE_TWO_IDENTITIES')
    if any(not candidates[folder] for folder in GROUP_A):
        raise ValueError('MISFILED_FOLDER_HAS_NO_VALIDATED_IDENTITY')
    ocr_count = 0
    for document in documents:
        row = document['row']; folder = row['fallnr']; known = candidates[folder]
        token, evidence = owner_from_text(document['profile'], document['header'], known)
        old = saved.get((folder, document['filename'], row['digest']))
        if token is None and old and old['token_private']:
            candidate = tuple(json.loads(old['token_private']))
            # Reuse a hash-bound narrow decision only inside the same approved
            # two-person scope. A changed source always requires fresh evidence.
            if candidate in known and folder in GROUP_B and len(known) == 2:
                token, evidence = candidate, old['evidence']
        if token is None:
            payload = parsed[row['digest']]
            for page_number in range(len(payload['pages'])):
                text = r.ocr_header(row['path'], page_number); ocr_count += 1
                ocr_profile = text_profile(text, 'ocr:' + row['digest'])
                token, evidence = owner_from_text(ocr_profile, text, known, physician_two_person=folder in GROUP_B)
                if token is not None:
                    evidence = 'LOCAL_OCR_' + evidence
                    break
        document.update(token=token, evidence=evidence)
        if token:
            key = ('PDF_OWNER', folder, token)
            profiles.setdefault(key, r.new_profile())
            merge_profile(profiles[key], document['profile'])
    primary = {}
    for folder in TARGETS:
        owners = {doc['token'] for doc in documents if doc['row']['fallnr'] == folder and doc['token']}
        explicit = {doc['token'] for doc in documents if doc['row']['fallnr'] == folder
                    and folder in doc['encounters'] and doc['token']}
        if len(explicit) == 1:
            primary[folder] = next(iter(explicit))
        elif folder in GROUP_A and len(owners) == 1:
            # Physician specifies one intended patient; current remaining PDFs
            # unanimously identify that one person. No second person is inferred.
            primary[folder] = next(iter(owners))
        else:
            raise ValueError('PRIMARY_PATIENT_NOT_DETERMINISTICALLY_ESTABLISHED')
    r.fallback_demographics({key: value for key, value in profiles.items() if isinstance(key, str)})
    results = r.assign(profiles)
    if r.regression_failures(profiles, results):
        raise ValueError('PRIVATE_IDENTITY_REGRESSION_FAILED')
    resolved_people = {}
    for key, result in results.items():
        token = result['token']
        if isinstance(key, tuple) and (token != key[2] or result['identity_type'] == r.UNRESOLVED):
            raise ValueError('PDF_OWNER_PROFILE_CONTRADICTS_ADJUDICATION')
        resolved_people[key] = resolve_person(secret, token, profiles[key], registry,
            demographic_people, person_at) if token else ''
    safe = {}; private = {}; pdf_rows = []; ledger = []; overrides = []; review = []
    source_people = defaultdict(set); encounters_by_person = defaultdict(set)
    def add_relationship(key, fallnr, status=None):
        result = results[key]; person = resolved_people[key]
        common = dict(person_id=person, fallnr=fallnr, identity_type=result['identity_type'],
                      identity_status=status or result['status'], identity_confidence=result['confidence'])
        pair = person, fallnr
        safe[pair] = common; private[pair] = private_row(common, profiles[key], result)
        if person:
            encounters_by_person[person].add(fallnr)
    for folder in patients:
        if folder in TARGETS:
            continue
        add_relationship(folder, folder)
        person = resolved_people[folder]
        if person:
            source_people[folder].add(person)
        else:
            review.append(dict(fallnr=folder, identity_status=results[folder]['status'], short_reason='Manual identity review required'))
    for document in documents:
        row = document['row']; folder = row['fallnr']; token = document['token']
        person = resolved_people[('PDF_OWNER', folder, token)] if token else ''
        status = AMBIGUOUS if not token else PRIMARY if token == primary[folder] else SECOND if folder in GROUP_B else MISFILED
        common = dict(source_fallnr=folder, filename=document['filename'], person_id=person, assignment_status=status)
        pdf_rows.append(common)
        ledger.append(dict(common, sha256=row['digest'], identity_type=token[0] if token else r.UNRESOLVED,
            identity_status='PHYSICIAN_ADJUDICATED_PDF_OWNER' if token else AMBIGUOUS,
            token_private=encode(token) if token else '', evidence=document['evidence']))
        if not token:
            review.append(dict(fallnr=folder, identity_status=AMBIGUOUS, short_reason='PDF ownership remains ambiguous'))
            continue
        source_people[folder].add(person)
        group = 'B' if folder in GROUP_B else 'A'
        key = ('PDF_OWNER', folder, token)
        for encounter in source_relationships(folder, group, primary[folder], token, document['encounters']):
            add_relationship(key, encounter, status)
    for folder in sorted(GROUP_B):
        if len(source_people[folder]) != 2:
            raise ValueError('PHYSICIAN_TWO_PERSON_SPLIT_COLLAPSED')
        for person in sorted(source_people[folder]):
            overrides.append(dict(person_id=person, source_fallnr=folder, CAA_PHYSICIAN_CONFIRMED='TRUE',
                                  source='PHYSICIAN_ADJUDICATION_TWO_PERSON_FOLDER'))
    # Validate exactly-once ownership and conservation before writing any output.
    expected = {(doc['row']['fallnr'], doc['filename']) for doc in documents}
    assert len(pdf_rows) == len(expected) == sum(row['fallnr'] in TARGETS for row in rows)
    assert {(row['source_fallnr'], row['filename']) for row in pdf_rows} == expected
    assert all(row['assignment_status'] in {PRIMARY, SECOND, MISFILED, AMBIGUOUS} for row in pdf_rows)
    assert all(bool(row['person_id']) == (row['assignment_status'] != AMBIGUOUS) for row in pdf_rows)
    assert all(len(value) <= 1 for value in person_at.values())
    assert all(len(person) == 18 and person.startswith('P_') for person in registry.values())
    safe_rows = sorted(safe.values(), key=lambda row: (row['fallnr'], row['person_id']))
    private_rows = sorted(private.values(), key=lambda row: (row['fallnr'], row['person_id']))
    raw_at = {token[1] for token in registry if token[0] == r.AT}
    public_values = [str(value) for row in safe_rows + pdf_rows + overrides for value in row.values()]
    assert not any(identifier in value for identifier in raw_at for value in public_values)
    after_patients, after_rows = base.inventory()
    assert patients == after_patients
    assert [(row['relative_path'], row['digest']) for row in rows] == [(row['relative_path'], row['digest']) for row in after_rows]
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    base.write_csv(private_path, list(private_rows[0]), private_rows, private=True)
    base.write_csv(ledger_path, LEDGER_FIELDS, sorted(ledger, key=lambda row: (row['source_fallnr'], row['filename'])), private=True)
    registry_rows = [dict(person_id=person, identity_token_private=encode(token)) for token, person in sorted(registry.items())]
    base.write_csv(registry_path, ['person_id', 'identity_token_private'], registry_rows, private=True)
    base.write_csv(root / 'data/patient_identity_crosswalk.csv', SAFE_FIELDS, safe_rows)
    base.write_csv(root / 'data/pdf_person_identity_crosswalk.csv', PDF_FIELDS,
                   sorted(pdf_rows, key=lambda row: (row['source_fallnr'], row['filename'])))
    base.write_csv(root / 'data/person_caa_physician_overrides.csv',
                   ['person_id', 'source_fallnr', 'CAA_PHYSICIAN_CONFIRMED', 'source'], overrides)
    base.write_csv(review_path, ['fallnr', 'identity_status', 'short_reason'],
                   sorted({encode(row): row for row in review}.values(), key=lambda row: row['fallnr']))
    people = set().union(*source_people.values())
    # Every PDF has either an explicit affected-folder row or an inherited
    # single-person relationship. No split patient is reduced to a folder key.
    assert all(row['fallnr'] in source_people or row['fallnr'] in {item['fallnr'] for item in review} for row in rows)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    counts = [('TOTAL_SOURCE_FALLNR', len(patients)), ('TOTAL_PDFS', len(rows)),
        ('UNIQUE_PERSONS', len(people)),
        ('PERSONS_WITH_MULTIPLE_FALLNR', sum(len(values) > 1 for values in encounters_by_person.values())),
        ('SOURCE_FOLDERS_WITH_MULTIPLE_PERSONS', sum(len(values) > 1 for values in source_people.values())),
        ('PHYSICIAN_CONFIRMED_TWO_PERSON_FOLDERS', len(GROUP_B)),
        ('MISFILED_PDFS_REASSIGNED', sum(row['assignment_status'] == MISFILED for row in pdf_rows)),
        ('IDENTITY_UNRESOLVED', len({row['fallnr'] for row in review})),
        ('IDENTITY_CONFLICTS_REMAINING', sum(result['identity_type'] == r.UNRESOLVED and result['status'] != r.NOT_FOUND for result in results.values())),
        ('SAFE_PERSON_CROSSWALK', 'data/patient_identity_crosswalk.csv'),
        ('PDF_PERSON_CROSSWALK', 'data/pdf_person_identity_crosswalk.csv'),
        ('SOURCE_PDF_FILES_MODIFIED', 0), ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]
    for label, value in counts:
        print(f'{label} = {value}', flush=True)
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    try:
        run(args.key_file)
    except Exception as error:
        # No exception values: they may contain direct identifiers or source text.
        print('IDENTITY_ADJUDICATION_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
