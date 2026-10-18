"""Strict CT acquisition-date ownership with the approved document fallback."""
import pytest
from test_radiology_v2_pet import bundle
from caa_pdf_extractor.extraction.radiology_v2 import normalize

def test_ct_hemorrhage_area_and_edema_across_page_furniture_keep_exact_evidence():
    report='Mittellinienverlagerung nach links. Das\nUniversitätsklinikum Beispiel\nBeispiel geb.: 01.01.1900; Seite 4\nBlutungsareal unverändert mit perifokaler Ödemzone.'
    r=ct('CT: Schädel nativ (28.05.2019)',report)
    assert not r.get('classification_error')
    assert {'MASS_EFFECT','EDEMA'}<={f['finding_type'] for f in r['findings']}
    for f in r['findings']:assert f['evidence_text'] in report

def test_cortical_infarct_qualification_does_not_suppress_independent_hemorrhage_denial():
    r=ct('CT: Schädel nativ (29.08.2017)','Kein akuter corticaler Infarkt, keine Blutung.')
    kinds={f['finding_type'] for f in r['negative_findings']}
    assert 'NO_ACUTE_HEMORRHAGE' in kinds and 'NO_ACUTE_ISCHEMIA' not in kinds

def test_negative_early_infarct_signs_do_not_broaden_qualified_absence_of_ischemia():
    r=ct('CT: Schädel nativ (02.01.2026)','Keine abgrenzbare rezente Ischämie, keine Infarktfrühzeichen.')
    assert 'NO_ACUTE_ISCHEMIA' not in {f['finding_type'] for f in r['negative_findings']}

def ct(header,body='Unauffälliger Befund.',document_date=None,fallback=True):
    rows=normalize(bundle(body,header,document_date),42,allow_document_fallback=fallback)
    assert len(rows)==1
    return rows[0]

@pytest.mark.parametrize('header,date',[
 ('CT: Schädel nativ (20.02.2018)','2018-02-20'),
 ('Computertomografie: CTA intracranielle Gefäße mit KM (\n26.02.2018)','2018-02-26'),
 ('Computertomografie: Schädel nativ, Röntgen: Rippen re (\n24.08.2017)','2017-08-24'),
 ('Computertomografie: Schädel nativ, Röntgen: Handgelenk li\n2E (24.10.2018)','2018-10-24'),
 ('Computertomografie:\nSchädel nativ,\nCT-Angiografie intracranielle Gefäße\n(20.02.2018)','2018-02-20'),
])
def test_owned_explicit_and_wrapped_headers(header,date):
    e=ct(header,'Voruntersuchung vom 01.11.2017. Keine Blutung.','2020-01-01')['exam']
    assert (e['exam_date'],e['exam_date_source'])==(date,'REPORT_EXPLICIT')

def test_comparison_only_header_uses_document_date_with_correct_provenance():
    e=ct('CTA venöse cranielle Gefäße mit KM: Letzte CCT am 07.12.2008. Kraniale MRT vorliegend vom 08.08.2008.',document_date='2013-12-30')['exam']
    assert (e['exam_date'],e['exam_date_source'])==('2013-12-30','DOCUMENT_DATE')

def test_admission_discharge_fallback_uses_discharge_not_admission_or_midpoint():
    body='Wir berichten über den Aufenthalt von 15.12.2013 bis 24.12.2013 zur stationären Behandlung.\nCTA: Vergleich mit CT vom 03.05.2020\nKein Aneurysma.'
    e=normalize(bundle(body,'Dokument ohne Datum'),42)[0]['exam']
    assert (e['exam_date'],e['exam_date_source'])==('2013-12-24','DOCUMENT_DISCHARGE_DATE')

@pytest.mark.parametrize('history',[
 'Voruntersuchung vom 01.11.2017.','Vergleich mit CT vom 03.05.2020.',
 'Historisches CT vom 03.05.2020.','Letzte CCT am 07.12.2008.',
])
def test_no_available_current_or_document_date_is_null(history):
    e=ct('CTA: '+history)['exam']
    assert e['exam_date'] is None and e['exam_date_source']=='UNKNOWN'

def test_date_attached_to_examination_overrides_document_fallback():
    e=ct('CT: Schädel nativ','Untersuchungsdatum: 20.02.2018\nVergleich mit CT vom 03.05.2017.','2020-01-01')['exam']
    assert (e['exam_date'],e['exam_date_source'])==('2018-02-20','REPORT_EXPLICIT')

def test_document_fallback_can_still_be_disabled():
    e=ct('CTA: Keine Datumsangabe',document_date='2020-01-01',fallback=False)['exam']
    assert e['exam_date'] is None and e['exam_date_source']=='UNKNOWN'

def test_neighbor_date_not_borrowed_and_mri_pet_date_semantics_unchanged():
    text='Unauffälliger Befund.\nMRT: Schädel (01.01.2020)\nUnauffälliger Befund.\nFDG-PET (02.01.2020)\nUnauffälliger Befund.'
    b=bundle(text,'CT: Schädel nativ',document_date='2021-01-01')
    rows=normalize(b,42);ctrow=next(r for r in rows if r['exam']['modality']=='CT')
    mri=next(r for r in rows if r['exam']['modality']=='MRI')
    assert (ctrow['exam']['exam_date'],ctrow['exam']['exam_date_source'])==('2021-01-01','DOCUMENT_DATE')
    assert (mri['exam']['exam_date'],mri['exam']['exam_date_source'])==('2020-01-01','REPORT_EXPLICIT')
