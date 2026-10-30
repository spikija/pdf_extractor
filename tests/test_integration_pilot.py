"""Isolation and evidence-ownership tests for the temporary pilot adapter."""
import json
import subprocess
import sys
from pathlib import Path

from caa_pdf_extractor.integration_pilot import decision_contract, event_score_slots, normalize_lab, phenotype_consistency, semantic_records
from caa_pdf_extractor.extraction import research_scores_v23 as scores


def test_slots_preserve_conflicting_explicit_values_and_source_owner():
    event = dict(event_id='E', event_type='ISCHEMIC_STROKE', source_mentions=[dict(source_document_id=1, section_id='s', start=5, end=100)])
    source = dict(event_id='S', source_document_id='1', section_id='s', evidence_id='1:s:10')
    rows = [dict(assessment_id='a', event_id='S', timepoint='ADMISSION', scale_name='NIHSS', value=4, source='EXPLICIT'),
            dict(assessment_id='b', event_id='S', timepoint='ADMISSION', scale_name='NIHSS', value=8, source='RETROSPECTIVE_REVIEW_REQUIRED')]
    assert event_score_slots([event], [source], rows)[0]['admission_NIHSS'] == 4
    rows.append(dict(rows[0], assessment_id='c', value=5))
    result = event_score_slots([event], [source], rows)[0]
    assert result['admission_NIHSS'] is None
    assert result['supporting_assessment_ids']['admission_NIHSS'] == ['a', 'c']
    source['source_document_id'] = 2
    assert not event_score_slots([event], [source], rows)[0]['supporting_assessment_ids']['admission_NIHSS']


def test_contextual_caa_is_not_a_decision_reason_and_laao_consideration_not_choice():
    row = dict(decision_type='LAAO_CONSIDERED', evidence_quote='Bei CAA eventuell Vorhofohrverschluss erwägen.', reason_codes=['CAA'])
    result = decision_contract(row)
    assert result['decision_type'] == 'NO_DECISION_DOCUMENTED'
    assert result['reason_strength'] == 'AMBIGUOUS'
    assert result['reason_codes'] == []
    assert result['contextual_reason_candidates'] == ['CAA']


def test_unknown_lab_date_is_never_admission_proxy():
    row = dict(decimal_value='13', biomarker='HB', unit_text='g/dl', date_of_exam=None, candidate_id='L', validation_status='VALID',
               source_document_id=1, page_number=2, column_role='first', reconstructed_row_text='Hb 13 g/dl')
    lab = normalize_lab(row, 'P')
    assert lab['sample_date'] is None
    assert scores.get_lab_value_before_index('P', 'HEMOGLOBIN', '2020-01-01', 90, labs=[lab]) is None


def test_guard_blocks_sql_external_network_and_outside_write(tmp_path):
    code = '''
import sys,socket,sqlite3
from pathlib import Path
from caa_pdf_extractor.integration_pilot import guard
root=Path(sys.argv[1]); guard(root)
for action in [lambda: sqlite3.connect(':memory:'),lambda: socket.socket().connect(('192.0.2.1',80)),lambda: (root.parent/'forbidden.txt').write_text('x')]:
    try: action()
    except RuntimeError: pass
    else: raise AssertionError('isolation failed')
(root/'allowed.json').write_text('{}')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'allowed.json').exists()
    assert not (tmp_path.parent / 'forbidden.txt').exists()


def test_master_pipeline_remains_blocked():
    from caa_pdf_extractor.full_pipeline import BLOCKERS
    assert BLOCKERS


def test_explicit_enteral_feeding_and_care_cannot_disappear_into_unknown():
    import pytest
    value = dict(evidence=[dict(exact_quote='vorbestehende Hemiplegie; wird gepflegt; PEG-Sonde; ja oder nein antworten', temporal_class='BASELINE')],
        functional_phenotype=dict(mobility_impairment='SEVERE', feeding_dependency='UNKNOWN', caregiver_dependency='UNKNOWN', communication_cognitive_impairment='UNKNOWN'))
    with pytest.raises(ValueError, match='ENTERAL_FEEDING'):
        phenotype_consistency(value)
    value['functional_phenotype'].update(feeding_dependency='ENTERAL_FEEDING', caregiver_dependency='DAILY', communication_cognitive_impairment='MODERATE')
    assert phenotype_consistency(value) == value


def test_encounter_header_bis_zum_uses_only_header_owned_dates():
    from types import SimpleNamespace
    from caa_pdf_extractor.extraction.encounter_mrs import encounter_identity
    text = 'wir berichten über diese Patientin, die sich vom 13.10.2015 bis zum 30.10.2015 in unserer stationären Behandlung befand.'
    section = SimpleNamespace(source_text=text, section_id='s', page_start=1, page_end=1)
    result = encounter_identity(SimpleNamespace(sections=[section], source_document_id=1))
    assert result['admission_date'] == '2015-10-13'
    assert result['discharge_date'] == '2015-10-30'


def test_post_inference_regression_never_repairs_rejected_numeric_candidate():
    from copy import deepcopy
    from caa_pdf_extractor.integration_pilot_review import cfs_regression
    value = dict(cfs=dict(validator_accepted=False, validated_best_cfs=None, validated_plausible_range=None, validator_errors=['INCONSISTENT']),
        raw_cfs_candidate=dict(best_cfs=5, plausible_range_min=5, plausible_range_max=5, confidence='MODERATE'),
        phenotype=dict(functional_phenotype=dict(personal_ADL_dependency='LIKELY_DEPENDENT')))
    before = deepcopy(value)
    result = cfs_regression(value)
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['validated_best_cfs'] is None
    assert value == before


def test_latest_neurological_annotations_keep_inflammatory_caa_separate():
    from caa_pdf_extractor.integration_pilot_review import annotate_diagnosis_rows
    rows = [dict(candidate_type='DIAGNOSIS_DISEASE', raw_candidate='CAA-related inflammation', canonical_name='CAA', category='CEREBROVASCULAR', domain='NEUROLOGIC'),
            dict(candidate_type='DIAGNOSIS_DISEASE', raw_candidate='Symptomatische Epilepsie', canonical_name='Epilepsie', category='', domain='NEUROLOGIC'),
            dict(candidate_type='ACUTE_CLINICAL_EVENT', raw_candidate='Intrazerebrale Blutung', canonical_name='', category='', domain='')]
    result = annotate_diagnosis_rows(rows)
    assert result[0]['canonical_name'] == 'CAA_RELATED_INFLAMMATION'
    assert result[0]['category'] == 'INFLAMMATORY_AUTOIMMUNE'
    assert result[1]['category'] == 'EPILEPSY_SEIZURE'
    assert result[2]['canonical_name'] == ''


def test_large_semantic_input_keeps_every_distinct_source_quote_without_audit_repetition():
    raw = dict(R1=dict(label='Named source disease', accepted_date=None, mentions=[dict(evidence_text='Exact source clause', item_id='one', date_audit='x' * 60000)]))
    result = semantic_records(raw)
    assert result['R1']['source_evidence'] == ['Exact source clause']
    assert result['R1']['source_evidence_ids'] == ['one']
    assert len(json.dumps(result)) < 1000
    assert len(raw['R1']['mentions'][0]['date_audit']) == 60000


def test_source_report_adapter_joins_explicit_same_lesion_ct_followup():
    from caa_pdf_extractor.extraction import caa_evidence_v21 as ce
    from caa_pdf_extractor.extraction.caa_report_adapter import attach_source_report
    scans = []
    for sid, day, onset, text in [('I1', '2020-01-01', '2020-01-01', 'Lobäre Blutung links frontal.'),
        ('I2', '2020-01-03', None, 'Vergleich CT vom 01.01.2020. Bekannte Blutung links frontal unverändert.')]:
        features = dict(mri_adequate=None, blood_sensitive=None, blood_sensitive_indirect=False, deep_exclusion='UNKNOWN',
            deep_ich=None, deep_cmb=None, lobar_ich=True, lobar_cmb=None, css_foci=None, severe_cso_pvs=None, multispot_wmh=None,
            finger_like_projections=None, subarachnoid_extension=None, apoe_e4_status='UNKNOWN', ich_event_date=onset,
            ich_identity='left_frontal', sources=dict(lobar_ich=[sid], ich_identity=[sid], ich_event_date=[sid] if onset else []))
        scans.append(attach_source_report(dict(id=sid, date=day, modality='CT', episode_id='EP1' if onset else 'EP2',
            admission_date=day, features=ce.ScanEvidence(**features)), dict(source_document_id=sid, modality='CT', raw_report_text=text,
            findings=[dict(finding_type='ICH', laterality='left', location='frontal', interval_new=False)], source_context=dict(discovery_kind='FORMAL_REPORT'))))
    episodes = dict(EP1=ce.EpisodeEvidence(presentation='SPONTANEOUS_ICH', spontaneous_ich=True, traumatic_ich=None,
        alternative_cause='NO_SUPPORTED_ALTERNATIVE_IDENTIFIED', current_ich_date='2020-01-01',
        sources=dict(presentation=['EP1'], spontaneous_ich=['EP1'], alternative_cause=['EP1'], current_ich_date=['EP1'])),
        EP2=ce.EpisodeEvidence(presentation='OTHER', spontaneous_ich=None, traumatic_ich=None,
            alternative_cause='UNKNOWN', current_ich_date=None, sources=dict(presentation=['EP2'])))
    result = ce.assemble_patient('P', '1940-01-01', scans, episodes)
    assert len(result['edinburgh_ich_events']) == 1
    assert result['edinburgh_ich_events'][0]['ct_ids'] == ['I1', 'I2']
    assert result['edinburgh_ich_events'][0]['event_date'] == '2020-01-01'
    unlocalized = attach_source_report(scans[1], dict(source_document_id='I2', modality='CT', raw_report_text=scans[1]['text'],
        findings=[dict(finding_type='ICH', laterality=None, location='frontal')], source_context={}))
    assert unlocalized['validated_ich_identity'] is None
    assert len(ce.assemble_patient('P', '1940-01-01', [scans[0], unlocalized], episodes)['edinburgh_ich_events']) == 2


def test_arm_vein_duplex_is_not_neurosonology_but_limited_transtemporal_report_is():
    from types import SimpleNamespace
    from caa_pdf_extractor.extraction.neurosonology_report_adapter import is_neurosonology
    arm = SimpleNamespace(type='ultrasound', location=None, header='Duplexsonographie der rechten oberen Extremität', text='Keine tiefe Armvenenthrombose.')
    assert not is_neurosonology(arm)
    brain = SimpleNamespace(type='ultrasound', location='brain', header='Transtemporal:', text='Links kein Schallfenster.')
    assert is_neurosonology(brain)
