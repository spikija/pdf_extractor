"""Existing DICOM identities -> CAA Excel demographics -> current people.

Excel defines a demographic bridge, not a replacement current cohort. Only the
CAA worksheet is read. No PDF/DICOM files, old case table or databases are read.
Fallnummer presence is reported separately from validated person ownership.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook

import link_dicom_to_current_person as matching
from caa_pdf_extractor import person_identity as base

ROOT = Path(__file__).resolve().parents[1]
EXCEL_AMBIGUOUS = 'AMBIGUOUS'
EXCEL_NO_MATCH = 'NO_MATCH'
CURRENT_ABSENT = 'EXCEL_MATCH_NOT_IN_CURRENT_PERSON_COHORT'
CURRENT_AMBIGUOUS = 'CURRENT_PERSON_AMBIGUOUS'
PRIVATE_FIELDS = ['MRI_identity_key', 'CAA_excel_fallnr', 'person_id', 'match_type']
PUBLIC_FIELDS = ['MRI_identity_key', 'person_id', 'match_type']


def validated_svnr(raw, birth):
    value = unicodedata.normalize('NFKC', str(raw or '')).strip()
    if re.fullmatch(r'[0-9]+\.0', value):
        value = value[:-2]
    # Do not strip letters from foreign IDs, coerce scientific notation or infer
    # missing digits. An Excel number must retain the entire validated SVNR.
    value = re.sub(r'[\s/\-]', '', value)
    if base.valid_svnr(value) and base.dob_suffix(birth) == value[-6:]:
        return value
    return None


def excel_index(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    groups = {}
    try:
        iterator = workbook['CAA'].iter_rows(values_only=True)
        headers = list(next(iterator))
        required = ['Fallnummer', 'Nachname', 'Vorname', 'Geburtsdatum']
        if any(headers.count(field) != 1 for field in required):
            raise ValueError('CAA_EXCEL_IDENTITY_HEADERS_NOT_UNIQUE_OR_MISSING')
        index = {field: headers.index(field) for field in required}
        svnr_column = headers.index('Vers.Nr.:') if 'Vers.Nr.:' in headers else None
        for row in iterator:
            fallnr = matching.previous.fallnr(row[index['Fallnummer']])
            surname = row[index['Nachname']]; given = row[index['Vorname']]
            birth = matching.previous.dob(row[index['Geburtsdatum']])
            if not fallnr or not surname or not given or not birth:
                continue
            person = str(surname), str(given), birth
            key = matching.canonical(person)
            group = groups.setdefault(key, dict(variants=set(), fallnr=set(), svnr=set()))
            group['variants'].add(person); group['fallnr'].add(fallnr)
            svnr = validated_svnr(row[svnr_column], birth) if svnr_column is not None else None
            if svnr:
                group['svnr'].add(svnr)
    finally:
        workbook.close()
    return groups


def match_excel(mri, excel):
    found = []
    for key, group in excel.items():
        methods = {matching.method(source, person) for source in mri['variants'] for person in group['variants']}
        methods.discard(None)
        if methods:
            found.append((group, matching.EXACT if matching.EXACT in methods else matching.SAFE))
    if not found:
        return None, EXCEL_NO_MATCH
    # Identical full demographic identities are already one group, regardless
    # of encounter count. Distinct middle-name identities need stronger support.
    if len(found) > 1:
        official = {tuple(sorted(group['svnr'])) for group, _ in found}
        if len(official) != 1 or len(next(iter(official))) != 1:
            return None, EXCEL_AMBIGUOUS
    if any(len(group['svnr']) > 1 for group, _ in found):
        return None, EXCEL_AMBIGUOUS
    combined = dict(variants=set(), fallnr=set(), svnr=set())
    for group, _ in found:
        for field in combined:
            combined[field].update(group[field])
    status = matching.EXACT if any(method == matching.EXACT for _, method in found) else matching.SAFE
    return combined, status


def current_svnr_index(rows):
    index = defaultdict(set)
    for row in rows:
        births = {matching.previous.dob(value) for value in row['dob'].split(';') if value}
        if len(births) != 1 or None in births:
            raise ValueError('CURRENT_PERSON_DOB_INVALID')
        birth = next(iter(births))
        for raw in row['svnr_normalized'].split(';'):
            value = validated_svnr(raw, birth)
            if value:
                index[value].add(row['person_id'])
    return index


def resolve_current(excel, people, svnr_index):
    official = set().union(*(svnr_index.get(value, set()) for value in excel['svnr']))
    demographic = {person_id for person_id, variants in people.items()
                   if any(matching.method(left, right) for left in excel['variants'] for right in variants)}
    if official:
        # A name change may be supported by SVNR, but a contradictory exact DOB
        # or competing demographic identity must not be silently overridden.
        if len(official) != 1 or demographic - official:
            return '', CURRENT_AMBIGUOUS
        person = next(iter(official))
        if {value[2] for value in people[person]} != {value[2] for value in excel['variants']}:
            return '', CURRENT_AMBIGUOUS
        return person, 'VALIDATED_SVNR_MATCH'
    if len(demographic) > 1:
        return '', CURRENT_AMBIGUOUS
    if demographic:
        person = next(iter(demographic))
        # Known different valid SVNRs cannot be bridged using name/DOB alone.
        current_official = {value for value, owners in svnr_index.items() if person in owners}
        if excel['svnr'] and current_official and not excel['svnr'] & current_official:
            return '', CURRENT_AMBIGUOUS
        return person, 'NAME_DOB_MATCH'
    return '', CURRENT_ABSENT


def run():
    candidates = [ROOT / 'old_db_definition/orbis_01012015_01062023_amyloidangiopat.xlsx',
                  ROOT / 'data/old_retool_db/orbis_01012015_01062023_amyloidangiopat.xlsx']
    workbook = next((path for path in candidates if path.is_file()), None)
    if workbook is None:
        raise ValueError('PREVIOUS_CAA_EXCEL_WORKBOOK_NOT_FOUND')
    private_dir = ROOT / 'caa_private_linkage'
    source = private_dir / 'mri_identity_series_PRIVATE.csv'
    safe_source = ROOT / 'data/patient_identity_crosswalk.csv'
    current_source = ROOT / 'data/private_identity/person_identity_crosswalk.csv'
    secret_path = private_dir / 'mri_exam_key_PRIVATE.key'
    private_path = private_dir / 'mri_via_caa_excel_to_current_person_PRIVATE.csv'
    safe_path = ROOT / 'output/mri_via_caa_excel_to_current_person.csv'
    protected_paths = {workbook, source, safe_source, current_source, secret_path}
    protected_paths.update(path for path in (ROOT / 'output').glob('*.csv') if path != safe_path)
    protected_paths.update(path for path in private_dir.glob('*.csv') if path != private_path)
    protected_paths.update((ROOT / 'data/private_identity').glob('*.csv'))
    protected = {path: base.utils.get_sha256(path) for path in protected_paths}
    secret = base.load_secret(secret_path)
    if secret is None:
        raise ValueError('EXISTING_LOCAL_HMAC_KEY_REQUIRED')
    mri = matching.collapse_mri(matching.previous.readcsv(source), secret)
    excel = excel_index(workbook)
    private_current = matching.previous.readcsv(current_source)
    safe_current = matching.previous.readcsv(safe_source)
    assert not any(row['identity_type'] == 'UNRESOLVED' or not row['person_id'] for row in safe_current)
    people = matching.current_index(private_current)
    assert len(people) == 378 and set(people) == {row['person_id'] for row in safe_current}
    svnr = current_svnr_index(private_current)
    folders = {path.name for path in (ROOT / 'pdf_db').iterdir() if path.is_dir()}
    safe_folders = {row['fallnr'] for row in safe_current}
    assert folders <= safe_folders
    counts = Counter(); private = []; public = []
    for group in sorted(mri.values(), key=lambda item: item['key']):
        excel_match, excel_status = match_excel(group, excel)
        person = ''; status = excel_status; fallnr = set()
        if excel_match is None:
            counts['excel_ambiguous' if excel_status == EXCEL_AMBIGUOUS else 'excel_no_match'] += 1
        else:
            counts['excel_matched'] += 1; fallnr = excel_match['fallnr']
            counts['direct_folder'] += bool(fallnr & folders & safe_folders)
            person, current_status = resolve_current(excel_match, people, svnr)
            if person:
                counts['current_matched'] += 1
            elif current_status == CURRENT_AMBIGUOUS:
                counts['current_ambiguous'] += 1; status = current_status
            else:
                counts['current_absent'] += 1; status = CURRENT_ABSENT
        common = dict(MRI_identity_key=group['key'], person_id=person, match_type=status)
        public.append(common)
        # One private row per Excel encounter preserves scalar Fallnummer values;
        # the safe output still has exactly one row per normalized MRI identity.
        for encounter in sorted(fallnr) or ['']:
            private.append(dict(MRI_identity_key=group['key'], CAA_excel_fallnr=encounter,
                                person_id=person, match_type=status))
    assert len(mri) == len(public) == len({row['MRI_identity_key'] for row in public}) == 99
    assert len(mri) == counts['excel_matched'] + counts['excel_ambiguous'] + counts['excel_no_match']
    assert counts['excel_matched'] >= 90, 'EXCEL_LINKAGE_BELOW_HISTORICAL_EXPECTATION'
    assert counts['excel_matched'] == counts['current_matched'] + counts['current_absent'] + counts['current_ambiguous']
    # This requested summary has no current-ambiguity bucket. Withhold publication
    # rather than misreport ambiguous current identities as absent.
    if counts['current_ambiguous']:
        raise ValueError('CURRENT_PERSON_AMBIGUITY_REQUIRES_SEPARATE_REVIEW')
    assert all(not row['person_id'] or row['person_id'] in people for row in public)
    assert len(private) == len({(row['MRI_identity_key'], row['CAA_excel_fallnr']) for row in private})
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    base.write_csv(private_path, PRIVATE_FIELDS, private, private=True)
    base.write_csv(safe_path, PUBLIC_FIELDS, public)
    assert all(base.utils.get_sha256(path) == digest for path, digest in protected.items())
    result = [('MRI_IDENTITIES_TOTAL', len(mri)), ('MRI_MATCHED_TO_CAA_EXCEL', counts['excel_matched']),
        ('MRI_AMBIGUOUS_IN_CAA_EXCEL', counts['excel_ambiguous']), ('MRI_NOT_MATCHED_TO_CAA_EXCEL', counts['excel_no_match']),
        ('MRI_EXCEL_MATCH_DIRECTLY_IN_CURRENT_PDFDB', counts['direct_folder']),
        ('MRI_EXCEL_MATCH_TO_CURRENT_PERSON_ID', counts['current_matched']),
        ('MRI_EXCEL_MATCH_NOT_IN_CURRENT_PERSON_COHORT', counts['current_absent']),
        ('PRIVATE_CROSSWALK', str(private_path)), ('SAFE_CROSSWALK', 'output/mri_via_caa_excel_to_current_person.csv'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]
    for label, value in result:
        print(f'{label} = {value}', flush=True)
    return dict(result)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        print('MRI_EXCEL_BRIDGE_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)
