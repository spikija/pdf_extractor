"""Synthetic MRI/person matching tests; no clinical source data or DB calls."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
SPEC = importlib.util.spec_from_file_location('dicom_current_person_link',
    Path(__file__).parents[1] / 'scripts/link_dicom_to_current_person.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def person(surname='Muster', given='Anna', birth='1960-01-02'):
    return surname, given, birth


def resolve(source, people):
    return m.resolve(dict(variants={source}), people)


def test_dicom_structured_name_and_middle_name():
    assert m.parse_name('MUSTER^ANNA^MARIA^^') == ('MUSTER', 'ANNA MARIA')
    assert m.parse_name(' Muster, Anna Maria ') == ('Muster', 'Anna Maria')


def test_exact_normalized_matching_handles_full_orthography():
    assert m.method(person('Müller-Lang', ' Anna  Maria '),
                    person('MUELLER LANG', 'ANNA-MARIA')) == m.EXACT
    assert m.method(person('Groß'), person('GROSS')) == m.EXACT


def test_safe_middle_name_omission_with_exact_dob():
    assert resolve(person(), {'one': {person(given='Anna Maria')}}) == ('one', m.SAFE, 1)


def test_conflicting_middle_names_never_use_first_name_only():
    assert resolve(person(given='Anna Maria'), {'one': {person(given='Anna Elisabeth')}}) == ('', m.NO_MATCH, 0)


def test_multiple_encounters_of_one_person_are_not_ambiguous():
    assert resolve(person(), {'one': {person(), person('MUSTER', 'ANNA')}}) == ('one', m.EXACT, 1)


def test_exact_and_safe_candidates_at_different_person_ids_are_ambiguous():
    assert resolve(person(), {'one': {person()}, 'two': {person(given='Anna Maria')}}) == ('', m.AMBIGUOUS, 2)


def test_identical_demographics_do_not_merge_different_valid_person_ids():
    assert resolve(person(), {'one': {person()}, 'two': {person()}}) == ('', m.AMBIGUOUS, 2)


def test_exact_dob_is_mandatory():
    assert resolve(person(), {'one': {person(birth='1961-01-02')}}) == ('', m.NO_MATCH, 0)


def test_initials_and_arbitrary_spelling_similarity_are_not_matches():
    assert m.method(person(given='A.'), person(given='A.')) is None
    assert m.method(person('Mustar'), person('Muster')) is None


def test_patientid_aliases_collapse_by_normalized_name_and_dob():
    rows = [dict(patient_name_dicom='MÜLLER-LANG^ANNA', birth_date_yyyymmdd='19600102',
                 dicom_patient_id='old-id', study_uid='study-1', series_uid='series-1'),
            dict(patient_name_dicom='MUELLER LANG, Anna', birth_date_yyyymmdd='1960-01-02',
                 dicom_patient_id='new-id', study_uid='study-2', series_uid='series-2')]
    groups = m.collapse_mri(rows, bytes(range(32)))
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert len(group['studies']) == 2 and len(group['series']) == 2 and len(group['raw_names']) == 2
    assert group['key'].startswith('MRI_')


def test_private_encounters_collapse_under_existing_person_id():
    row = dict(person_id='one', identity_type='AT_SVNR', surname_raw='Muster',
               given_name_raw='Anna', dob='1960-01-02', fallnr='old-encounter')
    people = m.current_index([row, dict(row, fallnr='new-encounter')])
    assert people == {'one': {person()}}
