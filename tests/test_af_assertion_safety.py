import json
import re
from pathlib import Path

import pytest

from caa_pdf_extractor.extraction.assertion_scope import AF_PATTERN, validate_assertion, af_observation, resolve_af
from caa_pdf_extractor.extraction import diagnosis_events_v4 as dx
from caa_pdf_extractor.extraction.research_scores_v23 import cha2ds2_va
from caa_pdf_extractor.extraction.research_evidence_v23 import facts_from_section, af_state_from_facts
from caa_pdf_extractor.extraction.assertion_review import checked_evidence, unsafe_af_identity
from caa_pdf_extractor.workbench_store import WorkbenchStore, sha, fingerprint
from review_app.display_models import diagnosis_entities


NEGATIVE = [
    'Ein Vorhofflimmern konnte nicht detektiert werden.',
    'Kein Vorhofflimmern nachweisbar.', 'Kein Hinweis auf Vorhofflimmern.',
    'kein Nachweis von Vorhofflimmern', 'Vorhofflimmern nicht nachweisbar',
    'Vorhofflimmern nicht festgestellt', 'Vorhofflimmern nicht detektiert',
    'kein VHF', 'VHF nicht dokumentiert', 'kein Anhalt für Vorhofflimmern',
    'ohne Nachweis von Vorhofflimmern', 'kein Vorhofflimmern im Monitoring',
    'kein VHF im Langzeit-EKG', 'kein Hinweis auf VHF',
    'während des Monitorings kein Vorhofflimmern',
    'ohne\nDetektion von Vorhofflimmern',
    'Vorhofflimmern konnte im vorliegenden 24-Stunden-\nEKG keines detektiert werden.',
]
UNCERTAIN = ['V.a. Vorhofflimmern.', 'Vorhofflimmern kann nicht ausgeschlossen werden.',
             'möglicherweise paroxysmales Vorhofflimmern',
             'zur Detektion von Vorhofflimmern']
POSITIVE = ['Bekanntes Vorhofflimmern.', 'Persistierendes Vorhofflimmern.',
            'Vorhofflimmern bekannt, aktuell keine Tachyarrhythmie.',
            'Bei bekanntem Vorhofflimmern konnte aktuell keine Arrhythmie detektiert werden.',
            'Kein Hinweis auf frische Ischämie bei bekanntem Vorhofflimmern.',
            'Keine frische Ischämie bei Vorhofflimmern.']


def observation(text, day='2020-01-01', **kwargs):
    match = AF_PATTERN.search(text)
    return af_observation(text, match.start(), match.end(), observation_date=day, **kwargs)


@pytest.mark.parametrize('text,expected', [(s, 'NEGATED') for s in NEGATIVE] + [(s, 'UNCERTAIN') for s in UNCERTAIN] + [(s, 'CONDITIONAL') for s in ['sofern eine strenge Indikation vorliegen sollte (z.B. KHK mit Stent, Vorhofflimmern) nur nach Bildgebung']] + [(s, 'ESTABLISHED') for s in POSITIVE])
def test_scoped_assertion_after_semantic_model(text, expected):
    result = observation(text, model_assertion='ESTABLISHED')
    assert result['assertion_status'] == expected
    assert result['AF_ESTABLISHED_FROM_THIS_EVIDENCE'] == (expected == 'ESTABLISHED')
    if expected == 'NEGATED':
        assert result['AF_PRESENT'] is False and result['scope'] == 'CURRENT_ASSESSMENT'


def synthetic(text, later=None):
    docs = [(text, '2020-01-01')]
    if later:
        docs.append((later, '2021-01-01'))
    bundle = {'patient_id': 'synthetic', 'documents': [dict(source_document_id=i, document_date=day,
        sections=[dict(section_id=f'd{i}', heading='Diagnosen', text=txt, page_start=1)])
        for i, (txt, day) in enumerate(docs, 1)]}
    return dx.assemble('synthetic', bundle, {'mentions': [], 'audit_only': []})


@pytest.mark.parametrize('text', NEGATIVE + UNCERTAIN)
def test_nonpositive_evidence_cannot_create_af_identity(text):
    result = synthetic(text)
    assert not any(r['canonical_name'] == 'ATRIAL_FIBRILLATION' for r in result['canonical_disease_identities'])
    assert result['af_state']['confirmed_af'] is False


def test_negative_monitoring_does_not_erase_independent_positive_or_backdate_index():
    result = synthetic('Bekanntes Vorhofflimmern.', 'Kein VHF im Langzeit-EKG.')
    assert result['af_state']['AF_state'] == 'CONFIRMED_AF'
    assert len(result['canonical_disease_identities']) == 1
    assert result['af_state']['negative_observation_count'] == 1
    result = synthetic('Kein VHF im Langzeit-EKG.', 'Bekanntes Vorhofflimmern.')
    assert result['af_state']['af_index_date'] == '2021-01-01'
    assert result['canonical_disease_identities'][0]['first_documented_date'] == '2021-01-01'
    assert resolve_af(result['af_observations'], index_date='2020-06-01')['confirmed_af'] is False


def test_direct_same_period_contradiction_requires_review():
    result = resolve_af([observation('Bekanntes Vorhofflimmern.'), observation('Kein Vorhofflimmern.')])
    assert result['AF_state'] == 'CONFLICTING_AF_EVIDENCE' and result['review_required']


def test_general_concept_scope_distinguishes_ischemia_and_arrhythmia_from_af():
    for text, concept in [('Keine frische Ischämie bei Vorhofflimmern.', 'Ischämie'),
                          ('Bei bekanntem Vorhofflimmern konnte aktuell keine Arrhythmie detektiert werden.', 'Arrhythmie'),
                          ('Eine Lungenembolie konnte nicht nachgewiesen werden.', 'Lungenembolie')]:
        start = text.index(concept)
        assert validate_assertion(text, start, start + len(concept), model_assertion='ESTABLISHED')['assertion_status'] == 'NEGATED'


def test_public_v4_classifier_cannot_repeat_original_failure():
    assert dx.classify(NEGATIVE[0], concept=True) == ('NEGATED', False)
    assert dx.classify('Vorhofflimmern kann nicht ausgeschlossen werden.', concept=True) == ('DIFFERENTIAL_DIAGNOSIS', False)


def test_rerun_state_schema_and_nonpositive_event_assertions_are_retained():
    result = synthetic('Kein Vorhofflimmern im Monitoring.\nKeine transitorische ischämische Attacke.')
    state = result['af_state']
    assert state['patient_id'] == 'synthetic'
    assert state['AF_state'] == 'NO_CONFIRMED_AF'
    assert state['negative_evidence_count'] == 1 and state['earliest_confirmed_date'] is None
    assert {'positive_evidence_count', 'uncertain_evidence_count', 'review_required'} <= state.keys()
    assert any(a['canonical_name'] == 'TIA' and a['assertion_status'] == 'NEGATED'
               and a['assertion_rule_version'] for a in result['diagnostic_assertion_timeline'])
    assert result['canonical_clinical_events'] == []


def test_prospective_exclusion_and_imaging_comparison_cannot_establish_disease():
    result = synthetic('Da vor Beginn einer Infliximab-Therapie eine Herzinsuffizienz ausgeschlossen sein soll, erfolgte ein Konsil mit erhaltener Pumpfunktion und kein Anhalt auf Klappenvitien.')
    assert not any(d['canonical_name']=='HEART_FAILURE' for d in result['canonical_disease_identities'])
    result = synthetic('In Zahl und Größe zunehmende Mikroblutungen wie bei Amyloidangiopathie, DD: Im Rahmen einer Vaskulitis.')
    assert not any(d['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY' for d in result['canonical_disease_identities'])
    assert any(a['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY' and a['assertion_status']=='SUSPECTED' for a in result['diagnostic_assertion_timeline'])


def test_differential_does_not_spill_into_independent_historical_event():
    result = synthetic('Verdacht auf Amyloidangiopathie Zustand nach Subarachnoidalblutungen 2013')
    assert any(e['event_type']=='SAH' for e in result['canonical_clinical_events'])
    assert not result['canonical_disease_identities']


def test_score_hard_gate_and_legacy_boolean_cannot_confirm_af():
    for state in (None, 'NEGATED', 'UNCERTAIN', 'SOURCE_ONLY', 'SUSPECTED_AF', 'NO_CONFIRMED_AF', 'CONFLICTING_AF_EVIDENCE'):
        result = cha2ds2_va(confirmed_af=True, af_state=state, index_date='2020-01-01', dob='1940-01-01', facts={})
        assert not result['applicable'] and result['score'] is None
    assert not af_state_from_facts([dict(component='AF', value=True, evidence_date='2020-01-01', evidence_quote='Vorhofflimmern')])['confirmed_af']
    facts = facts_from_section('Ein Vorhofflimmern konnte nicht detektiert werden.',
        patient_id='p', document_id=1, section_id='s1', available_date='2020-01-01')
    assert facts[0]['value'] is False and not af_state_from_facts(facts)['confirmed_af']
    positive = facts_from_section('Bekanntes Vorhofflimmern.', patient_id='p', document_id=2, section_id='s2', available_date='2019-01-01')
    assert af_state_from_facts(positive + facts)['confirmed_af']


def test_source_context_not_truncated_before_conditional_after_model():
    record = dict(canonical_candidate='ATRIAL_FIBRILLATION', assertion_status='ESTABLISHED',
        raw_text='KHK mit Stent, Vorhofflimmern) nur nach Bildgebung.',
        full_source_text='Eine Antikoagulation sollte, sofern eine strenge Indikation gegeben sein sollte (z.B. KHK mit Stent, Vorhofflimmern) nur nach Bildgebung erfolgen.')
    assert checked_evidence(record)['assertion_status'] == 'CONDITIONAL'


def test_existing_raw_payload_is_preserved_but_unsafe_af_is_suppressed_and_promotion_blocked(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    raw = dict(canonical_candidate='ATRIAL_FIBRILLATION', raw_text=NEGATIVE[0],
               evidence_id='ev', assertion_status='ESTABLISHED')
    artifacts = {'canonical_disease_identities.json': json.dumps([dict(diagnosis_identity_id='af', canonical_name='ATRIAL_FIBRILLATION', source_evidence_ids=['ev'])]),
                 'diagnosis_mentions.json': json.dumps([raw])}
    metadata = dict(module_version='4.0', source_pdf_hashes={}, code_hash='old', validation_test_results={'hard_gates': {'old_gate': True}})
    entry = store.publish('2435608415', 'diagnoses', '4.0+old', artifacts, metadata)
    original = {f['path']: sha(f['path']) for f in entry['artifacts']}
    assert unsafe_af_identity(entry)
    assert diagnosis_entities(entry) == []
    with pytest.raises(ValueError, match='Assertion safety'):
        store.promote('2435608415', 'diagnoses', entry['version'], confirmed=True)
    assert {path: sha(path) for path in original} == original


def test_assertion_safety_hold_prevents_patient_approval_even_after_entity_reviews(tmp_path):
    from review_app.views.patient_overview import module_rows, patient_status
    from review_app.review_repository import ReviewRepository
    entry = dict(version='4.0', status='VALIDATED', artifacts=[], assertion_safety={'promotion_blocked': True})
    rows = module_rows({'diagnoses': entry}, {'diagnoses': entry}, {'diagnoses': 'safety-hash'}, ReviewRepository(tmp_path), '2435608415')
    assert next(r for r in rows if r['Module'] == 'Diagnoses')['Status'] == 'STALE'
    assert patient_status(rows) == 'STALE'


def test_five_pilot_af_only_audit_never_runs_extraction(monkeypatch):
    from caa_pdf_extractor.af_assertion_audit import scan_patient
    store = WorkbenchStore()
    if not store.manifest('2435608415').get('source_context'):
        pytest.skip('Local pilot sources unavailable')
    def forbidden(*args, **kwargs):
        raise AssertionError('Full diagnosis extraction must not run during an AF-only audit')
    monkeypatch.setattr(dx, 'assemble', forbidden)
    from caa_pdf_extractor import workbench_runner
    monkeypatch.setattr(workbench_runner, 'run_module', forbidden)
    rows = scan_patient(store, '2435608415')
    state = resolve_af(rows)
    assert state['positive_evidence_count'] == 0 and state['AF_state'] == 'NO_CONFIRMED_AF'
    assert any(r['evidence_id'] == 'doc26357819338331_sec10:v4:1920' and r['assertion_status'] == 'NEGATED' for r in rows)
    second = resolve_af(scan_patient(store, '2552817617'))
    assert not second['confirmed_af'] and second['AF_state'] == 'SUSPECTED_AF'
