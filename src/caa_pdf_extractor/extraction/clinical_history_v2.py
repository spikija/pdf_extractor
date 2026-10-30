"""Dry-run history_v2 artifacts. Commit planning is read-only and performs no inference."""
import argparse,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from . import history_v2
from .radiology_artifacts import digest,VerifiedExtractionError

def create_artifact(bundle,case_id,legacy=None):
    if legacy:
        checked=dict(legacy);expected=checked.pop('artifact_sha256')
        if digest(checked)!=expected:raise VerifiedExtractionError('LEGACY_ARTIFACT_CHECKSUM_INVALID')
        if legacy['patient_id']!=bundle.patient_id:raise VerifiedExtractionError('PATIENT_MISMATCH')
    result=history_v2.extract_bundle(bundle,(legacy or {}).get('accepted_records',[]))
    value={'artifact_version':2,'extraction_type':'history_v2','patient_id':bundle.patient_id,'case_id':case_id,'created_at':datetime.now(timezone.utc).isoformat(),'validation_status':'VERIFIED_SOURCE','source_fingerprint':history_v2.source_fingerprint(bundle),'legacy_artifact_sha256':(legacy or {}).get('artifact_sha256'),'legacy_source_run_ids':(legacy or {}).get('source_run_ids',[]),'output':result,'clinical_commit_authorized':False}
    value['artifact_sha256']=digest(value);return value

def verify_artifact(value,bundle):
    checked=dict(value);checksum=checked.pop('artifact_sha256')
    if digest(checked)!=checksum:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    if value.get('artifact_version')!=2 or value.get('extraction_type')!='history_v2' or value['patient_id']!=bundle.patient_id or value['source_fingerprint']!=history_v2.source_fingerprint(bundle):raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    for mention in value['output']['mentions']:
        from .diagnosis_items import normalized_label
        section=next((s for d in bundle.documents for s in d.sections if d.source_document_id==mention['source_document_id'] and s.section_id==mention['section_id']),None)
        if not section or ' '.join(mention['evidence_text'].split()) not in ' '.join(section.source_text.split()):raise VerifiedExtractionError('HISTORY_EVIDENCE_UNSUPPORTED')
        if normalized_label(mention['disease']) not in normalized_label(mention['evidence_text']):raise VerifiedExtractionError('HISTORY_LABEL_UNSUPPORTED')
        own=mention.get('date_source_text',mention['evidence_text'])
        if ' '.join(own.split()) not in ' '.join(mention['evidence_text'].split()):raise VerifiedExtractionError('DATE_SOURCE_NOT_CONTIGUOUS')
        audit=mention['date_audit'];admission=audit.get('admission_provenance')
        if admission:
            sec=next((s for d in bundle.documents for s in d.sections if d.source_document_id==mention['source_document_id'] and s.section_id==admission['section_id']),None)
            if not sec or admission['evidence_text'] not in sec.source_text:raise VerifiedExtractionError('ADMISSION_SOURCE_UNSUPPORTED')
            matches=history_v2.FULL.findall(admission['evidence_text'])
            from datetime import date
            raw=matches[0];day=date.fromisoformat(raw) if '-' in raw else date(*map(int,raw.split('.')[::-1]))
            if day.isoformat()!=admission['date']:raise VerifiedExtractionError('ADMISSION_DATE_UNSUPPORTED')
        temporal,_=history_v2.temporal(own,mention['disease'],admission=admission['date'] if admission else None,current_evidence=mention['evidence_text'] if mention['evidence_role']!='PAST_HISTORY' else None)
        if temporal!=mention['temporal']:raise VerifiedExtractionError('HISTORY_DATE_NOT_OWNED')
    # Rebuild only the canonical evidence grouping from source-validated mentions.
    canonical,holds=history_v2.canonicalize(value['output']['mentions'])
    if canonical!=value['output']['records'] or holds!=value['output']['identity_holds']:raise VerifiedExtractionError('CANONICAL_HISTORY_INVALID')
    for row in value['output']['records']:
        history_v2.validate_temporal(row)
        label_key=history_v2.identity(row['disease'])
        if not history_v2.eligible(row['disease']) or (label_key!=row['canonical_key'] and not (label_key=='CHRONIC:CKD:UNSPECIFIED' and row['canonical_key'].startswith('CHRONIC:CKD:'))):raise VerifiedExtractionError('HISTORY_TARGET_INVALID')
        supports=[]
        for e in row['evidence']:
            section=next((s for d in bundle.documents for s in d.sections if d.source_document_id==e['source_document_id'] and s.section_id==e['section_id']),None)
            if not section or ' '.join(e['evidence_text'].split()) not in ' '.join(section.source_text.split()):raise VerifiedExtractionError('HISTORY_EVIDENCE_UNSUPPORTED')
            supports.append(e['date_assertion'])
        if not supports or not any(all(row[k]==s[k] for k in history_v2.unknown()) for s in supports):raise VerifiedExtractionError('HISTORY_DATE_UNSUPPORTED')
    return True

def extract_patient(patient_id,*,case_id,legacy_artifact=None,debug_root='data/debug/history_v2'):
    from ..preparation import build_patient_bundle
    from ..database import get_engine
    from sqlalchemy import text
    with get_engine().connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        ids=connection.execute(text('SELECT id FROM public."case" WHERE fallnr=:pid AND deleted IS NOT TRUE'),{'pid':patient_id}).scalars().all()
        if ids!=[case_id]:raise VerifiedExtractionError('PATIENT_CASE_IDENTITY_MISMATCH')
    bundle=build_patient_bundle(patient_id)
    legacy=json.loads(Path(legacy_artifact).read_text(encoding='utf8')) if legacy_artifact else None
    artifact=create_artifact(bundle,case_id,legacy);verify_artifact(artifact,bundle)
    root=Path(debug_root)/patient_id;root.mkdir(parents=True,exist_ok=True)
    path=root/('history_v2_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    with path.open('x',encoding='utf8') as stream:json.dump(artifact,stream,ensure_ascii=False,indent=2)
    return {'patient_id':patient_id,'case_id':case_id,'artifact':str(path),'counts':artifact['output']['counts'],'Granite_calls':0,'clinical_writes':0}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--patient',required=True);p.add_argument('--case-id',required=True,type=int);p.add_argument('--dry-run',required=True,action='store_true');p.add_argument('--legacy-artifact');p.add_argument('--debug-root',default='data/debug/history_v2')
    a=p.parse_args();print(json.dumps(extract_patient(a.patient,case_id=a.case_id,legacy_artifact=a.legacy_artifact,debug_root=a.debug_root)))
if __name__=='__main__':main()
