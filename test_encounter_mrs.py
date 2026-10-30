"""Counterexamples for admission ownership, functional facts, artifacts and plans."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from caa_pdf_extractor.preparation.models import PreparedSection
from caa_pdf_extractor.extraction import encounter_mrs as e
from test_case import bundle


def source(text, heading='Anamnese', extra=None):
    b = bundle('wir berichten über Frau Beispiel, die sich von 02.01.2026 bis 08.01.2026 zur stationären Behandlung in unserer Klinik befand.')
    d = b.documents[0]
    d.sections[0].heading = 'Briefkopf'
    d.sections.append(PreparedSection('s2', '123', 1, heading, 2, 2, text, text))
    if extra:
        h, t = extra
        d.sections.append(PreparedSection('s3', '123', 1, h, 3, 3, t, t))
    return b


def result(text, heading='Anamnese', extra=None):
    return e.extract_bundle(source(text, heading, extra))['encounters'][0]


@pytest.mark.parametrize('text,category', [
    ('Die stationäre Aufnahme erfolgte aufgrund einer akuten Hemiparese rechts.', 'FOCAL_NEUROLOGICAL_DEFICIT'),
    ('Die Aufnahme erfolgte wegen plötzlich aufgetretener Aphasie.', 'FOCAL_NEUROLOGICAL_DEFICIT'),
    ('Die Aufnahme erfolgte aufgrund einer Hemianopsie.', 'FOCAL_NEUROLOGICAL_DEFICIT'),
    ('Die Aufnahme erfolgte nach einem tonisch-klonischen Anfall.', 'EPILEPTIC_EVENT'),
    ('Der Patient wird mit konvulsivem Status epilepticus zutransferiert.', 'EPILEPTIC_EVENT'),
    ('Die stationäre Aufnahme erfolgte zur Abklärung psychomotorischer Verlangsamung und Phasen mit Desorientiertheit sowie Amnesie.', 'COGNITIVE_DECLINE'),
    ('Die Patientin wurde wegen einer akuten kognitiven Verschlechterung aufgenommen.', 'COGNITIVE_DECLINE'),
    ('Die Aufnahme erfolgt geplant zur weiteren Therapie.', 'OTHER'),
    ('Die Aufnahme erfolgt wegen Kopfschmerzen und isoliertem Schwindel.', 'OTHER'),
])
def test_reason_from_current_presentation(text, category):
    r = result(text)
    assert r['admission_reason']['primary_reason'] == category
    assert r['admission_reason']['flags'][category] is True


@pytest.mark.parametrize('text,heading', [
    ('Epilepsie und Schlaganfall mit Hemiparese seit 2015.', 'Vorerkrankungen'),
    ('Demenz und Hemiparese', 'Diagnosen'),
    ('Levetiracetam 1000 mg bei Epilepsie', 'Medikation'),
    ('Im EEG fanden sich epileptiforme Potentiale.', 'Anamnese'),
    ('Die Aufnahme erfolgte zur Kontrolle. Anamnestisch bekannt ist eine Epilepsie.', 'Anamnese'),
    ('Bereits 2015 hatte die Patientin einen epileptischen Anfall.', 'Anamnese'),
    ('Bei Entlassung trat eine Aphasie auf.', 'Anamnese'),
])
def test_history_lists_eeg_and_discharge_do_not_supply_reason(text, heading):
    r = result(text, heading)['admission_reason']
    assert r['flags']['EPILEPTIC_EVENT'] is None
    assert r['flags']['FOCAL_NEUROLOGICAL_DEFICIT'] is None
    assert r['flags']['COGNITIVE_DECLINE'] is None


def test_coexisting_seizure_and_postictal_focal_deficit():
    r = result('Die Aufnahme erfolgte nach einem tonisch-klonischen Anfall mit postiktaler Hemiparese.')['admission_reason']
    assert r['flags']['EPILEPTIC_EVENT'] and r['flags']['FOCAL_NEUROLOGICAL_DEFICIT']
    assert r['primary_reason'] == 'EPILEPTIC_EVENT'


@pytest.mark.parametrize('phrase', ['Verdacht auf einen epileptischen Anfall', 'keiner Aphasie', 'keinen epileptischen Anfall'])
def test_uncertain_and_negated_reason_not_positive(phrase):
    r = result('Die Aufnahme erfolgte wegen ' + phrase + '.')['admission_reason']
    assert not r['flags']['EPILEPTIC_EVENT'] and not r['flags']['FOCAL_NEUROLOGICAL_DEFICIT']


@pytest.mark.parametrize('text', ['Hemiparese rechts.', 'Aphasie.', 'Ein Rollator steht im Zimmer.', 'Rollator.', 'Im Rollstuhl gebracht.', 'Stationäre Aufnahme.'])
def test_diagnosis_aid_or_hospitalization_alone_never_supplies_mrs(text):
    r = result(text, 'Status bei Aufnahme')['admission_mrs']
    assert r['score'] is None and r['confidence'] == 'LOW'


@pytest.mark.parametrize('text,score,lo,hi', [
    ('Bei Aufnahme mRS 3.', 3, None, None),
    ('Bei Aufnahme mRS 2-3.', None, 2, 3),
    ('Bei Aufnahme ist der Patient bettlägerig.', 5, None, None),
    ('Bei Aufnahme benötigt die Patientin ständige Pflege.', 5, None, None),
    ('Bei Aufnahme Gehen nur mit einer Hilfsperson.', None, 4, 5),
    ('Bei Aufnahme benötigt der Patient Hilfe bei der Körperpflege.', None, 4, 5),
    ('Bei Aufnahme ist er selbständig mobil und benötigt Hilfe.', None, 2, 3),
    ('Bei Aufnahme ist er symptomatisch, alle üblichen Tätigkeiten möglich.', 1, None, None),
    ('Bei Aufnahme ist er vollständig beschwerdefrei.', 0, None, None),
])
def test_evidence_first_owned_exact_or_range(text, score, lo, hi):
    r = result(text)['admission_mrs']
    assert (r['score'], r['min'], r['max']) == (score, lo, hi)
    assert r['evidence']
    assert all(p['temporal_ownership'] == 'CURRENT_ADMISSION' for p in r['evidence'])
    assert r['source_type'] == ('EXPLICIT' if 'mRS' in text else 'ESTIMATED')


@pytest.mark.parametrize('text', [
    'Zuvor mRS 1.', 'Bei Entlassung mRS 1.',
    'Zuvor war die Patientin bettlägerig.', 'Bei Entlassung war die Patientin bettlägerig.',
    'Zuvor mobil ohne Hilfsmittel.', 'Vor der Aufnahme lebte er selbständig mit Rollator.',
])
def test_baseline_and_discharge_never_date_admission_score(text):
    r = result(text)
    assert r['admission_mrs']['score'] is None and r['admission_mrs']['confidence'] == 'LOW'


def test_baseline_admission_discharge_independent_ownership():
    r = result('Zuvor mRS 1. Bei Aufnahme mRS 4. Bei Entlassung mRS 2.')
    assert r['pre_event_mrs']['score'] == 1
    assert r['admission_mrs']['score'] == 4
    assert any(s['temporal_ownership'] == 'DISCHARGE' for s in r['functional_assessment']['explicit_scores'])


def test_conflicting_explicit_scores_do_not_become_fake_range():
    r = result('Bei Aufnahme mRS 2. Bei Aufnahme mRS 4.')['admission_mrs']
    assert r['score'] is None and r['min'] is None and r['confidence'] == 'LOW'


def test_living_admission_cannot_receive_mrs_6():
    r = result('Bei Aufnahme mRS 6.')['admission_mrs']
    assert r['score'] is None and r['confidence'] == 'LOW'


def test_explicit_owned_score_beats_weak_morphology():
    r = result('Bei Aufnahme mRS 2. Hemiparese rechts.')['admission_mrs']
    assert r['score'] == 2 and r['source_type'] == 'EXPLICIT'


def test_copied_prior_admission_is_not_current_episode():
    r = result('Am 02.06.2022 erfolgte die Aufnahme wegen einer Aphasie.', 'Zusammenfassung des Aufenthalts')
    assert r['admission_reason']['primary_reason'] == 'UNKNOWN'
    assert not r['admission_reason']['evidence']


def test_exact_offsets_and_single_page_source_evidence():
    b = source('Die stationäre Aufnahme erfolgte zur Abklärung psychomotorischer\n Verlangsamung und Desorientiertheit.')
    r = e.extract_bundle(b)['encounters'][0]
    for p in r['admission_reason']['evidence'] + [r['encounter_evidence']]:
        s = next(s for s in b.documents[0].sections if s.section_id == p['section_id'])
        assert s.source_text[p['source_start']:p['source_end']] == p['exact_source_text']
        assert p['page'] == s.page_start == s.page_end


@pytest.mark.parametrize('mutation', ['quote', 'mrs', 'case', 'patient', 'source'])
def test_artifact_tampering_and_identity_staleness_blocked(mutation):
    b = source('Bei Aufnahme mRS 2.')
    artifact = e.create_artifact(b, 1)
    if mutation == 'mrs':
        artifact['output']['encounters'][0]['admission_mrs']['score'] = 4
    elif mutation == 'quote':
        artifact['output']['encounters'][0]['encounter_evidence']['exact_source_text'] = 'Invented quote'
    elif mutation == 'case':
        artifact['case_id'] = 2
    elif mutation == 'patient':
        artifact['patient_id'] = 'wrong'
    else:
        b.documents[0].sha256 = 'different'
    artifact.pop('artifact_sha256')
    artifact['artifact_sha256'] = e.digest(artifact)
    with pytest.raises(ValueError):
        e.verify_artifact(artifact, b, 1)


def test_read_only_commit_plan_is_idempotent_and_blocks_overwrite():
    b = source('Bei Aufnahme mRS 2.')
    artifact = e.create_artifact(b, 1)
    plan = e.plan_commit(artifact, b, 1, [])
    assert plan[0]['status'] == 'INSERT'
    existing = [plan[0]['row']]
    assert e.plan_commit(artifact, b, 1, existing)[0]['status'] == 'UNCHANGED_IDEMPOTENT'
    curated = deepcopy(existing)
    curated[0]['admission_mrs'] = 1
    assert e.plan_commit(artifact, b, 1, curated)[0]['status'] == 'CONFLICT_EXISTING'
    assert e.plan_commit(artifact, b, 1, existing * 2)[0]['status'] == 'CONFLICT_EXISTING'


def test_outpatient_report_not_promoted_by_planned_admission():
    b = bundle('wir berichten über Frau Beispiel, die sich am 02.01.2026 zur ambulanten Behandlung befand.\nStationäre Aufnahme geplant.')
    assert e.extract_bundle(b)['excluded_or_held_documents'][0]['status'] == 'EXCLUDED_OUTPATIENT'


def test_two_source_documents_with_same_dates_remain_two_encounters():
    b = source('Die Aufnahme erfolgte wegen Aphasie.')
    second = deepcopy(b.documents[0])
    second.source_document_id = 2
    for s in second.sections:
        s.source_document_id = 2
    b.documents.append(second)
    assert len(e.extract_bundle(b)['encounters']) == 2


def test_reversed_dates_are_blocked_and_never_filled_from_today():
    b = bundle('wir berichten über Frau X, die sich von 08.01.2026 bis 02.01.2026 zur stationären Behandlung befand.')
    r = e.extract_bundle(b)
    assert not r['encounters'] and r['excluded_or_held_documents'][0]['status'] == 'AMBIGUOUS_ENCOUNTER_IDENTITY'


def test_zero_model_commit_planning():
    b = source('Bei Aufnahme mRS 2.')
    artifact = e.create_artifact(b, 1)
    with patch('caa_pdf_extractor.llm.granite.GraniteClient.generate_json', side_effect=AssertionError('No inference')) as mock:
        assert e.plan_commit(artifact, b, 1, [])[0]['status'] == 'INSERT'
        mock.assert_not_called()


def test_embedded_exam_and_page_continuation_keep_original_offsets():
    b = source('Auszüge aus erhobenen Befunden\nAnamnese (02.01.2026)\nDie Aufnahme erfolgte aufgrund von Desorientiertheit.\nVorerkrankungen:\nEpilepsie seit 2015.\nNeurologischerBefund (02.01.2026)\nWach.', 'Auszüge aus erhobenen Befunden',
               (None, 'Fortsetzung\nStand und Gang: selbständig mobil.'))
    r = e.extract_bundle(b)['encounters'][0]
    assert r['admission_reason']['flags']['COGNITIVE_DECLINE'] is True
    assert r['admission_reason']['flags']['EPILEPTIC_EVENT'] is None
    fact = next(p for p in r['functional_assessment']['facts'] if p['fact'] == 'walking_independent')
    assert fact['temporal_ownership'] == 'CURRENT_ADMISSION'
    assert fact['section_id'] == 's3' and fact['page'] == 3
    assert b.documents[0].sections[2].source_text[fact['source_start']:fact['source_end']] == fact['exact_source_text']


def test_older_embedded_exam_cannot_date_current_function():
    r = result('Neurologischer Befund (02.01.2025)\nBettlägerig.', 'Auszüge aus erhobenen Befunden')
    assert r['admission_mrs']['score'] is None


def test_wifes_nursing_is_not_patient_nursing():
    r = result('Der Patient lebt mit seiner Gattin. Die Gattin benötigt ständige Pflege.')
    assert r['admission_mrs']['score'] is None
    assert all(p['state'] == 'UNKNOWN' for p in r['functional_assessment']['facts'] if p['fact'] == 'constant_nursing')


def test_negated_independent_walking_not_extracted_positive():
    r = result('Ein selbständiges Gehen war zum Zeitpunkt der Vorstellung nicht möglich.')
    assert r['admission_mrs']['resolved_facts']['walking_independent'] == 'ABSENT'
    assert (r['admission_mrs']['score'], r['admission_mrs']['min'], r['admission_mrs']['max']) == (None, 4, 5)


def test_current_reason_survives_neighboring_historical_disease():
    r = result('Die Aufnahme erfolgt aufgrund akuter Aphasie bei vorbekannter Epilepsie.')
    assert r['admission_reason']['flags']['FOCAL_NEUROLOGICAL_DEFICIT'] is True
    assert r['admission_reason']['flags']['EPILEPTIC_EVENT'] is None


def test_treatment_decision_after_control_is_not_an_additional_reason():
    r = result('Die Aufnahme erfolgt zur Kontrolle. Aufgrund stabiler Befunde wurde auf Infliximab verzichtet.')
    assert len(r['admission_reason']['evidence']) == 1


def test_explicit_labeled_mrs_in_diagnosis_list_does_not_supply_reason():
    r = result('1. Epilepsie\n prä mRS 0\n bei Aufnahme NIHSS 1, mRS 3\n bei Entlassung NIHSS 1, mRS 2', 'Diagnosen bei Entlassung')
    assert r['admission_mrs']['score'] == 3 and r['pre_event_mrs']['score'] == 0
    assert r['admission_reason']['primary_reason'] == 'UNKNOWN'


@pytest.mark.parametrize('score', [0, 1, 2, 3, 4, 5])
def test_explicit_scale_levels_retained_without_estimating(score):
    assert result(f'Bei Aufnahme mRS {score}.')['admission_mrs']['score'] == score


def test_unspecified_help_range_not_forced_to_exact():
    r = result('Bei Aufnahme ist er selbständig mobil und benötigt Hilfe.')
    assert r['admission_mrs']['score'] is None and (r['admission_mrs']['min'], r['admission_mrs']['max']) == (2, 3)


def test_conflicting_functional_mobility_blocks_estimate():
    r = result('Bei Aufnahme selbständig mobil. Bei Aufnahme bettlägerig.')['admission_mrs']
    assert r['score'] is None and r['rule'] == 'CONFLICTING_FUNCTIONAL_FACTS'


@pytest.mark.parametrize('text', ['Bei Aufnahme am 02.01.2025 mRS 4.', 'Anamnese (02.01.2025)\nBei Aufnahme mRS 4.'])
def test_copied_older_explicit_scores_are_not_current(text):
    assert result(text, 'Auszüge aus erhobenen Befunden')['admission_mrs']['score'] is None


@pytest.mark.parametrize('text', ['Bei Aufnahme mRS 3.5', 'Bei Aufnahme mRS 3,5', 'Bei Aufnahme mRS 10'])
def test_invalid_noninteger_explicit_score_not_truncated(text):
    assert result(text)['admission_mrs']['score'] is None


@pytest.mark.parametrize('text', ['Patient selbst ist subjektiv beschwerdefrei.', 'Bei Aufnahme subjektiv beschwerdefrei.', 'Der Patient gibt keine Beschwerden an.'])
def test_subjective_no_complaints_does_not_prove_mrs0(text):
    assert result(text)['admission_mrs']['score'] is None


def test_transfer_persistent_deficit_does_not_become_new_focal_reason():
    text = ('Übernahme von der CDK, dort am 12.05.2025 mit intracerebralem Hämatom aufgenommen. '
            'Die Pat. aktuell mit starken Kopfschmerzen, Halbseitenschwäche links.')
    r = result(text)
    assert r['admission_reason']['flags']['FOCAL_NEUROLOGICAL_DEFICIT'] is None


@pytest.mark.parametrize('text', [
    'Die Patientin berichtet bei Übernahme, dass sie am 19.07.2020 Kopfschmerzen hatte.',
    'Zudem erfolgte wegen Schmerzen eine konsiliarische Vorstellung an der UK für Orthopädie.',
    'Nach initialer Aufnahme konnte sie bei Rückläufigkeit der Kopfschmerzen auf die Normalstation verlegt werden.',
])
def test_later_consults_and_recounted_old_events_not_admission_reason(text):
    assert not result(text)['admission_reason']['evidence']


def test_etiologic_modifier_negation_does_not_suppress_cognitive_reason():
    text = 'Frau Beispiel kommt zur Abklärung einer Vaskulitis (nicht näher bezeichnet) bei weiterhin bestehenden Episoden mit Kurzzeitgedächtnisstörung.'
    assert result(text)['admission_reason']['primary_reason'] == 'COGNITIVE_DECLINE'


@pytest.mark.parametrize('text', ['Die Aufnahme erfolgte ohne Aphasie und Hemiparese.', 'Die Aufnahme erfolgte ohne Aphasie, Hemiparese.'])
def test_shared_negation_applies_to_both_conjoined_deficits(text):
    r = result(text)
    assert not r['admission_reason']['flags']['FOCAL_NEUROLOGICAL_DEFICIT']


def test_postposed_denial_not_a_present_functional_fact():
    r = result('Bei Aufnahme wird Inkontinenz verneint.')
    assert r['admission_mrs']['resolved_facts']['incontinence'] == 'ABSENT'


def test_current_presenting_cognitive_clause_after_reason_anchor():
    text = 'Der Patient wurde heute aufgrund von Problemen im Alltag aufgenommen. Zusätzlich war er deutlich verwirrt gewesen.'
    assert result(text)['admission_reason']['flags']['COGNITIVE_DECLINE'] is True


@pytest.mark.parametrize('text', [
    'Die Patientin berichtet, dass sie zu Hause hauptsächlich mit dem Rollator mobil gewesen sei.',
    'Sonst ist sie mit Unterstützung und mit dem Rollator mobil.',
    'Ansonsten wohnt die Patientin alleine, versorgt sich selbst.',
    'Fremdanamnestisch durch Sohn braucht er Hilfe vor allem bei der Körperhygiene, ist mit Rollator mobil.',
])
def test_habitual_home_function_is_baseline_not_admission(text):
    r = result(text)
    assert r['functional_assessment']['facts']
    assert all(f['temporal_ownership'] == 'BASELINE' for f in r['functional_assessment']['facts'])
    assert r['admission_mrs']['score'] is None


def test_reflex_negation_does_not_negate_current_independent_walking():
    text = 'Achillessehnenreflex nicht erhältlich\nBabinski negativ\nStand und Gang: selbständig mobil.'
    r = result(text, 'Neurologischer Befund')
    assert r['admission_mrs']['resolved_facts']['walking_independent'] == 'PRESENT'


def test_baseline_rollator_and_current_rollator_observation_are_separate():
    r = result('Zuvor mit Rollator mobil. Bei Aufnahme ist der Patient mit Rollator mobil.')
    assert {f['temporal_ownership'] for f in r['functional_assessment']['facts'] if f['fact'] == 'walking_aid'} == {'BASELINE', 'CURRENT_ADMISSION'}


def test_literal_baseline_score_with_discordant_bodily_dependency_is_held():
    r = result('Prämorbid mRS 1. Zuvor benötigt er Hilfe bei der Körperpflege.')
    assert r['pre_event_mrs']['score'] is None
    assert r['pre_event_mrs']['rule'] == 'LITERAL_MRS_FUNCTION_DISCORDANCE_REVIEW'
    assert r['pre_event_mrs']['source_type'] == 'EXPLICIT'


def test_reporter_care_service_not_baseline_function_of_patient():
    r = result('Bei Aufnahme berichtet die Schwester, durch die 24-Stunden-Pflege sei heute eine Aphasie aufgefallen.')
    assert all(f['temporal_ownership'] == 'UNKNOWN' for f in r['functional_assessment']['facts'] if f['fact'] == 'baseline_function')


def test_long_discharge_sentence_home_help_does_not_become_baseline():
    text = ('Unter Therapie besserte sich der Verlauf, sodass sie am 15.07.2020 mit neu etablierter '
            'Hilfe zu Hause wieder nach Hause entlassen werden konnte.')
    r = result(text)
    assert all(f['temporal_ownership'] != 'BASELINE' for f in r['functional_assessment']['facts'])
    assert r['pre_event_mrs']['score'] is None and r['admission_mrs']['score'] is None


def test_acute_symptomatic_seizure_adjective_not_functional_symptom_score():
    r = result('Es handelt sich um akut symptomatische fokal epileptische Anfälle.')
    assert not any(f['fact'] == 'symptoms_present' for f in r['functional_assessment']['facts'])


def test_past_tense_current_presentation_frame_with_linked_focal_onset():
    text = ('Herr Beispiel stellte sich am 02.01.2026 in der Notfallambulanz vor. '
            'Er berichtete, gestern Nacht Taubheitsgefühl und Schwäche in der linken Hand bemerkt zu haben.')
    assert result(text, 'Zusammenfassung des Aufenthalts')['admission_reason']['primary_reason'] == 'FOCAL_NEUROLOGICAL_DEFICIT'


def test_wrapped_vorbekannt_word_is_not_a_section_boundary():
    text = ('Die Aufnahme erfolgte am 02.01.2026. Bei der Patientin ist eine Vaskulitis\n'
            'vorbekannt. Die Patientin stellt sich nun zur klinischen Verlaufskontrolle vor.')
    r = result(text, 'Zusammenfassung des Aufenthalts')
    assert r['admission_reason']['primary_reason'] == 'OTHER'
    assert any('zur klinischen Verlaufskontrolle' in p['exact_source_text'] for p in r['admission_reason']['evidence'])


def test_previous_clinic_control_is_not_purpose_of_current_admission():
    r = result('Die Aufnahme erfolgt nach Kontrolle in der Privatordination im Dezember 2025.')
    assert not r['admission_reason']['flags']['OTHER']


@pytest.mark.parametrize('data', json.loads((Path(__file__).parent / 'tests/fixtures/encounter_mrs_balanced.json').read_text(encoding='utf-8')), ids=lambda d: 'doc' + str(d['source_document_id']))
def test_balanced_retained_source_fixture(data):
    b = source(data['text'], data['heading'])
    b.documents[0].sections[0].source_text = ('wir berichten über Frau Beispiel, die sich von ' + data['admission_date'] + ' bis ' + data['discharge_date'] + ' zur stationären Behandlung befand.')
    r = e.extract_bundle(b)['encounters'][0]
    assert r['admission_reason']['flags'][data['expected_phenotype']] is True
    assert r['admission_reason']['primary_reason'] == data['expected_primary']
