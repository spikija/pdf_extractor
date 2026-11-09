"""Unified physician registry. Event history families retain links to distinct episodes."""
from collections import defaultdict
from ..module_artifacts import digest

VERSION='PATIENT_DIAGNOSES_1.0'
FAMILIES={'SYMPTOMATIC_EPILEPSY':'EPILEPSY','STRUCTURAL_EPILEPSY':'EPILEPSY','BREAST_CANCER':'BREAST_CARCINOMA'}
EVENT_FAMILIES={'ISCHEMIC_STROKE':'ISCHEMIC_STROKE_HISTORY','STROKE_UNSPECIFIED':'CEREBROVASCULAR_DISEASE',
    'PRIMARY_ICH':'INTRACEREBRAL_HEMORRHAGE_HISTORY','INTRACEREBRAL_HEMORRHAGE':'INTRACEREBRAL_HEMORRHAGE_HISTORY',
    'INTRACRANIAL_HEMORRHAGE_UNCLASSIFIED':'INTRACEREBRAL_HEMORRHAGE_HISTORY',
    'TREATMENT_ASSOCIATED_ICH':'INTRACEREBRAL_HEMORRHAGE_HISTORY','HEMORRHAGIC_TRANSFORMATION':'INTRACEREBRAL_HEMORRHAGE_HISTORY',
    'TIA':'TIA_HISTORY','SAH':'SAH_HISTORY','SEIZURE':'SEIZURE_HISTORY','STATUS_EPILEPTICUS':'STATUS_EPILEPTICUS_HISTORY',
    'DVT':'DVT_HISTORY','PULMONARY_EMBOLISM':'PULMONARY_EMBOLISM_HISTORY'}


def build(patient,result):
    groups=defaultdict(list)
    for row in result['canonical_disease_identities']:
        groups[FAMILIES.get(row['canonical_name'],row['canonical_name'])].append(('DIAGNOSIS',row))
    for row in result['canonical_clinical_events']:
        groups[EVENT_FAMILIES.get(row['event_type'],row['event_type'])].append(('EVENT',row))
    for row in result.get('clinical_event_candidates',[]):
        family=EVENT_FAMILIES.get(row['event_type'],row['event_type'])
        if family not in groups:
            groups[family].append(('EVENT_CANDIDATE',dict(row,event_id=row['candidate_id'],
                source_evidence_ids=row.get('source_evidence_ids',[row['evidence_id']]),source_document_ids=row.get('source_document_ids',[row['document_id']]))))
    registry=[]
    for name,members in sorted(groups.items()):
        event_ids=[r['event_id'] for kind,r in members if kind in ('EVENT','EVENT_CANDIDATE')]
        dates=[r.get('first_documented_date') for kind,r in members if kind=='DIAGNOSIS' and r.get('first_documented_date')]
        evidence=sorted({eid for _,r in members for eid in r.get('source_evidence_ids',[])})
        linked=[m for m in result['diagnosis_mentions'] if m['evidence_id'] in evidence]
        dates+= [m['document_date'] for m in linked if m.get('document_date')]
        dates+=[r['document_date'] for k,r in members if k=='EVENT_CANDIDATE' and r.get('document_date')]
        registry.append(dict(patient_diagnosis_id=f'{patient}:PDX:{digest(name)[:16]}',patient_id=patient,canonical_name=name,
            first_documented_date=min(dates) if dates else None,first_documented_date_precision='DAY' if dates else 'UNKNOWN',
            status='SOURCE_ESTABLISHED' if any(k!='EVENT_CANDIDATE' or r.get('assertion_status')=='ESTABLISHED' for k,r in members) else 'UNCERTAIN',source_evidence_ids=evidence,
            source_document_ids=sorted({doc for _,r in members for doc in r.get('source_document_ids',[])}),
            source_diagnosis_ids=[r['diagnosis_identity_id'] for k,r in members if k=='DIAGNOSIS'],linked_event_ids=event_ids,
            source_concepts=sorted({r['canonical_name'] if k=='DIAGNOSIS' else r['event_type'] for k,r in members}),
            attributes=[a for _,r in members for a in r.get('source_attributes',[])],
            subtype_assertions=[a for _,r in members for a in r.get('subtype_assertions',[])],
            mention_count=len({m['mention_id'] for m in linked}) or sum(r.get('mention_count',1) for _,r in members),
            document_count=len({m['document_id'] for m in linked}) or len({doc for _,r in members for doc in r.get('source_document_ids',[])}),
            registry_version=VERSION,review_required=True))
    return registry
