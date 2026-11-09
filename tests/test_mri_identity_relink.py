"""Synthetic identity tests; no private files, PDF access or database calls."""
import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('identity_relink', Path(__file__).parents[1] / 'scripts/relink_mri_patient_identity.py')
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('value,expected', [
    ('19600102', '1960-01-02'), ('02.01.1960', '1960-01-02'),
    ('1960-01-02T00:00:00', '1960-01-02'), ('invalid', None),
])
def test_dob(value, expected):
    assert module.dob(value) == expected


@pytest.mark.parametrize('left,right,expected', [
    (('MUSTER', 'ANNA', '1960-01-02'), ('Muster', 'Anna', '1960-01-02'), 'EXACT_NAME_DOB'),
    (('MÜLLER-LANG', 'ANNA', '1960-01-02'), ('MUELLER LANG', 'ANNA', '1960-01-02'), 'SAFE_ORTHOGRAPHY_DOB'),
    (('ÖHL', 'ANNA', '1960-01-02'), ('OEHL', 'ANNA', '1960-01-02'), 'SAFE_ORTHOGRAPHY_DOB'),
    (('MUSTER', 'ANNA', '1960-01-02'), ('MUSTER', 'ANNA MARIA', '1960-01-02'), 'SAFE_ADDITIONAL_GIVEN_NAMES_DOB'),
    (('MUSTER', 'A', '1960-01-02'), ('MUSTER', 'ANNA', '1960-01-02'), None),
    (('MUSTER', 'ANNA', '1960-01-02'), ('MUSTER', 'ANNA', '1961-01-02'), None),
    (('MUSTER', 'ANNA', '1960-01-02'), ('MUSTAR', 'ANNA', '1960-01-02'), None),
])
def test_matching(left, right, expected):
    assert module.match_method(module.identity(*left), module.identity(*right)) == expected


def test_many_encounters_are_retained():
    person = module.identity('Muster', 'Anna', '1960-01-02')
    result, issue = module.resolve(person, {'first': person, 'second': person, 'third': person})
    assert set(result) == {'first', 'second', 'third'} and issue is None


def test_ambiguous_middle_names_are_not_linked():
    person = module.identity('Muster', 'Anna', '1960-01-02')
    result, issue = module.resolve(person, {
        'one': module.identity('Muster', 'Anna Maria', '1960-01-02'),
        'two': module.identity('Muster', 'Anna Elisabeth', '1960-01-02'),
    })
    assert result == {} and issue == 'MULTIPLE_POSSIBLE_PERSONS'


def test_dicom_middle_name():
    result = module.dicom_identity(dict(patient_name_dicom='MUSTER^ANNA^MARIA^^', dicom_dob='19600102'))
    assert result == ('MUSTER', 'ANNA MARIA', '1960-01-02')


@pytest.mark.parametrize('text,expected', [
    ('Anna Maria Muster, geboren am 02.01.1960', ('MUSTER', 'ANNA MARIA', '1960-01-02')),
    ('Muster, Anna, geboren am 02.01.1960', ('MUSTER', 'ANNA', '1960-01-02')),
    ('Anna van der Muster, geboren am 02.01.1960', ('VAN DER MUSTER', 'ANNA', '1960-01-02')),
    ('Anna Muster, geboren am 02.01.1960; Aufnahme am 03.11.2026', ('MUSTER', 'ANNA', '1960-01-02')),
    ('Salzburg, Klinik, 03.11.2026', None),
])
def test_header_birth_date_ownership(text, expected):
    assert module.header_identities(text) == ({expected} if expected else set())


def test_compound_surname_reference():
    person = module.identity('Muster Beispiel', 'Anna', '1960-01-02')
    assert module.header_identities('Anna Muster Beispiel, geboren am 02.01.1960', [person]) == {person}


def test_reference_never_changes_header_dob():
    person = module.identity('Muster Beispiel', 'Anna', '1960-01-02')
    result = module.header_identities('Anna Muster Beispiel, geboren am 02.01.1961', [person])
    assert result == {('MUSTER BEISPIEL', 'ANNA', '1961-01-02')}
    assert module.match_method(person, next(iter(result))) is None


def test_ambiguous_full_name_segmentation_is_not_guessed():
    references = [module.identity('Beispiel', 'Anna Maria', '1960-01-02'),
                  module.identity('Maria Beispiel', 'Anna', '1960-01-02')]
    assert module.header_identities('Anna Maria Beispiel, geboren am 02.01.1960', references) == set()
