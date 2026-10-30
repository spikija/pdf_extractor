import copy
import pytest
from pydantic import ValidationError
from caa_pdf_extractor.extraction.caa_criteria_v2 import BostonFeatures,EdinburghFeatures,boston_v2,edinburgh_ct,require_provenance,caa_imaging_assessment
from caa_pdf_extractor.extraction.research_metadata_v2 import (
    mri_technique,macro_location,cmb_location,ich_dimensions,edinburgh_morphology,
    source_caa_assertion,medication_proposal,first_lab,echo_metadata,gender_promotion)
from caa_pdf_extractor.extraction.diagnosis_canonical_v2 import validate_mapping,stable_id,validate_proposal

def adequate(**changes):
    row=dict(age=70,qualifying_presentation=True,mri_adequate=True,blood_sensitive_sequence=True,
        lobar_ich=1,lobar_cmb=0,css_csah_foci=0,hemorrhagic_count_complete=True,
        deep_ich=False,deep_cmb=False,alternative_cause=False,severe_cso_pvs=False,multispot_wmh=False)
    row.update(changes);return BostonFeatures(**row)

@pytest.mark.parametrize('changes,expected',[
    ({},'POSSIBLE_CAA'),({'lobar_cmb':1},'PROBABLE_CAA'),({'severe_cso_pvs':True},'PROBABLE_CAA'),
    ({'lobar_ich':0,'multispot_wmh':True},'POSSIBLE_CAA'),
    ({'lobar_ich':0},'DOES_NOT_MEET'),({'deep_cmb':True},'DOES_NOT_MEET'),
    ({'deep_ich':True},'DOES_NOT_MEET'),({'alternative_cause':True},'DOES_NOT_MEET'),
    ({'age':49},'DOES_NOT_MEET'),({'age':None},'NOT_ASSESSABLE'),
    ({'deep_cmb':None},'NOT_ASSESSABLE'),({'mri_adequate':None},'NOT_ASSESSABLE'),
    ({'blood_sensitive_sequence':None},'NOT_ASSESSABLE'),
    ({'lobar_cmb':None,'hemorrhagic_count_complete':False},'NOT_ASSESSABLE'),
    ({'lobar_ich':0,'multispot_wmh':None},'NOT_ASSESSABLE'),
    ({'lobar_ich':2,'lobar_cmb':None,'hemorrhagic_count_complete':False},'PROBABLE_CAA'),
])
def test_boston_truth_table(changes,expected): assert boston_v2(adequate(**changes))['classification']==expected

def test_pathology_required_not_clinician_wording():
    assert source_caa_assertion('bekannte cerebrale Amyloidangiopathie')['source_caa_status']=='CLINICIAN_DIAGNOSED_CAA'
    assert boston_v2(adequate())['classification']!='DEFINITE_CAA'
    assert boston_v2(adequate(pathology='BIOPSY_OR_HEMATOMA_SOME_CAA',pathology_no_other_diagnostic_lesion=True))['classification']=='PROBABLE_WITH_SUPPORTING_PATHOLOGY'
    assert boston_v2(adequate(pathology='AUTOPSY_SEVERE_CAA_VASCULOPATHY',pathology_no_other_diagnostic_lesion=True))['classification']=='DEFINITE_CAA'
    assert boston_v2(adequate(pathology='AUTOPSY_SEVERE_CAA_VASCULOPATHY'))['classification']!='DEFINITE_CAA'

def ct(**kw):
    x=dict(adequate_mri_available=False,ct_available=True,lobar_ich=True,spontaneous_ich=True,appropriate_context=True,finger_like_projections=True,subarachnoid_extension=True)
    x.update(kw);return EdinburghFeatures(**x)

@pytest.mark.parametrize('kw,category,version',[
    ({},'HIGH_PROBABILITY','SIMPLIFIED'),
    ({'finger_like_projections':False},'INTERMEDIATE_OR_UNCERTAIN','SIMPLIFIED'),
    ({'finger_like_projections':False,'subarachnoid_extension':False},'LOW_PROBABILITY','SIMPLIFIED'),
    ({'lobar_ich':False},'NOT_APPLICABLE','SIMPLIFIED'),
    ({'spontaneous_ich':None},'NOT_ASSESSABLE','SIMPLIFIED'),
    ({'finger_like_projections':None},'NOT_ASSESSABLE','UNKNOWN'),
    ({'apoe_e4_status':'ONE_OR_MORE','finger_like_projections':None},'HIGH_PROBABILITY','ORIGINAL'),
    ({'apoe_e4_status':'ZERO'},'HIGH_PROBABILITY','ORIGINAL'),
    ({'apoe_e4_status':'ONE_OR_MORE','finger_like_projections':False},'HIGH_PROBABILITY','ORIGINAL'),
    ({'apoe_e4_status':'ZERO','finger_like_projections':False},'INTERMEDIATE_OR_UNCERTAIN','ORIGINAL'),
    ({'apoe_e4_status':'ZERO','subarachnoid_extension':False},'NOT_ASSESSABLE','ORIGINAL'),
])
def test_edinburgh_versions(kw,category,version):
    x=edinburgh_ct(ct(**kw));assert x['classification']==category;assert x['version']==version

def test_ct_only_does_not_require_mri():
    assert boston_v2(BostonFeatures())['classification']=='NOT_ASSESSABLE'
    assert edinburgh_ct(ct())['classification']=='HIGH_PROBABILITY'
    assert ct().apoe_e4_status=='UNKNOWN'


def test_adequate_mri_blocks_all_serial_ct_assessments():
    for changes in ({}, {'finger_like_projections':False}, {'apoe_e4_status':'ONE_OR_MORE'}):
        result=edinburgh_ct(ct(adequate_mri_available=True,**changes))
        assert result['classification']=='NOT_APPLICABLE'
        assert result['edinburgh_ct_applicable'] is False
        assert result['version']=='UNKNOWN'


def test_unknown_mri_adequacy_does_not_authorize_fallback():
    result=edinburgh_ct(ct(adequate_mri_available=None))
    assert result['classification']=='NOT_ASSESSABLE'
    assert result['holds']==['UNKNOWN_MRI_ADEQUACY']


def test_boston_missing_other_inputs_does_not_trigger_edinburgh():
    result=caa_imaging_assessment(adequate(qualifying_presentation=None),ct(adequate_mri_available=True))
    assert result['framework']=='BOSTON_V2'
    assert result['classification']=='NOT_ASSESSABLE'


@pytest.mark.parametrize('changes',[{'ct_available':False},{'lobar_ich':False},{'spontaneous_ich':False}])
def test_no_eligible_framework_is_not_assessable(changes):
    result=caa_imaging_assessment(BostonFeatures(),ct(**changes))
    assert result['framework']=='NONE'
    assert result['classification']=='NOT_ASSESSABLE'


def test_inadequate_mri_allows_ct_fallback():
    result=caa_imaging_assessment(BostonFeatures(mri_adequate=False),ct())
    assert result['framework']=='EDINBURGH'
    assert result['classification']=='HIGH_PROBABILITY'

@pytest.mark.parametrize('location,expected',[('frontal','LOBAR'),('parietal','LOBAR'),('temporal','LOBAR'),('okzipital','LOBAR'),('Thalamus','DEEP'),('Kleinhirn','INFRATENTORIAL'),('frontal und Pons','MIXED'),('Marklager','UNKNOWN')])
def test_conservative_location(location,expected): assert macro_location(location)['macro_location']==expected

def test_marklager_cmb_unknown(): assert cmb_location('Marklager')=='UNKNOWN'

def test_mri_omissions_and_explicit_sequences():
    x=mri_technique('SWI, T2*, DWI ADC FLAIR T1 T2 TOF, T1 nach KM mit Gadovist')
    assert x['swi_performed'] is True and x['t2star_gre_performed'] is True
    assert x['contrast_status']=='GADOLINIUM' and x['postcontrast_t1'] is True
    x=mri_technique('Keine akute Ischämie.')
    assert x['blood_sensitive_sequence_present'] is None and x['contrast_status']=='UNKNOWN'
    assert mri_technique('ohne Kontrastmittel')['contrast_status']=='NONE'

def test_dimensions_not_volume():
    x=ich_dimensions('Blutung 3,5 x 2 x 3 cm (ap x ml x cc).')
    assert [x[f'diameter_{i}_mm'] for i in (1,2,3)]==[35.,20.,30.]
    assert x['volume_ml_derived'] is None and x['volume_method'] is None
    assert ich_dimensions('Keine Größenangabe')['max_diameter_mm'] is None

def test_ct_morphology_unknown_and_literal():
    assert edinburgh_morphology('Blutung frontal')['finger_like_projections'] is None
    assert edinburgh_morphology('finger-like projections mit subarachnoid extension')==dict(finger_like_projections=True,subarachnoid_extension=True)
    assert edinburgh_morphology('keine subarachnoidale Extension')['subarachnoid_extension'] is False
    assert edinburgh_morphology('subarachnoidale Blutung')['subarachnoid_extension'] is None

@pytest.mark.parametrize('text,status',[
 ('kein Hinweis auf Amyloidangiopathie','CAA_EXPLICITLY_NEGATED'),
 ('Verdacht auf Amyloidangiopathie','SUSPECTED_CAA'),
 ('ätiologisch bei Amyloidangiopathie','CLINICIAN_DIAGNOSED_CAA'),
 ('probable CAA nach Boston 2.0','FORMAL_PROBABLE_CAA'),
 ('Kopfschmerz','NO_CAA_ASSERTION'),
])
def test_source_assertion_independent(text,status): assert source_caa_assertion(text)['source_caa_status']==status

@pytest.mark.parametrize('role,used',[('first',True),('Erster Wert',True),('last',False),('min',False),('max',False)])
def test_only_first_lab(role,used):
    row=dict(column_role=role,decimal_value='0.7',biomarker='crp',unit_text='mg/dl')
    x=first_lab(row,'2020-01-02',0,.5)
    assert (x is not None)==used
    if used:
        assert x['measurement_date']=='2020-01-02' and x['date_basis']=='ENCOUNTER_ADMISSION_DATE'
        assert x['abnormality']=='HIGH' and x['phlebotomy_date_known'] is False

@pytest.mark.parametrize('name,source,agent,role',[
 ('Heparin','therapeutische Heparinisierung','UFH','THERAPEUTIC_ANTICOAGULATION'),
 ('Fragmin','Thromboseprophylaxe','LMWH','VTE_PROPHYLAXIS'),
 ('Clexane','4000 IE','LMWH','UNKNOWN'),
 ('Marcoumar','','VKA','UNKNOWN'),
 ('Lixiana','','DOAC','UNKNOWN'),
 ('Thrombo ASS','','ASPIRIN','ANTIPLATELET'),
])
def test_medication_date_and_role(name,source,agent,role):
    x=medication_proposal(name,source,'2020-01-08')
    assert x['date']=='2020-01-08' and x['first_prescription_date'] is None
    assert x['date_basis']=='DISCHARGE_DATE' and x['agent_class']==agent and x['antithrombotic_role']==role

def test_echo_subtype_and_explicit_negation():
    assert echo_metadata('TTE: EF 55%, kein PFO')['echo_subtype']=='TTE'
    assert echo_metadata('TEE: kein LAA Thrombus')['echo_subtype']=='TEE'
    assert echo_metadata('Echokardiographie')['echo_subtype']=='UNKNOWN_ECHOCARDIOGRAPHY'
    x=echo_metadata('TTE: EF 55%, kein PFO');assert x['lvef_percent']==55 and x['findings']['PFO']['value'] is False

def test_gender_algorithm_verified_is_not_physician_approval():
    assert not gender_promotion(None,'FEMALE')['promotion_eligible']
    assert gender_promotion(None,'FEMALE',True)['promotion_eligible']
    assert not gender_promotion('MALE','FEMALE',True)['promotion_eligible']

def mapping(name,root,sub='OTHER'):
    return dict(identity_record_id=root,canonical_name=name,domain='NEUROLOGIC',subcategory=sub,etiology='UNKNOWN',status='CHRONIC',event_type='NONE')

def test_repeated_epilepsy_and_ckd_collapse_but_events_separate():
    rec={k:{} for k in ('R1','R2','R3','R4')}
    raw={'R1':mapping('SYMPTOMATIC_EPILEPSY','R1','EPILEPSY'),'R2':mapping('SYMPTOMATIC_EPILEPSY','R1','EPILEPSY'),
         'R3':mapping('CKD','R3'),'R4':mapping('CKD','R3')}
    checked=validate_mapping(raw,rec);assert len({r['identity_record_id'] for r in checked.values()})==2
    record_dates={'R1':dict(accepted_date='2013-12-15'),'R2':dict(accepted_date='2017-10-16')}
    p=dict(diseases=[dict(canonical_name='SYMPTOMATIC_EPILEPSY',diagnosis_domain='NEUROLOGIC',diagnosis_subcategory='EPILEPSY',etiology='VASCULAR_METABOLIC',status='CHRONIC',confidence='HIGH',record_ids=['R1','R2'])],evidence_only_record_ids=[],events=[dict(event_type='CONVULSIVE_STATUS_EPILEPTICUS',record_ids=[r],episode_ids=[],date_ref=r,etiology='UNKNOWN') for r in record_dates])
    assert len(validate_proposal(p,record_dates,{}).events)==2
    p['events'][1]['date_ref']='R1'
    with pytest.raises(ValueError,match='Unowned'): validate_proposal(p,record_dates,{})

def test_idempotency_and_evidence_loss_rejection():
    assert stable_id('D','123',['R1','R2'])==stable_id('D','123',['R1','R2'])
    with pytest.raises(ValueError,match='Incomplete'): validate_mapping({'R1':mapping('CKD','R1')},{'R1':{},'R2':{}})
    raw={'R1':mapping('CKD','R2'),'R2':mapping('CKD','R1')}
    with pytest.raises(ValueError,match='cyclic'): validate_mapping(raw,{'R1':{},'R2':{}})

def test_features_need_provenance_and_strict_types():
    with pytest.raises(ValueError,match='provenance'): require_provenance(BostonFeatures(age=70))
    assert require_provenance(BostonFeatures(age=70,sources={'age':['CASE1-DOB','DOC1-DATE']})).age==70
    with pytest.raises(ValidationError): BostonFeatures(lobar_cmb=True)
    with pytest.raises(ValidationError): EdinburghFeatures(apoe_e4_status='negative')
    with pytest.raises(ValueError,match='Unknown source IDs'): require_provenance(BostonFeatures(age=70,sources={'age':['MADE_UP']}),{'DOC1'})

def test_t1_gradient_echo_is_not_t2star():
    x=mri_technique('T1-gewichtete Gradientenecho 3D-Block vor nach intravenöser Kontrastmittelapplikation.')
    assert x['t2star_gre_performed'] is None
    assert x['postcontrast_t1'] is True
    assert x['contrast_status']=='GADOLINIUM'

def test_axis_labelled_dimensions_and_explicit_volume():
    x=ich_dimensions('Hämatom sagittal 4 cm x koronar 2,2 cm x kraniokaudal 4 cm. Volumen 17 ml.')
    assert [x[f'diameter_{i}_mm'] for i in (1,2,3)]==[40,22,40]
    assert x['volume_ml_explicit']==17 and x['volume_ml_derived'] is None

def test_lovenox_is_lmwh_and_contrast_not_from_negative_enhancement():
    assert medication_proposal('Lovenox 60 mg','Lovenox 60 mg',None)['agent_class']=='LMWH'
    assert mri_technique('Keine Kontrastmittelaufnahme der Läsion')['contrast_status']=='UNKNOWN'

def test_source_pathology_categories_do_not_promote_biopsy_to_definite():
    assert source_caa_assertion('definite CAA, histologisch in der Autopsie bestätigt')['source_caa_status']=='DEFINITE_PATHOLOGICAL_CAA'
    assert source_caa_assertion('probable CAA with supporting pathology in cortical biopsy')['source_caa_status']=='PROBABLE_WITH_SUPPORTING_PATHOLOGY'
    assert source_caa_assertion('CAA in der Biopsie')['source_caa_status']!='DEFINITE_PATHOLOGICAL_CAA'
