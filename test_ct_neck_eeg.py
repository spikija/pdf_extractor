"""Source-only clinical additions: nulls, assertion ownership and study identity."""
from unittest.mock import patch
import pytest
from test_radiology_v2_pet import bundle
from caa_pdf_extractor.extraction import case_rules,eeg,radiology_v2
from caa_pdf_extractor.extraction.death_rules import explicit_death_dates
from caa_pdf_extractor.extraction.neck_ultrasound import summarize_neck

@pytest.mark.parametrize('text',[
 'Exitus letalis am 22.05.2022','verstorben am 22.05.2022','verstarb am 22.05.2022','Tod am 22.05.2022',
 'Am 22.05.2022 um 07:19 Uhr verstarb Herr Beispiel überraschend.',
 'Exitus letalis am\n22.05.2022',
])
def test_explicit_patient_death_formats(text):
    b=bundle(text);result=case_rules.extract(case_rules.select_inputs(b))
    assert result['update_fields']['death_date']=='2022-05-22'
    assert all(p['evidence'] in text for p in result['provenance']['death_date'])

@pytest.mark.parametrize('text',[
 'Palliative Betreuung am 22.05.2022','DNR/DNE am 22.05.2022','Hospiz 22.05.2022',
 'Exitus letalis bei Diagnose 2. ICB am 22.05.2022',
 'Mutter verstarb am 22.05.2022','Am 22.05.2022 verstarb der Ehemann.',
 'Kein Tod am 22.05.2022','Tod am 22.05.2022 erwartet','Exitus letalis am 05/2022',
])
def test_no_death_inference_or_relative_attribution(text):assert explicit_death_dates(text)==[]

@pytest.mark.parametrize('text', ['ACI: keine Stenose >50%.','ACI: ohne Stenose 70%.','ACI: fragliche Stenose 70%.'])
def test_no_positive_neck_percentage_from_negative_or_uncertain(text):
    assert summarize_neck(text)['summary']=='INDETERMINATE'

def test_neck_reference_methods_are_not_interchangeable():
    assert summarize_neck('ACI: 50-60% nach ECST, 20% nach NASCET')['summary']=='INDETERMINATE'

def test_ct_vom_header_is_a_parent_with_owned_date():
    r=radiology_v2.normalize(bundle('Durchgeführte Untersuchung: Schädel nativ\nVergleich vom 01.01.2000. Parenchymblutung rechts frontal.','Computertomografie vom 22.05.2022'),42)
    assert len(r)==1 and r[0]['exam']['exam_date']=='2022-05-22'
    assert any(f['finding_type']=='ICH' for f in r[0]['findings'])

@pytest.mark.parametrize('text', ['Keine signifikante intraventrikuläre SAB.','Kein akuter kortikaler Infarkt.','Keine große Blutung.'])
def test_ct_qualified_absence_stays_audit_only(text):
    r=radiology_v2.normalize(bundle(text,'CT: Schädel nativ (22.05.2022)'),42)[0]
    assert not r['negative_findings'] and not r['findings']

def test_ct_no_midline_shift_does_not_exclude_all_mass_effect():
    r=radiology_v2.normalize(bundle('Keine Mittellinienverlagerung.','CT: Schädel nativ (22.05.2022)'),42)[0]
    assert [n['finding_type'] for n in r['negative_findings']]==['OTHER']

def test_eeg_postposed_negative_and_peer_boundary():
    r=interpret('Epileptiforme Potenziale kamen nicht zur Darstellung.\nEMG/NLG-Untersuchung (23.05.2022)\nFokale Verlangsamung links temporal.')
    assert r['eeg_epileptiform_discharges'] is False and r['eeg_focal_slowing'] is None
    assert 'EMG' not in r['eeg_report_text']

def test_eeg_negation_across_page_furniture_has_exact_evidence():
    t='Keine\nUniversitätsklinikum Beispiel\nParacelsus Universität\nBeispiel, geb.: 01.01.1900; Seite 4\n\nepileptiformen Potenziale.'
    r=interpret(t)
    assert r['eeg_epileptiform_discharges'] is False
    assert all(e['evidence_text'] in t for entries in r['evidence'].values() for e in entries)

def test_eeg_event_absence_is_explicit_without_inferred_normality():
    r=interpret('Während der EEG-Ableitung wurde keine habituelle Episode\naufgezeichnet.')
    assert r['eeg_clinical_event_captured'] is False and r['eeg_primary_finding'] is None

def test_bare_eeg_heading_and_negated_pathology():
    r=interpret('Kein pathologisches EEG.','EEG')
    assert r['eeg_status']=='performed' and r['eeg_primary_finding'] is None

def test_eeg_body_plan_is_not_a_performed_study_or_episode_timing():
    r=interpret('Ein EEG während der Episode wird empfohlen.')
    assert r['eeg_status']=='unknown' and r['eeg_primary_finding'] is None and r['eeg_tfne_timing']=='unknown'
    r=interpret('Fraglich unauffälliges EEG.')
    assert r['eeg_primary_finding'] is None

def test_ct_independent_lobar_and_subarachnoid_components():
    text='Akute lobäre Blutung rechts frontal mit geringen angrenzenden subarachnoidalen Blutungen.'
    r=radiology_v2.normalize(bundle(text,'CT: Schädel nativ (22.05.2022)'),42)[0]
    assert {'ICH','SAH'} <= {f['finding_type'] for f in r['findings']}
    assert not any(f['finding_type']=='ICH' and 'subarachnoidalen'==f.get('location') for f in r['findings'])

def test_ct_lobar_hemorrhage_area_and_isolated_sah_compartment():
    r=radiology_v2.normalize(bundle('Blutungsareal links frontal unverändert.','CT: Schädel nativ (22.05.2022)'),42)[0]
    assert [f['finding_type'] for f in r['findings']]==['ICH']
    r=radiology_v2.normalize(bundle('Subarachnoidale Blutung links frontal.','CT: Schädel nativ (22.05.2022)'),42)[0]
    assert [f['finding_type'] for f in r['findings']]==['SAH']

def test_ct_frontal_hemorrhage_area_retains_compartment_across_edema_modifier():
    t='Die Ausdehnung des Blutungsareales mit perifokaler Ödemzone links frontal zeigt sich nicht signifikant verändert.'
    r=radiology_v2.normalize(bundle(t,'CT: Schädel nativ (22.05.2022)'),42)[0]
    assert {'ICH','EDEMA'} <= {f['finding_type'] for f in r['findings']}
    r=radiology_v2.normalize(bundle('Subdurales Blutungsareal links frontal.','CT: Schädel nativ (22.05.2022)'),42)[0]
    assert not any(f['finding_type']=='ICH' for f in r['findings'])

def test_cervical_continuation_has_exact_full_source_and_reference_scales():
    from caa_pdf_extractor.extraction.examination_inputs import ExaminationInput
    from caa_pdf_extractor.extraction.neck_ultrasound import extend_cervical_reports
    t='Neurosonologie (22.05.2022)\nExtrakranielle Carotisabschnitte: Plaques.\nUniversitätsklinikum Beispiel\nBeispiel geb.: 01.01.1900; Seite 2\nA. carotis interna: 50-60% nach ECST, 20% nach NASCET\nTranstemporal: Ohne Kontrastmittel'
    b=bundle(t);d=b.documents[0];i=ExaminationInput(b.patient_id,'e',d.source_document_id,'s',1,'Neurosonologie (22.05.2022)',t.split('Universitätsklinikum')[0].strip(),'ultrasound','neck vessels','2022-05-22')
    extend_cervical_reports([i],b)
    assert 'NASCET' in i.text and 'Transtemporal' not in i.text
    assert i.text in t and summarize_neck(i.text)['reason']=='COMPETING_STENOSIS_REFERENCE_METHODS'

@pytest.mark.parametrize('header,report,expected',[
 ('Computertomografie: CTA intracranielle Gefäße mit KM (','26.02.2018)\nKeine Blutung.','2018-02-26'),
 ('Computertomografie: Schädel nativ, Röntgen: Handgelenk li','2E (24.10.2018)\nSchädel nativ: Keine Blutung.','2018-10-24'),
 ('CTA venöse cranielle Gefäße mit KM: Letzte CCT am 07.12.2008.','Kein Aneurysma.',None),
])
def test_ct_wrapped_owned_dates_and_no_document_or_comparison_fallback(header,report,expected):
    r=radiology_v2.normalize(bundle(report,header),42)[0]
    assert r['exam']['exam_date']==expected
    assert all(f['evidence_text'] in report for f in r['findings']+r['negative_findings'])

@pytest.mark.parametrize('text,result',[
 ('ACI Stenose <50%.','STENOSIS_LT_50'),('ACI Stenose 70%.','STENOSIS_GE_50'),
 ('ACI Stenose 50%.','STENOSIS_GE_50'),('ACI Stenose 40-60%.','INDETERMINATE'),
 ('ACI Verschluss.','OCCLUSION'),('ACI: kein Verschluss.','INDETERMINATE'),
 ('Carotis: keine Stenosen.','NO_STENOSIS'),('Carotis: keine hämodynamisch relevante Stenose.','INDETERMINATE'),
 ('Carotis: mildgradige Plaques.','INDETERMINATE'),
 ('ACI Stenose 20%.\nACI links Verschluss.','OCCLUSION'),
 ('Carotis: keine Stenosen.\nTranskranielle ACM Stenose 70%.','NO_STENOSIS'),
 ('ACM Stenose 80%.','INDETERMINATE'),
])
def test_worst_explicit_cervical_threshold(text,result):assert summarize_neck(text)['summary']==result

@pytest.mark.parametrize('text,kind',[
 ('Akute lobäre Blutung links frontal.','ICH'),('Subarachnoidale Blutung.','SAH'),
 ('Rezenter Mediateilinfarkt rechts.','ISCHEMIC_LESION'),('Perifokalödem.','EDEMA'),
 ('Mittellinienverlagerung nach links.','MASS_EFFECT'),('Hydrozephalus.','HYDROCEPHALUS'),
 ('Verkalkung im Thalamus.','CALCIFICATION'),
])
def test_atomic_ct_findings(text,kind):
    e=radiology_v2.normalize(bundle(text,'CT: Schädel nativ (22.05.2022)'),42)[0]
    assert any(f['finding_type']==kind and f['evidence_text'] in text for f in e['findings'])

def test_ct_negative_scope_multiple_rows_and_mri_independence():
    text='Keine große Blutung. Kein territorialer Infarkt. Ödem. Hydrozephalus.'
    e=radiology_v2.normalize(bundle(text,'CT: Schädel nativ (22.05.2022)'),42)[0]
    assert {'EDEMA','HYDROCEPHALUS'}<={f['finding_type'] for f in e['findings']}
    assert 'NO_TERRITORIAL_INFARCT' in {f['finding_type'] for f in e['negative_findings']}
    assert not any(f['finding_type'] in ('NO_ACUTE_HEMORRHAGE','NO_ACUTE_ISCHEMIA') for f in e['negative_findings'])
    assert not radiology_v2.normalize(bundle('Ödem. Hydrozephalus.'),42)[0]['findings']

def test_ct_zero_findings_current_header_date_and_cta_only_modality():
    e=radiology_v2.normalize(bundle('Vergleich CT vom 01.01.2020. Kein pathologischer Befund.','Computertomografie: CTA intracranielle Gefäße mit KM (22.05.2022)'),42)[0]
    assert e['exam']['modality']=='CTA' and e['exam']['exam_date']=='2022-05-22' and e['findings']==[]

def interpret(text,header='EEG Befund (22.05.2022)'):
    b=bundle(text,header);ss=eeg.discover(b);assert len(ss)==1
    return eeg.interpret(ss[0])

@pytest.mark.parametrize('text,primary,field,value,side',[
 ('Unauffälliges EEG.','normal','eeg_focal_slowing',None,'unknown'),
 ('Fokale Verlangsamung links temporal.','focal_slowing','eeg_focal_slowing',True,'left'),
 ('Generalisierte Verlangsamung.','generalized_slowing','eeg_focal_slowing',None,'generalized'),
 ('Epilepsietypische Potentiale rechts temporal.','epileptiform_discharges','eeg_epileptiform_discharges',True,'right'),
 ('Rhythmisch-periodisches Muster.','rhythmic_periodic_pattern','eeg_rhythmic_periodic_pattern',True,'unknown'),
 ('Elektrographischer Anfall.','electrographic_seizure','eeg_electrographic_seizure',True,'unknown'),
 ('Keine epilepsietypischen Potentiale.',None,'eeg_epileptiform_discharges',False,'unknown'),
])
def test_eeg_explicit_tristate(text,primary,field,value,side):
    r=interpret(text);assert r['eeg_primary_finding']==primary and r[field] is value and r['eeg_laterality']==side
    assert r['eeg_report_text']==text

def test_eeg_independent_booleans_priority_and_unknown_timing():
    r=interpret('Fokale Verlangsamung links temporal, keine epilepsietypischen Potentiale. Elektrographischer Anfall.')
    assert r['eeg_focal_slowing'] is True and r['eeg_epileptiform_discharges'] is False
    assert r['eeg_electrographic_seizure'] is True and r['eeg_primary_finding']=='electrographic_seizure'
    assert r['eeg_tfne_timing']=='unknown' and r['eeg_clinical_event_captured'] is None

@pytest.mark.parametrize('header',['EEG','Routine-EEG','Langzeit-EEG','Video-EEG','vEEG','Elektroenzephalographie','Elektroencephalographie','MOBILE Video-EEG-Monitoring'])
def test_eeg_aliases_and_parent_boundaries(header):
    b=bundle('Normalbefund.\nCT: Schädel (23.05.2022)\nAkute Blutung.',header+' (22.05.2022)')
    s=eeg.discover(b)[0];assert 'Blutung' not in s['eeg_report_text'] and s['eeg_date']=='2022-05-22'

def test_eeg_not_performed_and_plans_not_performed_inference():
    r=interpret('Nicht durchgeführt.','EEG angefordert (22.05.2022)')
    assert r['eeg_status']=='not_performed' and r['eeg_focal_slowing'] is None and r['eeg_primary_finding'] is None
    assert interpret('Nach Entlassung vorgesehen.','EEG geplant (22.05.2022)')['eeg_status']=='unknown'

@pytest.mark.parametrize('text,timing,event',[
 ('EEG während der Episode. Klinisches Ereignis aufgezeichnet.','during_episode',True),
 ('EEG nach der Episode.','post_episode',None),
 ('Interiktales EEG unauffällig. Keine habituellen Ereignisse abgeleitet.','interictal',False),
 ('Während derselben Aufnahme: EEG normal.','unknown',None),
])
def test_eeg_explicit_tfne_context_only(text,timing,event):
    r=interpret(text);assert r['eeg_tfne_timing']==timing and r['eeg_clinical_event_captured'] is event
