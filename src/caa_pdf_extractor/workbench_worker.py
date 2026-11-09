"""Single-request offline subprocess for existing diagnosis/medication assemblers."""
import csv
import io
import json
import os
import sys
from pathlib import Path

from .workbench_store import PILOTS, read, sha


def guard(output):
    output = output.resolve()
    def audit(event, args):
        if event.startswith(('socket.', 'sqlite3.')) or event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.spawn'):
            raise RuntimeError('WORKBENCH_NETWORK_DATABASE_AND_CHILD_PROCESS_FORBIDDEN')
        if event == 'import' and any(s in args[0].lower() for s in ('sqlalchemy', 'psycopg', 'requests', 'httpx')):
            raise RuntimeError('WORKBENCH_DATABASE_AND_HTTP_IMPORT_FORBIDDEN')
        if event == 'open':
            name, mode, flags = args
            writing = isinstance(mode, str) and any(c in mode for c in 'wax+')
            writing |= isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and isinstance(name, (str, bytes, os.PathLike)) and not Path(name).resolve().is_relative_to(output):
                raise RuntimeError('WORKBENCH_WRITE_OUTSIDE_NEW_RUN_FORBIDDEN')
        if event in ('os.rename', 'os.remove', 'os.rmdir'):
            raise RuntimeError('WORKBENCH_WORKER_REPLACEMENT_FORBIDDEN')
    sys.addaudithook(audit)


def csv_text(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys(k for row in rows for k in row)) or ['status'])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return out.getvalue()


def execute(request):
    patient, module = request['patient_id'], request['module']
    allowed = set(PILOTS)
    if request.get('cohort_registry'):
        registry = request['cohort_registry']
        if sha(registry['path']) != registry['sha256']:
            raise ValueError('Validation cohort changed during execution')
        from .workbench_store import WorkbenchStore
        allowed = WorkbenchStore(Path(registry['path']).parent).enabled_patients()
    if patient not in allowed or module not in ('diagnoses', 'medications'):
        raise ValueError('Exactly one registered validation patient and one supported module are required')
    source = Path(request['source_dir'])
    # Required inputs have been snapshotted; reject any mutation before execution.
    if any(sha(path) != value for path, value in request['input_hashes'].items()):
        raise ValueError('Saved source inputs changed')
    bundle = read(source / 'source_bundle.json')
    if str(bundle.get('patient_id')) != patient:
        raise ValueError('Source bundle patient mismatch')
    if module == 'diagnoses':
        from .extraction import diagnosis_events_v4 as dx
        from .diagnosis_v4_regression import regression
        result = dx.assemble(patient, bundle, read(source / 'source_mentions.json'))
        from .extraction.patient_diagnoses import build
        from .extraction.admission_summary import materialize
        from .extraction.admission_event_linkage import apply as apply_admission_dates
        model_results=read(request['admission_model_results']) if request.get('admission_model_results') else {}
        encounters=read(source/'encounters.json')
        before_dates={e['event_id']:e.get('event_date') for e in result['canonical_clinical_events']}
        before_signatures={(e['event_type'],e.get('event_date'),e.get('side'),e.get('territory'),e.get('location')) for e in result['canonical_clinical_events']}
        apply_admission_dates(patient,bundle,encounters,model_results,result)
        result['admission_clinical_summary']=materialize(patient,bundle,encounters,model_results,result['canonical_clinical_events'])
        result['patient_diagnoses']=build(patient,result)
        result['canonical_clinical_events'].sort(key=lambda e:(e.get('event_date') or '9999',e['event_id']))
        gates = regression({patient: result}, patient_id=patient)
        from .extraction.assertion_scope import VERSION as assertion_version
        mentions = result['diagnosis_mentions']
        gates.update(
            structured_diagnosis_item_loss=result['structured_diagnosis_reconciliation']['lost']==0,
            unmapped_structured_diagnosis_silently_dropped=all(r.get('candidate_id') and r.get('review_required') for r in result['unmapped_diagnosis_candidates']),
            explicit_supported_diagnosis_silently_dropped=all(r['disposition'] in ('MAPPED_DISEASE_OR_EVENT','NON_DISEASE_EVENT','UNMAPPED_DIAGNOSIS_CANDIDATE') for r in result['structured_diagnosis_items']),
            CAA_workup_target_establishes_CAA=not any(a.get('semantic_relation')=='WORKUP_TARGET' and a['assertion_status']=='ESTABLISHED' for a in result['diagnostic_assertion_timeline']),
            CAA_not_assessable_establishes_CAA=not any(a.get('final_assertion')=='NOT_ASSESSABLE' and a['assertion_status']=='ESTABLISHED' for a in result['diagnostic_assertion_timeline']),
            CAA_negative_pattern_establishes_CAA=not any(a.get('CAA_SUPPORT')=='NEGATIVE' and a['assertion_status']=='ESTABLISHED' for a in result['diagnostic_assertion_timeline']),
            unique_canonical_event_ids=len({e['event_id'] for e in result['canonical_clinical_events']})==len(result['canonical_clinical_events']),
            assertion_engine_applied=bool(result['diagnostic_assertion_timeline']) and all(
                a.get('assertion_rule_version') == assertion_version for a in result['diagnostic_assertion_timeline']),
            canonical_diseases_have_positive_support=all(any(
                m.get('diagnosis_identity_id') == d['diagnosis_identity_id'] and m['assertion_status'] == 'ESTABLISHED'
                for m in mentions) for d in result['canonical_disease_identities']),
            no_nonpositive_AF_identity_support=not any(m.get('canonical_candidate') == 'ATRIAL_FIBRILLATION'
                and m.get('diagnosis_identity_id') and m['assertion_status'] != 'ESTABLISHED' for m in mentions))
        gates.update(
            conditional_AF_establishes_AF=not any(a['canonical_name']=='ATRIAL_FIBRILLATION' and a.get('final_assertion')=='CONDITIONAL' and a.get('explicit_positive') for a in result['diagnostic_assertion_timeline']),
            negated_AF_establishes_AF=not any(m.get('canonical_candidate')=='ATRIAL_FIBRILLATION' and m.get('assertion_status')=='NEGATED' for m in mentions),
            uncertain_AF_establishes_confirmed_AF=not result['af_state']['confirmed_af'] or any(o.get('explicit_positive') and o['assertion_status']=='ESTABLISHED' for o in result['af_observations']),
            every_encounter_has_admission_summary=len(result['admission_clinical_summary'])==len(encounters),
            admission_inference_creates_duplicate_event=all(not a.get('admission_event_id') or a['admission_event_id'] in {e['event_id'] for e in result['canonical_clinical_events']} for a in result['admission_clinical_summary']) and not any(
                e.get('created_from')=='DETERMINISTIC_EVENT_CANDIDATE_WITH_ADMISSION_DATE_EVIDENCE' and (e['event_type'],e.get('event_date'),e.get('side'),e.get('territory'),e.get('location')) in before_signatures for e in result['canonical_clinical_events']),
            admission_date_assigned_to_unrelated_historical_event=all(e.get('event_date')==before_dates[e['event_id']] for e in result['canonical_clinical_events'] if e['event_id'] in before_dates)
                and all(not e.get('historical') and any(d.get('explicit_positive') for d in e.get('date_evidence',[])) for e in result['canonical_clinical_events'] if e.get('date_source')=='ADMISSION_DATE_IF_EVENT_IS_CLEARLY_THE_ADMISSION_EVENT'))
        execution_evidence = dict(assembler='caa_pdf_extractor.extraction.diagnosis_events_v4.assemble',
            assertion_guard='caa_pdf_extractor.extraction.assertion_scope.validate_assertion',
            assertion_engine_version=assertion_version,
            admission_source_method='LLM_SUMMARY_INFERENCE',local_admission_results_used=len(model_results),
            local_admission_model_digests=sorted({r['model_digest'] for r in model_results.values()}),
            guarded_assertion_count=len(result['diagnostic_assertion_timeline']))
        artifacts = {}
        for name, rows in result.items():
            artifacts[name + '.json'] = json.dumps(rows, ensure_ascii=False, indent=2)
            if isinstance(rows, list):
                artifacts[name + '.csv'] = csv_text(rows)
        version = dx.VERSION
    else:
        from .extraction.medication_identity_v3 import Resolver, COMMON_BRANDS, VERSION, assemble
        with (source / 'medications.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        # Same saved CSV decoding convention as the existing V3 runner.
        for row in rows:
            for key, value in row.items():
                if value and value[0] in '[{':
                    try:
                        row[key] = json.loads(value)
                    except ValueError:
                        pass
        for row in rows:
            if str(row.get('patient_id')) != patient:
                raise ValueError('Medication source row patient mismatch')
        from .extraction.medication_inputs import source_rows
        # Re-select from immutable saved prepared sections, not the legacy low-recall CSV.
        encounters = {str(e['source_document_id']): e for e in read(source / 'encounters.json')}
        rows=source_rows(bundle,encounters)
        if any(str(row['patient_id'])!=patient for row in rows):
            raise ValueError('Selected medication source row patient mismatch')
        resolver = Resolver()
        encounters = {str(e['source_document_id']): e for e in read(source / 'encounters.json')}
        mentions, canonical, exposures = assemble(patient, rows, resolver, encounters)
        gates = dict(source_mentions_and_exposures_retained=len(mentions) == len(exposures) == len(rows),
                     medication_source_row_silently_dropped=len(mentions)==len(rows),
                     common_brand_regressions_pass=all(resolver.resolve(b + ' 10 mg 1 0 1')['canonical_drug'] is not None for b in COMMON_BRANDS),
                     unique_drug_identities=len({r['drug_identity_id'] for r in canonical}) == len(canonical))
        artifacts = {}
        for name, records in [('medication_mentions', mentions), ('canonical_medications', canonical), ('medication_exposures', exposures)]:
            artifacts[name + '.json'] = json.dumps(records, ensure_ascii=False, indent=2)
            artifacts[name + '.csv'] = csv_text(records)
        artifacts['medication_source_rows.json']=json.dumps(rows,ensure_ascii=False,indent=2)
        artifacts['medication_source_rows.csv']=csv_text(rows)
        artifacts['medication_metrics.json']=json.dumps(dict(source_rows_detected=len(rows),
            extracted_mentions=len(mentions),resolved=sum(bool(m.get('canonical_drug')) for m in mentions),
            unresolved=sum(not m.get('canonical_drug') for m in mentions),
            normalization_resolution=sum(bool(m.get('canonical_drug')) for m in mentions)/len(mentions) if mentions else None,
            source_recall=None,source_recall_basis='Requires independent source inventory; not inferred from detected rows'),indent=2)
        version = VERSION
        execution_evidence = dict(assembler='caa_pdf_extractor.extraction.medication_identity_v3.assemble',
            source_selector='caa_pdf_extractor.extraction.medication_inputs.source_rows',source_rows=len(rows))
    return dict(artifacts=artifacts, module_version=version, hard_gates=gates, fatal_errors=[],
                patients_processed=[patient], modules_executed=[module], execution_evidence=execution_evidence,
                network_calls=0, database_writes=0)


def main():
    sys.dont_write_bytecode = True
    request_path = Path(sys.argv[1]).resolve()
    request = read(request_path)
    guard(request_path.parent)
    try:
        result = execute(request)
    except Exception as exc:
        result = dict(artifacts={}, hard_gates={}, fatal_errors=[f'{type(exc).__name__}: {exc}'],
                      patients_processed=[request.get('patient_id')], modules_executed=[request.get('module')])
    with (request_path.parent / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
