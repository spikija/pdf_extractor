import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from caa_pdf_extractor.extraction import encounter_granite_v2 as v2
from caa_pdf_extractor.preparation.models import PreparedDocument, PreparedSection


def snippet():
    return v2.Snippet('D15-SABC123', '123', 15, 'doc15_sec1', 'Anamnese', None, 1, 1, 0, 30, 'Historische Erkrankung und Aufnahme.')


def proposal():
    refs = ['D15-SABC123']
    return {'admission_reason': {'primary_reason': 'FOCAL_NEUROLOGICAL_DEFICIT',
            'flags': ['FOCAL_NEUROLOGICAL_DEFICIT', 'COGNITIVE_DECLINE'],
            'evidence_ids': refs, 'confidence': 'HIGH',
            'index_presentation': 'FOCAL_NEUROLOGICAL_DEFICIT',
            'current_encounter_purpose': 'REHABILITATION_CONTINUATION',
            'index_evidence_ids': refs, 'purpose_evidence_ids': refs},
            'functional_mrs': {'pre_event_mrs': None, 'pre_event_mrs_min': None, 'pre_event_mrs_max': None,
                'admission_mrs': None, 'admission_mrs_min': 3, 'admission_mrs_max': 4,
                'confidence': 'MODERATE', 'evidence_ids': refs,
                'pre_event_evidence_ids': [], 'admission_evidence_ids': refs,
                'functional_status': [{'domain': 'WALKING', 'interpretation': 'Semantische Modellinterpretation, kein Quellzitat erforderlich.',
                                      'temporal_ownership': 'CURRENT_ADMISSION', 'evidence_ids': refs}]}}


def test_model_semantics_and_paraphrase_preserved_without_second_classifier(monkeypatch):
    from caa_pdf_extractor.extraction import encounter_mrs, encounter_granite
    def forbidden(*args, **kwargs):
        pytest.fail('Legacy semantic function called')
    for module, names in [(encounter_mrs, ('classify_reason', 'functional_facts', 'estimate_mrs', 'temporal')),
                          (encounter_granite, ('locate', 'validate_reason', 'validate_function', 'score'))]:
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    raw = proposal()
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['candidate'] == raw
    assert result['status'] == 'TECHNICALLY_VALID_REVIEW_CANDIDATE'


@pytest.mark.parametrize('primary', ['FOCAL_NEUROLOGICAL_DEFICIT', 'EPILEPTIC_EVENT', 'COGNITIVE_DECLINE', 'OTHER', 'UNKNOWN'])
def test_all_semantic_categories_retained(primary):
    raw = proposal()
    raw['admission_reason']['primary_reason'] = primary
    assert v2.validate(raw, [snippet()], '123', 15)['candidate'] == raw


@pytest.mark.parametrize('score', list(range(7)))
def test_technical_layer_preserves_numeric_proposal_without_scoring_it(score):
    raw = proposal()
    raw['functional_mrs'].update(admission_mrs=score, admission_mrs_min=None, admission_mrs_max=None)
    assert v2.validate(raw, [snippet()], '123', 15)['candidate'] == raw


@pytest.mark.parametrize('patch', [
    {'admission_mrs': True, 'admission_mrs_min': None, 'admission_mrs_max': None},
    {'admission_mrs': 3.5, 'admission_mrs_min': None, 'admission_mrs_max': None},
    {'admission_mrs': 7, 'admission_mrs_min': None, 'admission_mrs_max': None},
    {'admission_mrs_min': 4, 'admission_mrs_max': 3},
    {'admission_mrs_min': 3, 'admission_mrs_max': 3},
    {'admission_mrs_min': 3, 'admission_mrs_max': None},
    {'admission_mrs': 3},
])
def test_numeric_structure_errors_only(patch):
    raw = proposal()
    raw['functional_mrs'].update(patch)
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['mrs_components']['admission']['status'] == 'TECHNICAL_FAILURE'
    assert result['candidate']['admission_reason'] == raw['admission_reason']
    assert result['mrs_components']['pre_event']['status'] == 'VALID'


@pytest.mark.parametrize('field,value', [('primary_reason', 'NEW_CATEGORY'), ('confidence', 'CERTAIN'), ('flags', ['UNKNOWN']), ('current_encounter_purpose', 'REHAB')])
def test_invalid_enums(field, value):
    raw = proposal()
    raw['admission_reason'][field] = value
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['admission_reason_status'] == 'TECHNICAL_FAILURE'
    assert result['functional_mrs_status'] == 'VALID'
    assert result['candidate']['functional_mrs'] == raw['functional_mrs']


@pytest.mark.parametrize('source,code', [
    (replace(snippet(), snippet_id='MISSING'), 'MISSING_SNIPPET_ID'),
    (replace(snippet(), patient_id='OTHER'), 'WRONG_PATIENT_OR_DOCUMENT'),
    (replace(snippet(), source_document_id=16), 'WRONG_PATIENT_OR_DOCUMENT'),
    (replace(snippet(), heading='Medikation'), 'DISALLOWED_SOURCE_SECTION'),
])
def test_evidence_registry_provenance(source, code):
    result = v2.validate(proposal(), [source], '123', 15)
    assert result['candidate'] is None and any(e['code'] == code for e in result['errors'])


def test_invalid_json_errors_do_not_log_patient_content():
    text = '{"sensitive_marker": PRIVATE_TEXT}'
    result = v2.validate(text, [snippet()], '123', 15)
    assert result['candidate'] is None and 'PRIVATE_TEXT' not in json.dumps(result)


def test_missing_keys_and_extra_quote_fields_rejected():
    for transform in ('missing', 'extra'):
        raw = proposal()
        if transform == 'missing':
            del raw['admission_reason']['primary_reason']
        else:
            raw['admission_reason']['exact_quote'] = 'Not part of ID-based interface'
        result = v2.validate(raw, [snippet()], '123', 15)
        assert result['admission_reason_status'] == 'TECHNICAL_FAILURE'
        assert result['candidate']['functional_mrs'] == raw['functional_mrs']


@pytest.mark.parametrize('period', ['admission', 'pre_event'])
@pytest.mark.parametrize('value', range(7))
def test_normalize_equal_exact_bounds_with_audit_and_no_input_mutation(period, value):
    raw = proposal()
    prefix = period + '_mrs'
    raw['functional_mrs'].update({prefix: value, prefix + '_min': value, prefix + '_max': value})
    before = copy.deepcopy(raw)
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['functional_mrs_status'] == 'VALID'
    assert result['mrs_components'][period]['candidate'] == {'score': value, 'min': None, 'max': None}
    assert result['normalizations'][0]['before'] == {'score': value, 'min': value, 'max': value}
    assert result['normalizations'][0]['after'] == {'score': value, 'min': None, 'max': None}
    assert raw == before


@pytest.mark.parametrize('values', [(2, 3, 4), (3, 2, 4), (None, 4, 3), (None, 3, None), (7, 7, 7), (-1, -1, -1), (True, True, True), (2.0, 2.0, 2.0)])
def test_genuinely_inconsistent_values_rejected_without_poisoning_siblings(values):
    raw = proposal()
    raw['functional_mrs'].update(dict(zip(('pre_event_mrs', 'pre_event_mrs_min', 'pre_event_mrs_max'), values)))
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['mrs_components']['pre_event']['status'] == 'TECHNICAL_FAILURE'
    assert result['mrs_components']['admission']['status'] == 'VALID'
    assert result['admission_reason_status'] == 'VALID'


def test_missing_reason_id_does_not_affect_function():
    raw = copy.deepcopy(proposal())
    raw['admission_reason']['evidence_ids'] = ['BAD_ID']
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['admission_reason_status'] == 'TECHNICAL_FAILURE'
    assert result['functional_mrs_status'] == 'VALID'
    assert result['candidate']['functional_mrs'] == raw['functional_mrs']
    error = next(e for e in result['errors'] if e['code'] == 'MISSING_SNIPPET_ID')
    assert error['id'] == 'BAD_ID' and error['path'] == ['admission_reason', 'evidence_ids']


def test_bad_functional_item_is_audited_without_discarding_scores_or_sibling():
    raw = copy.deepcopy(proposal())
    raw['functional_mrs']['functional_status'] *= 2
    raw['functional_mrs']['functional_status'][1] = {**raw['functional_mrs']['functional_status'][1], 'evidence_ids': ['MISSING']}
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['functional_mrs_status'] == 'PARTIAL_VALID'
    assert result['admission_reason_status'] == 'VALID'
    assert result['mrs_components']['admission']['status'] == 'VALID'
    assert len(result['candidate']['functional_mrs']['functional_status']) == 1
    assert result['functional_status_items'][1]['status'] == 'TECHNICAL_FAILURE'


def test_period_specific_reference_error_does_not_discard_other_period():
    raw = copy.deepcopy(proposal())
    raw['functional_mrs']['pre_event_evidence_ids'] = ['MISSING']
    result = v2.validate(raw, [snippet()], '123', 15)
    assert result['mrs_components']['pre_event']['status'] == 'TECHNICAL_FAILURE'
    assert result['mrs_components']['admission']['status'] == 'VALID'
    assert result['admission_reason_status'] == 'VALID'


def explicit_baseline(period, value=1):
    key = 'admission_mrs' if period == 'admission' else 'pre_event_mrs'
    time = 'CURRENT_ADMISSION' if period == 'admission' else 'BASELINE'
    return {key: {'score': value, 'min': None, 'max': None, 'source_type': 'EXPLICIT', 'confidence': 'HIGH', 'evidence': []},
            'functional_assessment': {'explicit_scores': [{'min': value, 'max': value, 'eligible': True, 'temporal_ownership': time}]}}


@pytest.mark.parametrize('period', ['admission', 'pre_event'])
def test_valid_explicit_score_preserved_conflicting_model_unchanged(period):
    raw = proposal()
    prefix = period + '_mrs'
    raw['functional_mrs'].update({prefix: 4, prefix + '_min': 4, prefix + '_max': 4})
    prior = explicit_baseline(period)
    before = copy.deepcopy(prior)
    result = v2.validate(raw, [snippet()], '123', 15, deterministic=prior)
    assert result['mrs_components'][period]['candidate']['score'] == 4
    assert result['final_mrs'][period]['value']['score'] == 1
    assert result['final_mrs'][period]['source_type'] == 'EXPLICIT'
    assert result['final_mrs'][period]['flags'] == ['EXPLICIT_MRS_CONFLICT']
    assert prior == before


def test_held_or_unowned_explicit_score_not_resurrected():
    raw = proposal()
    prior = explicit_baseline('admission')
    prior['functional_assessment']['explicit_scores'][0]['eligible'] = False
    result = v2.validate(raw, [snippet()], '123', 15, deterministic=prior)
    assert result['final_mrs']['admission']['source_type'] == 'GRANITE'
    assert result['final_mrs']['admission']['value']['min'] == 3


def test_coherent_stable_source_chunks_and_no_temporal_filtering():
    raw = ('Am 01.01.2026 trat eine Aphasie auf. Danach wurde sie verlegt. '
           'Bei Aufnahme besteht das Defizit. Zuvor ging sie selbständig. '
           'Bei Entlassung braucht sie Hilfe. Eine weitere Aussage.')
    section = PreparedSection('doc15_sec1', '123', 15, 'Anamnese', 1, 1, raw, raw)
    doc = PreparedDocument(15, 'test.pdf', 'test.pdf', 'sha', 'discharge_letter', '2026-01-10', [section])
    values, omissions = v2.prepare_snippets(doc, '123')
    assert len(values) == 2 and not omissions
    assert 'Danach wurde sie verlegt.' in values[0].text and 'Bei Aufnahme' in values[0].text
    assert 'Zuvor' in values[1].text and 'Entlassung' in values[1].text
    assert values == v2.prepare_snippets(copy.deepcopy(doc), '123')[0]
    for value in values:
        assert raw[value.source_start:value.source_end] == value.text


def test_prompt_delegates_scores_and_uses_ids():
    text = v2.prompt([snippet()], '123', 15, '2026-01-02', None)
    assert 'Schlage selbst pre_event_mrs und admission_mrs vor' in text
    assert 'D15-SABC123' in text and 'exact_quote' not in text
