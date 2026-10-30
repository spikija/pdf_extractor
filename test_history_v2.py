"""Permanent hierarchical history, temporal precision, canonical evidence and sex controls."""
from copy import deepcopy
from dataclasses import replace
import pytest
from caa_pdf_extractor.extraction import history_v2 as h
from caa_pdf_extractor.extraction.gender_evidence import extract_gender
from caa_pdf_extractor.extraction.clinical_history_v2 import create_artifact,verify_artifact
from caa_pdf_extractor.extraction.radiology_artifacts import digest
from test_case import bundle

def source(text,heading='Diagnosen'):
 b=bundle(text);b.documents[0].sections[0].heading=heading;return b
def rows(text):return h.extract_bundle(source(text))['records']

@pytest.mark.parametrize('text,year,month',[
 ('Herpes Zoster L3 rechts 12/2025',2025,12),
 ('Kardiale Dekompensation bei vorbekannter Herzinsuffizienz 12/2025',2025,12),
 ('Neuritis vestibularis links 02/2023',2023,2),
 ('Ischämischer Infarkt parietal rechts mit punktförmiger hämorrhagischer Transformation, am ehesten im Dezember 2025 - genauer Onset unklar',2025,12)])
def test_exact_partial_date_semantics(text,year,month):
 r=rows(text)[0];assert (r['date_disease'],r['date_disease_year'],r['date_disease_month'],r['date_disease_precision'],r['date_disease_basis'])==(None,year,month,'MONTH','EXPLICIT_MONTH_YEAR')
 if 'Onset' in text:assert r['temporal_qualifiers']==['genauer Onset unklar']

@pytest.mark.parametrize('text,disease',[
 ('Diabetes mellitus Typ 2 - HbA1c 6,2% 02.01.2026','Diabetes mellitus Typ 2'),
 ('Hypercholesterinämie - LDL-C 128 mg/dl 07.01.2026','Hypercholesterinämie'),
 ('Vorhofflimmern - elektrische Kardioversion 06/2017, 07/2017','Vorhofflimmern'),
 ('Nierenzellkarzinom links mit Nierenteilresektion 2007','Nierenzellkarzinom links'),
 ('Vorhofflimmern; elektrische Kardioversion 06.07.2017','Vorhofflimmern'),
 ('Diabetes mellitus Typ 2; HbA1c 02/2023','Diabetes mellitus Typ 2'),
 ('MRT 05.01.2026: Diabetes mellitus Typ 2','Diabetes mellitus Typ 2'),
 ('05.01.2026: Vorstellung; Diabetes mellitus Typ 2','Diabetes mellitus Typ 2')])
def test_no_procedure_measurement_or_preceding_date_leak(text,disease):
 t,_=h.temporal(text,disease);assert t==h.unknown()


def test_direct_year_and_day():
 t,_=h.temporal('Nierenzellkarzinom ED 2007','Nierenzellkarzinom')
 assert t['date_disease_year']==2007 and t['date_disease_precision']=='YEAR' and t['date_disease'] is None
 t,_=h.temporal('Ischämischer Infarkt am 01.01.2020','Ischämischer Infarkt')
 assert t['date_disease']=='2020-01-01' and t['date_disease_precision']=='DAY'


def test_current_admission_proxy_is_separate():
 t,_=h.temporal('Diabetes mellitus Typ 2, neu diagnostiziert','Diabetes mellitus Typ 2',admission='2026-01-02',current_evidence='Diabetes mellitus Typ 2, neu diagnostiziert')
 assert t['date_disease'] is None and t['date_disease_proxy']=='2026-01-02'
 assert (t['date_disease_precision'],t['date_disease_basis'])==('ADMISSION_PROXY','CURRENT_ADMISSION_PROXY')


@pytest.mark.parametrize('text',['Vorerkrankungen: Diabetes mellitus Typ 2','Z.n. Diabetes mellitus Typ 2','vorbekannter Diabetes mellitus Typ 2','Risikofaktoren: Diabetes mellitus Typ 2','Diabetes mellitus Typ 2 12/2025','Diabetes mellitus Typ 2'])
def test_no_admission_proxy_for_copied_historical_or_partial_dates(text):
 t,_=h.temporal(text,'Diabetes mellitus Typ 2',admission='2026-01-02',current_evidence=text)
 assert t['date_disease_proxy'] is None


def test_nested_diagnoses_and_attributes():
 result=h.extract_bundle(source('6. Zerebrovaskuläre Risikofaktoren:\n - Vorhofflimmern\n - Arterielle Hypertonie\n - Diabetes mellitus Typ 2 - HbA1c 6,2% 02.01.2026\n7. Herpes Zoster 12/2025\n - Aciclovir Behandlung\n8. Polyneuropathie\n - Thioctacid Infusion 13.02.2023\n9. Kardiale Diagnosen:\n - HFpEF bei permanentem VHF'))
 labels=[r['disease'] for r in result['records']]
 assert all(x in labels for x in ['Vorhofflimmern','Arterielle Hypertonie','Diabetes mellitus Typ 2','HFpEF bei permanentem VHF'])
 assert not any('Aciclovir' in x or 'Thioctacid' in x or 'Risikofaktoren' in x for x in labels)


def test_page_continuation_and_past_history_medication_boundary():
 b=source('1. Zerebrovaskuläre Risikofaktoren:\n - Vorhofflimmern')
 b.documents[0].sections.append(replace(b.documents[0].sections[0],section_id='s2',page_start=2,page_end=2,heading=None,source_text=' - Hypercholesterinämie\n2. Kardiale Diagnosen:\n - HFpEF bei permanentem VHF'))
 labels=[r['disease'] for r in h.extract_bundle(b)['records']]
 assert 'Hypercholesterinämie' in labels and 'HFpEF bei permanentem VHF' in labels
 b=source('Vorerkrankungen:\nHerpes Zoster 12/2025\nKardiale Dekompensation 12/2025\nPolyneuropathie\nBenigne Prostatahyperplasie\nPrämedikation:\nMetformin 500 mg',heading='Anamnese')
 labels=[r['disease'] for r in h.extract_bundle(b)['records']]
 assert len(labels)==4 and not any('Metformin' in x for x in labels)


def mention(label,text,doc):
 t,a=h.temporal(text,label)
 return {'disease':label,'temporal':t,'date_audit':a,'source_document_id':doc,'section_id':'s'+str(doc),'item_id':'i','page':1,'evidence_text':text,'evidence_role':'PRIMARY_DIAGNOSIS'}


def test_chronic_aliases_multiple_sources_and_best_direct_date():
 records,holds=h.canonicalize([mention('Vorhofflimmern','Vorhofflimmern ED 2007',1),mention('permanentes VHF','permanentes VHF',2),mention('VHF','VHF',3)])
 assert len(records)==1 and records[0]['supporting_source_mentions']==3 and records[0]['date_disease_year']==2007 and not holds


def test_distinct_neurologic_events_and_disagreeing_onsets_not_merged():
 records,_=h.canonicalize([mention('ICH links frontal','ICH links frontal am 01.02.2015',1),mention('ICH rechts frontal','ICH rechts frontal am 01.02.2025',2)])
 assert len(records)==2
 records,_=h.canonicalize([mention('Herpes Zoster','Herpes Zoster 12/2025',1),mention('Herpes Zoster','Herpes Zoster 02/2023',2)])
 assert len(records)==2


def gender(text):return extract_gender(source('Sepp Musser, geb.: 21.10.1941\n'+text,heading='Anamnese'))


@pytest.mark.parametrize('text,expected',[
 ('84-jähriger Patient','man'),('84-jährige Patientin','woman'),
 ('der Patient berichtet Schmerzen\nHerr Dr. Musser wurde entlassen','man'),
 ('die Patientin berichtet Schmerzen\nFrau Musser wurde entlassen','woman'),
 ('Geschlecht: männlich','man'),('Geschlecht: weiblich','woman')])
def test_patient_bound_gender(text,expected):assert gender(text)['value']==expected


@pytest.mark.parametrize('text',[
 'Sehr geehrter Herr Dr. Herbert Hiessberger','Herr Dr. Herbert Hiessberger',
 'Mutter ist eine 84-jährige Patientin','der Ehemann ist ein 84-jähriger Patient',
 'Patientin Sepp','Dr. Musser','Maler in Pension','Herr Dr. Musser\nHerr Dr. Musser'])
def test_recipients_relatives_name_occupation_and_repeats_not_gender(text):assert gender(text)['value'] is None


def test_conflicting_gender_is_blocked():
 out=gender('84-jähriger Patient\n84-jährige Patientin');assert out['value'] is None and out['status']=='CONFLICTING_GENDER'


def test_artifact_source_change_and_forgery_rejected():
 b=source('1. Herpes Zoster 12/2025');a=create_artifact(b,1);assert verify_artifact(a,b)
 stale=deepcopy(b);stale.documents[0].sections[0].source_text+='\nchanged'
 with pytest.raises(ValueError):verify_artifact(a,stale)
 forged=deepcopy(a);forged['output']['records'][0]['date_disease_year']=2020;forged.pop('artifact_sha256');forged['artifact_sha256']=digest(forged)
 with pytest.raises(ValueError):verify_artifact(forged,b)


def test_commit_plan_idempotent_and_duplicate_existing_blocked():
 artifact={'records':rows('1. Vorhofflimmern')};r=artifact['records'][0]
 assert h.plan_commit(artifact,[])[0]['status']=='INSERT'
 existing={'id':10,'disease':r['disease'],**{k:r[k] for k in h.unknown()}}
 ev=[{'clinical_history_id':10,**e} for e in r['evidence']]
 p=h.plan_commit(artifact,[existing],ev)[0];assert p['status']=='UNCHANGED' and not p['new_evidence']
 assert h.plan_commit(artifact,[existing,{**existing,'id':11}])[0]['status']=='BLOCKED_IDENTITY_REVIEW'

def test_forged_date_in_both_record_and_source_assertion_is_blocked():
 b=source('1. Herpes Zoster 12/2025');a=create_artifact(b,1)
 a['output']['mentions'][0]['temporal']['date_disease_year']=2020
 a['output']['records'][0]['date_disease_year']=2020
 a['output']['records'][0]['evidence'][0]['date_assertion']['date_disease_year']=2020
 a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
 with pytest.raises(ValueError):verify_artifact(a,b)

def test_existing_curated_day_is_not_downgraded_to_unknown():
 artifact={'records':rows('1. Vorhofflimmern')}
 existing={'id':10,'disease':'Vorhofflimmern','date_disease':'2020-01-01'}
 assert h.plan_commit(artifact,[existing])[0]['status']=='BLOCKED_EXISTING_DATE_CONFLICT'

def test_best_day_beats_compatible_month_and_copied_unknown():
 records,_=h.canonicalize([mention('Ischämischer Infarkt','Ischämischer Infarkt 12/2025',1),mention('Ischämischer Infarkt','Ischämischer Infarkt am 05.12.2025',2),mention('Ischämischer Infarkt','Ischämischer Infarkt',3)])
 assert len(records)==1 and records[0]['date_disease']=='2025-12-05' and records[0]['supporting_source_mentions']==3

def test_no_document_date_or_invalid_month_padding():
 b=source('1. Vorhofflimmern');b.documents[0].document_date='2026-01-02'
 assert h.extract_bundle(b)['records'][0]['date_disease_precision']=='UNKNOWN'
 t,_=h.temporal('Diabetes mellitus Typ 2 neu diagnostiziert 13/2025','Diabetes mellitus Typ 2',admission='2026-01-02',current_evidence='Diabetes mellitus Typ 2 neu diagnostiziert')
 assert t==h.unknown()

def test_unspecified_CKD_merges_only_when_one_explicit_grade_exists():
 records,_=h.canonicalize([mention('Chronische Niereninsuffizienz G2','Chronische Niereninsuffizienz G2',1),mention('Niereninsuffizienz','Niereninsuffizienz',2)])
 assert len(records)==1 and records[0]['supporting_source_mentions']==2

@pytest.mark.parametrize('text,label',[
 ('Amyloidangiopathie (stabiler Befund vom Oktober 2025)','Amyloidangiopathie'),
 ('NIT des Dünndarms, operiert November 2021','NIT des Dünndarms'),
 ('Excision eines exulcerierten Basalioms am Rücken Dezember 2011','Basaliom'),
 ('akute Cholecystitis, CHE im August 2021','akute Cholecystitis')])
def test_interpretation_followup_and_additional_procedure_date_guards(text,label):
 assert h.temporal(text,label)[0]==h.unknown()

def test_full_hemorrhage_date_owns_ventricular_complication_modifier():
 text='Intrazerebrales Hämatom rechts mit Ventrikeleinbruch und Mittellinienshift am 12.05.2019'
 assert h.temporal(text,'Intrazerebrales Hämatom rechts mit Ventrikeleinbruch und Mittellinienshift')[0]['date_disease']=='2019-05-12'

def test_plain_diagnoses_split_without_borrowing_procedure_date():
 r=rows('Nebendiagnosen\nDemenz\nInsulinabhängiger Diabetes mellitus II\nZustand nach ACM-Infarkt rechts 2018\npAVK IIa\nHyperlipidämie\nakute Cholecystitis, CHE im August 2021')
 assert any(x['disease']=='Demenz' and x['date_disease_precision']=='UNKNOWN' for x in r)
 assert not any('Demenz Insulin' in x['disease'] for x in r)
 assert not any('Polygraphische Diagnose' in x['disease'] for x in rows('1. Polygraphische Diagnose 09/2016; AHI 25,9'))

@pytest.mark.parametrize('text,label',[
 ('Radiusfraktur links operat 6.11.2018 KH Hallein','Radiusfraktur links'),
 ('Radiusfraktur links, offene Reposition, Verplattung, Aptus radius am 6.11.2018','Radiusfraktur links'),
 ('Intracerebrales Hämatom rechts frontal (Zustand nach osteoplastischer Craniotomie und Hämatomentleerung 12.02.2020)','Intracerebrales Hämatom rechts frontal'),
 ('Gastroenteritis laufende intermittierende Selbstkatheterisierung, Zustand nach Neoplasie - Anlage 25.08.2016 in Ulm','Gastroenteritis'),
 ('Ischämischer Infarkt, systemische Lyse am 05.01.2026','Ischämischer Infarkt')])
def test_extended_procedure_date_ownership(text,label):assert h.temporal(text,label)[0]==h.unknown()

def test_two_hemorrhages_do_not_share_left_event_date():
 r=rows('1. Intracerebrale Blutung links suprainsulär am 27.12.2020 sowie intracerebrale Blutung rechts parietal unbekannten Alters')
 assert len(r)==2 and r[0]['date_disease']=='2020-12-27' and r[1]['date_disease_precision']=='UNKNOWN'

def test_bare_explicit_disease_year_retained_without_duplicate_undated_label():
 r=rows('1. Ischämischer Infarkt 2007\n2. Ischämischer Infarkt')
 assert len(r)==1 and r[0]['date_disease_year']==2007 and r[0]['date_disease_precision']=='YEAR' and r[0]['date_disease'] is None

@pytest.mark.parametrize('text,label',[
 ('Glaukomanfall 1995, 2001, 2008 YAG-Iridotomie 2001','Glaukomanfall'),
 ('HP positive Gastritis, Eradikation 15.01.2015','HP positive Gastritis'),
 ('Nierenzellkarzinom, Segmentresektion 2007','Nierenzellkarzinom')])
def test_legacy_and_target_procedure_guards_remain_in_force(text,label):assert h.temporal(text,label)[0]==h.unknown()

def test_explicit_other_gender_is_preserved():assert gender('Geschlecht: other')['value']=='other'

def test_grammatical_marker_cannot_override_other_explicit_gender():
 out=gender('Geschlecht: other\n84-jähriger Patient');assert out['value'] is None and out['status']=='CONFLICTING_GENDER'

def test_source_hierarchy_keeps_category_parent_and_disease_detail_parent():
 b=source('1. Kardiale Diagnosen:\n - Vorhofflimmern\n -- elektrische Kardioversion 06/2017\n2. Herpes Zoster 12/2025\n - Aciclovir Behandlung')
 mentions,audit=h.hierarchical_mentions(b)
 af=next(m for m in mentions if m['disease']=='Vorhofflimmern')
 category=next(a for a in audit if a['disposition']=='CATEGORY_HEADING')
 assert af['parent_item_id']==category['item_id']
 treatment=next(a for a in audit if a.get('evidence_text')=='Aciclovir Behandlung')
 herpes=next(m for m in mentions if m['disease']=='Herpes Zoster')
 assert treatment['parent_item_id']==herpes['item_id']
 cardioversion=next(a for a in audit if a.get('evidence_text')=='elektrische Kardioversion 06/2017')
 assert cardioversion['parent_item_id']==af['item_id']

def test_wrapped_MRI_detail_never_becomes_diagnosis_or_comparison_onset():
 r=rows('1. Ischämischer Infarkt parietal rechts im Dezember 2025\n - cMRT vom 05.01.2026: demarkierter Infarkt mit\n Thrombusnachweisin der SWI,\n mäßiggradige globale Hirnvolumenminderung, geringe Zunahme zu 02/2023\n2. Herpes Zoster 12/2025')
 assert len(r)==2 and not any('Thrombusnachweis' in x['disease'] or 'Ventrikelweite' in x['disease'] for x in r)
 assert h.temporal('Ischämischer Infarkt, geringe Zunahme zu 02/2023','Ischämischer Infarkt')[0]==h.unknown()
