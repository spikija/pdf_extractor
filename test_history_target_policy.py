"""Procedure-only exclusion, source projection and independent commit guards."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import pytest
from caa_pdf_extractor.extraction import clinical_history,document_artifacts
from caa_pdf_extractor.extraction.history_target_policy import VERSION,classify_target,require_target_eligibility
from caa_pdf_extractor.extraction.diagnosis_items import validate_recall_responses,diagnosis_items,deterministic_diagnosis
from caa_pdf_extractor.extraction.models import ClinicalHistoryItem
from caa_pdf_extractor.extraction.radiology_artifacts import digest,VerifiedExtractionError
from test_source_evidence import source,records
from test_batch_regressions import AuditHarness


@pytest.mark.parametrize('text,category',[
    ('Appendektomie','SURGERY_ONLY'),('Appendektomie 1985','SURGERY_ONLY'),
    ('Tonsillektomie','SURGERY_ONLY'),('Hernienoperation 1992','SURGERY_ONLY'),
    ('Hernien-Operation rechts inguinal vor ca. 30 Jahren','SURGERY_ONLY'),
    ('Cholezystektomie','SURGERY_ONLY'),('Gastroskopie','PROCEDURE_ONLY'),
    ('Koloskopie','PROCEDURE_ONLY'),('PEG-Anlage','PROCEDURE_ONLY'),
    ('Portimplantation','PROCEDURE_ONLY'),('Referenztext','OTHER'),
])
def test_non_target_source_is_retained_in_audit_only(text,category):
    _,s=source('1. '+text)
    result,rejected,meta=validate_recall_responses(json.dumps(records([s])) ,[],'123',[s])
    assert not rejected and not result.clinical_history
    assert meta[0]['record']['source_text']==text
    assert meta[0]['record']['date_disease'] is None
    assert meta[0]['target_classification']==category and meta[0]['audit_only'] is True
    assert meta[0]['target_policy_version']==VERSION
    assert deterministic_diagnosis(diagnosis_items([s])[0]) is None


@pytest.mark.parametrize('text',[
    'Rippenfraktur links','intracerebral hemorrhage','arterial hypertension',
    'Arterielle Hypertonie','Zerebrale Amyloidangiopathie',
    'Intracerebrales Hämatom mit osteoplastischer Craniotomie und Hämatomentleerung',
    'Z. n. mechanischem AK-Ersatz bei hochgradiger AK-Stenose',
])
def test_explicit_disease_or_major_event_remains_eligible(text):
    _,s=source('1. '+text)
    result,rejected,meta=validate_recall_responses(json.dumps(records([s])),[],'123',[s])
    assert not rejected and len(result.clinical_history)==1
    row=result.clinical_history[0].model_dump(mode='json')
    require_target_eligibility([row],meta)
    assert row['source_text']==text and meta[0]['persistence_eligible'] is True
    assert row['date_disease'] is None


@pytest.mark.parametrize('text,expected',[
    ('Appendizitis, Appendektomie 1985','Appendizitis'),
    ('Appendizitis, Appendektomie 05.04.2019','Appendizitis'),
    ('Gastroskopie 05.04.2019: chronische Gastritis','chronische Gastritis'),
])
def test_explicit_disease_survives_with_procedure_only_in_provenance(text,expected):
    _,s=source('1. '+text)
    result,rejected,meta=validate_recall_responses(json.dumps(records([s])),[],'123',[s])
    assert not rejected and result.clinical_history[0].disease==expected
    assert result.clinical_history[0].source_text==text
    assert result.clinical_history[0].date_disease is None
    assert meta[0]['procedure_text'] in text
    require_target_eligibility([result.clinical_history[0].model_dump(mode='json')],meta)


def test_event_date_is_not_borrowed_by_associated_caa():
    _,s=source('1. ICB am 11.05.2025 bei Amyloidangiopathie')
    p=records([s],['ICB'])
    p['clinical_history'].append({**p['clinical_history'][0],'disease':'Amyloidangiopathie'})
    result,rejected,meta=validate_recall_responses(json.dumps(p),[],'123',[s])
    assert not rejected
    values={r.disease:r.model_dump(mode='json')['date_disease'] for r in result.clinical_history}
    assert values=={'ICB':'2025-05-11','Amyloidangiopathie':None}


def workflow(tmp_path,text='Hypertonie'):
    b,s=source('1. '+text);h=AuditHarness()
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(records([s]))))
    def run(dry=True):
        with patch.object(clinical_history,'build_patient_bundle',return_value=b):
            return clinical_history.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    return run,client,h,b


def test_old_artifact_without_target_version_is_stale_and_commit_never_infers(tmp_path):
    run,client,h,_=workflow(tmp_path)
    out=run();p=Path(out['verified_artifact']);a=json.loads(p.read_text(encoding='utf-8'))
    assert a['target_policy_version']==VERSION
    a.pop('target_policy_version');a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    p.write_text(json.dumps(a),encoding='utf-8')
    client.generate_json.side_effect=AssertionError('No inference')
    assert run(False)['status']=='VERIFIED_EXTRACTION_STALE'
    assert not h.rows and client.generate_json.call_count==1


def test_surgery_artifact_is_blocked_even_if_rehashed_and_marked_disease(tmp_path):
    run,client,h,_=workflow(tmp_path)
    out=run();p=Path(out['verified_artifact']);a=json.loads(p.read_text(encoding='utf-8'))
    for key in ('accepted_records','normalized_output'):
        a[key][0]['disease']='Appendektomie'
    a['evidence_metadata'][0]['record']['disease']='Appendektomie'
    a['evidence_metadata'][0]['target_classification']='DISEASE'
    a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    p.write_text(json.dumps(a),encoding='utf-8')
    client.generate_json.side_effect=AssertionError('No inference')
    assert run(False)['status']=='CLINICAL_HISTORY_TARGET_NOT_ELIGIBLE'
    assert not h.rows and client.generate_json.call_count==1


def test_commit_planner_rechecks_targets_even_if_loader_is_bypassed(tmp_path):
    run,client,h,b=workflow(tmp_path)
    value=ClinicalHistoryItem(disease='Tonsillektomie',date_disease=None,disease_category=None,
        source_document_id=1,page_number=1,section_id=b.documents[0].sections[0].section_id,source_text='Tonsillektomie')
    fake={'target_policy_version':VERSION,'evidence_metadata':[{'record':value.model_dump(mode='json'),
        'target_policy_version':VERSION,'target_classification':'DISEASE','persistence_eligible':True}]}
    client.generate_json.side_effect=AssertionError('No inference')
    with patch.object(document_artifacts,'load',return_value=(fake,Path('unused'),[value])):
        assert run(False)['status']=='CLINICAL_HISTORY_TARGET_NOT_ELIGIBLE'
    assert not h.rows and client.generate_json.call_count==0


def test_pure_surgery_dry_run_is_verified_empty_and_audit_is_immutable_input(tmp_path):
    run,client,h,_=workflow(tmp_path,'Appendektomie, Tonsillektomie')
    out=run();assert out['status']=='SUCCESS' and out['artifact_validation_status']=='VERIFIED_EMPTY'
    a=json.loads(Path(out['verified_artifact']).read_text(encoding='utf-8'))
    assert not a['accepted_records'] and len(a['audit_only_records'])==1
    client.generate_json.side_effect=AssertionError('No commit inference')
    assert run(False)['inserted']==0
    assert not h.rows and client.generate_json.call_count==1
