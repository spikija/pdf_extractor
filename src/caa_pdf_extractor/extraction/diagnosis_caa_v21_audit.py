"""Independent invariants for review outputs; no clinical inference."""
from collections import Counter
from .diagnosis_canonical_v21 import DiseaseGroup, category_errors


def audit_tables(tables,eligibility,expected_mentions,patient_inputs):
    candidates={(r['patient_id'],r['record_id']):r for r in tables['diagnosis_candidate_eligibility']}
    accepted={(r['patient_id'],rid) for r in tables['canonical_diagnoses'] for rid in r['member_ids']}
    expected_disease={(pid,rid) for pid,rows in eligibility.items() for rid,v in rows.items() if v.candidate_type=='DIAGNOSIS_DISEASE'}
    expected_events={(pid,v.event_type,candidates[(pid,rid)]['accepted_date'])
                     for pid,rows in eligibility.items() for rid,v in rows.items()
                     if v.candidate_type=='ACUTE_CLINICAL_EVENT' and candidates[(pid,rid)]['accepted_date']}
    actual_events={(r['patient_id'],r['event_type'],r['event_date']) for r in tables['clinical_events'] if r['event_date']}
    feature_ids={r['id'] for r in tables['boston_features']}
    efeature_ids={r['id'] for r in tables['edinburgh_features']}
    promoted=sum(eligibility[p][r].candidate_type!='DIAGNOSIS_DISEASE' for p,r in accepted)
    mismatch=0
    for row in tables['canonical_diagnoses']:
        fields={k:row[k] for k in DiseaseGroup.model_fields}
        mismatch+=bool(category_errors(DiseaseGroup(**fields),[candidates[(row['patient_id'],r)]['raw_candidate'] for r in row['member_ids']]))
    missed_presentation=double_count=missed_context=0
    for pid,inputs in patient_inputs.items():
        scans=inputs['scans'];episodes=inputs['episodes']
        for row in tables['boston_features']:
            if row['patient_id']!=pid:continue
            available=set()
            for s in scans:
                ep=episodes.get(s['episode_id'])
                clinical_day=((ep.current_ich_date or s.get('admission_date')) if ep.presentation=='SPONTANEOUS_ICH' else (s.get('admission_date') or ep.current_ich_date)) if ep else None
                if s.get('date') and s['date']<=row['date'] and clinical_day and clinical_day<=row['date']:available.add(s['episode_id'])
            qualified=any(episodes[e].presentation in ('SPONTANEOUS_ICH','TFNE','COGNITIVE_IMPAIRMENT_DEMENTIA') for e in available if e in episodes)
            missed_presentation+=qualified and row['qualifying_presentation'] is not True
            keys=[tuple(k) for k in row['distinct_ich_lesion_keys']]
            double_count+=len(keys)!=len(set(keys)) or (row['lobar_ich'] or 0)>len(set(keys))
        for row in tables['edinburgh_ich_events']:
            if row['patient_id']!=pid or not row.get('primary_ct_id') or not row.get('id'):continue
            primary=next(s for s in scans if s['id']==row['primary_ct_id'])
            ep=episodes.get(primary['episode_id'])
            if ep and ep.spontaneous_ich is not None:missed_context+=row['spontaneous'] is None
    ct_assignments=[(r['patient_id'],sid) for r in tables['edinburgh_ich_events'] for sid in r.get('ct_ids',[])]
    duplicate_ct=sum(n-1 for n in Counter(ct_assignments).values() if n>1)
    unresolved=sum(bool(r.get('id')) and not r.get('event_date') for r in tables['edinburgh_ich_events'])
    prohibited_categories={'HIGH_PROBABILITY','LOW_PROBABILITY','INTERMEDIATE_OR_UNCERTAIN'}
    classified_edinburgh=[r for r in tables['edinburgh_ich_events'] if r.get('original_edinburgh') in prohibited_categories or r.get('simplified_edinburgh') in prohibited_categories]
    return dict(non_diagnosis_promoted_to_canonical=promoted,
        obvious_domain_subcategory_mismatch=mismatch,
        diagnosis_mentions_lost=abs(expected_mentions-len(tables['diagnosis_mentions'])),
        duplicate_mention_ids=len(tables['diagnosis_mentions'])-len({r['id'] for r in tables['diagnosis_mentions']}),
        eligible_diseases_without_canonical_group=len(expected_disease-accepted),
        duplicate_canonical_identity_names=sum(n-1 for n in Counter((r['patient_id'],r['canonical_name'].strip().casefold()) for r in tables['canonical_diagnoses']).values() if n>1),
        duplicate_CAA_or_epilepsy_disease_identities=sum(n-1 for n in Counter((r['patient_id'],r['subcategory']) for r in tables['canonical_diagnoses'] if r['subcategory'] in ('CAA','EPILEPSY')).values() if n>1),
        recurrent_events_collapsed=len(expected_events-actual_events),
        CAA_source_vs_computed_conflation=sum(r.get('source_diagnosis_date') is not None for name in ('boston_patient_timepoints','edinburgh_ich_events') for r in tables[name]),
        qualifying_presentation_not_joined_when_available=missed_presentation,
        serial_same_lesion_double_count=double_count,
        computed_result_without_feature_trace=sum(bool(r.get('assessable')) and r.get('feature_id') not in feature_ids for r in tables['boston_patient_timepoints']),
        edinburgh_result_without_feature_trace=sum(r.get('id') is not None and r.get('feature_id') not in efeature_ids for r in tables['edinburgh_ich_events']),
        lobar_ICH_context_not_joined_when_available=missed_context,
        serial_followup_treated_as_new_ICH_event=duplicate_ct,
        unresolved_ICH_event_identity=unresolved,
        nonlobar_ICH_Edinburgh_application=sum(r.get('lobar') is not True for r in classified_edinburgh),
        nonspontaneous_ICH_Edinburgh_application=sum(r.get('spontaneous') is not True for r in classified_edinburgh),
        Edinburgh_without_confirmed_MRI_fallback=sum(r.get('adequate_mri_available') is not False for r in classified_edinburgh))
