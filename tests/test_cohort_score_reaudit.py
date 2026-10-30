import copy
import pytest
from caa_pdf_extractor.cohort_score_reaudit import functional_errors, minimum_score, FUNCTION_PROMPT, locked_baseline_units, recover_current_event_links
from caa_pdf_extractor.extraction.clinical_scales_v23 import extract_section

def proposal():
    units={'B':dict(text='Vorbestehende Hemiplegie. Wird vom Gatten gepflegt. PEG. Bis dato ja oder nein.',heading='Anamnese')}
    ph=dict(mobility_impairment='SEVERE',personal_ADL_dependency='LIKELY_DEPENDENT',IADL_dependency='UNKNOWN',feeding_dependency='ENTERAL_FEEDING',caregiver_dependency='SUBSTANTIAL',communication_cognitive_impairment='MILD',bedbound='UNKNOWN',terminal_state='UNKNOWN')
    estimate=dict(best=6,range_min=6,range_max=7,confidence='MODERATE',supporting_evidence_ids=['B'],limiting_missing_information=['Transfers unknown'],rationale='Concordant chronic dependency')
    raw=dict(evidence=[dict(evidence_id='B',exact_quote=units['B']['text'],temporal_class='BASELINE',domain='MOBILITY')],functional_phenotype=ph,cfs=estimate,pre_event_mrs=dict(estimate,best=None,range_min=4,range_max=5),acute_features_excluded=['Acute seizure, new aphasia and ICH excluded'])
    return raw,units

def test_incomplete_exact_with_minimum_five_still_has_determined_recommendation():
    r=minimum_score(dict(C=None,H=1,A2=2,D=None,S2=2,V=None,A=0))
    assert r['cha2ds2_va_exact'] is None
    assert r['score_range']==[5,8]
    assert r['esc_oac_recommendation']=='OAC_RECOMMENDED_FROM_MINIMUM_CONFIRMED_SCORE'
    assert r['missing_components']==['C','D','V']

@pytest.mark.parametrize('age2,age,maximum',[(None,None,8),(2,0,8),(0,None,7)])
def test_age_categories_not_double_counted_in_upper_bound(age2,age,maximum):
    r=minimum_score(dict(C=None,H=None,A2=age2,D=None,S2=None,V=None,A=age))
    assert r['cha2ds2_va_maximum_possible']==maximum

@pytest.mark.parametrize('points,expected',[(0,'LOW_RISK'),(1,'CONSIDER_OAC'),(2,'OAC_RECOMMENDED')])
def test_complete_thresholds(points,expected):
    p=dict(C=0,H=0,A2=0,D=0,S2=0,V=0,A=0)
    p['S2']=2 if points==2 else 0;p['H']=1 if points==1 else 0
    assert minimum_score(p)['esc_oac_recommendation']==expected

def test_undetermined_recommendation_is_not_missingness_as_absence():
    p=dict(C=None,H=0,A2=0,D=0,S2=0,V=0,A=0)
    assert minimum_score(p)['esc_oac_recommendation']=='RECOMMENDATION_RANGE_UNCERTAIN'
    assert p['C'] is None

def test_incomplete_adls_allow_reviewable_bounded_inference():
    raw,u=proposal();v=functional_errors(raw,u)
    assert v['cfs']['accepted'] and v['pre_event_mrs']['accepted']
    assert v['pre_event_mrs']['best'] is None

def test_inconsistent_five_is_rejected_without_changing_raw_score():
    raw,u=proposal();raw['cfs'].update(best=5,range_min=5)
    before=copy.deepcopy(raw);v=functional_errors(raw,u)
    assert not v['cfs']['accepted']
    assert 'CFS_INTERNAL_CONSISTENCY_WARNING' in v['cfs']['validator_errors']
    assert raw==before

@pytest.mark.parametrize('score',[8,9])
def test_neurologic_dependency_does_not_imply_cfs_eight_or_nine(score):
    raw,u=proposal();raw['cfs'].update(best=score,range_min=score,range_max=score)
    assert not functional_errors(raw,u)['cfs']['accepted']

def test_acute_support_cannot_be_baseline():
    raw,u=proposal();u['B']['text']='Neu aufgetretene Aphasie bei Aufnahme.';raw['evidence'][0]['exact_quote']=u['B']['text']
    assert not functional_errors(raw,u)['cfs']['accepted']

def test_invented_quote_rejected():
    raw,u=proposal();raw['evidence'][0]['exact_quote']='Independently walking yesterday'
    assert not functional_errors(raw,u)['pre_event_mrs']['accepted']

def test_exact_mrs_five_requires_defining_dependency():
    raw,u=proposal();raw['pre_event_mrs'].update(best=5,range_min=5,range_max=5)
    assert not functional_errors(raw,u)['pre_event_mrs']['accepted']

def test_age_withheld_from_numerical_anchor_selection():
    assert 'Age is withheld' in FUNCTION_PROMPT

def test_discharge_independence_is_not_baseline_independence():
    raw,u=proposal();u['B'].update(text='Mobil und selbstständig.',parent_context='Bei Entlassung mobil und selbstständig.',heading='Entlassung')
    raw['evidence'][0]['exact_quote']=u['B']['text']
    assert not functional_errors(raw,u)['cfs']['accepted']

def test_baseline_care_paragraph_keeps_peg_but_excludes_new_aphasia():
    parent='Vorbestehende Hemiplegie und neu aufgetretene Aphasie. Wird vom Gatten gepflegt und über PEG ernährt.'
    u={str(i):dict(document_id='1',section_id='S',text=q,parent_context=parent,heading='Zusammenfassung') for i,q in enumerate(['Vorbestehende Hemiplegie','neu aufgetretene Aphasie','Wird vom Gatten gepflegt','über PEG ernährt'])}
    v=dict(evidence=[dict(evidence_id='0',temporal_class='BASELINE')])
    kept=locked_baseline_units(v,u)
    assert '0' in kept and '2' in kept and '3' in kept and '1' not in kept

def test_explicit_outcome_block_preserves_all_timepoints():
    text='Ischämischer Infarkt am 01.01.2020\nNeurologisches Outcome: prä-mRS 1\nbei Aufnahme NIHSS 10, mRS 4\nbei Entlassung NIHSS 1, mRS 1'
    rows,_=extract_section(text,patient_id='P',document_id='1',section_id='S',admission_date='2020-01-01',discharge_date='2020-01-05')
    assert [(r['scale_name'],r['timepoint'],r['value']) for r in rows]==[('mRS','PRE_EVENT',1),('NIHSS','ADMISSION',10),('mRS','ADMISSION',4),('NIHSS','DISCHARGE',1),('mRS','DISCHARGE',1)]

def link_fixture(header='Ischämischer Infarkt rechts'):
    text=header+'\nNIHSS 10'
    rows=[dict(patient_id='P',source_document_id='1',section_id='S',scale_name='NIHSS',event_id=None,source_event_type='ISCHEMIC_STROKE',source_offset=len(header)+1,assessment_id='X')]
    event=dict(patient_id='P',id='E',source_document_ids=[1],event_date='2020-01-01',event_type='ISCHEMIC_STROKE')
    docs={('P','1'):dict(sections=[dict(section_id='S',source_text=text)])}
    enc={('P','1'):dict(admission_date='2020-01-01')}
    return rows,event,docs,enc

def test_unique_current_source_stroke_can_be_linked_without_guessing_historical_stroke():
    rows,e,docs,enc=link_fixture()
    changes=recover_current_event_links(rows,[e,dict(e,id='OLD',event_date='2006-01-01')],docs,enc)
    assert len(changes)==1 and rows[0]['event_id']=='E'

def test_historical_year_block_cannot_be_attached_to_current_event():
    rows,e,docs,enc=link_fixture('Ischämischer Infarkt rechts 2006')
    assert not recover_current_event_links(rows,[e],docs,enc)

def test_competing_current_events_remain_unlinked():
    rows,e,docs,enc=link_fixture()
    assert not recover_current_event_links(rows,[e,dict(e,id='OTHER')],docs,enc)
