"""Conservative date ownership for an existing deterministic admission-event candidate."""
import re
from ..module_artifacts import digest
from .diagnosis_events_v4 import concepts,classify_concept,event_details,compatible
from .admission_summary import inputs,ground


def apply(patient,bundle,encounters,model_results,result):
    events=result['canonical_clinical_events']
    for source in inputs(patient,bundle,encounters):
        if source['encounter_id'] not in model_results:continue
        cached=model_results[source['encounter_id']]['validated']
        inference=ground({k:v for k,v in cached.items() if k!='evidence'},source)
        if inference['confidence'] not in ('HIGH','MODERATE') or not source.get('admission_date'):continue
        event_type=inference['probable_admission_event_type']
        candidates=[c for c in result.get('clinical_event_candidates',[]) if c['document_id']==source['document_id']
            and c['event_type']==event_type and c['assertion_status']=='ESTABLISHED' and not c.get('event_date') and not c.get('historical') and not c.get('resolved_event_id')]
        signatures={(c.get('side'),c.get('territory'),c.get('location')) for c in candidates}
        if len(signatures)!=1:continue
        for evidence in inference['evidence']:
            text=evidence['raw_text']
            if not re.search(r'aufnahme|aufgenommen|admission',text,re.I) or not re.search(r'akut|plötzlich|neu\s+aufgetreten',text,re.I):continue
            if re.search(r'übernahme|verlegung|rehabilitation|zustand\s+nach|\bz\s*\.\s*n\s*\.|vorbekannt|anamnestisch',text,re.I):continue
            hits=[h for h in concepts(text) if h[2]==event_type and h[3]]
            if len(hits)!=1:continue
            a,b,_,_=hits[0];clause,left,_,promote,guard=classify_concept(text,a,b,event=True)
            if not promote or not guard['explicit_positive']:continue
            # The candidate supplies type/anatomy; Granite cannot create either.
            candidate=candidates[0]
            if not any(candidate.get(f) for f in ('side','location','territory')):continue
            unit=dict(raw_text=text,start=0,document_date=None)
            detail=event_details(unit,clause,dict(text=text),event_type,a-left,a)
            if detail.get('event_date'):
                value=detail['event_date'];basis='EXPLICIT_NARRATIVE_DATE';precision=detail['event_date_precision']
            else:
                value=source['admission_date'];basis='ADMISSION_DATE_IF_EVENT_IS_CLEARLY_THE_ADMISSION_EVENT';precision='DAY'
            existing=[e for e in events if e.get('event_date')==value and compatible(dict(candidate,event_date=value),e)]
            if len(existing)>1:continue
            if existing:
                chosen=existing[0]
            else:
                chosen={k:v for k,v in candidate.items() if k not in ('candidate_id','candidate_type','evidence','status')}
                chosen.update(event_id=f'{patient}:EV4:{digest([event_type,value,candidate.get("side"),candidate.get("territory"),candidate.get("location")])[:16]}',
                    event_date=value,event_date_precision=precision,event_date_basis=basis,date_source=basis,historical=False,
                    created_from='DETERMINISTIC_EVENT_CANDIDATE_WITH_ADMISSION_DATE_EVIDENCE',review_required=True)
                events.append(chosen)
            chosen.setdefault('date_evidence',[]).append(dict(event_date=value,precision=precision,basis=basis,
                encounter_id=source['encounter_id'],evidence_id=evidence['evidence_id'],explicit_positive=True))
            for c in candidates:c['resolved_event_id']=chosen['event_id'];c['status']='CORROBORATING_EVENT_REFERENCE'
            break
