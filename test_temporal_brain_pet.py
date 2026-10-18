"""Age vs interval change and exact brain ownership in composite PET reports."""
from pathlib import Path
from unittest.mock import patch,MagicMock
import pytest
from test_radiology_v2_pet import bundle,findings,memory_db
from caa_pdf_extractor.extraction.pet_rules import normalize_pet
from caa_pdf_extractor.extraction.pet_components import partition_pet
from caa_pdf_extractor.extraction import radiology_v2,examinations,examination_artifacts
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.database.models import RadiologyExam,RadiologyFinding

@pytest.mark.parametrize('text,kind,status,interval,acute',[
 ('Neue Mikro-Hämosiderinresiduen links okzipital.','CMB','INTERVAL_NEW',True,None),
 ('Neu aufgetretene Mikroblutungen links frontal.','CMB','INTERVAL_NEW',True,None),
 ('Microbleeds neu seit Voruntersuchung.','CMB','INTERVAL_NEW',True,None),
 ('Vorbestehende Mikroblutungen rechts temporal.','CMB','CHRONIC',False,False),
 ('Unveränderte Mikroblutungen rechts frontal.','CMB','CHRONIC',False,False),
 ('Alte Mikro-Hämosiderinresiduen links frontal.','CMB','CHRONIC',False,False),
 ('Multiple Microbleeds rechts frontal.','CMB','INDETERMINATE',None,None),
 ('Multiple Microbleeds im Vergleich zur alten MRT.','CMB','INDETERMINATE',None,None),
 ('Akute Mikroblutungen links frontal.','CMB','ACUTE',None,True),
 ('Rezente punktförmige ischämische Läsion links mesiotemporal.','ISCHEMIC_LESION','ACUTE',None,True),
 ('DWI-positive acute infarct left frontal.','ISCHEMIC_LESION','ACUTE',None,True),
 ('Alte ischämische Läsion links frontal.','ISCHEMIC_LESION','CHRONIC',None,False),
 ('Subakuter Infarkt rechts frontal.','ISCHEMIC_LESION','SUBACUTE',None,None),
 ('Akute Blutung links frontal.','ICH','ACUTE',None,True),
 ('Resorbierte Blutung links frontal.','ICH','CHRONIC',None,False),
 ('Defekt nach Blutung rechts frontal.','ICH','CHRONIC',None,False),
 ('Hämosiderinresiduum nach Blutung links frontal.','ICH','CHRONIC',None,False),
 ('Neue oberflächliche Hämosiderose links frontal.','CSS','CHRONIC',True,False),
 ('Oberflächliche Hämosiderose links frontal.','CSS','CHRONIC',None,False),
])
def test_temporal_status(text,kind,status,interval,acute):
    f=next(f for f in findings(text)['findings'] if f['finding_type']==kind)
    assert (f['temporal_status'],f['interval_new'],f['acute_lesion'])==(status,interval,acute)


def test_neighbor_acute_ich_does_not_date_cmb_or_css():
    fs=findings('Akute Blutung frontal, neben multiplen Microbleeds und oberflächlicher Hämosiderose.')['findings']
    assert next(f for f in fs if f['finding_type']=='CMB')['acute_lesion'] is None
    assert next(f for f in fs if f['finding_type']=='CSS')['acute_lesion'] is False


@pytest.mark.parametrize('text,result,pattern,brain',[
 ('Hirn:\nNormaler Metabolismus.\nAbdomen:\nPathologische Mehrspeicherung im Magen bei Gastritis.','NORMAL','NO_SPECIFIC_PATTERN',True),
 ('Brain: Normal metabolism; Stomach: abnormal uptake.','NORMAL','NO_SPECIFIC_PATTERN',True),
 ('Kopf, Hals:\nLymphknoten mit gesteigerter Traceranreicherung.\nAbdomen:\nGastritis mit pathologischer Mehrspeicherung.','INDETERMINATE',None,False),
 ('Body:\nPathologische Mehrspeicherung im Knochen.','INDETERMINATE',None,False),
 ('Hirn:\nDas Hirn wird separat befundet.\nKörperstamm:\nKeine pathologische Mehrspeicherung.','INDETERMINATE',None,False),
 ('Hirn:\nAbnormaler Metabolismus.','ABNORMAL','NONSPECIFIC',True),
 ('Hirn:\nHypometabolismus, Alzheimer-typisches Muster.','ABNORMAL','AD_TYPICAL',True),
 ('Hirn:\nUnklarer Metabolismus.','INDETERMINATE',None,True),
 ('Hirn:\nTechnisch unzureichend, nicht auswertbar.','NON_DIAGNOSTIC',None,True),
 ('Schädel-CT axial nativ:\nNicht auswertbar.\nAbdomen:\nPathologische Mehrspeicherung.','INDETERMINATE',None,False),
])
def test_brain_only_fdg(text,result,pattern,brain):
    value=normalize_pet('PET_CT_FDG',text)
    assert (value['fdg_pet_result'],value['fdg_pet_pattern'],value['brain_component_present'])==(result,pattern,brain)
    assert all(c['component']=='BRAIN' for c in value['components'] if c['brain_interpretation'])
    assert all(q in text for q in value['classification_evidence'])
    assert all(c['text']==text[c['start']:c['end']] for c in value['components'])


def test_whole_brain_header_can_own_result_but_composite_header_cannot():
    assert normalize_pet('PET_CT_FDG','Normaler Metabolismus.',header='FDG PET: Hirn')['fdg_pet_result']=='NORMAL'
    assert normalize_pet('PET_CT_FDG','Keine pathologische Mehrspeicherung.',header='PET-CT: Hirn, PET-CT: Stamm')['fdg_pet_result']=='INDETERMINATE'


def test_interval_cmb_persistence_and_null_acute_compatibility(memory_db):
    store,session,factory,root=memory_db;b=bundle('Neue Mikro-Hämosiderinresiduen links frontal. Vorbestehende Mikroblutungen rechts temporal.')
    with patch.object(radiology_v2,'build_patient_bundle',return_value=b),patch.object(radiology_v2,'resolve_case_id',return_value=42):
        radiology_v2.extract_patient('123',session_factory=factory,debug_root=root)
        radiology_v2.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert {r.temporal_status for r in store[RadiologyFinding]}=={'INTERVAL_NEW','CHRONIC'}
        f=next(r for r in store[RadiologyFinding] if r.temporal_status=='INTERVAL_NEW')
        assert f.interval_new is True and f.acute_lesion is None
        assert radiology_v2.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)['parent_rows_inserted']==0


def test_pet_only_excludes_unrelated_examinations_and_uses_separate_artifact(tmp_path):
    import test_examinations as old
    test=old.PipelineTests();test.setUp()
    try:
        b=bundle('Hirn: Normaler Metabolismus.\nECHOKARDIOGRAPHIE (23.08.2022)\nGute Pumpfunktion.','FDG PET/CT (23.08.2022)')
        with patch.object(examinations,'build_patient_bundle',return_value=b),patch.object(examinations,'select_examination_inputs',side_effect=AssertionError('Unrelated examination selector must not run')):
            result=examinations.extract_pet_patient('123',client=test.client,session_factory=test.factory,debug_root=tmp_path)
        assert result['selected_examinations']==1 and result['scope']=='PET_ONLY'
        assert Path(result['verified_artifact']).name.startswith('pet_examinations_')
        assert not list(Path(tmp_path).rglob('examinations_*.json'))
        assert all(r.raw_output['examination']['imaging_modality']=='PET_CT_FDG' for r in test.runs.values())
    finally:test.doCleanups()
