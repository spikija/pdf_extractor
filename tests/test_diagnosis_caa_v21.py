"""Patient-free semantic and temporal regressions for frozen review assembly."""
import copy
import pytest
from caa_pdf_extractor.extraction.diagnosis_canonical_v21 import (
    Eligibility,DiseaseGroup,validate_eligibility,validate_groups,category_errors,grouping_prompt,grouping_schema)
from caa_pdf_extractor.extraction.caa_evidence_v21 import EpisodeEvidence,ScanEvidence,assemble_patient,validate_evidence
from caa_pdf_extractor.extraction.caa_evidence_v21 import reconcile_validated_scan
from caa_pdf_extractor.extraction.caa_evidence_v21 import event_date_aliases
from caa_pdf_extractor.extraction.diagnosis_caa_v21_audit import audit_tables


def episode(**kw):
    values=dict(presentation='SPONTANEOUS_ICH',spontaneous_ich=True,traumatic_ich=False,
        alternative_cause='EXPLICITLY_EXCLUDED',current_ich_date='2020-01-01',
        sources={k:['DOC-1'] for k in ('presentation','spontaneous_ich','traumatic_ich','alternative_cause','current_ich_date')})
    values.update(kw);return EpisodeEvidence(**values)


def scan(sid='MRI-1',day='2020-01-02',modality='MRI',**changes):
    values=dict(mri_adequate=True if modality=='MRI' else None,blood_sensitive=True if modality=='MRI' else None,
        blood_sensitive_indirect=False,deep_exclusion='EXPLICITLY_ABSENT' if modality=='MRI' else 'UNKNOWN',
        deep_ich=None,deep_cmb=None,lobar_ich=True,lobar_cmb=1 if modality=='MRI' else None,css_foci=None,
        severe_cso_pvs=None,multispot_wmh=None,finger_like_projections=True,subarachnoid_extension=True,
        apoe_e4_status='UNKNOWN',ich_event_date='2020-01-01',ich_identity='LEFT_FRONTAL',sources={})
    values.update(changes)
    values['sources']={k:[sid] for k,v in values.items() if k!='sources' and v is not None}
    return dict(id=sid,date=day,modality=modality,episode_id='DOC-1',admission_date='2020-01-01',features=ScanEvidence(**values))


def assemble(scans,ep=None):return assemble_patient('P1','1950-06-01',scans,{'DOC-1':ep or episode()})


@pytest.mark.parametrize('with_mri',[False,True])
def test_undated_ct_evidence_never_invents_or_scores_an_ich_event(with_mri):
    ct=scan('CT-1','2020-01-01','CT',ich_event_date=None,ich_identity='UNKNOWN')
    ct['admission_date']=None
    scans=[ct]+([scan()] if with_mri else [])
    result=assemble(scans,episode(current_ich_date=None))
    row=result['edinburgh_ich_events'][0]
    assert row['id'] is None and row['event_date'] is None
    assert row['original_edinburgh']==('NOT_APPLICABLE' if with_mri else 'NOT_ASSESSABLE')
    assert row['ct_ids']==['CT-1']
    assert not result['edinburgh_features']
    result.update(diagnosis_candidate_eligibility=[],canonical_diagnoses=[],clinical_events=[],diagnosis_mentions=[])
    inputs={'P1':dict(scans=scans,episodes={'DOC-1':episode(current_ich_date=None)})}
    assert audit_tables(result,{},0,inputs)['unresolved_ICH_event_identity']==0
    row.update(id='FORBIDDEN_UNDATED_EVENT',spontaneous=True)
    assert audit_tables(result,{},0,inputs)['unresolved_ICH_event_identity']==1


def test_eligibility_requires_every_candidate():
    with pytest.raises(ValueError):validate_eligibility({}, {'R1':{}})


def test_acute_event_requires_event_type():
    with pytest.raises(ValueError):validate_eligibility({'R1':dict(candidate_type='ACUTE_CLINICAL_EVENT',reason='ICH',event_type='NONE')},{'R1':{}})


@pytest.mark.parametrize('label',['St.p. Intrazerebrales Hämatom rechts','Ischämischer Arteria-cerebri-media-infarkt links','Mediateilinfarkt rechts','Zustand nach Hirnstamminfarkt 2008','Intraparenchymale Massenblutung rechts frontal','Intracerebrale Massenblutung rechts','Lakunäre Ischämie rechts'])
def test_named_historical_cerebral_event_cannot_be_negated_or_disease(label):
    for kind in ('NEGATED_DIAGNOSIS','DIAGNOSIS_DISEASE','INVESTIGATION_RESULT'):
        with pytest.raises(ValueError):
            validate_eligibility({'R1':dict(candidate_type=kind,reason='Historical',event_type='NONE')},{'R1':{'label':label}})


def test_parenthetical_caa_does_not_change_primary_dementia_category():
    assert not category_errors(group(canonical_name='Mischdemenz (CAA)',subcategory='COGNITIVE_DISORDER_DEMENTIA'))


def test_response_schema_encodes_supported_dates_and_single_epilepsy_identity():
    from caa_pdf_extractor.extraction.caa_evidence_v21 import evidence_schema
    s=evidence_schema(['S1'],['S1'],[])
    assert s['properties']['scans']['properties']['S1']['properties']['ich_event_date']=={'enum':[None]}
    records={rid:dict(label='Symptomatische Epilepsie',accepted_date=None) for rid in ('R1','R2')}
    s=grouping_schema(records)
    assert s['properties']['R2']['properties']['identity_record_id']['enum']==['R1']


@pytest.mark.parametrize('label',['Ätiologisch: am ehesten mikroangiopathisch bei Diagnose 2','Subklinische epileptische Anfälle in erster Linie bei Diagnose 2'])
def test_cross_reference_and_seizures_alone_do_not_create_chronic_epilepsy(label):
    with pytest.raises(ValueError):
        validate_eligibility({'R1':dict(candidate_type='DIAGNOSIS_DISEASE',reason='Disease',event_type='NONE')},{'R1':{'label':label}})


@pytest.mark.parametrize('candidate_type',['INVESTIGATION_RESULT','LAB_RESULT','MEDICATION','HEADING_OR_FRAGMENT','NEGATED_DIAGNOSIS'])
def test_non_diseases_remain_non_diseases(candidate_type):
    result=validate_eligibility({'R1':dict(candidate_type=candidate_type,reason='Source evidence only',event_type='NONE')},{'R1':{}})
    assert result['R1'].candidate_type!='DIAGNOSIS_DISEASE'


def group(**kw):
    values=dict(canonical_name='Cerebrale Amyloidangiopathie',domain='NEUROLOGIC',subcategory='CAA',
        etiology='',status='CHRONIC',first_supported_date=None,member_ids=['R1'])
    values.update(kw);return DiseaseGroup(**values)


@pytest.mark.parametrize('changes',[
    {'subcategory':'HEADACHE_MIGRAINE'},
    {'canonical_name':'Ovarialkarzinom','domain':'ONCOLOGIC_HEMATOLOGIC','subcategory':'CAA'},
    {'canonical_name':'Hypertonie','domain':'NEUROLOGIC','subcategory':'HEADACHE_MIGRAINE'},
    {'canonical_name':'Intracerebrale Blutung frontal','subcategory':'HEADACHE_MIGRAINE'},
    {'canonical_name':'Status epilepticus','subcategory':'STATUS_EPILEPTICUS'}])
def test_incompatible_categories_rejected_without_relabeling(changes):
    g=group(**changes);before=g.model_dump();assert category_errors(g);assert g.model_dump()==before


def test_grouping_cannot_reintroduce_filtered_candidate():
    with pytest.raises(ValueError):validate_groups({'diseases':[group(member_ids=['R2']).model_dump()]},{'R1':{'accepted_date':None}})


def test_group_date_must_be_owned_earliest_date():
    with pytest.raises(ValueError):validate_groups({'diseases':[group(first_supported_date='2010-01-01').model_dump()]},{'R1':{'accepted_date':'2020-01-01'}})


def test_admission_presentation_join_and_distinct_ich_cmb():
    out=assemble([scan()]);row=out['boston_patient_timepoints'][0]
    assert row['presentation']==['SPONTANEOUS_ICH']
    assert row['classification']=='PROBABLE_CAA'
    assert out['boston_features'][0]['sources']['qualifying_presentation']==['DOC-1']


def test_serial_same_lesion_not_counted_twice():
    out=assemble([scan('CT-1','2020-01-01','CT'),scan('CT-2','2020-01-02','CT'),scan()])
    assert out['boston_features'][0]['lobar_ich']==1
    assert len(out['edinburgh_ich_events'])==1
    assert len(out['edinburgh_ich_events'][0]['followup_ct_ids'])==1


def test_recurrent_ich_dates_retained_separately():
    out=assemble([scan('CT-1','2020-01-01','CT'),scan('CT-2','2020-02-01','CT',ich_event_date='2020-02-01')])
    assert len(out['edinburgh_ich_events'])==2


def test_no_future_hemorrhagic_evidence():
    out=assemble([scan(lobar_ich=None,lobar_cmb=None),scan('MRI-2','2020-03-01',lobar_cmb=4)])
    assert out['boston_features'][0]['lobar_cmb'] is None
    assert out['boston_features'][1]['lobar_cmb']==4


def test_mri_adequacy_suppresses_all_event_cts():
    out=assemble([scan('CT-1','2020-01-01','CT'),scan('CT-2','2020-01-03','CT'),scan()])
    row=out['edinburgh_ich_events'][0]
    assert row['adequate_mri_available'] is True
    assert row['original_edinburgh']==row['simplified_edinburgh']=='NOT_APPLICABLE'


def test_inadequate_mri_does_not_block_ct_fallback():
    out=assemble([scan('CT-1','2020-01-01','CT'),scan(mri_adequate=False,blood_sensitive=False)])
    assert out['edinburgh_ich_events'][0]['simplified_edinburgh']=='HIGH_PROBABILITY'


def test_indirect_susceptibility_adequacy_flagged():
    out=assemble([scan(blood_sensitive_indirect=True)])
    assert out['boston_patient_timepoints'][0]['classification']=='PROBABLE_CAA'
    assert any(h['code']=='BLOOD_SENSITIVE_EVIDENCE_INDIRECTLY_CONFIRMED' for h in out['review_holds'])


def test_complete_distribution_without_deep_sentence_has_review_trace():
    out=assemble([scan(deep_exclusion='ADEQUATE_REPORT_NO_DEEP_LESION_MENTION')])
    assert out['boston_patient_timepoints'][0]['classification']=='PROBABLE_CAA'
    assert any(h['code']=='DEEP_EXCLUSION_REQUIRES_PHYSICIAN_REVIEW' for h in out['review_holds'])


def test_omission_is_not_deep_absence():
    out=assemble([scan(deep_exclusion='UNKNOWN')])
    assert out['boston_patient_timepoints'][0]['classification']=='NOT_ASSESSABLE'


def test_current_deep_lesion_not_overwritten_by_exclusion_support():
    out=assemble([scan(deep_ich=True)])
    assert out['boston_patient_timepoints'][0]['classification']=='DOES_NOT_MEET'


def test_trauma_prevents_fallback():
    out=assemble([scan('CT-1','2020-01-01','CT')],episode(traumatic_ich=True))
    assert out['edinburgh_ich_events'][0]['simplified_edinburgh']=='NOT_APPLICABLE'


def test_ct_morphology_omission_unknown():
    out=assemble([scan('CT-1','2020-01-01','CT',finger_like_projections=None)])
    assert out['edinburgh_ich_events'][0]['simplified_edinburgh']=='NOT_ASSESSABLE'


def test_original_and_simplified_separate():
    out=assemble([scan('CT-1','2020-01-01','CT',apoe_e4_status='ONE_OR_MORE',finger_like_projections=False,subarachnoid_extension=False)])
    row=out['edinburgh_ich_events'][0]
    assert row['original_edinburgh']=='INTERMEDIATE_OR_UNCERTAIN'
    assert row['simplified_edinburgh']=='LOW_PROBABILITY'


def test_earliest_morphology_assessable_ct_selected():
    out=assemble([scan('CT-1','2020-01-01','CT',finger_like_projections=None),scan('CT-2','2020-01-02','CT'),scan('CT-3','2020-01-03','CT')])
    assert out['edinburgh_ich_events'][0]['primary_ct_id']=='CT-2'


def test_no_input_mutation_or_source_date_transfer():
    ss=[scan()];before=copy.deepcopy(ss);out=assemble(ss)
    assert ss==before
    assert out['boston_patient_timepoints'][0]['source_diagnosis_date'] is None


def test_redundant_mri_state_not_repeated():
    out=assemble([scan(),scan('MRI-2','2020-01-03')])
    assert len(out['boston_features'])==2
    assert len(out['boston_patient_timepoints'])==1


def test_earliest_sufficient_evidence_date_survives_later_changed_state():
    out=assemble([scan(),scan('MRI-2','2020-03-01',lobar_cmb=3)])
    assert len(out['boston_patient_timepoints'])==2
    assert out['boston_patient_timepoints'][1]['computed_classification_date']=='2020-01-02'


def test_serial_localization_refinement_is_not_a_second_ich():
    out=assemble([scan('CT-1','2020-01-01','CT',ich_identity='frontal'),scan(ich_identity='left_frontal')])
    assert out['boston_features'][0]['lobar_ich']==1


def test_source_swi_is_direct_and_validated_cmb_not_dropped():
    s=scan(blood_sensitive_indirect=True,lobar_cmb=None,css_foci=1)
    s.update(text='SWI: multiple lobar microbleeds.',validated_positive=[dict(finding_type='CMB',location='frontal',deleted=False)])
    updated,holds=reconcile_validated_scan(s)
    assert updated['features'].blood_sensitive_indirect is False
    assert updated['features'].lobar_cmb==1
    assert updated['features'].css_foci is None
    assert holds[0]['code']=='MODEL_FEATURE_DISAGREEMENT_WITH_SOURCE'
    assert s['features'].blood_sensitive_indirect is True


def test_negative_evidence_needs_sources_too():
    ep=episode().model_dump();ep['sources']['traumatic_ich']=[]
    with pytest.raises(ValueError,match='traumatic_ich'):
        validate_evidence(dict(episode=ep,scans={}),[],{'DOC-1'},{'2020-01-01'})


def audit_fixture():
    ss=[scan('CT-1','2020-01-01','CT'),scan()];out=assemble(ss)
    out.update(diagnosis_candidate_eligibility=[],canonical_diagnoses=[],clinical_events=[],diagnosis_mentions=[])
    inputs={'P1':dict(scans=ss,episodes={'DOC-1':episode()})}
    return out,inputs


def test_independent_gates_detect_missing_join_and_double_count():
    out,inputs=audit_fixture()
    assert all(v==0 for v in audit_tables(out,{},0,inputs).values())
    out['boston_features'][0]['qualifying_presentation']=None
    out['boston_features'][0]['lobar_ich']=2
    audit=audit_tables(out,{},0,inputs)
    assert audit['qualifying_presentation_not_joined_when_available']==1
    assert audit['serial_same_lesion_double_count']==1


def test_independent_gates_detect_forbidden_edinburgh_and_source_conflation():
    out,inputs=audit_fixture()
    out['edinburgh_ich_events'][0]['simplified_edinburgh']='HIGH_PROBABILITY'
    out['boston_patient_timepoints'][0]['source_diagnosis_date']='2020-01-02'
    audit=audit_tables(out,{},0,inputs)
    assert audit['Edinburgh_without_confirmed_MRI_fallback']==1
    assert audit['CAA_source_vs_computed_conflation']==1


def test_complete_source_distribution_supplies_review_state_not_explicit_absence():
    s=scan(deep_exclusion='UNKNOWN');s.update(complete_diagnostic_report=True,
        text='SWI: lobar microbleeds frontal. '+('Diagnostic brain interpretation. '*10),
        validated_positive=[dict(finding_type='CMB',location='frontal',deleted=False)])
    updated,_=reconcile_validated_scan(s)
    assert updated['features'].deep_exclusion=='ADEQUATE_REPORT_NO_DEEP_LESION_MENTION'
    assert updated['features'].deep_cmb is None
    s['complete_diagnostic_report']=False
    assert reconcile_validated_scan(s)[0]['features'].deep_exclusion=='UNKNOWN'


def test_explicit_followup_reference_joins_conflicting_onset_dates():
    first=scan('CT-1','2020-01-01','CT')
    followup=scan('CT-2','2020-01-10','CT',ich_event_date='2019-12-30')
    for s in (first,followup):s['validated_ich_identity']='left_frontal'
    followup['text']='Vergleichs CT vom 1.1.2020 vorliegend. Die frontale Blutung abgeblasst in Resorption.'
    out=assemble([first,followup])
    assert len(out['edinburgh_ich_events'])==1
    assert out['edinburgh_ich_events'][0]['event_date']=='2019-12-30'


def test_similar_location_alone_cannot_merge_recurrent_events():
    first=scan('CT-1','2020-01-01','CT')
    later=scan('CT-2','2020-02-01','CT',ich_event_date='2020-02-01')
    for s in (first,later):s['validated_ich_identity']='left_frontal'
    later['text']='Neue frontale Blutung.'
    assert len(assemble([first,later])['edinburgh_ich_events'])==2


def test_prior_adequate_mri_survives_later_unreported_mri():
    out=assemble([scan(),scan('MRI-2','2020-01-03',mri_adequate=False,blood_sensitive=None,deep_exclusion='UNKNOWN')])
    assert len(out['boston_patient_timepoints'])==1
    assert out['boston_features'][1]['assessment_mri_id']=='MRI-1'
    assert out['boston_features'][1]['current_mri_adequate'] is False


def test_explicit_event_date_supplies_time_bound_without_admission_date():
    s=scan();s['admission_date']=None
    assert assemble([s])['boston_patient_timepoints'][0]['qualifying_presentation'] is True


def test_chronic_epilepsy_with_separate_status_history_is_still_disease():
    records={'R1':dict(label='Symptomatische Epilepsie mit Anfällen und Status epilepticus')}
    raw={'R1':dict(candidate_type='DIAGNOSIS_DISEASE',event_type='NONE',reason='Explicit chronic epilepsy')}
    assert validate_eligibility(raw,records)['R1'].candidate_type=='DIAGNOSIS_DISEASE'


def test_future_event_date_cannot_be_assigned_to_older_scan():
    s=scan(ich_event_date='2020-03-01')
    raw=dict(episode=episode().model_dump(),scans={s['id']:s['features'].model_dump()})
    with pytest.raises(ValueError,match='cannot follow'):
        validate_evidence(raw,[s['id']],{'MRI-1','DOC-1'},{'2020-01-01','2020-03-01'},{'MRI-1':'2020-01-02'})


def test_unknown_deep_support_cannot_supply_negative_lesion_status():
    s=scan(deep_exclusion='UNKNOWN',deep_ich=False,deep_cmb=False)
    updated,holds=reconcile_validated_scan(s)
    assert updated['features'].deep_ich is None
    assert updated['features'].deep_cmb is None


def test_later_inhospital_ich_does_not_qualify_earlier_mri():
    out=assemble([scan(lobar_ich=None,ich_event_date=None)],episode(current_ich_date='2020-02-01'))
    assert out['boston_features'][0]['qualifying_presentation'] is None


def test_trauma_exclusion_uses_trauma_source_when_spontaneity_unknown():
    ep=episode(traumatic_ich=True,spontaneous_ich=None)
    ep.sources['spontaneous_ich']=[]
    out=assemble([scan('CT-1','2020-01-01','CT')],ep)
    assert out['edinburgh_features'][0]['sources']['spontaneous_ich']==['DOC-1']
    assert out['edinburgh_ich_events'][0]['simplified_edinburgh']=='NOT_APPLICABLE'


def test_generation_schema_rejects_explicit_impossible_categories_without_naming_disease():
    rs={'R1':dict(label='Vorhofflimmern',accepted_date=None),'R2':dict(label='Ovarialkarzinom',accepted_date=None),
        'R3':dict(label='Cerebrale Amyloidangiopathie',accepted_date=None)}
    fields=grouping_schema(rs)['properties']
    assert fields['R1']['properties']['domain']['enum']==['CARDIOVASCULAR']
    assert fields['R2']['properties']['subcategory']['enum']==['OTHER']
    assert fields['R3']['properties']['subcategory']['enum']==['CAA']
    assert fields['R1']['properties']['canonical_name']['enum']==[rs['R1']['label']]


@pytest.mark.parametrize('label',[
    'Diagnose:','Normalbefund. Keine epileptiformen Potenziale.',
    'Echokardiographie zeigt erhaltene linksventrikuläre Pumpfunktion.',
    'Trikuspidalklappe zart, keine Insuffizienz','Thyrex 75 µg 1-0-0-0',
    'Der HbA1c liegt normal; kein Hinweis auf Diabetes'])
def test_explicit_non_diagnosis_regressions_cannot_be_promoted(label):
    with pytest.raises(ValueError):
        validate_eligibility({'R1':dict(candidate_type='DIAGNOSIS_DISEASE',event_type='NONE',reason='Disease')},
                             {'R1':dict(label=label)})


def test_epilepsy_etiology_caa_does_not_change_primary_disease_category():
    label='Symptomatische Epilepsie mit fokalen Anfällen, symptomatisch bei Amyloidangiopathie'
    g=group(canonical_name='SYMPTOMATIC_EPILEPSY',subcategory='EPILEPSY')
    assert category_errors(g,[label])==[]
    schema=grouping_schema({'R1':dict(label=label,accepted_date=None)})
    assert schema['properties']['R1']['properties']['subcategory']['enum']==['EPILEPSY']


def test_cerebral_arteriitis_is_not_caa_subtype():
    assert category_errors(group(canonical_name='Zerebrale Arteriitis nicht weiter klassifiziert'),['Zerebrale Arteriitis nicht weiter klassifiziert'])


@pytest.mark.parametrize('label',[
    'Atypisches intracerebrales Hämatom rechts','Intracranielle Blutung rechts temporal',
    'Intracerebralhämatom rechts temporal','Atypische intracerebrale Hämorrhagie rechts frontal',
    'Hämorrhagischer Infarkt im Mediastromgebiet links','Subarachnoidalblutung Grad IV',
    'Intracerebrale Blutung, ätiologisch bei Amyloidangiopathie',
    'Zustand nach osteoplastischer Kraniotomie und Hämatomentleerung'])
def test_hemorrhage_occurrences_and_procedures_cannot_be_chronic_diseases(label):
    with pytest.raises(ValueError):validate_eligibility({'R1':dict(candidate_type='DIAGNOSIS_DISEASE',event_type='NONE',reason='Disease')},{'R1':dict(label=label)})


@pytest.mark.parametrize('label',['Ischämischer Infarkt im Stromgebiet der ACM rechts','Zustand nach ACM-Infarkt rechts','Ischämischer Grenzzoneninfarkt rechts','TIA'])
def test_cerebral_ischemic_occurrences_are_events(label):
    with pytest.raises(ValueError):validate_eligibility({'R1':dict(candidate_type='DIAGNOSIS_DISEASE',event_type='NONE',reason='Disease')},{'R1':dict(label=label)})


@pytest.mark.parametrize('label,subtype', [('pAVK IIa','ISCHEMIC_STROKE'),('Ischämische Kolitis','TIA')])
def test_non_cerebral_ischemia_cannot_be_a_neurologic_subcategory(label,subtype):
    assert category_errors(group(canonical_name=label,subcategory=subtype),[label])


def test_year_comparison_links_unique_known_event_without_inventing_a_date():
    first=scan('CT-1','2020-01-01','CT')
    later=scan('CT-2','2024-01-01','CT',ich_event_date=None);later['episode_id']='DOC-2'
    for s in (first,later):s['validated_ich_identity']='left_frontal'
    later['text']='Die Blutung aus der Voruntersuchung vom Jahr 2020 zwischenzeitlich resorbiert.'
    eps={'DOC-1':episode(),'DOC-2':episode(current_ich_date=None)}
    assert event_date_aliases([first,later],eps)['CT-2']=='2020-01-01'
    assert later['features'].ich_event_date is None
    second=scan('CT-3','2020-02-01','CT',ich_event_date='2020-02-01');second['validated_ich_identity']='left_frontal'
    assert event_date_aliases([first,second,later],eps)['CT-2'] is None
