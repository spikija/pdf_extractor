import copy
from caa_pdf_extractor.single_document_function_debug import validate_candidates

def fixture():
    units=[dict(evidence_id='B',source_page=2,source_section='summary',text='Vorbestehende Hemiplegie rechts. Pflege durch den Gatten, PEG-Ernährung.'),
        dict(evidence_id='A',source_page=2,source_section='summary',text='Neu aufgetretene motorische Aphasie.'),
        dict(evidence_id='D',source_page=2,source_section='summary',text='Neurologisch unverändert mit Aphasie entlassen.')]
    proposal=dict(evidence=[dict(text_concept='pre-existing dependency',exact_quote=units[0]['text'],temporal_class='BASELINE',functional_domain='CAREGIVER_DEPENDENCY',evidence_id='B',temporal_reason='Explicitly pre-existing')],
        cfs=dict(candidate_cfs=7,confidence='MODERATE',review_required=True,baseline_dependency_summary='Severe chronic dependency suggested',supporting_evidence_ids=['B'],missing_information=['Exact personal ADLs'],acute_features_excluded=['New aphasia']),
        pre_event_mrs=dict(pre_event_mrs_candidate=None,candidate_range=[4,5],confidence='MODERATE',review_required=True,supporting_evidence_ids=['B'],missing_information=['Bedbound status','Continence'],rationale='No false precision'),admission_mrs=None)
    return proposal,units

def test_baseline_inference_does_not_require_explicit_scale_or_complete_adls():
    p,u=fixture();r=validate_candidates(p,u)
    assert r['validated_cfs']==7 and r['candidate_range']==[4,5]
    assert r['validated_cfs_source']=='RETROSPECTIVE_REVIEW_REQUIRED'
    assert r['admission_mrs'] is None and not r['validator_errors']

def test_acute_fact_cannot_be_baseline():
    p,u=fixture();p['evidence'].append(dict(p['evidence'][0],exact_quote=u[1]['text'],evidence_id='A'))
    r=validate_candidates(p,u)
    assert r['validated_cfs'] is None and r['validator_errors'][0]['rule']=='NO_ACUTE_STATE_RECONSTRUCTION'

def test_discharge_unchanged_is_not_preevent():
    p,u=fixture();p['evidence'].append(dict(p['evidence'][0],exact_quote=u[2]['text'],evidence_id='D'))
    r=validate_candidates(p,u)
    assert r['validated_cfs'] is None and r['validator_errors'][0]['rule']=='NO_DISCHARGE_TO_BASELINE_BACKPROPAGATION'

def test_peg_and_hemiplegia_do_not_prove_exact_mrs5():
    p,u=fixture();p['pre_event_mrs'].update(pre_event_mrs_candidate=5,candidate_range=None)
    r=validate_candidates(p,u)
    assert r['validated_pre_event_mrs'] is None
    assert r['validated_cfs']==7
    assert 'MRS5_REQUIRES_BASELINE_BEDBOUND' in r['validator_errors'][0]['rule']

def test_invented_source_quote_rejected():
    p,u=fixture();p['evidence'][0]['exact_quote']='Patient is bedridden and incontinent.'
    r=validate_candidates(p,u)
    assert r['validated_cfs'] is None and r['validator_errors'][0]['rule']=='EXACT_SOURCE_PROVENANCE_REQUIRED'

def test_mrs_zero_still_requires_baseline_evidence():
    p,u=fixture();p['pre_event_mrs'].update(pre_event_mrs_candidate=0,candidate_range=None,supporting_evidence_ids=[])
    r=validate_candidates(p,u)
    assert r['validated_pre_event_mrs'] is None
    assert any(e['rule']=='BASELINE_SUPPORT_IDS_REQUIRED' and e['scale']=='MRS' for e in r['validator_errors'])
