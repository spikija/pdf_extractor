"""Synthetic ownership cases; never embed real patients or private HMAC keys."""
from collections import defaultdict

from caa_pdf_extractor import identity_adjudication as a
from caa_pdf_extractor import identity_resolution as r


def svnr(start=100):
    for serial in range(start, 1000):
        nine = str(serial) + '020160'
        check = sum(int(d) * w for d, w in zip(nine, (3, 7, 9, 5, 8, 4, 2, 1, 6))) % 11
        if check < 10:
            return nine[:3] + str(check) + nine[3:]
    raise AssertionError('Synthetic fixture unavailable')


def candidates():
    return {(r.AT, svnr()): {('MUSTER', 'ANNA', '1960-01-02')},
            (r.AT, svnr(200)): {('BEISPIEL', 'MARIA', '1960-01-02')}}


def test_pdf_svnr_owner_wins_over_storage_location():
    known = candidates(); second = list(known)[1]
    p = a.text_profile('SVNR: ' + second[1], 'synthetic')
    assert a.owner_from_text(p, '', known)[0] == second


def test_full_name_and_dob_need_exact_whole_names():
    known = candidates(); first = next(iter(known))
    text = 'Patient: Anna Muster\nDOB 2.1.1960'
    assert a.owner_from_text(r.new_profile(), text, known)[0] == first
    assert a.owner_from_text(r.new_profile(), 'Hannah Muster 2.1.1960', known)[0] is None
    assert a.owner_from_text(r.new_profile(), 'A. Muster 2.1.1960', known)[0] is None


def test_name_only_requires_physician_confirmed_two_person_scope():
    known = candidates(); first = next(iter(known)); p = r.new_profile()
    assert a.owner_from_text(p, 'Anna Muster', known)[0] is None
    token, evidence = a.owner_from_text(p, 'Anna Muster', known, physician_two_person=True)
    assert token == first and evidence == 'PHYSICIAN_TWO_PERSON_SCOPE_EXACT_FULL_NAME'
    assert a.owner_from_text(p, 'Anna Muster', {first: known[first]}, physician_two_person=True)[0] is None


def test_two_names_or_no_evidence_never_assign_by_majority():
    known = candidates(); p = r.new_profile()
    for text in ['', 'Muster', 'Anna Muster and Maria Beispiel']:
        assert a.owner_from_text(p, text, known, physician_two_person=True)[0] is None


def test_conflicting_dob_or_multiple_official_ids_block_name_fallback():
    known = candidates(); p = r.new_profile(); p['dobs'].add('1970-01-02')
    assert a.owner_from_text(p, 'Anna Muster', known, physician_two_person=True)[0] is None
    text = '\n'.join('SVNR: ' + token[1] for token in known)
    p = a.text_profile(text, 'synthetic')
    assert a.owner_from_text(p, 'Anna Muster', known, physician_two_person=True)[0] is None


def test_unknown_official_id_cannot_fall_back_to_familiar_name():
    p = a.text_profile('SVNR: ' + svnr(400), 'synthetic')
    assert a.owner_from_text(p, 'Anna Muster', candidates(), physician_two_person=True)[0] is None


def test_misfiled_pdf_does_not_link_secondary_person_to_source_encounter():
    assert a.source_relationships('source', 'A', 'primary', 'secondary', {'real', 'source'}) == {'real'}
    assert a.source_relationships('source', 'A', 'primary', 'secondary', set()) == set()
    assert a.source_relationships('source', 'A', 'primary', 'primary', {'other'}) == {'source'}


def test_both_physician_confirmed_people_retain_source_relationship():
    for owner in ['primary', 'secondary']:
        assert a.source_relationships('source', 'B', 'primary', owner, {'other'}) == {'source'}


def test_new_svnr_reuses_existing_unique_demographic_person_id():
    known = candidates(); token = next(iter(known)); profile = r.new_profile()
    profile['demographics'].add(('Muster', 'Anna', '1960-01-02', '2.1.1960'))
    profile['dobs'].add('1960-01-02')
    registry = {}; people = {('MUSTER', 'ANNA', '1960-01-02'): {'P_existing'}}
    at = defaultdict(set)
    assert a.resolve_person(bytes(range(32)), token, profile, registry, people, at) == 'P_existing'
    assert registry[token] == 'P_existing' and at['P_existing'] == {token[1]}


def test_different_valid_svnr_is_never_merged_by_same_demographics():
    token = next(iter(candidates())); profile = r.new_profile()
    profile['demographics'].add(('Muster', 'Anna', '1960-01-02', '2.1.1960'))
    profile['dobs'].add('1960-01-02')
    people = {('MUSTER', 'ANNA', '1960-01-02'): {'P_existing'}}
    at = defaultdict(set, {'P_existing': {svnr(300)}})
    assert a.resolve_person(bytes(range(32)), token, profile, {}, people, at) != 'P_existing'


def test_persisted_adjudication_routes_automatic_rebuild(tmp_path, monkeypatch):
    ledger = tmp_path / 'data/private_identity/pdf_identity_adjudications.csv'
    ledger.parent.mkdir(parents=True); ledger.write_text('hash-bound-ledger', encoding='utf-8')
    monkeypatch.setattr(r, 'ROOT', tmp_path)
    marker = object(); monkeypatch.setattr(a, 'run', lambda key: marker)
    assert r.run('existing-key') is marker
