"""Synthetic person-level CAA/MRI safety cases; no real patients or DB access."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
SPEC = importlib.util.spec_from_file_location('caa_person_mri_export',
    Path(__file__).parents[1] / 'scripts/build_caa_person_mri_list.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def person(surname='Muster', given='Anna', birth='1960-01-02'):
    return m.identity.identity(surname, given, birth)


def entry(value=None, **extra):
    return dict(identity=value, possible_dobs=set(), **extra)


def test_exact_name_dob_needs_no_shared_encounter_id():
    status, candidates, _ = m.resolve_mri(entry(person()), {'person-one': {person()}})
    assert status == m.EXACT and candidates == {'person-one'}


def test_safe_transliteration_hyphen_whitespace_and_dicom_middle_names():
    source = m.identity.dicom_identity(dict(patient_name_dicom='MÜLLER-LANG^ANNA^MARIA^^', dicom_dob='19600102'))
    status, candidates, _ = m.resolve_mri(entry(source), {'person-one': {person('Mueller Lang', 'Anna Maria')}})
    assert status == m.NORMALIZED and candidates == {'person-one'}


def test_middle_name_omission_is_reviewed_without_auto_available():
    status, candidates, _ = m.resolve_mri(entry(person()), {'one': {person(given='Anna Maria')}})
    assert status == m.AMBIGUOUS and candidates == {'one'}


def test_competing_middle_name_identity_prevents_unique_exact_auto_match():
    status, candidates, _ = m.resolve_mri(entry(person()),
        {'one': {person()}, 'two': {person(given='Anna Maria')}})
    assert status == m.AMBIGUOUS and candidates == {'one', 'two'}


def test_identical_name_wrong_dob_is_never_automatic():
    status, candidates, _ = m.resolve_mri(entry(person()), {'one': {person(birth='1961-01-02')}})
    assert status == m.AMBIGUOUS and candidates == {'one'}


def test_name_disagreement_with_same_surname_dob_is_reviewed():
    status, candidates, _ = m.resolve_mri(entry(person()), {'one': {person(given='Maria')}})
    assert status == m.AMBIGUOUS and candidates == {'one'}


def test_initials_are_not_full_given_names():
    status, candidates, _ = m.resolve_mri(entry(person(given='A.')), {'one': {person(given='A.')}})
    assert status == m.AMBIGUOUS and candidates == {'one'}


def test_multiple_person_ids_with_identical_demographics_stay_distinct():
    status, candidates, _ = m.resolve_mri(entry(person()), {'one': {person()}, 'two': {person()}})
    assert status == m.AMBIGUOUS and candidates == {'one', 'two'}


def test_clearly_different_identity_is_outside_current_cohort():
    status, candidates, _ = m.resolve_mri(entry(person()), {'one': {person('Beispiel', 'Maria', '1950-09-09')}})
    assert status == m.NO_MATCH and not candidates


def test_unidentified_positive_saved_flag_blocks_possible_dob_person():
    source = entry(); source['possible_dobs'] = {'1960-01-02'}
    status, candidates, _ = m.resolve_mri(source, {'one': {person()}, 'two': {person(birth='1950-01-02')}})
    assert status == m.AMBIGUOUS and candidates == {'one'}


def test_uncertain_linkage_is_excluded_from_extraction_list():
    assert m.partition_target({'one': 'CAA_ESTABLISHED', 'two': 'CAA_SUSPECTED',
        'three': 'CAA_REVIEW_CANDIDATE'}, {'one'}, {'two'}) == ({'one'}, {'two'}, {'three'})


def test_known_saved_mri_dominates_another_ambiguous_mri_identity():
    assert m.partition_target({'one': 'CAA_ESTABLISHED'}, {'one'}, {'one'}) == ({'one'}, set(), set())


def test_mixed_folder_diagnosis_is_not_transferred_to_other_person():
    target, _ = m.split_target_statuses({'source': 'CAA_ESTABLISHED'},
        {'source': {'one', 'two'}}, {'source': {'one': 'CAA_ESTABLISHED', 'two': 'CAA_NONE'}}, set())
    assert target == {'one': 'CAA_ESTABLISHED'}


def test_both_physician_confirmed_people_remain_eligible():
    target, _ = m.split_target_statuses({'source': 'CAA_ESTABLISHED'},
        {'source': {'one', 'two'}}, {'source': {'one': 'CAA_ESTABLISHED', 'two': 'CAA_NEGATED'}},
        {('one', 'source'), ('two', 'source')})
    assert target == {'one': 'CAA_ESTABLISHED', 'two': 'CAA_REVIEW_CANDIDATE'}


def test_corrected_single_patient_folder_does_not_keep_removed_person_diagnosis():
    target, _ = m.split_target_statuses({'source': 'CAA_ESTABLISHED'},
        {'source': {'one'}}, {'source': {'one': 'CAA_NONE'}}, set())
    assert not target


def test_multiple_encounters_aggregate_once_with_strongest_status():
    target, _ = m.split_target_statuses({'a': 'CAA_ESTABLISHED', 'b': 'CAA_SUSPECTED'},
        {'a': {'one'}, 'b': {'one'}}, {}, set())
    assert target == {'one': 'CAA_ESTABLISHED'}


def test_saved_flags_require_positive_value():
    assert all(m.positive(value) for value in ['t', 'TRUE', True])
    assert not any(m.positive(value) for value in ['f', '', None, False, 'unknown'])


def test_saved_flag_outside_inventory_is_linked_by_full_demographics():
    cases = [dict(id='case-key', fallnr='old-encounter', date_of_birth='1960-01-02')]
    radiology = [dict(case_id='case-key', saved_on_hdd='t'), dict(case_id='case-key', saved_on_hdd='t')]
    sources = m.saved_mri_sources([], cases, radiology, {'old-encounter': {person()}}, bytes(range(32)))
    assert len(sources) == 1
    source = next(iter(sources.values()))
    assert source['identity'] == person() and source['case_ids'] == {'case-key'}
    assert m.resolve_mri(source, {'current-person': {person()}})[0] == m.EXACT


def test_unknown_saved_flag_is_retained_as_review_evidence():
    cases = [dict(id='case-key', fallnr='old-encounter', date_of_birth='1960-01-02')]
    sources = m.saved_mri_sources([], cases, [dict(case_id='case-key', saved_on_hdd='t')],
        {'old-encounter': set()}, bytes(range(32)))
    source = next(iter(sources.values()))
    assert source['identity'] is None
    assert m.resolve_mri(source, {'current-person': {person()}})[0] == m.AMBIGUOUS
