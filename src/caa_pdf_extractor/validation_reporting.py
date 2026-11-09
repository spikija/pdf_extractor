"""Read-only validation summaries and physician queues for a frozen selection."""
from collections import Counter, defaultdict
from pathlib import Path
import json

from .workbench_store import WorkbenchStore, read, sha, atomic, candidate_entities, integrity
from .validation_cohort import csv_write
from .extraction.medication_identity_v3 import Resolver, COMMON_BRANDS
from review_app.artifact_repository import layer
from review_app.display_models import diagnosis_entities


def summarize(output, store=None):
    output = Path(output); store = store or WorkbenchStore()
    registry = read(store.root / 'validation_cohort.json')
    progress = read(output / 'processing_progress.json')
    data, entries, failures, gate_failures = {}, {}, [], []
    for patient in registry['patient_ids']:
        entries[patient] = {}
        if patient in registry['reference_patient_ids']:
            manifest = store.manifest(patient)
            for module in ('diagnoses','medications'):
                version = registry['reference_versions'][patient][module]['version']
                entries[patient][module] = manifest['modules'][module]['versions'][version]
        else:
            result = progress.get(patient,{})
            if result.get('errors'):failures.append(dict(patient_id=patient, errors=result['errors']))
            entries[patient] = result.get('modules',{})
        data[patient] = {}
        for module in ('diagnoses','medications'):
            entry = entries[patient].get(module)
            if entry is None:
                failures.append(dict(patient_id=patient,module=module,errors=['NO_MODULE_OUTPUT']));continue
            md = read(entry['metadata_path'])
            if md.get('fatal_errors'):failures.append(dict(patient_id=patient,module=module,errors=md['fatal_errors']))
            for gate,value in md.get('validation_test_results',{}).get('hard_gates',{}).items():
                if value is not True:gate_failures.append(dict(patient_id=patient,module=module,gate=gate))
            if not integrity(entry):gate_failures.append(dict(patient_id=patient,module=module,gate='ARTIFACT_HASH_MISMATCH'))
            data[patient][module] = entry
    unresolved = defaultdict(list)
    diagnosis_queue, patients = [], []
    violation = Counter(dict.fromkeys(['negated_AF_creates_AF_identity','uncertain_AF_creates_confirmed_AF',
        'negative_AF_monitoring_erases_prior_confirmed_AF','acute_event_disappears_from_physician_view',
        'repeated_same_chronic_disease_creates_duplicate_identity','same_event_repeated_across_documents_creates_duplicate_event',
        'distinct_events_in_different_years_incorrectly_merged','narrative_junk_promoted_to_diagnosis',
        'known_common_trade_name_unresolved','clinical_database_write','cloud_patient_data_calls'],0))
    for patient in registry['patient_ids']:
        dx=data[patient].get('diagnoses',{});med=data[patient].get('medications',{})
        diseases=layer(dx,'canonical_disease_identities');events=layer(dx,'canonical_clinical_events')
        mentions=layer(dx,'diagnosis_mentions');assertions=layer(dx,'diagnostic_assertion_timeline')
        af=layer(dx,'af_state') or {};identities=layer(med,'canonical_medications');exposures=layer(med,'medication_exposures')
        medication_mentions=layer(med,'medication_mentions')
        af_identity=any(d['canonical_name']=='ATRIAL_FIBRILLATION' for d in diseases)
        af_positive=any(m.get('canonical_candidate')=='ATRIAL_FIBRILLATION' and m['assertion_status']=='ESTABLISHED' for m in mentions)
        violation['negated_AF_creates_AF_identity']+=int(af_identity and not af_positive)
        violation['uncertain_AF_creates_confirmed_AF']+=int(af.get('AF_state')=='CONFIRMED_AF' and not af_positive)
        violation['negative_AF_monitoring_erases_prior_confirmed_AF']+=int(af.get('positive_evidence_count',0)>0 and af.get('negative_evidence_count',0)>0 and not af_identity)
        violation['acute_event_disappears_from_physician_view']+=len(events)-len(diagnosis_entities(dx,True))
        violation['repeated_same_chronic_disease_creates_duplicate_identity']+=len(diseases)-len({d['canonical_name'] for d in diseases})
        signatures=[tuple(str(e.get(k)) for k in ('event_type','event_date','side','territory','location')) for e in events]
        violation['same_event_repeated_across_documents_creates_duplicate_event']+=len(signatures)-len(set(signatures))
        event_ids=Counter(e['event_id'] for e in events)
        if any(count>1 for count in event_ids.values()):
            gate_failures.append(dict(patient_id=patient,module='diagnoses',gate='DUPLICATE_CANONICAL_EVENT_ID'))
        for event in events:
            years={str(m.get('event',{}).get('event_date'))[:4] for m in mentions if m.get('event_id')==event['event_id'] and m.get('event',{}).get('event_date')}
            violation['distinct_events_in_different_years_incorrectly_merged']+=int(len(years)>1)
        violation['narrative_junk_promoted_to_diagnosis']+=sum(m.get('semantic_class') in ('HEADING','PROCEDURE','INVESTIGATION_FINDING','NEGATED','DIFFERENTIAL_DIAGNOSIS') for m in mentions)
        for mention in medication_mentions:
            if not mention.get('canonical_drug'):
                name=mention.get('normalized_trade_name') or mention.get('cleaned_product_name') or mention.get('raw_medication','').strip()
                unresolved[name].append(dict(mention,patient_id=patient))
        def queue(reason, entity='', evidence='', detail=''):
            diagnosis_queue.append(dict(patient_id=patient,reason=reason,entity_id=entity,evidence_id=evidence,detail=detail,review_required=True))
        for identity,count in event_ids.items():
            if count>1:queue('DUPLICATE_CANONICAL_EVENT_ID',identity,detail=f'{count} raw rows share an event ID; ambiguous partial-year references require extraction repair, not silent merging.')
        if af.get('AF_state')=='CONFLICTING_AF_EVIDENCE':queue('AF_EVIDENCE_CONFLICT')
        elif af.get('AF_state')=='SUSPECTED_AF':queue('AF_SUSPICION_REQUIRES_REVIEW')
        for row in layer(dx,'unresolved_candidates'):
            queue(row.get('reason','DISEASE_EVENT_CLASSIFICATION_UNCERTAINTY'),row.get('event_id',row.get('mention_id','')),row.get('evidence_id',row.get('span_id','')),str(row.get('raw_text',''))[:300])
        grouped=defaultdict(list)
        for a in assertions:grouped[a['canonical_name']].append(a)
        for name, rows in grouped.items():
            states={r['assertion_status'] for r in rows}
            if states & {'SUSPECTED','UNCERTAIN','NOT_ASSESSABLE','DIFFERENTIAL','CONDITIONAL'}:
                queue('ASSERTION_UNCERTAINTY_OR_EVOLUTION',name,detail='Suspicion is retained separately; independent positive evidence is required for an identity.')
            by_day=defaultdict(set)
            for a in rows:by_day[a.get('assertion_date')].add(a['assertion_status'])
            if any('ESTABLISHED' in states and 'NEGATED' in states for day,states in by_day.items() if day):
                queue('SAME_PERIOD_ASSERTIONS_REQUIRE_SCOPE_REVIEW',name,detail='Negative current findings may coexist with historical disease; inspect provenance.')
        for event in events:
            if not event.get('event_date') or event.get('event_date_precision')=='UNKNOWN':queue('EVENT_DATE_OR_MERGE_UNCERTAINTY',event['event_id'])
        new=patient in registry['new_patient_ids']
        entities=candidate_entities(dx,'diagnoses')+candidate_entities(med,'medications') if dx and med else []
        patients.append(dict(patient_id=patient,reference=not new,AF_state=af.get('AF_state','UNKNOWN'),
            canonical_diseases=len(diseases),clinical_events=len(events),medication_identities=len(identities),medication_exposures=len(exposures),
            unresolved_medication_mentions=sum(not m.get('canonical_drug') for m in medication_mentions),
            review_required_count=sum(bool(e.get('review_required')) for e in entities) if new else 0))
    resolver=Resolver()
    violation['known_common_trade_name_unresolved']=sum(not resolver.resolve(b+' 10 mg 1 0 1')['canonical_drug'] for b in COMMON_BRANDS)
    medication_queue=[]
    for name,mentions in unresolved.items():
        medication_queue.append(dict(normalized_raw_name=name,example_raw_string=mentions[0].get('raw_medication',''),
            patient_count=len({m['patient_id'] for m in mentions}),mention_count=len(mentions),
            suggested_active_ingredient_if_available='; '.join(sorted({i for m in mentions for i in m.get('active_ingredients',[])})),review_required=True))
    medication_queue.sort(key=lambda r:(-r['patient_count'],-r['mention_count'],r['normalized_raw_name']))
    failed_patients={f['patient_id'] for f in failures+gate_failures}
    for patient in patients:
        patient['validation_status']='REFERENCE_APPROVED' if patient['reference'] else 'HELD_GATE_FAILURE' if patient['patient_id'] in failed_patients else 'CANDIDATE_PASS'
    csv_write(output/'validation_unresolved_medications.csv',medication_queue,['normalized_raw_name','example_raw_string','patient_count','mention_count','suggested_active_ingredient_if_available','review_required'])
    csv_write(output/'validation_diagnosis_review_queue.csv',diagnosis_queue,['patient_id','reason','entity_id','evidence_id','detail','review_required'])
    csv_write(output/'validation_patient_results.csv',patients)
    protected=read(output/'reference_protection_hashes.json')
    changed=[path for path,digest in protected.items() if not Path(path).is_file() or sha(path)!=digest]
    report=dict(patients=patients,selected_new_patient_ids=registry['new_patient_ids'],patients_successfully_processed=sum(p in progress and len(progress[p].get('modules',{}))==2 and not progress[p]['errors'] and p not in failed_patients for p in registry['new_patient_ids']),
        patients_failed=sorted(failed_patients),patients_with_both_modules_executed=len(progress),pipeline_failures=failures,hard_gate_failures=gate_failures,
        safety_gate_violations=dict(violation),protected_file_count=len(protected),protected_files_changed=changed,
        totals={k:sum(p[k] for p in patients) for k in ['canonical_diseases','clinical_events','medication_identities','medication_exposures','unresolved_medication_mentions','review_required_count']},
        AF_states={state:sum(p['AF_state']==state for p in patients) for state in ('CONFIRMED_AF','SUSPECTED_AF','NO_CONFIRMED_AF','CONFLICTING_AF_EVIDENCE')},diagnosis_review_queue_rows=len(diagnosis_queue),unresolved_medication_groups=len(medication_queue),
        diagnosis_pipeline_version='4.1',medication_pipeline_version='3.0',AF_assertion_engine_version='ASSERTION_SCOPE_1.1',
        new_module_status='CANDIDATE',auto_promotions=0,database_writes='NONE',cloud_patient_calls='NONE')
    atomic(output/'validation_results.json',report)
    selection=read(output/'selected_patients.json');screen={r['patient_id']:r for r in read(output/'source_screening.json')}
    new=[p for p in patients if not p['reference']]
    text=['# TWENTY_PATIENT_VALIDATION_READY_FOR_PHYSICIAN_REVIEW','',
        '**Two diagnosis candidates failed independent validation and are held for repair. They are available for inspection, not approval.**','',
        f'Patients: {len(patients)} total ({len(registry["reference_patient_ids"])} preserved references, {len(new)} new).',
        f'PDFs: {sum(screen[p]["pdf_count"] for p in registry["patient_ids"])}. Pages: {sum(screen[p]["page_count"] for p in registry["patient_ids"])}.',
        f'New patients successfully processed: {report["patients_successfully_processed"]}. Failed: {len(report["patients_failed"])}.',
        '', 'Selection was frozen in validation_cohort_selection.csv before module extraction. Source-screening flags are wording indicators, not diagnoses. Only selected new patients ran; all others received at most bounded source screening.',
        '', '## Counts', '', '| Measure | All 20 | New 15 |','| --- | ---: | ---: |']
    for k,label in [('canonical_diseases','Diagnosis entities'),('clinical_events','Clinical events (raw candidate rows)'),('medication_identities','Medication identities'),('medication_exposures','Medication exposures'),('review_required_count','Raw entities requiring review or repair'),('unresolved_medication_mentions','Unresolved medication mentions')]:
        text.append(f'| {label} | {report["totals"][k]} | {sum(p[k] for p in new)} |')
    text+=['',f'AF states (diagnosis-layer resolution): {json.dumps(report["AF_states"])}.',
        f'Unresolved medication groups: {len(medication_queue)}. Diagnosis queue rows: {len(diagnosis_queue)}.',
        f'Hard-gate failures: {len(gate_failures)}. Pipeline failures: {len(failures)}. Safety violations: {sum(violation.values())}.',
        'Clinical-event counts above are raw candidate rows. Five repeated partial-year rows share canonical IDs in two held candidates; they are not approved distinct clinical events. Every raw source row is exposed in the UI, with ambiguous-entity adjudication and candidate promotion blocked.',
        '', '## Boundaries and review', '',
        'Original-five reference versions are frozen in reference_versions.json based on the physician review reported by the user. Their source files, active pointers and adjudications were not modified. Reference approval is recorded only in the cohort registry; no individual review is fabricated.',
        'All new module outputs remain CANDIDATE. No CAA classification, neurosonology structured extraction, EEG, reconstructed scales, risk scores or OAC decisions ran. Only source preparation, explicit encounter-date lookup, V4.1 diagnoses/events and V3 medications were used.',
        f'Protected pre-existing files: {len(protected)}; changed: {len(changed)}. Medication dictionary unchanged. Database writes = NONE. Cloud patient calls = NONE.',
        'Parsed-PDF cache keys include source SHA-256, PyMuPDF version and parser code hash. Existing matching entries are reused. Native-text gaps and unresolved mappings require review; no OCR/model inference was added.',
        '', '## New patients', '', '| Patient | AF state | Diseases | Events | Medication identities | Exposures | Review entities | Validation |', '| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for p in new:text.append(f'| {p["patient_id"]} | {p["AF_state"]} | {p["canonical_diseases"]} | {p["clinical_events"]} | {p["medication_identities"]} | {p["medication_exposures"]} | {p["review_required_count"]} | {p["validation_status"]} |')
    (output/'validation_cohort_summary.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return report
