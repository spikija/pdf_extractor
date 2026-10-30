"""Patient-free regressions for analysis identity, missingness and testing."""
import pytest
from caa_pdf_extractor.cohort_analysis_dataset import exact_age,unique_encounters,exam_inventory,mrs_score,medication_exposure,first_presentation
from caa_pdf_extractor.cohort_analysis_statistics import categorical,continuous,compare,pformat,make_table


def encounter(day='2020-01-02',end='2020-01-05',doc=1):
    return dict(admission_date=day,discharge_date=end,source_document_id=doc,status='VERIFIED_SOURCE_ENCOUNTER',
        reason=dict(primary_reason='FOCAL_NEUROLOGICAL_DEFICIT'),mrs={})


def test_age_does_not_impute_year_or_missing_admission():
    assert exact_age('1950', '2020-01-01') is None
    assert exact_age('1950-01-01',None) is None
    assert exact_age('2020-01-02','2020-01-01') is None
    assert exact_age('1950-01-01','2020-01-01')==pytest.approx(70,abs=.01)


def test_duplicate_encounter_documents_are_one_admission():
    rows=[encounter(doc=1),encounter(doc=2),encounter('2021-01-02','2021-01-06',3)]
    result=unique_encounters(rows)
    assert len(result)==2 and result[0]['source_document_ids']==[1,2]
    assert result[0]['discharge_date']=='2020-01-05'


def test_discharge_conflict_is_not_arbitrarily_resolved():
    result=unique_encounters([encounter(),encounter(end='2020-01-06')])[0]
    assert result['discharge_date'] is None and result['discharge_conflict']


def test_duplicate_scans_and_unresolved_same_day_reports():
    a=dict(raw_source_date='2020-01-01',imaging_id='A',raw_report_text='Normal cerebral report.',source_document_id=1)
    b=dict(a,imaging_id='B',source_document_id=2)
    count,lower,audit=exam_inventory('P',[a,b],'CT','IMAGING')
    assert count==lower==1 and audit[0]['source_rows']==2
    b['raw_report_text']='Different report, possible separate scan.'
    count,lower,audit=exam_inventory('P',[a,b],'CT','IMAGING')
    assert count is None and lower==1 and audit[0]['identity_status']=='SAME_DAY_IDENTITY_UNRESOLVED'


def test_undated_scan_does_not_generate_exact_count():
    a=dict(raw_source_date=None,imaging_id='A',raw_report_text='Brain report',source_document_id=1)
    assert exam_inventory('P',[a],'MRI','IMAGING')[:2]==(None,0)


def test_mrs_bounds_and_conflicts_are_missing():
    r=encounter();r['mrs']={'admission':{'status':'VALID','value':{'score':None,'min':3,'max':5}}}
    assert mrs_score([r],'admission') is None
    assert mrs_score([r],'discharge') is None
    a=encounter();a['mrs']={'admission':{'status':'VALID','value':{'score':2}}}
    b=encounter();b['mrs']={'admission':{'status':'VALID','value':{'score':3}}}
    assert mrs_score([a,a],'admission')==2 and mrs_score([a,b],'admission') is None


def drug(agent='LMWH',role='UNKNOWN',source='Enoxaparin 40 mg'):
    return dict(category='ANTITHROMBOTIC',antithrombotic_subclass='ANTICOAGULANT',agent_class=agent,antithrombotic_role=role,source_row=source)


def test_missing_medication_list_is_not_non_use():
    assert all(v is None for v in medication_exposure([],False).values())
    assert medication_exposure([],True)['antiplatelet'] is False


def test_prophylaxis_and_unspecified_heparin_not_therapeutic():
    v=medication_exposure([drug(role='VTE_PROPHYLAXIS')],True)
    assert v['vte_prophylaxis'] is True and v['therapeutic_anticoagulation'] is False
    v=medication_exposure([drug()],True)
    assert v['lmwh'] is True and v['therapeutic_anticoagulation'] is None
    v=medication_exposure([drug('DOAC')],True)
    assert v['doac'] is True and v['therapeutic_anticoagulation'] is None


def test_paused_anticoagulant_does_not_count_as_active():
    assert medication_exposure([drug('DOAC',source='Eliquis pausiert')],True)['anticoagulant'] is False
    assert medication_exposure([drug(role='THERAPEUTIC_ANTICOAGULATION')],True)['therapeutic_anticoagulation'] is True


def test_dapt_requires_concurrent_active_aspirin_and_p2y12():
    a=dict(category='ANTITHROMBOTIC',antithrombotic_subclass='ANTIPLATELET',agent_class='ASPIRIN',source_row='ASS 100 mg')
    b=dict(a,agent_class='P2Y12_INHIBITOR',source_row='Clopidogrel 75 mg')
    assert medication_exposure([a,b],True)['dual_antiplatelet'] is True
    assert medication_exposure([a,dict(b,source_row='Clopidogrel abgesetzt')],True)['dual_antiplatelet'] is False


def test_primary_ich_requires_clinical_event_and_spontaneous_episode():
    first=unique_encounters([encounter()])[0]
    ep={'DOC-1':dict(presentation='SPONTANEOUS_ICH',spontaneous_ich=True,current_ich_date='2020-01-01')}
    assert first_presentation(first,[],ep)[0]=='OTHER_FOCAL_NEUROLOGICAL_DEFICIT'
    event=dict(id='E1',event_type='INTRACEREBRAL_HEMORRHAGE',event_date='2020-01-01',source_document_ids=[1])
    assert first_presentation(first,[event],ep)[0]=='SPONTANEOUS_ICH'
    assert first_presentation(first,[dict(event,source_document_ids=[2])],ep)[0]=='OTHER_FOCAL_NEUROLOGICAL_DEFICIT'


def test_first_presentation_does_not_use_historical_stroke():
    first=unique_encounters([encounter()])[0]
    event=dict(id='E1',event_type='ISCHEMIC_STROKE',event_date='2010-01-02',source_document_ids=[1])
    assert first_presentation(first,[event])[0]=='OTHER_FOCAL_NEUROLOGICAL_DEFICIT'


def test_available_case_percentages_and_no_empty_summary():
    assert categorical([True,False,None])=='1/2 (50.0%)'
    assert categorical([None])=='NA' and continuous([None])=='NA'
    assert continuous([1,2,3,4]).startswith('2.5 [1.8–3.2]')


def test_fisher_matches_known_reference():
    result=compare([True]+[False]*9,[True]*11+[False]*3,'categorical')
    assert result['p_value_raw']==pytest.approx(.0027594561852200836)
    assert result['p_value']=='0.003'


def test_sparse_and_invariant_tests_are_not_reported():
    assert compare([True],[False]*8,'categorical')['p_value']=='NA'
    assert compare([False]*10,[False]*10,'categorical')['p_value']=='NA'
    assert compare([True]+[False]*9,[False]*10,'categorical')['p_value']=='NA'
    assert compare([1]*8,[1]*8,'continuous')['p_value']=='NA'
    assert pformat(.0001)=='<0.001'


def test_missingness_reconciles_group_denominators():
    rows=[dict(caa_positive=True,x=True),dict(caa_positive=True,x=None),dict(caa_positive=False,x=False)]
    specs=[dict(variable='x',characteristic='Test',section='Example',kind='categorical',definition='Test')]
    table,tests,missing=make_table(rows,specs)
    assert table[0]['overall']=='1/2 (50.0%)'
    assert missing[0]['available_n_overall']==missing[0]['available_n_CAA']+missing[0]['available_n_nonCAA']==2


def test_pet_annotation_requires_literal_brain_evidence_and_keeps_indeterminate():
    from caa_pdf_extractor.extraction.pet_rules import normalize_pet
    a=normalize_pet('pet ct fdg','Hirn: Hypometabolismus temporo-mesial beidseits.')
    assert a['fdg_pet_result']=='ABNORMAL' and a['classification_evidence']
    assert all(q in 'Hirn: Hypometabolismus temporo-mesial beidseits.' for q in a['classification_evidence'])
    a=normalize_pet('pet ct amyloid','Unklare kortikale Anreicherung.')
    assert a['amyloid_pet_result']=='INDETERMINATE'
    a=normalize_pet('pet ct fdg','Körperstamm: Pathologische Mehrspeicherung in der Lunge.')
    assert a['fdg_pet_result']=='INDETERMINATE' and not a['brain_component_present']


def test_death_dates_require_explicit_patient_owned_source_wording():
    from caa_pdf_extractor.extraction.death_rules import explicit_death_dates
    assert explicit_death_dates('Exitus letalis am 20.05.2025')[0]['value']=='2025-05-20'
    assert not explicit_death_dates('Am 20.05.2025 verstarb der Vater.')
    assert not explicit_death_dates('Möglicherweise Tod am 20.05.2025.')
    assert not explicit_death_dates('Aufgenommen am 20.05.2025. Exitus letalis.')
