"""Synthetic export safety cases; no real names, IDs, PDF/DICOM or DB access."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
SPEC = importlib.util.spec_from_file_location('andreea_export',
    Path(__file__).parents[1] / 'scripts/export_andreea_mri_download.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def person(given='Anna', birth='1960-01-02', surname='Muster'):
    return surname, given, birth


def test_academic_title_in_current_given_name_is_safe_normalization():
    assert m.person_method(person(), person('Dr. Anna')) == m.SAFE
    assert m.resolve_person({person()}, {'one': {person('Dr. Anna')}}, {'one'}) == (m.SAFE, {'one'})


def test_title_normalization_does_not_change_real_name_prefixes():
    assert m.without_title('Dragan') == 'Dragan'
    assert m.person_method(person(), person('Dragan')) is None


def test_name_matching_still_requires_exact_dob_after_title_removal():
    assert m.person_method(person(), person('Dr. Anna', '1961-01-02')) is None


def test_unresolvable_svnr_supported_name_disagreement_is_review_not_download():
    assert m.resolve_person({person()}, {'one': {person('Maria')}}, {'one'}) == (m.AMBIGUOUS, {'one'})


def test_conflicting_given_names_with_same_surname_and_dob_are_review_only():
    assert m.resolve_person({person()}, {'one': {person('Maria')}}) == (m.AMBIGUOUS, {'one'})


def test_competing_exact_and_middle_name_candidates_are_ambiguous():
    assert m.resolve_person({person()}, {'one': {person()}, 'two': {person('Anna Maria')}}) == (m.AMBIGUOUS, {'one', 'two'})


def test_unrelated_person_is_absent():
    assert m.resolve_person({person()}, {'one': {person('Maria', '1970-02-03', 'Beispiel')}}) == (m.ABSENT, set())


def test_known_mri_and_review_people_are_excluded_from_download_set():
    target = {'one': 'CAA_ESTABLISHED', 'two': 'CAA_SUSPECTED', 'three': 'CAA_REVIEW_CANDIDATE'}
    assert m.cohort.partition_target(target, {'one'}, {'two'}) == ({'one'}, {'two'}, {'three'})


def test_source_preference_uses_caa_evidence_instead_of_invented_encounter_dates():
    assert m.preferred_source('one', {'one': {'old-source', 'new-source'}},
        {('one', 'old-source'): 'CAA_ESTABLISHED', ('one', 'new-source'): 'CAA_SUSPECTED'}) == 'old-source'


def test_operational_identity_cleans_title_and_keeps_identifier_private():
    rows = [dict(person_id='one', fallnr='source', identity_type='FOREIGN_ID',
        surname_raw='Muster', given_name_raw='Dr. Anna', dob='1960-01-02',
        identity_value_private='Z123456789', svnr_normalized='')]
    assert m.operational_identity('one', 'source', rows) == dict(name='Anna Muster', dob='1960-01-02', SVNR_or_foreign_id='Z123456789')
    assert set(m.SAFE_FIELDS) == {'person_id', 'preferred_fallnr', 'all_fallnr', 'CAA_status'}
