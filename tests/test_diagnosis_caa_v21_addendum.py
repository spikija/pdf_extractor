import copy
import pytest
from caa_pdf_extractor.extraction.csf_analytes import canonical_analyte, normalize_analyte, extract_row
from caa_pdf_extractor.extraction.diagnosis_caa_v21_addendum import beta_variant_regressions, csf_census, apply_taxonomy
from caa_pdf_extractor.extraction.neurologic_taxonomy import classify_neurologic, validate_taxonomy


def test_beta_variants_through_numeric_extraction():
    assert beta_variant_regressions()
    assert normalize_analyte('Straße')=='straße'
    assert normalize_analyte('ß-Amyloid')=='beta-amyloid'
    assert normalize_analyte('Straße mit ß-Amyloid')=='straße mit beta-amyloid'


@pytest.mark.parametrize('label,canonical',[
    ('Gesamt-Tau','CSF_TOTAL_TAU'),('Tau gesamt','CSF_TOTAL_TAU'),('Total-Tau-Protein','CSF_TOTAL_TAU'),
    ('pTau','CSF_PTAU'),('phosphoryliertes Tau','CSF_PTAU'),('p-Tau181','CSF_PTAU181'),
    ('pTau217','CSF_PTAU217'),('Phospho-Tau 231','CSF_PTAU231')])
def test_tau_isoforms(label,canonical):
    rows=extract_row(label+': 62 pg/ml',csf_context=True)
    assert rows[0]['canonical_analyte']==canonical
    assert rows[0]['value']=='62'


def test_reference_precedes_unit_is_not_result_and_unspecified_is_not_42():
    source='  ß-Amyloid                 >600     pg/ml     425 !'
    row=extract_row(source,csf_context=True)[0]
    assert row['value']=='425' and row['reference_low']=='600'
    assert row['reference_high'] is None and row['reference_text']=='>600'
    assert row['canonical_analyte']=='CSF_ABETA_UNSPECIFIED'
    assert row['original_analyte_text']=='ß-Amyloid' and row['original_source_row']==source
    assert row['abnormality']=='SOURCE_FLAGGED' and not row['derived']


def test_ratio_is_reported_not_recomputed():
    rows=extract_row('Aβ42/40: 0,061',csf_context=True)
    assert rows[0]['canonical_analyte']=='CSF_ABETA42_40_RATIO'
    assert rows[0]['value']=='0.061' and not rows[0]['derived']
    assert not extract_row('Aβ42 >600 pg/ml 100 200',csf_context=True)
    assert not extract_row('Amyloidangiopathie seit 2012',csf_context=True)
    assert not extract_row('Aβ42: 42 pg/ml')
    assert not extract_row('Plasma Aβ42: 42 pg/ml',csf_context=True)


@pytest.mark.parametrize('label,category,subcategory',[
 ('Zerebrale Vaskulitis','INFLAMMATORY_AUTOIMMUNE','CNS_VASCULITIS'),
 ('Zerebrale Arteriitis nicht weiter klassifiziert','INFLAMMATORY_AUTOIMMUNE','CNS_VASCULITIS'),
 ('Beinbetonte axonale sensible Polyneuropathie','PERIPHERAL_NERVE','AXONAL_SENSORY_POLYNEUROPATHY'),
 ('Subjektive kognitive Verschlechterung bei 2','COGNITIVE_DEMENTIA','SUBJECTIVE_COGNITIVE_DECLINE'),
 ('Restless-legs-Syndrom','MOVEMENT_DISORDER','RESTLESS_LEGS_SYNDROME'),
 ('Symptomatische Epilepsie mit komplex fokalen Anfällen bei Amyloidangiopathie','EPILEPSY_SEIZURE','SYMPTOMATIC_EPILEPSY'),
 ('Traumatische Subarachnoidalblutung','CEREBROVASCULAR','SUBARACHNOID_HEMORRHAGE'),
 ('Enzephalopathiebei Influenza-A-Infektion','ENCEPHALOPATHY','INFECTIOUS_ENCEPHALOPATHY'),
 ('Senile Demenz vom Mischtyp','COGNITIVE_DEMENTIA','MIXED_DEMENTIA'),
 ('Parkinsonsyndrom','MOVEMENT_DISORDER','PARKINSONISM'),
 ('Subarachnoidalblutung rechts frontal','CEREBROVASCULAR','SUBARACHNOID_HEMORRHAGE'),
 ('CAA','CEREBROVASCULAR','CEREBRAL_AMYLOID_ANGIOPATHY'),
 ('Dementia','COGNITIVE_DEMENTIA','DEMENTIA_UNSPECIFIED'),
 ('CAA-ri','INFLAMMATORY_AUTOIMMUNE','CAA_RELATED_INFLAMMATION'),
 ('ABRA','INFLAMMATORY_AUTOIMMUNE','ABETA_RELATED_ANGIITIS'),
 ('cSAH','CEREBROVASCULAR','CONVEXITY_SUBARACHNOID_HEMORRHAGE'),
])
def test_explicit_taxonomy_regressions(label,category,subcategory):
    row=classify_neurologic(label)
    assert (row['category'],row['subcategory'])==(category,subcategory)
    validate_taxonomy(row)


def test_etiology_and_hemorrhagic_transformation_and_delirium():
    row=classify_neurologic('Ischämischer Infarkt A.cerebri media links mit sekundärer Hämorrhagie',layer='EVENT')
    assert row['subcategory']=='ISCHEMIC_STROKE' and row['hemorrhagic_transformation']
    assert classify_neurologic('Traumatische Subarachnoidalblutung')['etiology']=='TRAUMATIC'
    assert classify_neurologic('Atraumatische Subarachnoidalblutung')['etiology']=='UNKNOWN'
    assert classify_neurologic('Delir bei V.a Demenz')['domain']=='PSYCHIATRIC'
    assert classify_neurologic('Symptomatische Epilepsie bei CAA')['etiology']=='CAA'
    assert classify_neurologic('Symptomatische Epilepsie mit fokalen Anfällen',layer='EVENT')['subcategory']=='FOCAL_SEIZURE'
    assert classify_neurologic('Polyneuropathie, diabetogen')['subcategory']=='POLYNEUROPATHY'
    with pytest.raises(ValueError):validate_taxonomy(dict(domain='NEUROLOGIC',category='HEADACHE',subcategory='CEREBRAL_AMYLOID_ANGIOPATHY'))


def test_census_reads_raw_pages_and_separate_tables_and_preserves_sources():
    snapshot=dict(patient_ids=['p'],tables=dict(source_documents=[dict(id=5,document_json=dict(
        document=dict(patient_id='p'),pages=[dict(page_number=1,text='Liquor\nTau gesamt 0-449 pg/ml 342\nAmyloidangiopathie',
            tables=[dict(rows=[['ß-Amyloid','>600','pg/ml','425','!']])])]))]))
    before=copy.deepcopy(snapshot)
    census,rows,gates,audit=csf_census(snapshot,[],{('p','DOC-5'):dict(admission_date='2020-01-02')})
    assert snapshot==before and all(v==0 for v in gates.values())
    assert len(rows)==2 and len(census)==2
    beta=next(r for r in rows if r['canonical_analyte'].startswith('CSF_ABETA'))
    assert beta['value']=='425' and beta['source_evidence_id'].startswith('DOC-5:P1:LT1R1')
    assert beta['date']=='2020-01-02' and beta['date_basis']=='ENCOUNTER_ADMISSION_DATE'
    assert audit['pages_scanned']==1


def test_census_blocks_ambiguous_numeric_cells_instead_of_silent_omission():
    snapshot=dict(patient_ids=['p'],tables=dict(source_documents=[dict(id=5,document_json=dict(
        document=dict(patient_id='p'),pages=[dict(page_number=1,text='Liquor\nTau gesamt 0-449 pg/ml 342\nAβ42 >600 pg/ml 425 426',tables=[])]))]))
    census,_,gates,_=csf_census(snapshot,[],{('p','DOC-5'):dict(admission_date='2020-01-02')})
    assert gates['supported_beta_amyloid_mentions_missed']==1
    assert gates['tau_detected_but_neighboring_beta_amyloid_missed']==1
    assert census[0]['reason_if_not_extracted']=='SUPPORTED_LAB_ROW_REQUIRES_REVIEW'
    snapshot['tables']['source_documents'][0]['document_json']['pages'][0]['text']='Liquor\nAβ42/40: 0.04 0.05'
    _,_,gates,_=csf_census(snapshot,[],{('p','DOC-5'):dict(admission_date='2020-01-02')})
    assert gates['supported_beta_amyloid_mentions_missed']==1


def test_taxonomy_retains_dates_and_source_links_and_explicit_scd_exception():
    candidates=[];mentions=[]
    for rid,label,kind,dxid in [('R001','Subjektive kognitive Verschlechterung','SYMPTOM_ONLY',None),
            ('R002','Traumatische Subarachnoidalblutung','ACUTE_CLINICAL_EVENT',None),
            ('R003','Intrazerebrale Blutung','ACUTE_CLINICAL_EVENT',None)]:
        candidates.append(dict(patient_id='p',record_id=rid,raw_candidate=label,candidate_type=kind,
            canonical_diagnosis_id=dxid,accepted_date='2020-02-03' if rid!='R001' else None,
            source_ids=[dict(doc=5,section='s',text=label)]))
        mentions.append(dict(patient_id='p',id='M'+rid,record_ids=[rid],canonical_diagnosis_ids=[]))
    tables=dict(diagnosis_candidate_eligibility=candidates,canonical_diagnoses=[],diagnosis_mentions=mentions,
        diagnosis_rejected_non_disease_text=[candidates[0]],clinical_events=[dict(patient_id='p',id='EV',
        record_ids=['R002','R003'],event_type='INTRACEREBRAL_HEMORRHAGE',event_date='2020-02-03',etiology='UNKNOWN',
        source_document_ids=[5],evidence_ids=['MR002','MR003'])])
    review,gates,metrics=apply_taxonomy(tables)
    assert all(v==0 for v in gates.values())
    assert len(tables['canonical_diagnoses'])==1
    assert tables['canonical_diagnoses'][0]['subcategory']=='SUBJECTIVE_COGNITIVE_DECLINE'
    assert candidates[0]['original_candidate_type']=='SYMPTOM_ONLY'
    assert tables['diagnosis_rejected_non_disease_text']==[]
    assert len(tables['clinical_events'])==2
    assert {r['event_type'] for r in tables['clinical_events']}=={'SUBARACHNOID_HEMORRHAGE','INTRACEREBRAL_HEMORRHAGE'}
    assert all(r['event_date']=='2020-02-03' and r['evidence_ids'] for r in tables['clinical_events'])
    assert len(mentions)==3 and len(review)==3
