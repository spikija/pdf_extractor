"""Independent identity/grammar counterexamples for ranked patient gender."""
from copy import deepcopy

import pytest

from test_case import bundle
from caa_pdf_extractor.extraction.gender_evidence import extract_gender, source_fingerprint, apply_to_case_output


FEMALE = 'Frau Ursula Oberbichler, geboren am 07.11.1947'
MALE = 'Herr Max Mustermann, geboren am 07.11.1947'


def source(text, heading='Anamnese', docid=1):
    out = bundle(text)
    out.documents[0].source_document_id = docid
    out.documents[0].sections[0].source_document_id = docid
    out.documents[0].sections[0].section_id = f'doc{docid}_s1'
    out.documents[0].sections[0].heading = heading
    return out


def combined(*texts):
    out = source(texts[0])
    for n, text in enumerate(texts[1:], 2):
        out.documents.extend(source(text, docid=n).documents)
    return out


@pytest.mark.parametrize('text,expected,tier', [
    (FEMALE, 'woman', 1),
    (MALE, 'man', 1),
    ('Ursula Oberbichler, geb.: 07.11.1947\nwir berichten über Frau Ursula Oberbichler, die sich vorstellt.', 'woman', 2),
    ('Max Mustermann, geb.: 07.11.1947\nwir berichten über Herrn Max Mustermann, der sich vorstellt.', 'man', 2),
    ('wir berichten über Frau Ursula Oberbichler, die sich vorstellt.', 'woman', 2),
    ('wir berichten über Ihren Patienten Max Mustermann, der sich vorstellt.', 'man', 2),
    ('wir berichten über Ihre Patientin Ursula Oberbichler, die sich vorstellt.', 'woman', 2),
    ('Ursula Oberbichler, geb.: 07.11.1947\n72-jährige Patientin', 'woman', 3),
    ('Max Mustermann, geb.: 07.11.1947\n84-jähriger Patient', 'man', 3),
])
def test_ranked_identity_evidence(text, expected, tier):
    result = extract_gender(source(text))
    assert (result['value'], result['winning_tier'], result['status']) == (expected, tier, 'VERIFIED')


@pytest.mark.parametrize('stray', [
    'Der Patient berichtet Schmerzen.',
    '84-jähriger Patient',
    'Die stationäre Aufnahme von Herrn Oberbichler erfolgte nach Vorstellung.',
])
def test_lower_tier_masculine_phrase_does_not_override_female_identity(stray):
    result = extract_gender(source(FEMALE + '\n' + stray))
    assert result['value'] == 'woman' and result['status'] == 'VERIFIED'
    rejected = [p for p in result['rejected_markers'] if p['value'] == 'man']
    assert rejected and all(p['reason'] == 'REJECTED_LOWER_TIER_CONTRADICTION' for p in rejected)
    assert all(p['value'] == 'woman' for p in result['provenance'])


def test_repeated_female_intro_beats_false_male_surname_in_narrative():
    intro = 'Ursula Oberbichler, geb.: 07.11.1947\nwir berichten über Frau Ursula Oberbichler, die sich vorstellt.'
    result = extract_gender(combined(intro + '\nDie stationäre Aufnahme von Herrn Oberbichler erfolgte akut.', intro))
    assert result['value'] == 'woman'
    assert {p['source_document_id'] for p in result['provenance'] if p['evidence_tier'] == 2} == {1, 2}
    assert any(p['reason'] == 'REJECTED_LOWER_TIER_CONTRADICTION' for p in result['rejected_markers'])


@pytest.mark.parametrize('other', [
    'Sehr geehrter Herr Dr. Herbert Hiessberger,',
    'Herrn Dr. Herbert Hiessberger',
    'Frau Dr. Anna Empfänger',
    'Der Hausarzt Herr Dr. Muster berichtet, der Patient sei gesund.',
    'Der Ehemann berichtet laut Patient: Der Patient hat Schmerzen.',
    'Der Informant Herr Oberbichler berichtet, der Patient sei gesund.',
    'Ihr Sohn ist ein 84-jähriger Patient.',
    'Die Freundin ist eine 72-jährige Patientin.',
    'Die Mutter berichtet: "Der Patient ist gesund."',
    'Herr Max Schmidt erzählt.\nDer Patient berichtet Schmerzen.',
    'Anna Müller erzählt, der Patient habe Schmerzen.',
    'Anna Müller erzählt.\nDer Patient habe Schmerzen.',
])
def test_recipient_relative_informant_and_clinician_are_rejected(other):
    result = extract_gender(source(FEMALE + '\n' + other))
    assert result['value'] == 'woman'
    assert all(p['value'] == 'woman' and p['evidence_tier'] == 1 for p in result['provenance'])


@pytest.mark.parametrize('heading', ['Empfänger', 'Adresse', 'Korrespondenz'])
def test_full_name_in_recipient_block_is_not_patient_header(heading):
    assert extract_gender(source(FEMALE, heading))['value'] is None


@pytest.mark.parametrize('titles', [
    ('Frau Ursula Oberbichler, geboren am 07.11.1947', 'Herr Ursula Oberbichler, geboren am 07.11.1947'),
    ('Ursula Oberbichler, geboren am 07.11.1947\nwir berichten über Frau Ursula Oberbichler, die sich vorstellt.',
     'Ursula Oberbichler, geboren am 07.11.1947\nwir berichten über Herrn Ursula Oberbichler, der sich vorstellt.'),
])
def test_genuine_same_level_identity_conflict_blocks(titles):
    result = extract_gender(combined(*titles))
    assert result['value'] is None and result['status'] == 'CONFLICTING_GENDER'


def test_many_repeats_do_not_outvote_identity_conflict():
    result = extract_gender(combined(*([FEMALE] * 6 + ['Herr Ursula Oberbichler, geboren am 07.11.1947'])))
    assert result['value'] is None and result['status'] == 'CONFLICTING_GENDER'


def test_male_identity_equivalent_with_stray_feminine_narrative():
    result = extract_gender(source(MALE + '\nDie Patientin berichtet Schmerzen.'))
    assert result['value'] == 'man'
    assert result['rejected_markers'][0]['reason'] == 'REJECTED_LOWER_TIER_CONTRADICTION'


@pytest.mark.parametrize('text', [
    'Patient Patientin Herr Frau der die',
    '72-jährige Patientin',
    'Sehr geehrte Frau Dr. Anna Muster',
    'Herrn Dr. Max Mustermann\nDer Patient berichtet Schmerzen.',
    'Die Mutter Ursula Oberbichler, geboren am 07.11.1947\n72-jährige Patientin',
    'Ursula Oberbichler, geb.: 07.11.1947\nwir berichten über Frau Anna Anders, die sich vorstellt.',
    'Ursula Oberbichler, geb.: 07.11.1947\nwir berichten über Frau Ursula Oberbichler, der sich vorstellt.',
    'Ursula Oberbichler, geb.: 07.11.1947\n„72-jährige Patientin\nDie Patientin berichtet Schmerzen.“',
    'Ursula Oberbichler, geb.: 07.11.1947\n84-jährige Patient',
    'Max Mustermann, geb.: 07.11.1947\n72-jähriger Patientin',
])
def test_unbound_and_mismatched_markers_do_not_qualify(text):
    assert extract_gender(source(text))['value'] is None


def test_titles_are_not_removed_from_name_prefixes():
    text = 'Alois Profanter, geb.: 07.11.1947\nwir berichten über Herrn Alois Profanter, der sich vorstellt.'
    assert extract_gender(source(text))['value'] == 'man'


def test_known_patient_name_reanchors_after_informant():
    text = ('Ursula Oberbichler, geb.: 07.11.1947\nHerr Max Schmidt berichtet.\n'
            'Frau Oberbichler wurde untersucht.\nDie Patientin berichtet Schmerzen.')
    result = extract_gender(source(text))
    assert result['value'] == 'woman'
    assert any(p['kind'] == 'subject' for p in result['provenance'])


def test_case_output_uses_only_winning_provenance_and_preserves_other_fields():
    output = {'update_fields': {'smoking': 'former'}, 'provenance': {'smoking': []},
              'demographics': {'update_fields': {}}, 'source_conflicts': []}
    result = apply_to_case_output(output, source(FEMALE + '\nDer Patient berichtet Schmerzen.'))
    assert result['update_fields'] == {'smoking': 'former', 'gender': 'woman'}
    assert all(p['value'] == 'woman' and p['evidence_tier'] == 1 for p in result['provenance']['gender'])


def test_source_fingerprint_changes_when_evidence_changes():
    original = source(FEMALE)
    changed = deepcopy(original)
    changed.documents[0].sections[0].source_text = MALE
    assert source_fingerprint(original) != source_fingerprint(changed)


@pytest.mark.parametrize('heading', ['Familienanamnese', 'Fremdanamnese', 'Angehörigenbericht'])
def test_other_person_section_is_not_index_narrative(heading):
    text = 'Ursula Oberbichler, geb.: 07.11.1947\nHerr Oberbichler wurde untersucht.\nDer Patient berichtet Schmerzen.'
    assert extract_gender(source(text, heading))['value'] is None


def test_explicit_identity_tier_outranks_lower_intro():
    b = combined(FEMALE, 'Ursula Oberbichler, geboren am 07.11.1947\nwir berichten über Herrn Ursula Oberbichler, der sich vorstellt.')
    result = extract_gender(b)
    assert result['value'] == 'woman'
    assert any(p['evidence_tier'] == 2 and p['reason'] == 'REJECTED_LOWER_TIER_CONTRADICTION' for p in result['rejected_markers'])


def test_repeated_low_tier_boilerplate_is_insufficient():
    text = 'Ursula Oberbichler, geb.: 07.11.1947\nDie Patientin berichtet Schmerzen.\nDie Patientin berichtet Schmerzen.'
    assert extract_gender(source(text))['value'] is None


def test_unanchored_section_does_not_inherit_only_document_identity():
    text = 'Ursula Oberbichler, geb.: 07.11.1947\n72-jährige Patientin'
    assert extract_gender(source(text, 'Allgemeine Informationen'))['value'] is None


@pytest.mark.parametrize('intro_name', ['Dr. Ferdinand K. Piech', 'Prof. Dr. Ferdinand K. Piech', 'Prof. h.c. Dr. Ferdinand K. Piech'])
def test_academic_prefixes_do_not_split_same_patient_identity(intro_name):
    text = ('Prof. h.c. Dr. Ferdinand K. PIECH, geboren am 17.04.1937\n'
            'Dr. Ferdinand K. Piech, geb.: 17.04.1937; Seite 2\n'
            'wir berichten über Herrn ' + intro_name + ', der sich vorstellt.')
    result = extract_gender(source(text))
    assert result['value'] == 'man' and result['winning_tier'] == 2


def test_academic_title_without_identity_is_not_gender_evidence():
    assert extract_gender(source('Prof. h.c. Dr. Ferdinand K. PIECH')).get('value') is None


def test_different_names_remain_ambiguous_after_academic_title_normalization():
    text = ('Prof. h.c. Dr. Ferdinand K. PIECH, geboren am 17.04.1937\n'
            'Dr. Franz Piech, geb.: 17.04.1937\n'
            'wir berichten über Herrn Dr. Ferdinand K. Piech, der sich vorstellt.')
    assert extract_gender(source(text))['value'] is None
