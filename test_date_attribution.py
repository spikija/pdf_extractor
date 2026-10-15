"""Disease-specific date ownership and inference-free persistence barriers."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from caa_pdf_extractor.extraction import clinical_history
from caa_pdf_extractor.extraction.date_attribution import attribute, require_date_ownership
from caa_pdf_extractor.extraction.diagnosis_items import validate_independent
from caa_pdf_extractor.extraction.radiology_artifacts import digest, VerifiedExtractionError
from test_source_evidence import source, records
from test_batch_regressions import AuditHarness
import test_radiology as radio

PARENT = ('Subarachnoidalblutung rechts (Sulcus frontalis superior, Hippocampus) am 29.03.2018\n'
          ' - Subarachnoidalblutung rechts frontal am 20.02.2018\n'
          ' - ätiologisch bei cerebraler Amyloidangiopathie\n'
          ' - Hämorrhagischer Infarkt im Stromgebiet der Arteria cerebri media links,\n'
          '   - ätiologisch nicht klassifiziert (2005)')


@pytest.mark.parametrize('text,disease,expected,reason',[
    ('ICB frontal rechts am 11.05.2025 bei Amyloidangiopathie','Amyloidangiopathie',None,'ASSOCIATED_DISEASE_NO_DATE'),
    ('ICB frontal rechts am 11.05.2025 bei Amyloidangiopathie','ICB frontal rechts','2025-05-11',None),
    (PARENT,'Subarachnoidalblutung rechts frontal','2018-02-20',None),
    (PARENT,'Hämorrhagischer Infarkt im Stromgebiet der Arteria cerebri media links',None,'PARTIAL_DATE_NOT_PERSISTABLE'),
    ('Gastroskopie 05.04.2019:\n - Motilitätsstörung im oGIT/Gastroparese\n - V. a. chronische Gastritis','chronische Gastritis',None,'PROCEDURE_DATE_NOT_DISEASE_DATE'),
    ('Gastroskopie 05.04.2019:\n - Gastroparese','Gastroparese',None,'PROCEDURE_DATE_NOT_DISEASE_DATE'),
    ('Chronische Gastritis diagnostiziert am 05.04.2019','Chronische Gastritis','2019-04-05',None),
    ('Blutung am 29.03.2018\n - Infarkt','Infarkt',None,'PARENT_DATE_NOT_INHERITED'),
    ('Blutung am 29.03.2018\n - Infarkt 2005','Infarkt',None,'PARTIAL_DATE_NOT_PERSISTABLE'),
    ('Infarkt 07/2017','Infarkt',None,'PARTIAL_DATE_NOT_PERSISTABLE'),
    ('Demenz MMSE 25/30 08/17','Demenz',None,'PROCEDURE_DATE_NOT_DISEASE_DATE'),
    ('Blutung am 11.05.2025 im Rahmen einer Amyloidangiopathie','Amyloidangiopathie',None,'ASSOCIATED_DISEASE_NO_DATE'),
    ('Blutung am 11.05.2025 bei arterieller Hypertonie','arterieller Hypertonie',None,'ASSOCIATED_DISEASE_NO_DATE'),
    ('Infarkt am 01.02.2020\n - Infarkt am 02.03.2021','Infarkt',None,'DATE_NOT_OWNED_BY_DISEASE'),
    ('Hypertonie. Infarkt am 01.02.2020','Hypertonie',None,'DATE_NOT_OWNED_BY_DISEASE'),
    ('Hypertonie; Infarkt am 01.02.2020','Hypertonie',None,'DATE_NOT_OWNED_BY_DISEASE'),
    ('Hypertonie Infarkt am 01.02.2020','Hypertonie',None,'DATE_NOT_OWNED_BY_DISEASE'),
])
def test_event_ownership(text,disease,expected,reason):
    day,meta=attribute(text,disease,offset=12,item_id='item')
    assert day==expected and meta['reason']==reason
    assert (meta['status']=='VERIFIED_DIRECT') == (day is not None)
    for clause in meta['clauses']:
        assert text[clause['start']-12:clause['end']-12]==clause['source_text']
    if '2005' in text and expected is None: assert '2005' in meta['partial_date_text']


@pytest.mark.parametrize('procedure',['MRT','MRI','CT','Operation','Biopsie','Endoskopie','Therapie','Aufnahme','Entlassung','Eradikation','operat','Kraniotomie','Subtraktionsangiographie','AK-Ersatz'])
def test_procedure_parent_never_dates_findings(procedure):
    day,meta=attribute(procedure+' 01.02.2020:\n - Gastritis','Gastritis')
    assert day is None and meta['reason']=='PROCEDURE_DATE_NOT_DISEASE_DATE'


def test_partial_diagnosis_date_does_not_authorize_later_surgery_date():
    day,meta=attribute('Karzinom ED 2005, Resektion am 07.04.2005','Karzinom')
    assert day is None and meta['reason']=='PROCEDURE_DATE_NOT_DISEASE_DATE'


def test_narrative_model_date_is_discarded_and_whitespace_reanchored():
    b,s=source('Hypertonie\n   bei Amyloidangiopathie')
    p={'patient_id':'123','clinical_history':[dict(disease='Amyloidangiopathie',date_disease='2025-05-11',disease_category=None,
        source_document_id=s.source_document_id,page_number=s.page_start,section_id=s.section_id,
        source_text='Hypertonie bei Amyloidangiopathie')]}
    result,rejected,meta=validate_independent(json.dumps(p),'123',[s])
    assert not rejected and result.clinical_history[0].date_disease is None
    assert result.clinical_history[0].source_text in s.text
    assert meta[0]['date_validation']['reason']=='ASSOCIATED_DISEASE_NO_DATE'


def test_all_four_retained_date_regressions_and_exact_offsets():
    for text,diseases,expected in [
        (PARENT,['Subarachnoidalblutung rechts frontal','Hämorrhagischer Infarkt im Stromgebiet der Arteria cerebri media links'],[date(2018,2,20),None]),
        ('ICB frontal rechts am 11.05.2025 bei Amyloidangiopathie',['ICB frontal rechts','Amyloidangiopathie'],[date(2025,5,11),None]),
        ('Gastroskopie 05.04.2019:\n - V. a. chronische Gastritis',['chronische Gastritis'],[None])]:
        _,s=source('1. '+text);p=records([s],[diseases[0]])
        p['clinical_history']=[dict(p['clinical_history'][0],disease=d,date_disease='2099-01-01') for d in diseases]
        result,rejected,meta=validate_independent(json.dumps(p),'123',[s])
        assert not rejected and [r.date_disease for r in result.clinical_history]==expected
        for m in meta:
            span=m['date_validation']['disease_span']
            assert s.text[span['start']:span['end']]==span['text']
        require_date_ownership([r.model_dump(mode='json') for r in result.clinical_history],meta)


@pytest.mark.parametrize('tamper',['missing','status','stale','unsafe'])
def test_artifact_date_barrier_zero_inference_and_no_writes(tmp_path,tamper):
    b,s=source('1. ICB frontal rechts am 11.05.2025 bei Amyloidangiopathie')
    h=AuditHarness();p=records([s],['Amyloidangiopathie'])
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(p)))
    def run(dry):
        with patch.object(clinical_history,'build_patient_bundle',return_value=b):
            return clinical_history.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    dry=run(True);path=Path(dry['verified_artifact']);a=json.loads(path.read_text(encoding='utf-8'))
    if tamper=='missing': a['evidence_metadata']=[]
    elif tamper=='status':
        a['accepted_records'][0]['date_disease']='2025-05-11'
    elif tamper=='stale':a['prompt_version']='clinical_history_v2_source_items'
    else:
        a['accepted_records'][0]['date_disease']='2025-05-11'
        a['evidence_metadata'][0]['record']=deepcopy(a['accepted_records'][0])
        a['evidence_metadata'][0]['date_validation'].update(status='VERIFIED_DIRECT',relation='DIRECT')
    a.pop('artifact_sha256');a['artifact_sha256']=digest(a);path.write_text(json.dumps(a),encoding='utf-8')
    client.generate_json.side_effect=AssertionError('No commit inference')
    result=run(False)
    assert result['status'] in ('DATE_ATTRIBUTION_REQUIRED','DATE_NOT_OWNED_BY_DISEASE','VERIFIED_EXTRACTION_STALE','VERIFIED_EXTRACTION_INVALID')
    assert not h.rows and client.generate_json.call_count==1


def test_direct_date_commit_is_idempotent_and_inference_free(tmp_path):
    b,s=source('1. ICB frontal rechts am 11.05.2025')
    h=AuditHarness();client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(records([s],['ICB frontal rechts']))))
    with patch.object(clinical_history,'build_patient_bundle',return_value=b):
        kwargs=dict(client=client,session_factory=h.factory,debug_root=tmp_path)
        clinical_history.extract_patient('123',**kwargs)
        client.generate_json.side_effect=AssertionError('No commit inference')
        assert clinical_history.extract_patient('123',dry_run=False,**kwargs)['inserted']==1
        assert clinical_history.extract_patient('123',dry_run=False,**kwargs)['inserted']==0
    assert h.rows[0].date_disease==date(2025,5,11) and client.generate_json.call_count==1


def test_distinct_null_date_mri_procedures_block_before_any_writes():
    h=radio.PipelineTests();h.setUp()
    try:
        procedures=[radio.procedure(procedure_id='doc1_proc01',date=None,result_text='Multiple Microbleeds.'),
                    radio.procedure(procedure_id='doc1_proc02',date=None,result_text='Multiple Microbleeds.')]
        responses=[]
        for p in procedures:
            raw=radio.empty_payload(p);raw['findings']=[dict(type='cmb',acute_lesion=False,evidence=p.result_text)]
            responses.append(json.dumps(raw))
        h.extract(procedures,responses)
        result=h.commit_saved(procedures)
        assert result['status']=='DUPLICATE_IDENTITY_AMBIGUOUS' and not h.rows and not result['granite_called']
    finally:h.doCleanups()
