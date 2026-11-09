"""Synthetic SVNR/privacy tests; no patient files or actual project secrets."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from caa_pdf_extractor import person_identity as identity


def synthetic_svnr(tail, start=100):
    # Independent nine-position reference calculation (fourth digit is absent).
    for serial in range(start, 1000):
        nine = f'{serial:03d}' + tail
        digit = sum(int(x)*w for x,w in zip(nine, (3,7,9,5,8,4,2,1,6))) % 11
        if digit < 10:
            return nine[:3] + str(digit) + nine[3:]
    raise AssertionError('No synthetic checksum fixture available')


def test_official_checksum_reference():
    # Public authority's worked example, assembled so no ten-digit ID is logged.
    value = '782' + '9' + '280755'
    assert identity.valid_svnr(value)
    assert not identity.valid_svnr(value[:3] + '8' + value[4:])


@pytest.mark.parametrize('tail,valid', [('290200', True), ('290201', False), ('310299', False),
                                       ('011380', False), ('000180', False)])
def test_real_calendar_date_required(tail, valid):
    assert identity.valid_svnr(synthetic_svnr(tail)) is valid


def test_structure_and_remainder_ten():
    assert not identity.valid_svnr('0' * 10)
    assert not identity.valid_svnr('1' * 9)
    for serial in range(100,1000):
        nine = str(serial) + '010180'
        if sum(int(x)*w for x,w in zip(nine,(3,7,9,5,8,4,2,1,6))) % 11 == 10:
            assert not identity.valid_svnr(nine[:3] + '0' + nine[3:])
            break
    else:
        raise AssertionError('Reference remainder-ten case missing')


@pytest.mark.parametrize('separator', ['', ' ', '-', '/'])
def test_labelled_format_variants(separator):
    value = synthetic_svnr('020160')
    text = 'SVNR: ' + value[:4] + separator + value[4:] + '\nAZ: unrelated'
    assert identity.candidates(text) == {value}


def test_unlabelled_number_is_not_automatically_an_svnr():
    value = synthetic_svnr('020160')
    assert identity.candidates('AZ: ' + value) == set()
    assert identity.candidates('Reference ' + value) == set()
    formatted = value[:4] + ' ' + value[4:]
    assert identity.candidates(formatted, {'1960-01-02'}, header=True) == {value}
    assert identity.candidates(formatted, {'1960-02-01'}, header=True) == set()
    assert identity.candidates(formatted, set(), header=True) == set()


def test_long_number_never_truncates_to_valid_candidate():
    value = synthetic_svnr('020160')
    result = identity.candidates('SVNR: ' + value + '1234')
    assert value not in result
    assert all(not identity.valid_svnr(v) for v in result)


def test_all_assertion_states_and_last_six_dob():
    first = synthetic_svnr('020160')
    second = synthetic_svnr('030160',200)
    assert identity.resolve({first}, {first}, {'1960-01-02'}) == identity.MATCH
    assert identity.resolve({first}, {first}, set()) == identity.UNAVAILABLE
    assert identity.resolve({first}, {first}, {'1960-02-01'}) == identity.DOB_CONFLICT
    assert identity.resolve({first}, {first}, {'1960-01-02','1960-02-01'}) == identity.DOB_CONFLICT
    assert identity.resolve({first,second}, {first,second}, {'1960-01-02'}) == identity.MULTIPLE
    assert identity.resolve(set(), {'malformed'}, set()) == identity.INVALID
    assert identity.resolve(set(), set(), set()) == identity.NOT_FOUND


def test_birth_date_ownership():
    value = dict(document={}, pages=[dict(text='Anna Muster, geboren am 02.01.1960\nAufnahme: 03.11.2026')])
    assert identity.document_dobs(value) == {'1960-01-02'}
    assert identity.document_dobs(dict(document={},pages=[dict(text='Aufnahme: 03.11.2026')])) == set()


def test_hmac_stability_domain_and_missing_key(tmp_path):
    key = bytes(range(32))  # synthetic test key, never the project secret
    value = synthetic_svnr('020160')
    first = identity.person_id(key,value)
    assert first == identity.person_id(key,value)
    assert first.startswith('P_') and len(first)==18
    assert value not in first
    assert first != identity.person_id(bytes(reversed(key)),value)
    missing = tmp_path/'missing.key'
    assert identity.load_secret(missing) is None and not missing.exists()
    path = tmp_path/'existing.key';path.write_bytes(key)
    assert identity.load_secret(path)==key  # includes binary whitespace bytes
    assert path.read_bytes()==key


def test_safe_csv_schema(tmp_path):
    path=tmp_path/'crosswalk.csv'
    rows=[dict(person_id='P_'+'a'*16,fallnr='synthetic',identity_status=identity.MATCH)]
    identity.write_csv(path,['person_id','fallnr','identity_status'],rows)
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        assert reader.fieldnames==['person_id','fallnr','identity_status']
        assert list(reader)==rows


def test_current_empty_native_artifact_is_reused_for_review(tmp_path):
    path=tmp_path/'empty.json'
    digest='a'*64
    payload=dict(document=dict(sha256=digest),pages=[dict(text='')])
    path.write_text(json.dumps(payload),encoding='utf-8')
    assert identity.read_payload(path,digest) is None
    assert identity.read_payload(path,digest,allow_empty=True)==payload
