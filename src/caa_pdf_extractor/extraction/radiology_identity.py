"""Source-linked observation planning before any radiology write."""
from collections import defaultdict
from .radiology_artifacts import digest, VerifiedExtractionError
from .radiology_inputs import report_text


def observations(procedures, entries):
    result=[]
    for p in procedures:
        if p.modality!='MRI': continue
        event=entries[p.procedure_id]['normalized_output']
        study={'source_document_id':p.source_document_id,'procedure_id':p.procedure_id,
               'mri_date':event['mri_date'],'report_sha256':digest(report_text(p))}
        for finding in event['findings']:
            result.append({'study':study,'identity':[event['mri_date'],finding['type'],finding['acute_lesion']],
                           'finding':finding,'observation_fingerprint':digest({'study':study,'finding':finding})})
    return result


def same_study(a,b):
    return (a==b or
        (a['source_document_id']==b['source_document_id'] and a['procedure_id']==b['procedure_id'] and a['report_sha256']==b['report_sha256']) or
        (a['mri_date'] is not None and a['mri_date']==b['mri_date'] and a['report_sha256']==b['report_sha256']))


def plan_reconciliation(candidates, rows, audits):
    existing=defaultdict(list);groups=defaultdict(list);links=defaultdict(list)
    for row in rows:
        existing[(row.mri_date.isoformat() if row.mri_date else None,row.type,row.acute_lesion)].append(row)
    for candidate in candidates:groups[tuple(candidate['identity'])].append(candidate)
    for audit in audits:
        raw=getattr(audit,'raw_output',None) or {}
        if getattr(audit,'status',None)!='SUCCESS' or raw.get('operation')!='COMMIT_VERIFIED_EXTRACTION':continue
        for link in raw.get('radiology_observations',[]):links[tuple(link['identity'])].append(link)
    plan={};collisions=[]
    for key,items in groups.items():
        first=items[0];matches=existing[key]
        if any(not same_study(first['study'],item['study']) for item in items):
            raise VerifiedExtractionError('DUPLICATE_IDENTITY_AMBIGUOUS')
        if len(matches)>1:raise VerifiedExtractionError('DUPLICATE_IDENTITY_AMBIGUOUS')
        row=matches[0] if matches else None
        if row is not None:
            proofs=[link for link in links[key] if link['row_id']==row.id]
            # Every audited owner of this identity must agree with this study.
            if not proofs or any(not same_study(first['study'],p['study']) for p in proofs):
                raise VerifiedExtractionError('DUPLICATE_IDENTITY_AMBIGUOUS')
        canonical=first['observation_fingerprint']
        for item in items:
            plan[item['observation_fingerprint']]={'row':row,'canonical':canonical,
                'reason':'IDEMPOTENT_REPLAY' if row is not None else 'SAME_STUDY_DUPLICATE' if item is not first else 'NEW_OBSERVATION'}
        if len(items)>1:collisions.append({'identity':list(key),'resolution':'SAME_STUDY_DUPLICATE','sources':[i['study'] for i in items]})
    return plan,collisions
