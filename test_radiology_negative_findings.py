"""Assertion, explicit negatives, source boundaries and safe future persistence."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from test_radiology_v2_pet import bundle,findings,memory_db
from caa_pdf_extractor.extraction.imaging_reports import discover_studies,document_text
from caa_pdf_extractor.extraction import radiology_v2
from caa_pdf_extractor.database.models import RadiologyFinding,RadiologyNegativeFinding
from caa_pdf_extractor.extraction.radiology_artifacts import VerifiedExtractionError

@pytest.mark.parametrize('text,negative',[
 ('Kein Nachweis einer rezenten Ischämie.','NO_ACUTE_ISCHEMIA'),
 ('Keine frische Ischämie.','NO_ACUTE_ISCHEMIA'),
 ('Keine intrakranielle Blutung.','NO_ACUTE_HEMORRHAGE'),
 ('Kein Nachweis einer Subarachnoidalblutung.','NO_SAH'),
 ('Keine neuen Mikroblutungen.','NO_NEW_CMB'),
 ('Kein pathologisches Kontrastmittelenhancement.','NO_ENHANCEMENT'),
 ('Kein wandständiges KM-Enhancement der Gefäße im Sinne einer Vaskulitis.','NO_VASCULITIS_ENHANCEMENT'),
 ('Keine neugetretenen Kaliberirregularitäten.','NO_VASCULAR_CALIBER_ABNORMALITY'),
 ('Keine kortikale Siderose.','NO_CSS'),
 ('Kein Nachweis einer Raumforderung.','NO_MASS_LESION'),
 ('Keine intracerebrale Blutung.','NO_ICH'),
 ('Kein Hinweis auf akute Ischämie, oder Blutung.','NO_ACUTE_HEMORRHAGE'),
 ('Ausschluss einer frischen intrakraniellen Blutung.','NO_ACUTE_HEMORRHAGE'),
 ('Eine neue frische intrakranielle Blutung, Raumforderung oder umschriebene postischämische Läsion sind nicht sichtbar.','NO_ACUTE_HEMORRHAGE'),
])
def test_explicit_negatives_separate_from_positive_children(text,negative):
    value=findings(text)
    assert value['findings']==[]
    assert negative in {f['finding_type'] for f in value['negative_findings']}
    assert all(f['evidence_text'] in text for f in value['negative_findings'])

@pytest.mark.parametrize('text',[
 'Keine sichere Ischämie.','Ischämie nicht auszuschließen.','Fragliche Blutung.',
 'Falls eine parenchymale Komponente der Blutung besteht.','V. a. ICH.',
])
def test_uncertain_is_not_negative_or_positive(text):
    value=findings(text)
    assert value['findings']==[] and value['negative_findings']==[]
    assert value['uncertain_findings']

def test_clause_specific_negative_positive_and_absent():
    value=findings('Keine frische Ischämie. Multiple alte Mikroblutungen.')
    assert [f['finding_type'] for f in value['negative_findings']]==['NO_ACUTE_ISCHEMIA']
    assert [(f['finding_type'],f['temporal_status']) for f in value['findings']]==[('CMB','CHRONIC')]
    assert findings('Normale Ventrikelweite.')['negative_findings']==[]

@pytest.mark.parametrize('text,expected',[
 ('Akute intraparenchymale Blutung frontal rechts.',{'ICH'}),
 ('Subarachnoidale Blutung konvexitätsnah.',{'SAH'}),
 ('Sulcale Blutung konvexitätsnah.',{'SAH'}),
 ('Sulkale Blutung konvexitätsnah.',{'SAH'}),
 ('Oberflächliche Siderose entlang der Hirnfurchen mit Blutungsresiduen.',{'CSS'}),
 ('Alte Mikroblutungen.',{'CMB'}),
 ('Blutung.',set()),
 ('Bekanntes subdurales Hämatom frontal rechts.',set()),
 ('Intrakranielle Blutung\nHämosiderin/alte Mikroblutungen: Fokale Mikroblutungen.',{'CMB'}),
 ('Fragestellung: ICB?',set()),
 ('Mehrere Mikroblutungen, DD Amyloidangiopathie/Vaskulitis.',{'CMB'}),
])
def test_compartment_and_assertion_ownership(text,expected):
    assert {f['finding_type'] for f in findings(text)['findings']}==expected

@pytest.mark.parametrize('header',[
 'MRT: Schädel (23.08.2022)',
 'Kernspintomographie: Schädel craniell nativ,\n Kernspintomographie: Diffusion (23.08.2022)',
 'Kernspintomographie: Schädel craniell nativ,\n Kernspintomographie: Schädel craniell mit KM,\n Kernspintomographie: Diffusion\n (23.08.2022)',
 'Kernspintomographie: Schädel craniell nativ, Kernspintomographie: arterielle\n intracranielle Gefäße nativ (23.08.2022)',
 'Kernspintomographie:Schädel craniell nativ, Kernspintomographie:Diffusion, Kernspintomographie:\n SWI/Hämosequenz, Kernspintomographie:arterielle intracranielleGefäße nativ (23.08.2022)',
 'Kernspintomographie: Schädel nativ, Kernspintomographie: Black Blood\n Sequenz, Kernspintomographie: Black Blood Sequenz (23.08.2022)',
])
def test_logical_header_dates_and_physical_offsets(header):
    b=bundle('Vergleich mit MRI vom 01.01.2020. Keine frische Ischämie.',header,document_date='2024-01-01')
    s=discover_studies(b)[0];assert s.exam_date=='2022-08-23' and s.exam_date_source=='REPORT_EXPLICIT'
    assert len([p for p in s.source_spans if p['role']=='header_fragment'])==len(header.splitlines())
    assert all(document_text(b.documents[0])[p['start']:p['end']]==p['text'] for p in s.source_spans)

@pytest.mark.parametrize('body',['Vergleich mit MRI vom 23.08.2022. Keine Blutung.','Am 23.08.2022 fiel eine Verschlechterung auf.'])
def test_body_date_never_attached_to_undated_header(body):
    s=discover_studies(bundle(body,'MRT: Schädel',document_date='2024-01-01'))[0]
    assert s.exam_date=='2024-01-01' and s.exam_date_source=='DOCUMENT_DATE'

def test_actual_angiography_boundary_does_not_create_mri_css():
    b=bundle('Nach Resorption einer Blutung rechts frontal Defekte.\nAngiografie: A. carotis interna re (01.12.2023)\nStattgehabte ICB mit superfizieller Siderose.',
        'Kernspintomographie: Schädel nativ (28.11.2023)')
    result=radiology_v2.normalize(b,42)[0]
    assert 'Angiografie' not in result['exam']['report_text']
    assert not any(f['finding_type']=='CSS' for f in result['findings'])

def test_stability_is_not_ich_age_and_future_resorption_is_not_current():
    f=findings('Unverändert die Blutung rechts temporal.')['findings'][0]
    assert f['temporal_status']=='INDETERMINATE' and f['acute_lesion'] is None
    assert not findings('Nach Resorption der Blutungsareale wird ein MRI empfohlen.')['findings']

@pytest.mark.parametrize('header,body,scope,performed',[
 ('Kernspintomographie: Lendenwirbelsäule nativ (23.08.2022)','Alte Mikroblutungen.','SPINE',True),
 ('MRT: Schädel (23.08.2022)','Die Untersuchung wurde abgebrochen.','BRAIN',False),
])
def test_nonbrain_and_aborted_have_no_positive_or_negative_children(header,body,scope,performed):
    e=radiology_v2.normalize(bundle(body,header),42)[0]
    assert e['exam']['anatomic_scope']==scope and e['exam']['performed']==performed
    assert not e['findings'] and not e['negative_findings']

def test_negative_persistence_idempotence_conflict_and_zero_inference(memory_db):
    store,session,factory,root=memory_db;b=bundle('Keine frische Ischämie. Alte Mikroblutungen.')
    with patch.object(radiology_v2,'build_patient_bundle',return_value=b),patch.object(radiology_v2,'resolve_case_id',return_value=42):
        radiology_v2.extract_patient('123',session_factory=factory,debug_root=root)
        first=radiology_v2.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert first['negative_rows_inserted']==1 and first['finding_rows_inserted']==1 and not first['granite_called']
        second=radiology_v2.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert second['negative_rows_inserted']==0 and second['parent_rows_inserted']==0
        store[RadiologyNegativeFinding][0].evidence_text='Forged negative'
        with pytest.raises(VerifiedExtractionError,match='CONFLICT_EXISTING_NEGATIVE_FINDINGS'):
            radiology_v2.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
