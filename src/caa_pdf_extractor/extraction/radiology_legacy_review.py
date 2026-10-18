"""Read-only v1 compatibility plan. Never fabricate source ownership or backfill."""
import argparse
import json
from pathlib import Path
from sqlalchemy import select,text
from ..database import SessionLocal
from ..database.models import Radiology,ExtractionRun
from ..preparation import build_patient_bundle
from .imaging_reports import discover_studies

def compatibility_plan(patient_id,session_factory=None):
    factory=session_factory or SessionLocal;bundle=build_patient_bundle(patient_id,factory)
    studies=discover_studies(bundle);prepared={p.procedure_id:p for d in bundle.documents for p in d.diagnostic_procedures}
    with factory() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        cases=session.execute(text('SELECT id, deleted FROM public."case" WHERE fallnr=:pid'),{'pid':patient_id}).all()
        if len(cases)!=1 or cases[0].deleted:raise ValueError('Missing, deleted or ambiguous case identity')
        case_id=cases[0].id
        rows=list(session.scalars(select(Radiology).where(Radiology.case_id==case_id)))
        audits=list(session.scalars(select(ExtractionRun).where(ExtractionRun.status=='SUCCESS',ExtractionRun.raw_output['resolved_case_id'].astext==str(case_id))))
        result=[]
        for row in rows:
            links=[link for run in audits if (run.raw_output or {}).get('operation')=='COMMIT_VERIFIED_EXTRACTION'
                   for link in (run.raw_output or {}).get('radiology_observations',[]) if link.get('row_id')==row.id]
            owners={(link['study']['source_document_id'],link['study']['procedure_id']) for link in links}
            entry={'legacy_row_id':row.id,'case_id':case_id,'legacy_type':row.type,'classification':'AUDIT_REFERENCE_ONLY',
                   'reason':'Missing or ambiguous audited procedure linkage','commit_eligible':False}
            if len(owners)==1:
                doc,pid=next(iter(owners));source=prepared.get(pid)
                matches=[s for s in studies if s.source_document_id==doc and s.kind!='PET' and source
                         and ' '.join(s.header.split()) in ' '.join(source.source_text.split())]
                if len(matches)==1:
                    entry.update(classification='SOURCE_LINKED_REQUIRES_V2_REVALIDATION',reason='Unique original audit and exact prepared report header',
                        proposed_exam_identity={'case_id':case_id,'source_document_id':doc,'procedure_id':matches[0].procedure_id},
                        next_step='Use a new independently validated radiology_v2 artifact; never copy the v1 acuity or type without revalidation.')
            result.append(entry)
    return {'patient_id':patient_id,'read_only':True,'legacy_table_untouched':True,'clinical_rows_changed':0,'rows':result}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('patient_id');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=compatibility_plan(args.patient_id);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
