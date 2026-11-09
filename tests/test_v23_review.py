from datetime import date
import pytest
from caa_pdf_extractor.extraction.diagnosis_taxonomy_v23 import normalize
from caa_pdf_extractor.extraction.medication_taxonomy_v23 import normalize as med,load_dictionary
from caa_pdf_extractor.extraction.clinical_scales_v23 import extract_section,resolve_observations
from caa_pdf_extractor.extraction.research_scores_v23 import component,cha2ds2_va,orbit,stess,get_lab_value_before_index,at_index
from caa_pdf_extractor.extraction.neurosonology_v23 import parse_exam
from caa_pdf_extractor.extraction.research_evidence_v23 import facts_at_index,labs_from_section
from caa_pdf_extractor.extraction.anticoagulation_decisions_v23 import extract
from caa_pdf_extractor.extraction.cognitive_tests_v23 import extract_cerad

GOLD='''Ischämischer Infarkt im Mediastromgebiet links am 28.03.2022
- Ätiologie: offen
- Akuttherapie: mechanische Thrombektomie TICI 3 am 28.03.2022
- neurologisches Outcome: prä-mRS 1
- bei Aufnahme: NIHSS 10, mRS 4
- bei Entlassung: NIHSS 1, mRS 1'''
def scales(text):return extract_section(text,patient_id='SYNTHETIC',document_id='D',section_id='S',admission_date='2022-03-28',discharge_date='2022-04-01')
def fact(v,day='2022-03-28'):return component(v,day,['SYNTHETIC'])
def lab(a,v,unit,day='2022-03-28'):return dict(patient_id='SYNTHETIC',analyte=a,value=v,unit=unit,sample_date=day,source_evidence_ids=['SYNTHETIC'],validation_status='VALID')

def test_required_stroke_regression():
    rows,eps=scales(GOLD)
    assert {(r['scale_name'],r['timepoint']):r['value'] for r in rows}=={('mRS','PRE_EVENT'):1,('NIHSS','ADMISSION'):10,('mRS','ADMISSION'):4,('NIHSS','DISCHARGE'):1,('mRS','DISCHARGE'):1}
    assert all(r['source']=='EXPLICIT' for r in rows)
    e=eps[0]
    assert (e['stroke_date'],e['vascular_territory'],e['side'],e['etiology'])==('2022-03-28','MCA','LEFT','UNKNOWN')
    assert (e['mechanical_thrombectomy'],e['reperfusion_grade_system'],e['reperfusion_grade'],e['procedure_date'])==(True,'TICI','3','2022-03-28')

def test_required_orbit_regression_and_future_invariance():
    facts={'antiplatelet':fact(True),'bleeding_history':fact(True)}
    labs=[lab('HEMOGLOBIN',11.4,'g/dL'),lab('EGFR',48,'mL/min/1.73m2')]
    kw=dict(patient_id='SYNTHETIC',index_date='2022-03-28',dob='1944-01-01',sex='female',facts=facts)
    first=orbit(**kw,labs=labs)
    assert first['components']==dict(age=1,anemia=2,bleeding_history=2,antiplatelet=1,renal=1)
    assert (first['orbit_score'],first['orbit_category'],first['orbit_complete'])==(7,'HIGH',True)
    later=orbit(**kw,labs=labs+[lab('HEMOGLOBIN',15,'g/dL','2022-03-29'),lab('EGFR',90,'mL/min/1.73m2','2022-03-29')])
    assert later==first

@pytest.mark.parametrize('label,name,category,domain',[
 ('Art. Hypertonie','ARTERIAL_HYPERTENSION','HYPERTENSION','CARDIOVASCULAR'),
 ('HTN','ARTERIAL_HYPERTENSION','HYPERTENSION','CARDIOVASCULAR'),
 ('Diabetes mellitus Typ II','DIABETES_MELLITUS_TYPE_2','DIABETES','ENDOCRINE_METABOLIC'),
 ('DM2','DIABETES_MELLITUS_TYPE_2','DIABETES','ENDOCRINE_METABOLIC'),
 ('insulinpflichtiger Diabetes mellitus Typ 2','DIABETES_MELLITUS_TYPE_2','DIABETES','ENDOCRINE_METABOLIC'),
 ('Hypercholesterinämie','HYPERCHOLESTEROLEMIA','LIPID_DISORDER','ENDOCRINE_METABOLIC'),
 ('Hyperlipidämie','LIPID_DISORDER','LIPID_DISORDER','ENDOCRINE_METABOLIC'),
 ('Milde normochrome Anämie','ANEMIA_UNSPECIFIED','ANEMIA','ONCOLOGIC_HEMATOLOGIC'),
 ('normozytäre normochrome Anämie','NORMOCYTIC_NORMOCHROMIC_ANEMIA','ANEMIA','ONCOLOGIC_HEMATOLOGIC'),
 ('VHF','ATRIAL_FIBRILLATION','ARRHYTHMIA','CARDIOVASCULAR'),
 ('mittelgradigeMINS','MITRAL_REGURGITATION','STRUCTURAL_HEART_DISEASE','CARDIOVASCULAR'),
 ('TRINS','TRICUSPID_REGURGITATION','STRUCTURAL_HEART_DISEASE','CARDIOVASCULAR')])
def test_taxonomy(label,name,category,domain):
    row=normalize(label)[0];assert (row['canonical_name'],row['category'],row['domain'])==(name,category,domain)

@pytest.mark.parametrize('label',['Kein Diabetes mellitus Typ 2','V.a. Hypertonie','Pulmonale Hypertonie','atrial flutter','insulinpflichtiger Diabetes mellitus'])
def test_no_invented_diagnosis(label):assert not normalize(label)

def test_multidisease_not_merged():
    assert {r['canonical_name'] for r in normalize('arterielle Hypertonie, Hyperlipidämie')}=={'ARTERIAL_HYPERTENSION','LIPID_DISORDER'}

@pytest.mark.parametrize('name,canonical,therapeutic,research',[
 ('Atorvalan','ATORVASTATIN','HMG_COA_REDUCTASE_INHIBITOR','LIPID_LOWERING'),
 ('Rosuvalan','ROSUVASTATIN','HMG_COA_REDUCTASE_INHIBITOR','LIPID_LOWERING'),
 ('Doxazosin','DOXAZOSIN','ALPHA_1_ADRENERGIC_BLOCKER','ANTIHYPERTENSIVE')])
def test_approved_medications(name,canonical,therapeutic,research):
    r=med(name,load_dictionary());assert (r['canonical_drug'],r['therapeutic_class'],r['research_class'])==(canonical,therapeutic,research)

@pytest.mark.parametrize('name',['UNKNOWNBRAND','DoxazosinExtra','Candeblo Plus','Lisinopril/HCT'])
def test_no_drug_guess(name):assert med(name,load_dictionary())['review_required']

@pytest.mark.parametrize('phrase,tp',[('prä-mRS 1','PRE_EVENT'),('premorbid mRS 1','PRE_EVENT'),('baseline mRS 1','PRE_EVENT'),('mRS vor Aufnahme: 1','PRE_EVENT'),('initial NIHSS 1','ADMISSION'),('NIHSS Aufnahme: 1','ADMISSION'),('Entlassungs-NIHSS 1','DISCHARGE'),('discharge mRS 1','DISCHARGE'),('90-Tage-mRS 1','FOLLOWUP'),('mRS nach 3 Monaten 1','FOLLOWUP')])
def test_temporal_synonyms(phrase,tp):assert scales(phrase)[0][0]['timepoint']==tp

def test_strokes_are_separate():
    rows,eps=scales(GOLD+'\nIschämischer Infarkt rechts am 01.01.2020\nbei Aufnahme: NIHSS 20, mRS 5')
    assert rows[-1]['event_id']!=rows[0]['event_id']
    assert rows[-1]['event_date']=='2020-01-01'

def test_prequalifier_not_shared():
    rows,_=scales('prä-mRS 1, mRS 4\nNIHSS 10')
    assert [r['timepoint'] for r in rows]==['PRE_EVENT','OTHER','OTHER']

def test_conflicts_not_imputed():
    rows,_=scales('bei Aufnahme: NIHSS 10\nbei Aufnahme: NIHSS 12')
    assert resolve_observations(rows)[0]['value'] is None
    assert resolve_observations(rows)[0]['conflict']

def test_explicit_wins_inference():
    rows,_=scales('bei Aufnahme: NIHSS 10');inferred=dict(rows[0],source='RETROSPECTIVE_PARTIAL',value=20)
    assert resolve_observations(rows+[inferred])[0]['value']==10

def test_nonstroke_no_score_fabrication():assert scales('Isolierter Anfall, postiktal verwirrt, sediert, intubiert')[0]==[]

def test_orbit_missing_not_zero():
    r=orbit(patient_id='SYNTHETIC',index_date='2022-03-28',dob='1944-01-01',sex='female',facts={},labs=[])
    assert r['score'] is None and set(r['missing_components'])=={'anemia','renal','antiplatelet','bleeding_history'}

def test_negative_anemia_history_without_labs_not_zero():
    r=orbit(patient_id='SYNTHETIC',index_date='2022-03-28',dob='1944-01-01',sex='female',facts={'anemia_history':fact(False)},labs=[])
    assert r['components']['anemia'] is None

@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True])
def test_invalid_labs_excluded(value):
    assert get_lab_value_before_index('SYNTHETIC','EGFR','2022-03-28',90,labs=[lab('EGFR',value,'mL/min/1.73m2')]) is None

def test_future_score_not_assigned_admission():
    rows,_=scales('bei Aufnahme NIHSS 10 am 30.03.2022')
    assert rows[0]['timepoint']=='OTHER'

def test_pre_mrs_does_not_interrupt_admission_scope():
    rows,_=scales('Bei Aufnahme NIHSS 10, prä-mRS: 2, mRS: 5.')
    assert [(r['timepoint'],r['value']) for r in rows]==[('ADMISSION',10),('PRE_EVENT',2),('ADMISSION',5)]

def test_compound_af_and_hypertension_retained():
    assert {r['canonical_name'] for r in normalize('Vorhofflimmern, arterielle Hypertonie')}=={'ATRIAL_FIBRILLATION','ARTERIAL_HYPERTENSION'}

def test_cerad_explicit_totals_and_historical_reference_no_conversion():
    rows,refs=extract_cerad('CERAD-Total-Score bei\n55,1; Vorbefund von 02/2023: CERAD-Total-Score bei 70,1',patient_id='SYNTHETIC',document_id='D',section_id='S')
    assert [r['raw_score'] for r in rows]==[55.1,70.1]
    assert rows[1]['assessment_role']=='HISTORICAL_REFERENCE'
    assert all(r['test_name']=='CERAD_TOTAL' and r['maximum_score'] is None for r in rows)
    assert not extract_cerad('CERAD wurde empfohlen; MMSE 24/30',patient_id='SYNTHETIC',document_id='D',section_id='S')[0]

def test_cerad_table_header_cannot_consume_a_date_or_mmse_column():
    for text in ['MMSE CERAD-Total-Score\n11/2022 25 54.6','MMSE: CERAD-Total-Score:\n20.12.2022 29 70.1']:
        assert not extract_cerad(text,patient_id='SYNTHETIC',document_id='D',section_id='S')[0]

def test_cha_age_exclusive_and_no_future():
    fs={k:fact(False) for k in ('C','H','D','S2','V')}
    r=cha2ds2_va(confirmed_af=True,af_state="CONFIRMED_AF",index_date='2022-03-28',dob='1944-01-01',facts=fs)
    assert r['score']==2 and r['components']['A']==0
    fs['H']=fact(True,'2022-03-29')
    assert cha2ds2_va(confirmed_af=True,af_state="CONFIRMED_AF",index_date='2022-03-28',dob='1944-01-01',facts=fs)['score'] is None
    assert not cha2ds2_va(confirmed_af=False,index_date='2022-03-28',dob='1944-01-01',facts=fs)['applicable']

def test_boolean_zero_not_a_sourced_negative():assert at_index(fact(0),'2022-03-28') is None

def test_undated_lab_rejected():
    assert get_lab_value_before_index('SYNTHETIC','HEMOGLOBIN','2022-03-28',90,labs=[lab('HEMOGLOBIN',11,'g/dL',None)]) is None

def test_ambiguous_same_day_lab_rejected():
    assert get_lab_value_before_index('SYNTHETIC','HEMOGLOBIN','2022-03-28',90,labs=[lab('HEMOGLOBIN',11,'g/dL'),lab('HEMOGLOBIN',14,'g/dL')]) is None

def test_lab_unit_and_date():
    rows=labs_from_section('Hb 114 g/l am 28.03.2022',patient_id='SYNTHETIC',document_id='D',section_id='S')
    assert rows[0]['value']==11.4 and rows[0]['sample_date']=='2022-03-28'

def test_stess_true_status_only_and_unknown_history():
    kw=dict(index_date='2022-03-28',dob='1944-01-01',previous_seizures=None,seizure_type=component('GENERALIZED_CONVULSIVE','2022-03-28',['S']),consciousness=component('COMATOSE','2022-03-28',['S']))
    assert not stess(true_status=False,**kw)['applicable']
    r=stess(true_status=True,**kw);assert r['score'] is None and r['missing_components']==['previous_seizures']
    kw['previous_seizures']=fact(False)
    assert stess(true_status=True,**kw)['score']==5

def test_neuro_nascet_not_velocity():
    def exam(t):return parse_exam(dict(patient_id='SYNTHETIC',id='E',source_document_id='D',section='S',report=t))
    r=exam('ICA links: NASCET 90%, PSV 300 cm/s')[0]
    assert (r['stenosis_percent'],r['stenosis_category'],r['classification_basis'])==(90,'PCT_70_99','SOURCE_REPORTED_NASCET')
    r=exam('ICA links: PSV 300 cm/s')[0]
    assert r['stenosis_percent'] is None and r['stenosis_category']=='UNKNOWN'
    assert exam('ICA rechts NASCET 20%, ICA links NASCET 90%')[0]['stenosis_percent'] is None

def test_neuro_nondominance_not_hypoplasia():
    r=parse_exam(dict(patient_id='SYNTHETIC',id='E',source_document_id='D',section='S',report='Vertebralis links nicht dominant, antegrad'))[0]
    assert r['hypoplasia']=='UNKNOWN'

def test_decision_indication_requires_link_and_direction():
    kw=dict(patient_id='SYNTHETIC',document_id='D',section_id='S',page=1,admission_date='2022-03-28',discharge_date='2022-04-01')
    r=extract('Bei Vorhofflimmern aufgrund Amyloidangiopathie keine OAK.',**kw)[0]
    assert (r['decision_type'],r['indication'],r['reason_direction'])==('WITHHOLD','AF','AGAINST_OAC')
    r=extract('OAK fortführen.',**kw)[0];assert r['indication']=='UNKNOWN'
