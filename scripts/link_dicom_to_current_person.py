"""Link the existing DICOM identity CSV directly to current people.

No DICOM/PDF files or databases are opened. Old ORBIS encounter numbers and saved
flags are not matching inputs. Public output contains only pseudonyms/statuses;
names, birth dates and unmatched identity review remain in protected local files.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import relink_mri_patient_identity as previous
from caa_pdf_extractor import person_identity as base
from caa_pdf_extractor.identity_resolution import full_name, normalized_name

ROOT = Path(__file__).resolve().parents[1]
EXACT = 'EXACT_NAME_DOB_MATCH'
SAFE = 'SAFE_NORMALIZED_NAME_DOB_MATCH'
AMBIGUOUS = 'MRI_PERSON_AMBIGUOUS'
NO_MATCH = 'MRI_PERSON_NO_MATCH'
PUBLIC_FIELDS = ['MRI_identity_key', 'person_id', 'match_type']
PRIVATE_FIELDS = PUBLIC_FIELDS + ['raw_PatientName', 'PatientBirthDate', 'study_count', 'series_count']
REVIEW_FIELDS = ['MRI_identity_key', 'PatientName', 'PatientBirthDate', 'candidate_person_count', 'issue']


def parse_name(raw):
    value = unicodedata.normalize('NFKC', str(raw or '')).split('=')[0].strip()
    if '^' in value:
        parts = value.split('^')
        surname, given = parts[0], ' '.join(part for part in parts[1:3] if part)
    elif ',' in value:
        surname, given = value.split(',', 1)
    else:
        raise ValueError('DICOM_PATIENT_NAME_ORDER_NOT_EXPLICIT')
    return surname.strip(), given.strip()


def canonical(person):
    surname, given, birth = person
    date = previous.dob(birth)
    result = normalized_name(surname), normalized_name(given), date
    if not all(result):
        raise ValueError('INCOMPLETE_PATIENT_IDENTITY_METADATA')
    return result


def opaque_key(secret, person):
    value = json.dumps(person, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'MRI_' + hmac.new(secret, b'pdf-extractor.mri.patient-name-dob.v1\0' + value,
                            hashlib.sha256).hexdigest()[:24]


def collapse_mri(rows, secret):
    groups = {}
    for row in rows:
        surname, given = parse_name(row['patient_name_dicom'])
        birth = previous.dob(row['birth_date_yyyymmdd'])
        person = surname, given, birth
        key = canonical(person)
        group = groups.setdefault(key, dict(key=opaque_key(secret, key), variants=set(),
            raw_names=set(), studies=set(), series=set()))
        group['variants'].add(person)
        group['raw_names'].add(row['patient_name_dicom'])
        if row['study_uid'].strip():
            group['studies'].add(row['study_uid'].strip())
        if row['series_uid'].strip():
            group['series'].add(row['series_uid'].strip())
    if len({group['key'] for group in groups.values()}) != len(groups):
        raise ValueError('MRI_IDENTITY_KEY_COLLISION')
    return groups


def current_index(rows):
    people = defaultdict(set)
    for row in rows:
        if not row['person_id'] or row['identity_type'] == 'UNRESOLVED':
            raise ValueError('CURRENT_PERSON_IDENTITY_UNRESOLVED')
        dates = {previous.dob(value) for value in row['dob'].split(';') if value}
        if len(dates) != 1 or None in dates:
            raise ValueError('CURRENT_PERSON_DOB_MISSING_OR_CONFLICTING')
        birth = next(iter(dates))
        for surname in row['surname_raw'].split(';'):
            for given in row['given_name_raw'].split(';'):
                if surname.strip() and given.strip():
                    canonical((surname, given, birth))
                    people[row['person_id']].add((surname, given, birth))
    if any(len({value[2] for value in variants}) != 1 for variants in people.values()):
        raise ValueError('CURRENT_PERSON_HAS_CONFLICTING_DOB_ACROSS_ENCOUNTERS')
    return people


def method(left, right):
    if left[2] != right[2]:
        return None
    # A shared initial is insufficient even if the two sources both abbreviate.
    if not all(full_name(value[0]) and full_name(value[1], given=True) for value in (left, right)):
        return None
    if canonical(left) == canonical(right):
        return EXACT
    # Reuse the established full-token middle-name and orthography rules. There
    # is no edit-distance, first-three-letters or DOB-only automatic matching.
    normalized_left = previous.identity(*left)
    normalized_right = previous.identity(*right)
    if normalized_left and normalized_right and previous.match_method(normalized_left, normalized_right):
        return SAFE
    return None


def resolve(group, people):
    candidates = {}
    for person_id, variants in people.items():
        methods = {method(mri, current) for mri in group['variants'] for current in variants}
        if EXACT in methods:
            candidates[person_id] = EXACT
        elif SAFE in methods:
            candidates[person_id] = SAFE
    if len(candidates) > 1:
        return '', AMBIGUOUS, len(candidates)
    if candidates:
        person = next(iter(candidates))
        return person, candidates[person], 1
    return '', NO_MATCH, 0


def run():
    private_dir = ROOT / 'caa_private_linkage'
    source = private_dir / 'mri_identity_series_PRIVATE.csv'
    current = ROOT / 'data/private_identity/person_identity_crosswalk.csv'
    safe_source = ROOT / 'data/patient_identity_crosswalk.csv'
    secret_path = private_dir / 'mri_exam_key_PRIVATE.key'
    safe_path = ROOT / 'output/mri_to_current_person.csv'
    private_path = private_dir / 'mri_to_current_person_PRIVATE.csv'
    review_path = private_dir / 'mri_to_current_person_review_PRIVATE.csv'
    protected_paths = {source, current, safe_source, secret_path}
    protected_paths.update(path for path in (ROOT / 'output').glob('*.csv') if path != safe_path)
    protected_paths.update(path for path in private_dir.glob('*.csv') if path not in {private_path, review_path})
    protected_paths.update((ROOT / 'data/private_identity').glob('*.csv'))
    protected = {path: base.utils.get_sha256(path) for path in protected_paths}
    safe_people = previous.readcsv(safe_source)
    assert not any(row['identity_type'] == 'UNRESOLVED' or not row['person_id'] for row in safe_people)
    people = current_index(previous.readcsv(current))
    assert set(people) == {row['person_id'] for row in safe_people}
    assert len(people) == 378
    secret = base.load_secret(secret_path)
    if secret is None:
        raise ValueError('EXISTING_LOCAL_HMAC_KEY_REQUIRED')
    groups = collapse_mri(previous.readcsv(source), secret)
    assert len(groups) == 99
    public = []; private = []; review = []; counts = Counter()
    for identity, group in sorted(groups.items(), key=lambda item: item[1]['key']):
        person_id, status, candidate_count = resolve(group, people)
        counts[status] += 1
        common = dict(MRI_identity_key=group['key'], person_id=person_id, match_type=status)
        public.append(common)
        raw_name = ';'.join(sorted(group['raw_names']))
        private.append(dict(common, raw_PatientName=raw_name, PatientBirthDate=identity[2],
            study_count=str(len(group['studies'])), series_count=str(len(group['series']))))
        if status in {AMBIGUOUS, NO_MATCH}:
            review.append(dict(MRI_identity_key=group['key'], PatientName=raw_name,
                PatientBirthDate=identity[2], candidate_person_count=str(candidate_count), issue=status))
    assert sum(counts.values()) == len(public) == len({row['MRI_identity_key'] for row in public}) == 99
    assert set(counts) <= {EXACT, SAFE, AMBIGUOUS, NO_MATCH}
    assert all(bool(row['person_id']) == (row['match_type'] in {EXACT, SAFE}) for row in public)
    assert all(not row['person_id'] or row['person_id'] in people for row in public)
    assert len(review) == counts[AMBIGUOUS] + counts[NO_MATCH]
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    base.write_csv(private_path, PRIVATE_FIELDS, private, private=True)
    if review or review_path.exists():
        base.write_csv(review_path, REVIEW_FIELDS, review, private=True)
    base.write_csv(safe_path, PUBLIC_FIELDS, public)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    values = [('MRI_IDENTITIES_TOTAL', len(groups)), ('MRI_EXACT_NAME_DOB_MATCH', counts[EXACT]),
        ('MRI_SAFE_NORMALIZED_MATCH', counts[SAFE]), ('MRI_PERSON_AMBIGUOUS', counts[AMBIGUOUS]),
        ('MRI_PERSON_NO_MATCH', counts[NO_MATCH]), ('MRI_TOTAL_MATCHED_CURRENT_PERSON', counts[EXACT] + counts[SAFE]),
        ('SAFE_CROSSWALK', 'output/mri_to_current_person.csv'), ('PRIVATE_CROSSWALK', str(private_path)),
        ('REVIEW_FILE', str(review_path) if review else 'NONE'), ('DICOM_REREAD_REQUIRED', 'NO'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]
    for label, value in values:
        print(f'{label} = {value}', flush=True)
    return dict(values)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        # Never log exception values, source names, birth dates or identifiers.
        print('MRI_PERSON_LINKAGE_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)
