"""Source-identified radiology exams with atomic children and inference-free commits.

Canonical v2 workflow: python -m caa_pdf_extractor.extraction.radiology_v2 PATIENT
--commit is available for a separately authorized, reviewed artifact; this task
uses dry-run only. Legacy radiology remains an independent reference table.
"""
import argparse
from datetime import datetime, timezone, date
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, text, null
from ..database import SessionLocal
from ..database.models import RadiologyExam, RadiologyFinding, RadiologyNegativeFinding, ExtractionRun
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id
from .radiology_artifacts import artifact_directory, digest, VerifiedExtractionError
from .imaging_reports import discover_studies, document_text, VERSION as DISCOVERY_VERSION
from .radiology_v2_rules import extract_findings, pattern, VERSION as RULE_VERSION
from .radiology_assertions import study_scope,study_performed

ARTIFACT_VERSION=2
EXTRACTION_TYPE='radiology_v2'
POLICY_VERSION='source_exam_concept_ownership_v4'

def source_fingerprint(bundle,allow_document_fallback):
    return digest({'bundle':bundle.to_dict(),'extraction_type':EXTRACTION_TYPE,
        'policy_version':POLICY_VERSION,'rule_version':RULE_VERSION,'discovery_version':DISCOVERY_VERSION,
        'allow_document_fallback':allow_document_fallback,
        'implementation':{p:Path(__file__).with_name(p).read_text(encoding='utf-8')
                          for p in ('imaging_reports.py','radiology_v2_rules.py','radiology_assertions.py','ct_rules.py','clinical_schema.py','radiology_v2.py','imaging_schema.py')}})

def normalize(bundle,case_id,allow_document_fallback=True,*,modalities=None):
    studies=discover_studies(bundle,allow_document_fallback);docs={d.source_document_id:d for d in bundle.documents};exams=[]
    for study in studies:
        if study.kind=='PET' or study.modality not in ('MRI','CT','CTA','MRA'):continue
        if modalities is not None and study.modality not in modalities:continue
        from .ct_rules import extract_ct,ct_modality,ct_scope,complete_ct_header
        complete_ct_header(study,docs[study.source_document_id],allow_document_fallback)
        study.modality=ct_modality(study)
        # Source/procedure identity is established before classification. A
        # normal, uncertain, unavailable or failed finding result keeps the exam.
        doc=docs[study.source_document_id];source=document_text(doc)
        assert all(source[s['start']:s['end']]==s['text'] for s in study.source_spans)
        scope=ct_scope(study) if study.modality in ('CT','CTA') else study_scope(study)
        performed=study_performed(study)
        try:
            result=(extract_ct(study,doc,studies) if study.modality in ('CT','CTA') else extract_findings(study,doc,studies)) if scope=='BRAIN' and performed else {
                'findings':[],'negative_findings':[],'uncertain_findings':[],'other_assertions':[],
                'status':'VERIFIED_EMPTY','rejected_candidates':[{'reason':'NON_BRAIN_SCOPE' if performed else 'NOT_PERFORMED','evidence_text':study.report_text}],
                'rule_version':RULE_VERSION}
        except Exception as exc:
            result={'findings':[],'overall_pattern':None,'status':'PARTIAL_VALID',
                    'classification_error':type(exc).__name__,'rejected_candidates':[],'negative_findings':[],'uncertain_findings':[],'other_assertions':[],'rule_version':RULE_VERSION}
        exams.append({'exam':{'case_id':case_id,'source_document_id':study.source_document_id,
            'procedure_id':study.procedure_id,'modality':study.modality,'anatomic_scope':scope,'performed':performed,'exam_date':study.exam_date,
            'exam_date_source':study.exam_date_source,'report_text':study.report_text},
            **result,'source_context':study.context(),'overall_pattern':pattern(result['findings'])})
    return exams

def _expected_audit(patient_id,case_id,fp,exams,allow_fallback):
    return {'patient_id':patient_id,'resolved_case_id':case_id,'dry_run':True,'extraction_type':EXTRACTION_TYPE,
        'inference_performed':False,'artifact_version':ARTIFACT_VERSION,'policy_version':POLICY_VERSION,
        'source_fingerprint':fp,'allow_document_fallback':allow_fallback,'exams':exams}

def load_verified(patient_id,bundle,case_id,session,root):
    folder=artifact_directory(patient_id,root);files=sorted(folder.glob('radiology_v2_*.json'))
    if not files:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE' if list(folder.glob('radiology_*.json')) else 'NO_VERIFIED_EXTRACTION')
    path=files[-1]
    try:
        a=json.loads(path.read_text(encoding='utf-8'));checksum=a.pop('artifact_sha256')
        if digest(a)!=checksum:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        a['artifact_sha256']=checksum
        if a['artifact_version']!=2 or a['extraction_type']!=EXTRACTION_TYPE:raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        if (a['patient_id']!=patient_id or a['resolved_case_id']!=case_id or datetime.fromisoformat(a['created_at']).tzinfo is None
                or a['validation_status'] not in ('VERIFIED','VERIFIED_EMPTY','PARTIAL_VALID')):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if a['policy_version']!=POLICY_VERSION or a['rule_version']!=RULE_VERSION:raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        fallback=a['allow_document_fallback'];fp=source_fingerprint(bundle,fallback)
        if a['source_fingerprint']!=fp:raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        exams=normalize(bundle,case_id,fallback)
        if a['exams']!=exams:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        run=session.get(ExtractionRun,a['source_run_id'])
        if (run is None or run.status!='SUCCESS' or run.model_name!='deterministic-radiology-v2'
                or run.model_version!=RULE_VERSION or run.prompt_version!=EXTRACTION_TYPE
                or run.source_document_id!=bundle.documents[0].source_document_id
                or run.raw_output!=_expected_audit(patient_id,case_id,fp,exams,fallback)):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        return a,path
    except VerifiedExtractionError:raise
    except (KeyError,TypeError,ValueError,OSError,AttributeError):raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None

def _db_date(value):return date.fromisoformat(value) if value else None
def exam_values(e):
    return {**e['exam'],'exam_date':_db_date(e['exam']['exam_date']),'overall_pattern':e['overall_pattern'],'deleted':False}
FINDING_COLUMNS=('finding_type','acute_lesion','temporal_status','interval_new','location','laterality','evidence_text')
OPTIONAL_COLUMNS=('lobar','deep','infratentorial','cortical','subcortical','distribution')
def finding_values(f):
    return {k:f[k] for k in FINDING_COLUMNS}|{'deleted':False,'fazekas_grade':f.get('attributes',{}).get('fazekas_grade')}|{k:None for k in OPTIONAL_COLUMNS}

NEGATIVE_COLUMNS=('finding_type','location','evidence_text')
def negative_values(f):return {k:f[k] for k in NEGATIVE_COLUMNS}|{'deleted':False}

def plan_commit(a,session):
    plan=[]
    for event in a['exams']:
        values=exam_values(event)
        row=session.scalar(select(RadiologyExam).where(RadiologyExam.case_id==values['case_id'],
            RadiologyExam.source_document_id==values['source_document_id'],RadiologyExam.procedure_id==values['procedure_id']).with_for_update())
        if row is not None:
            if any(getattr(row,k)!=v for k,v in values.items()):raise VerifiedExtractionError('CONFLICT_EXISTING_EXAM')
            children=list(session.scalars(select(RadiologyFinding).where(RadiologyFinding.radiology_exam_id==row.id)))
            expected=sorted(json.dumps(finding_values(f),sort_keys=True) for f in event['findings'])
            actual=sorted(json.dumps({k:getattr(c,k) for k in (*FINDING_COLUMNS,*OPTIONAL_COLUMNS,'fazekas_grade','deleted')},sort_keys=True) for c in children)
            if actual!=expected:raise VerifiedExtractionError('CONFLICT_EXISTING_FINDINGS')
            negatives=list(session.scalars(select(RadiologyNegativeFinding).where(RadiologyNegativeFinding.radiology_exam_id==row.id)))
            expected=sorted(json.dumps(negative_values(f),sort_keys=True) for f in event['negative_findings'])
            actual=sorted(json.dumps({k:getattr(c,k) for k in (*NEGATIVE_COLUMNS,'deleted')},sort_keys=True) for c in negatives)
            if actual!=expected:raise VerifiedExtractionError('CONFLICT_EXISTING_NEGATIVE_FINDINGS')
        plan.append((event,row))
    return plan

def commit_verified(patient_id,bundle,factory,root):
    with factory.begin() as session:
        case_id=resolve_case_id(patient_id,session=session)
        session.execute(text('SELECT pg_advisory_xact_lock(:id)'),{'id':case_id})
        artifact,path=load_verified(patient_id,bundle,case_id,session,root)
        plan=plan_commit(artifact,session) # All conflicts checked before any insert.
        parents=children=negative_children=skipped=0;links=[]
        for e,row in plan:
            if row is not None:skipped+=1;links.append({'exam_id':row.id,'procedure_id':e['exam']['procedure_id'],'operation':'EXACT_IDEMPOTENT_EXISTING'});continue
            values=exam_values(e);values['exam_date']=values['exam_date'] or null()
            values['overall_pattern']=values['overall_pattern'] or null()
            row=RadiologyExam(**values);session.add(row);session.flush();parents+=1
            ids=[]
            for f in e['findings']:
                fv=finding_values(f)
                for name in ('acute_lesion','location','laterality'):
                    if fv[name] is None:fv[name]=null()
                child=RadiologyFinding(radiology_exam_id=row.id,**fv);session.add(child);session.flush();ids.append(child.id);children+=1
            negative_ids=[]
            for f in e['negative_findings']:
                child=RadiologyNegativeFinding(radiology_exam_id=row.id,**negative_values(f));session.add(child);session.flush()
                negative_ids.append(child.id);negative_children+=1
            links.append({'exam_id':row.id,'finding_ids':ids,'negative_finding_ids':negative_ids,'procedure_id':e['exam']['procedure_id'],'operation':'INSERT_NEW'})
        session.add(ExtractionRun(source_document_id=bundle.documents[0].source_document_id,model_name='deterministic-radiology-v2',
            model_version=RULE_VERSION,prompt_version=EXTRACTION_TYPE,status='SUCCESS',completed_at=datetime.now(timezone.utc),
            raw_output={'patient_id':patient_id,'resolved_case_id':case_id,'dry_run':False,'extraction_type':EXTRACTION_TYPE,
                'operation':'COMMIT_VERIFIED_EXTRACTION','inference_performed':False,'artifact_sha256':artifact['artifact_sha256'],
                'source_run_id':artifact['source_run_id'],'exam_links':links,'parent_rows_inserted':parents,'finding_rows_inserted':children,'negative_rows_inserted':negative_children}))
    return {'status':'SUCCESS','verified_artifact_used':True,'verified_artifact':str(path),'granite_called':False,
            'parent_rows_inserted':parents,'finding_rows_inserted':children,'negative_rows_inserted':negative_children,'idempotent_exams_skipped':skipped,'exam_links':links}

def extract_patient(patient_id,*,dry_run=True,session_factory=None,debug_root=None,allow_document_fallback=True):
    artifact_directory(patient_id,debug_root);factory=session_factory or SessionLocal
    bundle=build_patient_bundle(patient_id,factory)
    if not dry_run:return commit_verified(patient_id,bundle,factory,debug_root)
    if not bundle.documents:raise VerifiedExtractionError('NO_SOURCE_DOCUMENTS')
    with factory.begin() as session:case_id=resolve_case_id(patient_id,session=session)
    exams=normalize(bundle,case_id,allow_document_fallback);fp=source_fingerprint(bundle,allow_document_fallback)
    with factory.begin() as session:
        if resolve_case_id(patient_id,session=session)!=case_id:raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        run=ExtractionRun(source_document_id=bundle.documents[0].source_document_id,model_name='deterministic-radiology-v2',
            model_version=RULE_VERSION,prompt_version=EXTRACTION_TYPE,status='SUCCESS',completed_at=datetime.now(timezone.utc),
            raw_output=_expected_audit(patient_id,case_id,fp,exams,allow_document_fallback))
        session.add(run);session.flush();run_id=run.id
    created=datetime.now(timezone.utc)
    status='PARTIAL_VALID' if any(e['status']=='PARTIAL_VALID' for e in exams) else 'VERIFIED' if exams else 'VERIFIED_EMPTY'
    a={'artifact_version':2,'extraction_type':EXTRACTION_TYPE,'patient_id':patient_id,'resolved_case_id':case_id,
       'created_at':created.isoformat(),'validation_status':status,'policy_version':POLICY_VERSION,'rule_version':RULE_VERSION,
       'source_run_id':run_id,'source_fingerprint':fp,'allow_document_fallback':allow_document_fallback,'exams':exams}
    a['artifact_sha256']=digest(a);folder=artifact_directory(patient_id,debug_root);folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'radiology_v2_{created.strftime("%Y%m%dT%H%M%S%fZ")}_{uuid4().hex}.json'
    with path.open('x',encoding='utf-8') as stream:stream.write(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
    return {'status':'SUCCESS','validation_status':status,'patient_id':patient_id,'case_id':case_id,'dry_run':True,
        'granite_called':False,'verified_artifact':str(path),'exams_detected':len(exams),
        'findings_detected':sum(len(e['findings']) for e in exams),'parent_rows_inserted':0,'finding_rows_inserted':0}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('patient_id');p.add_argument('--commit',action='store_true')
    p.add_argument('--no-document-fallback',action='store_true');p.add_argument('--debug-root',type=Path)
    args=p.parse_args(argv)
    print(json.dumps(extract_patient(args.patient_id,dry_run=not args.commit,debug_root=args.debug_root,
        allow_document_fallback=not args.no_document_fallback),ensure_ascii=False))

if __name__=='__main__':main()
