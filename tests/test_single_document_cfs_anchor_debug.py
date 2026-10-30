import copy
import hashlib,json
import pytest
from caa_pdf_extractor.single_document_cfs_anchor_debug import age_context,check_phenotype,check_estimate,CFSBestEstimate,behavior_regression,physician_adjudication,CONSISTENCY_RETRY_INSTRUCTION

def sample():
    text='Vorbestehende Hemiplegie rechts. Wird vom Partner gepflegt. PEG-Ernährung.'
    units=[dict(evidence_id='P',source_page=2,source_section='summary',text=text)]
    evidence=[dict(text_concept='baseline motor impairment',exact_quote='Vorbestehende Hemiplegie rechts.',temporal_class='BASELINE',functional_domain='MOBILITY',evidence_id='P',temporal_reason='Explicit pre-existing'),
        dict(text_concept='caregiver dependency',exact_quote='Wird vom Partner gepflegt.',temporal_class='BASELINE',functional_domain='CAREGIVER_DEPENDENCY',evidence_id='P',temporal_reason='Ongoing baseline care'),
        dict(text_concept='enteral feeding',exact_quote='PEG-Ernährung.',temporal_class='BASELINE',functional_domain='FEEDING',evidence_id='P',temporal_reason='Ongoing baseline feeding')]
    phenotype=dict(mobility_impairment='SEVERE',personal_ADL_dependency='LIKELY_DEPENDENT',IADL_dependency='DEPENDENT',feeding_dependency='ENTERAL_FEEDING',caregiver_dependency='SUBSTANTIAL',communication_cognitive_impairment='UNKNOWN',bedbound='UNKNOWN',terminal_state='UNKNOWN')
    proposal=dict(evidence=evidence,functional_phenotype=phenotype,supporting_evidence_ids=['P'],limiting_missing_information=['Individual ADLs incompletely enumerated'],acute_features_excluded=['New stroke deficit'],rationale='Global interpretation')
    estimate=dict(best_cfs=7,plausible_range_min=6,plausible_range_max=7,confidence='MODERATE',review_required=True,functional_phenotype=phenotype,supporting_evidence_ids=['P'],limiting_missing_information=['Individual ADLs incompletely enumerated'],acute_features_excluded=['New stroke deficit'],rationale='Global anchor fit')
    return units,proposal,estimate

def test_requested_regression_shape_is_accepted_without_adl_enumeration():
    u,p,c=sample();v=check_phenotype(p,u);r=check_estimate(c,v)
    assert r['validated_best_cfs']==7 and r['validated_plausible_range']==[6,7]
    assert r['source_type']=='RETROSPECTIVE_REVIEW_REQUIRED' and r['validator_accepted']
    assert len(v['baseline_dependency_signals'])==3  # One paragraph, three clues.

def test_age_formula_and_protocol_uncertainty():
    age=age_context('1944-05-14','2015-03-18')
    assert age['patient_age_years']==70 and age['CFS_APPLICABILITY_REVIEW_REQUIRED']
    assert not age_context('1944-05-14','2015-03-18',65)['CFS_APPLICABILITY_REVIEW_REQUIRED']
    assert age_context('2000-01-01','2020-01-01',65)['CFS_APPLICABILITY_REVIEW_REQUIRED']

def test_age_is_not_an_input_to_score_validation():
    u,p,c=sample();assert check_estimate(c,check_phenotype(p,u))['validated_best_cfs']==7
    assert age_context('2000-01-01','2020-01-01',65)['age_context_only']

def test_one_signal_is_insufficient():
    u,p,c=sample();p['evidence']=p['evidence'][:1]
    r=check_estimate(c,check_phenotype(p,u))
    assert not r['validator_accepted']
    assert any('TWO_CONCORDANT' in e['rule'] for e in r['validator_errors'])

def test_unknown_source_id_rejected():
    u,p,c=sample();c['supporting_evidence_ids']=['INVENTED']
    assert not check_estimate(c,check_phenotype(p,u))['validator_accepted']

def test_acute_features_cannot_support_baseline():
    u,p,c=sample();u[0]['text']+=' Neu aufgetretene motorische Aphasie.'
    p['evidence'].append(dict(p['evidence'][0],exact_quote='Neu aufgetretene motorische Aphasie.'))
    assert not check_estimate(c,check_phenotype(p,u))['validator_accepted']

@pytest.mark.parametrize('best',[8,9])
def test_neurological_disability_alone_does_not_establish_cfs8_or9(best):
    u,p,c=sample();c.update(best_cfs=best,plausible_range_min=best,plausible_range_max=best)
    assert not check_estimate(c,check_phenotype(p,u))['validator_accepted']

def test_bedbound_not_inferred_from_peg():
    u,p,c=sample();p['functional_phenotype']=dict(p['functional_phenotype'],bedbound='YES')
    assert check_phenotype(p,u)['validator_errors']

def test_range_must_contain_best_estimate():
    u,p,c=sample();c['plausible_range_max']=6
    with pytest.raises(ValueError):CFSBestEstimate.model_validate(c)

def test_model_abstention_preserved_without_forcing_a_score():
    u,p,c=sample();c.update(best_cfs=None,plausible_range_min=None,plausible_range_max=None)
    r=check_estimate(c,check_phenotype(p,u))
    assert r['validated_best_cfs'] is None and r['source_type']=='NOT_ASSESSABLE'

def test_iadl_only_anchor_cannot_describe_likely_personal_adl_dependency():
    u,p,c=sample();c.update(best_cfs=5,plausible_range_min=5,plausible_range_max=6)
    r=check_estimate(c,check_phenotype(p,u))
    assert not r['validator_accepted']
    assert any('IADL_ONLY_CFS_ANCHOR_CONTRADICTS' in e['rule'] for e in r['validator_errors'])

def test_missing_adls_expand_uncertainty_not_null_for_valid_range():
    u,p,c=sample();c.update(best_cfs=6,plausible_range_min=6,plausible_range_max=7)
    assert check_estimate(c,check_phenotype(p,u))['validator_accepted']
    c.update(plausible_range_min=6,plausible_range_max=6)
    assert not check_estimate(c,check_phenotype(p,u))['validator_accepted']

def behavioral_sample():
    u,p,c=sample();u[0]['text']+=' Kann nur ja oder nein antworten.'
    p['evidence'].append(dict(p['evidence'][0],text_concept='Pre-existing communication limitation',exact_quote='Kann nur ja oder nein antworten.',functional_domain='COMMUNICATION'))
    p['functional_phenotype']['communication_cognitive_impairment']='MILD'
    p['acute_features_excluded']=c['acute_features_excluded']=['acute seizure','new aphasia','acute ICH']
    return check_phenotype(p,u),c

@pytest.mark.parametrize('best',[6,7])
def test_behavior_regression_accepts_either_best_anchor(best):
    p,c=behavioral_sample();c['best_cfs']=best
    r=behavior_regression(c,p)
    assert r['passed'] and r['exact_best_score_required'] is None

def test_range5_is_rejected_without_silently_rewriting_raw_candidate():
    p,c=behavioral_sample();c.update(best_cfs=6,plausible_range_min=5,plausible_range_max=6)
    original=copy.deepcopy(c);r=behavior_regression(c,p)
    assert not r['passed'] and c==original
    assert not r['gates']['no_iadl_only_range_for_likely_personal_dependency']

def test_high_confidence_not_capped_silently():
    p,c=behavioral_sample();c['confidence']='HIGH';r=behavior_regression(c,p)
    assert not r['passed'] and c['confidence']=='HIGH'

def test_physician_adjudication_does_not_replace_model_seven():
    p,c=behavioral_sample();c['best_cfs']=7
    assert behavior_regression(c,p)['passed']
    physician=physician_adjudication()
    assert physician['retrospective_CFS_physician_review']==6
    assert physician['plausible_range']==[6,7] and physician['confidence']=='MODERATE'
    assert c['best_cfs']==7

def test_consistency_retry_uses_requested_wording():
    assert CONSISTENCY_RETRY_INSTRUCTION=='Reconsider the CFS anchor using your already extracted functional phenotype. Do not add facts. A CFS 5 phenotype is primarily dependent in higher-order activities; your extracted phenotype indicates likely personal-ADL dependence. Select the best-fitting anchor and uncertainty interval without assuming undocumented ADLs.'

def test_inconsistent_retry_is_not_retried_again(tmp_path,monkeypatch):
    from caa_pdf_extractor import single_document_cfs_anchor_debug as module
    p,c=behavioral_sample();initial=copy.deepcopy(c);initial.update(best_cfs=6,plausible_range_min=5,plausible_range_max=6)
    second=copy.deepcopy(c);second.update(best_cfs=5,plausible_range_min=5,plausible_range_max=5)
    calls=[]
    def fake_call(client,out,name,prompt,schema,allowed_evidence_ids):
        calls.append(prompt)
        return copy.deepcopy(initial if len(calls)==1 else second)
    monkeypatch.setattr(module,'model_call',fake_call)
    source=tmp_path/'synthetic.txt';source.write_bytes(b'synthetic source, no PDF parsed')
    monkeypatch.setattr(module.prior,'PDF',source)
    out=tmp_path/'review';out.mkdir();context=module.age_context('1944-05-14','2015-03-18')
    module.finish_estimate(None,out,context,p,{'pages':[]},context['date_of_birth'],hashlib.sha256(source.read_bytes()).hexdigest(),'Synthetic test')
    assert len(calls)==2 and CONSISTENCY_RETRY_INSTRUCTION in calls[1]
    selected=json.loads((out/'anchor_consistency_retry'/'result.json').read_text(encoding='utf-8'))
    assert selected['raw_model_candidate']['best_cfs']==5 and selected['validated_best_cfs'] is None
    assert selected['physician_adjudication']['retrospective_CFS_physician_review']==6
    assert selected['status']=='SINGLE_DOCUMENT_CFS_TEST_PARTIAL'
