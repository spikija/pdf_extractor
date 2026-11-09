"""Synthetic Excel bridge tests; no clinical files, PDF/DICOM reads or DBs."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
SPEC = importlib.util.spec_from_file_location('mri_excel_bridge',
    Path(__file__).parents[1] / 'scripts/link_mri_via_caa_excel.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def person(surname='Muster', given='Anna', birth='1960-01-02'):
    return surname, given, birth


def group(value=None, fallnr=None, svnr=None):
    return dict(variants={value or person()}, fallnr=set(fallnr or ['old-encounter']), svnr=set(svnr or []))


def svnr(start=100):
    for serial in range(start, 1000):
        nine = str(serial) + '020160'
        check = sum(int(d) * w for d, w in zip(nine, (3, 7, 9, 5, 8, 4, 2, 1, 6))) % 11
        if check < 10:
            return nine[:3] + str(check) + nine[3:]
    raise AssertionError('Synthetic fixture unavailable')


def test_same_excel_identity_multiple_encounters_is_one_person():
    excel = group(fallnr=['old-one', 'old-two'])
    result, status = m.match_excel(group(), {'same-person': excel})
    assert status == m.matching.EXACT and result['fallnr'] == {'old-one', 'old-two'}


def test_middle_name_omission_uses_safe_matching():
    result, status = m.match_excel(group(), {'same-person': group(person(given='Anna Maria'))})
    assert result and status == m.matching.SAFE


def test_different_excel_middle_name_identities_are_not_merged_by_missing_middle_name():
    result, status = m.match_excel(group(), {'one': group(person(given='Anna Maria')),
                                            'two': group(person(given='Anna Elisabeth'))})
    assert result is None and status == m.EXCEL_AMBIGUOUS


def test_explicit_shared_validated_svnr_supports_excel_name_variants():
    value = svnr()
    result, status = m.match_excel(group(), {'one': group(person(given='Anna Maria'), ['old-one'], [value]),
                                            'two': group(person(), ['old-two'], [value])})
    assert result['fallnr'] == {'old-one', 'old-two'} and status == m.matching.EXACT


def test_conflicting_valid_svnr_values_in_one_excel_identity_are_ambiguous():
    result, status = m.match_excel(group(), {'one': group(svnr=[svnr(), svnr(200)])})
    assert result is None and status == m.EXCEL_AMBIGUOUS


def test_dob_mismatch_is_not_an_excel_match():
    assert m.match_excel(group(), {'one': group(person(birth='1961-01-02'))}) == (None, m.EXCEL_NO_MATCH)


def test_global_demographics_work_without_shared_fallnr():
    assert m.resolve_current(group(fallnr=['old-only']), {'current-person': {person()}}, {}) == ('current-person', 'NAME_DOB_MATCH')


def test_same_fallnr_cannot_replace_conflicting_patient_demographics():
    assert m.resolve_current(group(fallnr=['shared-folder']),
        {'different-person': {person('Beispiel', 'Maria', '1970-01-02')}}, {}) == ('', m.CURRENT_ABSENT)


def test_validated_svnr_can_bridge_a_name_change():
    value = svnr()
    assert m.resolve_current(group(svnr=[value]),
        {'one': {person('Beispiel')}}, {value: {'one'}}) == ('one', 'VALIDATED_SVNR_MATCH')


def test_svnr_does_not_silently_override_conflicting_dob():
    value = svnr()
    assert m.resolve_current(group(svnr=[value]),
        {'one': {person(birth='1961-01-02')}}, {value: {'one'}}) == ('', m.CURRENT_AMBIGUOUS)


def test_distinct_valid_svnr_values_cannot_be_merged_by_identical_names():
    assert m.resolve_current(group(svnr=[svnr()]),
        {'one': {person()}}, {svnr(200): {'one'}}) == ('', m.CURRENT_AMBIGUOUS)


def test_svnr_validation_preserves_full_number_and_rejects_wrong_dob():
    value = svnr()
    assert m.validated_svnr(value[:4] + ' ' + value[4:], '1960-01-02') == value
    assert m.validated_svnr(value + '.0', '1960-01-02') == value
    assert m.validated_svnr(value, '1961-01-02') is None
    assert m.validated_svnr('Z' + value, '1960-01-02') is None
