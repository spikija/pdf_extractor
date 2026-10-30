import copy

import pytest

from caa_pdf_extractor.extraction import encounter_granite as g
from caa_pdf_extractor.extraction import encounter_mrs as b


def snippet(text, heading='Anamnese'):
    return {'source_document_id': 1, 'section_id': 's1', 'page': 1,
            'source_start': 10, 'source_end': 10 + len(text), 'exact_source_text': text,
            'heading': heading, 'section_date': None, 'reason_section': heading == 'Anamnese'}


def frozen():
    function = {'facts': [], 'explicit_scores': []}
    return {'admission_date': '2026-01-02', 'functional_assessment': function,
            'admission_reason': {'primary_reason': 'UNKNOWN', 'flags': {k: None for k in g.FLAGS}},
            'admission_mrs': b.estimate_mrs(function, 'CURRENT_ADMISSION'),
            'pre_event_mrs': b.estimate_mrs(function, 'BASELINE')}


def reason(category, text):
    return {'primary_reason': category, **{v: k == category for k, v in g.FLAGS.items()},
            'reasoning_evidence': [{'category': category, 'exact_quote': text}]}


@pytest.mark.parametrize('category,text', [
    ('FOCAL_NEUROLOGICAL_DEFICIT', 'Die Aufnahme erfolgte wegen einer akuten Hemiparese.'),
    ('EPILEPTIC_EVENT', 'Die Aufnahme erfolgte wegen eines generalisierten Anfalls.'),
    ('COGNITIVE_DECLINE', 'Die Aufnahme erfolgte wegen zunehmender Verwirrtheit.'),
    ('OTHER', 'Die geplante Aufnahme erfolgte zur Kontrolle.'),
])
def test_current_reason(category, text):
    result, accepted, rejected = g.validate_reason(reason(category, text), [snippet(text)], frozen())
    assert result['primary_reason'] == category and accepted and not rejected


@pytest.mark.parametrize('category,text', [
    ('FOCAL_NEUROLOGICAL_DEFICIT', 'Aufnahme zur Kontrolle bei Zustand nach Hemiparese.'),
    ('EPILEPTIC_EVENT', 'Aufnahme zur Kontrolle bei vorbekanntem epileptischem Anfall.'),
    ('COGNITIVE_DECLINE', 'Aufnahme zur Kontrolle bei vorbekannter Gedächtnisstörung.'),
    ('EPILEPTIC_EVENT', 'Die Aufnahme erfolgte zur EEG-Kontrolle unter Levetiracetam.'),
    ('FOCAL_NEUROLOGICAL_DEFICIT', 'Bei Aufnahme zeigte sich eine Hemiparese.'),
])
def test_history_and_exam_are_not_presenting_reason(category, text):
    result, accepted, rejected = g.validate_reason(reason(category, text), [snippet(text)], frozen())
    assert not accepted and rejected and result['primary_reason'] == 'UNKNOWN'


def test_unicode_whitespace_exact_mapping_and_paraphrase():
    s = snippet('Die Körperpflege\n  ist selbständig möglich.')
    p, error = g.locate('Die Körperpflege ist selbständig möglich.', [s])
    assert not error and p['quote_start'] == 10
    assert p['quote_end'] == s['source_end']
    assert g.locate('Körperpflege ist unabhängig möglich.', [s])[1] == 'EVIDENCE_QUOTE_MISMATCH'
    composed = snippet('Ko\u0308rperpflege selbständig')
    assert g.locate('Körperpflege selbständig', [composed])[0]


@pytest.mark.parametrize('text,expected', [
    ('Bei Aufnahme kann sie selbständig gehen.', 'CURRENT_ADMISSION'),
    ('Zuvor konnte sie selbständig gehen.', 'BASELINE'),
    ('Bei Entlassung kann sie selbständig gehen.', 'DISCHARGE'),
    ('Zu Hause geht sie mit Rollator.', 'BASELINE'),
])
def test_temporal_ownership(text, expected):
    assert g.ownership(snippet(text), '2026-01-02') == expected


@pytest.mark.parametrize('fact,state,text', [
    ('walking', 'HUMAN_ASSISTANCE', 'Bei Aufnahme geht sie mit Rollator.'),
    ('walking', 'UNABLE', 'Bei Aufnahme sitzt sie im Rollstuhl.'),
    ('bedbound', True, 'Bei Aufnahme besteht eine Hemiparese.'),
    ('constant_nursing', True, 'Zu Hause lebt eine 24-Stunden-Betreuerin.'),
    ('bodily_needs', 'DEPENDENT', 'Bei Aufnahme besteht eine Aphasie.'),
    ('own_affairs', 'INDEPENDENT', 'Sie lebt alleine.'),
])
def test_no_disability_shortcuts(fact, state, text):
    assert not g.semantic_fact(fact, state, text)


def test_aid_only_cannot_score():
    f = frozen()
    accepted = [{**snippet('Bei Aufnahme mit Rollator mobil.'), 'fact': 'walking_aid', 'state': 'PRESENT', 'temporal_ownership': 'CURRENT_ADMISSION'}]
    result = g.score(f, accepted, 'CURRENT_ADMISSION')
    assert result['score'] is None and result['min'] is None


def test_human_walking_help_conservative_four_five():
    text = 'Bei Aufnahme ist Gehen nur mit personeller Hilfe möglich.'
    claim = {'fact': 'walking', 'state': 'HUMAN_ASSISTANCE', 'exact_quote': text, 'temporal_ownership': 'CURRENT_ADMISSION'}
    accepted, rejected = g.validate_function({'facts': [claim]}, [snippet(text)], frozen())
    assert accepted and not rejected
    result = g.score(frozen(), accepted, 'CURRENT_ADMISSION')
    assert result['score'] is None and (result['min'], result['max']) == (4, 5)


def test_temporal_tag_cannot_relabel_home_or_discharge():
    for text in ('Zu Hause geht sie selbständig.', 'Bei Entlassung kann sie selbständig gehen.'):
        claim = {'fact': 'walking', 'state': 'INDEPENDENT', 'exact_quote': text, 'temporal_ownership': 'CURRENT_ADMISSION'}
        accepted, rejected = g.validate_function({'facts': [claim]}, [snippet(text)], frozen())
        assert not accepted and rejected[0]['error'] == 'TEMPORAL_OWNERSHIP_ERROR'


def test_own_affairs_does_not_score_four():
    rows = [{'fact': 'own_affairs_independent', 'state': 'PRESENT', 'temporal_ownership': 'CURRENT_ADMISSION'}]
    assert g.score(frozen(), rows, 'CURRENT_ADMISSION')['score'] is None


def test_explicit_value_is_immutable_on_discordance():
    f = frozen()
    f['functional_assessment']['explicit_scores'] = [{'min': 1, 'max': 1, 'eligible': True, 'temporal_ownership': 'CURRENT_ADMISSION'}]
    f['admission_mrs'] = b.estimate_mrs(f['functional_assessment'], 'CURRENT_ADMISSION')
    previous = copy.deepcopy(f)
    rows = [{'fact': 'needs_ADL_help', 'state': 'PRESENT', 'temporal_ownership': 'CURRENT_ADMISSION'}]
    result = g.score(f, rows, 'CURRENT_ADMISSION')
    assert result['score'] == 1 and result['source_type'] == 'EXPLICIT'
    assert result['hybrid_flags'] == ['EXPLICIT_FUNCTIONAL_DISCORDANCE']
    assert previous == f


def test_tied_baseline_reason_not_forced_by_model():
    f = frozen()
    f['admission_reason']['flags']['COGNITIVE_DECLINE'] = True
    text = 'Die Aufnahme erfolgte wegen einer Hemiparese.'
    result, accepted, rejected = g.validate_reason(reason('FOCAL_NEUROLOGICAL_DEFICIT', text), [snippet(text)], f)
    assert accepted and result['primary_reason'] == 'UNKNOWN'


def test_prompts_request_facts_not_scores():
    value = g.prompt('function', [], '2026-01-02')
    assert 'Never return or estimate mRS' in value and '"score"' not in value


def test_existing_source_period_cannot_be_changed_by_narrow_quote():
    text = 'Sie ist mit Rollator mobil.'
    f = frozen()
    f['functional_assessment']['facts'] = [{**snippet(text), 'fact': 'walking_aid', 'state': 'PRESENT', 'temporal_ownership': 'BASELINE'}]
    claim = {'fact': 'walking', 'state': 'AID_ONLY', 'exact_quote': text, 'temporal_ownership': 'CURRENT_ADMISSION'}
    accepted, rejected = g.validate_function({'facts': [claim]}, [snippet(text)], f)
    assert not accepted and rejected[0]['error'] == 'TEMPORAL_OWNERSHIP_ERROR'


@pytest.mark.parametrize('text', ['Sie kann nicht selbständig gehen.', 'Sie kann nicht mehr selbstständig gehen.'])
def test_negated_independent_walking_rejected(text):
    assert not g.semantic_fact('walking', 'INDEPENDENT', text)


def test_habitual_informant_function_is_baseline():
    p = snippet('Fremdanamnestisch laut Sohn braucht sie einen Rollator und ist mobil.')
    assert g.ownership(p, '2026-01-02') == 'BASELINE'
