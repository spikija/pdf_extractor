"""Exam existence, atomic clauses, source dates, PET and immutable v2 commits."""
from copy import deepcopy
from datetime import date
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import pytest
from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.preparation.models import PatientDocumentBundle,PreparedDocument,PreparedSection
from caa_pdf_extractor.extraction.imaging_reports import discover_studies,document_text
from caa_pdf_extractor.extraction.radiology_v2_rules import extract_findings,pattern
from caa_pdf_extractor.extraction.pet_rules import normalize_pet
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.extraction.examination_validators import source_response,validate_examinations
from caa_pdf_extractor.extraction import radiology_v2 as workflow
from caa_pdf_extractor.extraction.radiology_artifacts import digest,VerifiedExtractionError
from caa_pdf_extractor.database.models import RadiologyExam,RadiologyFinding,RadiologyNegativeFinding,ExtractionRun


def bundle(body,header='MRT: Schädel (23.08.2022)',document_date=None):
    text=header+'\n'+body
    section=PreparedSection('doc1_sec01','123',1,None,1,1,text,text)
    return PatientDocumentBundle('123',[PreparedDocument(1,'synthetic.pdf','123/synthetic.pdf','synthetic','unknown',document_date,[section])])
def findings(body,header='MRT: Schädel (23.08.2022)'):
    b=bundle(body,header);ss=discover_studies(b);assert len(ss)==1
    return extract_findings(ss[0],b.documents[0],ss)


@pytest.mark.parametrize('body,types',[
 ('Alte Mikroblutungen frontal links.',{'CMB'}),
 ('Alte Mikroblutungen frontal. Oberflächliche Hämosiderose. Resorbierte Blutung rechts frontal.',{'CMB','CSS','ICH'}),
 ('Rezente punktförmige ischämische Läsion links mesiotemporal. Vorbestehende parenchymatöse Hämosiderinresiduen bihemisphärisch.',{'ISCHEMIC_LESION','CMB'}),
 ('Mikroangiopathien Fazekas II.',{'WMH'}),
 ('White matter hyperintensities.',{'WMH'}),
 ('Marklagerläsionen.',{'WMH'}),
 ('Keine rezente Ischämie. Keine intrakranielle Blutung.',set()),
 ('Kein Nachweis einer rezenten Ischämie bzw. Blutung.',set()),
 ('Kein Nachweis einer rezenten Ischämie- oder Blutung.',set()),
 ('Kein Nachweis von Mikroblutungen.',set()),
 ('Suszeptibilitätsartefakte durch Metall am Zahnimplantat.',set()),
 ('DD Microbleeds.',set()),
 ('Keine wesentliche Änderung der geringen Marklagerläsionen.',{'WMH'}),
])
def test_atomic_phenotypes_and_negation(body,types):
    assert {f['finding_type'] for f in findings(body)['findings']}==types


@pytest.mark.parametrize('text,kind,acute',[
 ('Rezente punktförmige ischämische Läsion links mesiotemporal.','ISCHEMIC_LESION',True),
 ('Abgesehen von einer rezenten punktförmigen ischämischen Läsion links mesiotemporal unveränderte Befundverhältnisse.','ISCHEMIC_LESION',True),
 ('Frischer Infarkt rechts frontal.','ISCHEMIC_LESION',True),
 ('Alte ischämische Läsion links frontal.','ISCHEMIC_LESION',False),
 ('Vorbestehende Mikro-Hämosiderinresiduen bihemisphärisch.','CMB',False),
 ('Nach Resorption einer Blutung rechts frontal Defekte und Hämosiderose.','ICH',False),
 ('Resorbierte Blutung rechts frontal.','ICH',False),
 ('Keine frische Ischämie.','ISCHEMIC_LESION',None),
])
def test_finding_specific_acuity(text,kind,acute):
    fs=[f for f in findings(text)['findings'] if f['finding_type']==kind]
    if acute is None:assert fs==[]
    else:assert fs and fs[0]['acute_lesion'] is acute


def test_new_and_preexisting_cmb_components_in_one_mri():
    result=findings('Einzelne, neue Mikro-Hämosiderinresiduen subkortikal okzipital und temporal links. '
        'Multiple parenchymatöse Mikro-Hämosiderinresiduen bihemisphärisch im Wesentlichen unverändert. Fazekas Grad II.')
    assert {f['acute_lesion'] for f in result['findings'] if f['finding_type']=='CMB'}=={None,False}
    assert {f['temporal_status'] for f in result['findings'] if f['finding_type']=='CMB'}=={'INTERVAL_NEW','CHRONIC'}
    assert next(f['attributes']['fazekas_grade'] for f in result['findings'] if f['finding_type']=='WMH')==2


def test_susceptibility_context_links_only_microhemorrhagic_findings():
    result=findings('Multiple punktförmige Hämosiderinablagerungen supratentoriell. '
                    'Mehrere neu aufgetretene Suszeptibilitätsartefakte okzipital rechts und kortikal insulär links.')
    # Explicit hemosiderin deposits are chronic; interval-new susceptibility
    # findings still carry no inferred acute biological age.
    assert {f['acute_lesion'] for f in result['findings'] if f['finding_type']=='CMB'}=={False,None}
    assert {f['temporal_status'] for f in result['findings'] if f['finding_type']=='CMB'}=={'INTERVAL_NEW','CHRONIC'}
    assert any('supporting_context' in f for f in result['findings'])


def test_unique_month_linked_context_for_2021_regression():
    b=bundle('Vorbestehende Suszeptibiltätsartefakte supra- und infratentoriell, DD post-/vaskulitisch. '
             'Kein Nachweis einer rezenten Ischämie bzw. Blutung. Mikroangiopathien Fazekas II.',
             'cMRT-Kontrolle 12/2021:\n -- keine rezente Ischämie\n -- parenchymatöse Hämosiderinresiduen bihemisphärisch (Microbleeds)\n\nMRT: Schädel (13.12.2021)')
    ss=discover_studies(b);result=extract_findings(ss[0],b.documents[0],ss)
    cmb=next(f for f in result['findings'] if f['finding_type']=='CMB')
    assert cmb['acute_lesion'] is False
    assert cmb['supporting_context']['rule']=='UNIQUE_SAME_DOCUMENT_MRI_MONTH_LINK'
    assert not any(f['finding_type']=='ICH' for f in result['findings'])


@pytest.mark.parametrize('header,body,document_date,expected,source',[
 ('MRT: Schädel (23.08.2022)','Vergleich mit 13.12.2021.',None,'2022-08-23','REPORT_EXPLICIT'),
 ('MRT: Schädel','Untersuchungsdatum: 23.08.2022\nVergleich mit 13.12.2021.',None,'2022-08-23','REPORT_EXPLICIT'),
 ('MRT: Schädel','Vergleich mit 13.12.2021.','2022-08-24','2022-08-24','DOCUMENT_DATE'),
 ('MRT: Schädel','Normaler Befund.',None,None,'UNKNOWN'),
 ('Kernspintomographie: SWI/Hämosequenz, Kernspintomographie:\n Diffusion (12.04.2023)','Vergleich mit 23.08.2022.',None,'2023-04-12','REPORT_EXPLICIT'),
])
def test_date_ownership(header,body,document_date,expected,source):
    s=discover_studies(bundle(body,header,document_date))[0]
    assert (s.exam_date,s.exam_date_source)==(expected,source)


def test_top_level_discharge_fallback_and_off_switch():
    b=bundle('Normaler Befund.','Wir berichten, die sich von 01.08.2022 bis 24.08.2022 zur stationären Behandlung befand.\nMRT: Schädel')
    assert discover_studies(b)[0].exam_date_source=='DOCUMENT_DISCHARGE_DATE'
    assert discover_studies(b)[0].exam_date=='2022-08-24'
    assert discover_studies(b,False)[0].exam_date is None


def test_many_findings_pattern_and_offsets():
    b=bundle('Rezente ischämische Läsion links frontal. Alte Mikroblutungen rechts temporal.')
    ss=discover_studies(b);a=extract_findings(ss[0],b.documents[0],ss)
    assert a['overall_pattern']=='ISCHEMIC_AND_HEMORRHAGIC_FINDINGS'
    for f in a['findings']:
        o=f['source_offsets'];assert document_text(b.documents[0])[o['start']:o['end']]==f['evidence_text']
    assert pattern([{'finding_type':'CMB'},{'finding_type':'CSS'},{'finding_type':'ICH'}])=='MULTIPLE_HEMORRHAGIC_FINDINGS'


@pytest.mark.parametrize('header,text,field,result,pattern_value',[
 ('Amyloid PET/CT (23.08.2022)','Amyloid-positiv mit pathologischer kortikaler Amyloid-Bindung.','amyloid_pet_result','POSITIVE',None),
 ('β-Amyloid PET (23.08.2022)','Keine pathologische kortikale Amyloid-Bindung.','amyloid_pet_result','NEGATIVE',None),
 ('Florbetaben','Grenzwertige kortikale Bindung.','amyloid_pet_result','INDETERMINATE',None),
 ('Flutemetamol','Technisch unzureichend, nicht beurteilbar.','amyloid_pet_result','NON_DIAGNOSTIC',None),
 ('18F-FDG PET/CT (23.08.2022)','Hypometabolismus, Alzheimer-typisches Muster.','fdg_pet_result','ABNORMAL','AD_TYPICAL'),
 ('FDG PET','Hypometabolismus temporo-mesial beidseits.','fdg_pet_result','ABNORMAL','NONSPECIFIC'),
 ('FDG PET','Hirn: Physiologisch normaler Metabolismus.','fdg_pet_result','NORMAL','NO_SPECIFIC_PATTERN'),
 ('FDG PET','Unklarer Befund.','fdg_pet_result','INDETERMINATE',None),
 ('FDG PET','Brain: Uninterpretable.','fdg_pet_result','NON_DIAGNOSTIC',None),
])
def test_pet_detection_result_and_pattern(header,text,field,result,pattern_value):
    inputs=select_examination_inputs(bundle(text,header));assert len(inputs)==1
    item=inputs[0];output=validate_examinations(source_response(item),item)
    assert len(output['records'])==1 and output['records'][0][field]==result
    assert output['records'][0]['fdg_pet_pattern']==pattern_value
    assert all(q in item.text for q in output['pet_classification_audit']['classification_evidence'])


def test_generic_abnormal_does_not_imply_alzheimer():
    assert normalize_pet('PET_CT_FDG','Abnormaler Metabolismus.')['fdg_pet_pattern'] is None
    assert normalize_pet('PET_CT_FDG','Hirn: Abnormaler Metabolismus.')['fdg_pet_pattern']=='NONSPECIFIC'
    assert normalize_pet('PET_CT_FDG','Kein Alzheimer-typisches Muster. Unklarer Befund.')['fdg_pet_pattern'] is None
    assert normalize_pet('PET_CT_FDG','Hirn:\nHypometabolismus frontal.\nNormaler bilateraler temporoparietaler und posteriorer Cingulum Metabolismus.')['fdg_pet_pattern']=='NONSPECIFIC'
    assert normalize_pet('PET_CT_AMYLOID','Kein Nachweis pathologischer kortikaler Amyloid-Bindung.')['amyloid_pet_result']=='NEGATIVE'


def test_pet_header_only_keeps_examination_indeterminate_and_ct_does_not_classify_pet():
    item=select_examination_inputs(bundle('','FDG PET'))[0]
    result=validate_examinations(source_response(item),item)
    assert len(result['records'])==1 and result['records'][0]['fdg_pet_result']=='INDETERMINATE'
    assert normalize_pet('PET_CT_FDG','Schädel-CT axial nativ:\nNicht beurteilbar.')['fdg_pet_result']=='INDETERMINATE'
    unknown=select_examination_inputs(bundle('Kein pathologischer Befund.','PET-CT: Körper (23.08.2022)'))[0]
    result=validate_examinations(source_response(unknown),unknown)
    assert result['records'][0]['fdg_pet_result'] is None and result['records'][0]['amyloid_pet_result'] is None


def test_source_procedure_identity_ignores_phenotype_and_keeps_zero_finding_exam():
    a=discover_studies(bundle('Normaler Befund.'))[0]
    b=discover_studies(bundle('Multiple Mikroblutungen.'))[0]
    assert a.procedure_id==b.procedure_id
    assert len(workflow.normalize(bundle('Normaler Befund.'),42))==1


def test_standard_radiology_cli_defaults_to_v2_and_forbids_legacy_commit(capsys):
    from caa_pdf_extractor.extraction.radiology import main
    with patch.object(workflow,'extract_patient',return_value={'status':'SUCCESS'}) as extract:
        assert main(['--patient','123','--dry-run'])==0
        extract.assert_called_once_with('123',dry_run=True)
    with pytest.raises(SystemExit):main(['--patient','123','--legacy-v1','--commit'])


def test_repeated_pet_header_fragments_merge_but_distinct_dates_do_not():
    b=bundle('Aktivität: F18-FDG\nKeine pathologische Mehrspeicherung.\n'
        'PET-CT: Hirn, PET-CT: Stamm (14.01.2020)\nStamm: F18-FDG. Keine Mehrspeicherungen.\n'
        'PET-CT: Hirn, PET-CT: Stamm (24.01.2020)\nFDG Hypometabolismus temporo-mesial.',
        'PET-CT: Hirn, PET-CT: Stamm (14.01.2020)')
    ss=discover_studies(b);assert len(ss)==2
    assert len([s for s in ss[0].source_spans if s['role'] in ('header','report')])==4
    assert [s.exam_date for s in ss]==['2020-01-14','2020-01-24']


def test_delayed_pet_tracer_is_resolved_but_distinct_tracers_are_not_merged():
    b=bundle('Siehe NUK-Befund.\nPET-CT: Hirn (14.01.2020)\nAktivität F18-FDG. Normaler Metabolismus.',
             'PET-CT: Hirn (14.01.2020)')
    ss=discover_studies(b);assert len(ss)==1 and ss[0].modality=='PET_CT_FDG'
    b=bundle('Florbetaben. Amyloid-positive Bindung.\nPET-CT: Hirn (14.01.2020)\nAktivität F18-FDG. Normaler Metabolismus.',
             'PET-CT: Hirn (14.01.2020)')
    assert {s.modality for s in discover_studies(b)}=={'PET_CT_AMYLOID','PET_CT_FDG'}


@pytest.fixture
def memory_db(tmp_path):
    store={RadiologyExam:[],RadiologyFinding:[],RadiologyNegativeFinding:[],ExtractionRun:[]};session=MagicMock()
    def add(row):
        values=store[type(row)];row.id=len(values)+1;values.append(row)
    session.add.side_effect=add
    session.get.side_effect=lambda cls,key:next((r for r in store[cls] if r.id==key),None)
    def scalar(stmt):
        p=stmt.compile().params
        return next((r for r in store[RadiologyExam] if r.case_id==p['case_id_1'] and r.source_document_id==p['source_document_id_1'] and r.procedure_id==p['procedure_id_1']),None)
    session.scalar.side_effect=scalar
    session.scalars.side_effect=lambda stmt:[r for r in store[stmt.column_descriptions[0]['entity']] if r.radiology_exam_id==stmt.compile().params['radiology_exam_id_1']]
    class Tx:
        def __enter__(self):self.before=deepcopy(store);return session
        def __exit__(self,typ,*args):
            if typ:store.clear();store.update(self.before)
            else:
                for rows in store.values():
                    for row in rows:
                        for attr in ('exam_date','overall_pattern','acute_lesion','location','laterality'):
                            if hasattr(row,attr) and isinstance(getattr(row,attr),Null):setattr(row,attr,None)
            return False
    factory=MagicMock();factory.begin.side_effect=Tx
    return store,session,factory,tmp_path


@pytest.mark.parametrize('body,count', [('Normaler Befund.',0),('Fragliche Ischämie?',0),
    ('Neue Mikro-Hämosiderinresiduen links okzipital. Vorbestehende Mikro-Hämosiderinresiduen rechts frontal.',2),
    ('Alte Mikroblutungen. Oberflächliche Hämosiderose. Resorbierte Blutung rechts frontal.',3)])
def test_artifact_commit_parent_and_children_zero_inference_idempotency(memory_db,body,count):
    store,session,factory,root=memory_db;b=bundle(body)
    with patch.object(workflow,'build_patient_bundle',return_value=b),patch.object(workflow,'resolve_case_id',return_value=42),patch('caa_pdf_extractor.llm.backend.get_client',side_effect=AssertionError('No inference')):
        dry=workflow.extract_patient('123',session_factory=factory,debug_root=root)
        assert dry['findings_detected']==count and len(store[RadiologyExam])==0
        before=Path(dry['verified_artifact']).read_bytes()
        first=workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        second=workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert first['parent_rows_inserted']==1 and first['finding_rows_inserted']==count
        assert second['parent_rows_inserted']==0 and second['idempotent_exams_skipped']==1
        assert len(store[RadiologyExam])==1 and all(f.radiology_exam_id==1 for f in store[RadiologyFinding])
        assert Path(dry['verified_artifact']).read_bytes()==before


def test_classifier_failure_keeps_verified_exam_parent(memory_db):
    store,session,factory,root=memory_db;b=bundle('Unknown findings.')
    with patch.object(workflow,'build_patient_bundle',return_value=b),patch.object(workflow,'resolve_case_id',return_value=42),patch.object(workflow,'extract_findings',side_effect=ValueError('classification failed')):
        dry=workflow.extract_patient('123',session_factory=factory,debug_root=root)
        assert dry['validation_status']=='PARTIAL_VALID'
        first=workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert first['parent_rows_inserted']==1 and first['finding_rows_inserted']==0


def test_v1_stale_forged_artifact_and_existing_conflict_block_before_writes(memory_db):
    store,session,factory,root=memory_db;b=bundle('Alte Mikroblutungen.')
    folder=root/'123'/'verified';folder.mkdir(parents=True);(folder/'radiology_old.json').write_text('{}',encoding='utf-8')
    with pytest.raises(VerifiedExtractionError,match='STALE'):workflow.load_verified('123',b,42,session,root)
    with patch.object(workflow,'build_patient_bundle',return_value=b),patch.object(workflow,'resolve_case_id',return_value=42):
        dry=workflow.extract_patient('123',session_factory=factory,debug_root=root)
        p=Path(dry['verified_artifact']);data=json.loads(p.read_text(encoding='utf-8'));original=deepcopy(data)
        data['exams'][0]['findings'][0]['acute_lesion']=True;data.pop('artifact_sha256');data['artifact_sha256']=digest(data);p.write_text(json.dumps(data),encoding='utf-8')
        with pytest.raises(VerifiedExtractionError):workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert not store[RadiologyExam]
        p.write_text(json.dumps(original),encoding='utf-8');workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        store[RadiologyExam][0].report_text='Curated different report'
        with pytest.raises(VerifiedExtractionError,match='CONFLICT'):workflow.extract_patient('123',dry_run=False,session_factory=factory,debug_root=root)
        assert store[RadiologyExam][0].report_text=='Curated different report'


def test_pet_commit_fields_and_replay_without_inference():
    import test_examinations as old
    test=old.PipelineTests();test.setUp()
    try:
        b=bundle('Hypometabolismus temporo-mesial beidseits.','FDG PET/CT (23.08.2022)')
        with patch('caa_pdf_extractor.extraction.examinations.build_patient_bundle',return_value=b):
            from caa_pdf_extractor.extraction.examinations import extract_patient
            test.client=SimpleNamespace(model_name='fake',model_version='1',runtime='fake',generate_json=MagicMock(side_effect=AssertionError('No inference')))
            assert extract_patient('123',client=test.client,session_factory=test.factory,debug_root=test.temp.name)['extracted_records']==1
            first=extract_patient('123',dry_run=False,client=test.client,session_factory=test.factory,debug_root=test.temp.name)
            second=extract_patient('123',dry_run=False,client=test.client,session_factory=test.factory,debug_root=test.temp.name)
            assert first['new_rows_inserted']==1 and second['new_rows_inserted']==0
            assert test.rows[0].fdg_pet_result=='ABNORMAL' and test.rows[0].fdg_pet_pattern=='NONSPECIFIC'
            test.client.generate_json.assert_not_called()
    finally:test.doCleanups()
