"""Instrument-based examination reports with inference-free verified commits."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from pydantic import ValidationError
from sqlalchemy import select, text, null
from ..database import SessionLocal
from ..database.models import Examination, ExtractionRun
from ..llm.backend import get_client
from ..llm.examination_prompts import PROMPT_VERSION, examination_prompt
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT, run_debug_record
from .examination_inputs import select_examination_inputs,select_pet_inputs
from .examination_validators import validate_examinations, record_key, source_response
from .examination_artifacts import save_artifact, load_artifact, verify_sources
from .examination_identity import plan_reconciliation, ExaminationIdentityError
from .radiology_artifacts import artifact_directory, VerifiedExtractionError
from .validators import parse_json


def save_debug(patient_id, summary, records, normalized, root):
    output = {**summary, 'runs': records, 'normalized_output': normalized,
              'granite_response': [r['granite_response'] for r in records]}
    prefix='pet_examinations' if summary.get('scope')=='PET_ONLY' else 'examinations'
    path = Path(root or DEBUG_ROOT) / patient_id / f'{prefix}_{PROMPT_VERSION}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def commit_verified(patient_id, inputs, factory, client, root, summary, *, pet_only=False):
    records, normalized = [], []
    try:
        artifact, path = load_artifact(patient_id, inputs, client, root, scope='PET_ONLY' if pet_only else 'ALL')
        with factory.begin() as session:
            case_id = resolve_case_id(patient_id, session=session)
            summary.update(case_id=case_id, resolved_case_id=case_id)
            session.execute(text('SELECT pg_advisory_xact_lock(:case_id)'), {'case_id': case_id})
            normalized, sources = verify_sources(artifact, inputs, session, case_id)
            existing_rows = list(session.scalars(select(Examination).where(
                Examination.case_id == case_id, Examination.deleted.is_(False))))
            previous_commits = list(session.scalars(select(ExtractionRun).where(
                ExtractionRun.prompt_version.like('examinations_%'), ExtractionRun.status == 'SUCCESS',
                ExtractionRun.raw_output['resolved_case_id'].astext == str(case_id))))
            plan, collisions = plan_reconciliation(normalized, existing_rows, previous_commits)
            inserted = skipped = 0
            inserted_sources = {}
            for event in normalized:
                item_inserted = item_skipped = 0
                matched_ids, inserted_ids = [], []
                for record in event['records']:
                    existing_row, reuse_reason = plan[event['examination_id']]
                    from .examination_identity import source_identity
                    source_key=source_identity(event)
                    existing_row=existing_row or inserted_sources.get(source_key)
                    if existing_row is not None:
                        if record.get('neck_ultrasound_summary') is not None and existing_row.neck_ultrasound_summary!=record['neck_ultrasound_summary']:
                            raise ExaminationIdentityError([{'examination_id':event['examination_id'],'resolution':'Existing neck summary differs; explicit reviewed update required'}])
                        skipped += 1; item_skipped += 1
                        matched_ids.append(existing_row.id)
                        continue
                    row = Examination(case_id=case_id, type=record['type'] or null(),
                        location=record['location'] or null(), result=record['result'],
                        neck_vessel_result=record['neck_vessel_result'] or null(), deleted=False,
                        amyloid_pet_result=record.get('amyloid_pet_result'),fdg_pet_result=record.get('fdg_pet_result'),
                        fdg_pet_pattern=record.get('fdg_pet_pattern'),
                        neck_ultrasound_summary=record.get('neck_ultrasound_summary'),
                        date_examination=date.fromisoformat(record['date_examination']) if record['date_examination'] else null())
                    session.add(row); session.flush()
                    inserted_sources[source_key]=row
                    inserted_ids.append(row.id); inserted += 1; item_inserted += 1
                source = sources[event['examination_id']]
                entry = next(e for e in artifact['inputs'] if e['context']['examination_id'] == event['examination_id'])
                run = ExtractionRun(source_document_id=event['source_document_id'], model_name=artifact['model_name'],
                    model_version=artifact['model_version'], prompt_version=PROMPT_VERSION, status='SUCCESS',
                    completed_at=datetime.now(timezone.utc), raw_output={
                        'patient_id': patient_id, 'dry_run': False, 'extraction_type': 'examinations',
                        'operation': 'COMMIT_VERIFIED_EXTRACTION', 'inference_performed': False,
                        'verified_artifact': str(path), 'artifact_sha256': artifact['artifact_sha256'],
                        'source_fingerprint': entry['source_fingerprint'], 'source_run_id': source.id,
                        'examination': entry['context'], 'response': source.raw_output['response'],
                        'normalized_output': event, 'resolved_case_id': case_id,
                        'patient_validation_status': 'SUCCESS', 'inserted': item_inserted, 'duplicates_skipped': item_skipped,
                        'matched_examination_ids': matched_ids, 'inserted_examination_ids': inserted_ids,
                        'reuse_reason': reuse_reason if matched_ids else None, 'existing_result_preserved': bool(matched_ids)})
                session.add(run); session.flush()
                records.append(run_debug_record(run))
        summary.update(status='SUCCESS', validation_status='VERIFIED', verified_artifact_used=True,
                       verified_artifact=str(path), new_rows_inserted=inserted, duplicates_skipped=skipped,
                       extracted_records=sum(len(e['records']) for e in normalized), model=artifact['model_name'],
                       identity_collisions=collisions, unresolved_identity_ambiguities=[])
    except ExaminationIdentityError as exc:
        summary.update(status=exc.status, validation_status='VERIFIED',
                       unresolved_identity_ambiguities=exc.details,
                       next_step='Review study/procedure identity; no clinical rows were inserted or updated.')
        records = []
    except (VerifiedExtractionError, CaseResolutionError) as exc:
        summary.update(status=exc.status, validation_status=exc.status, next_step='Run --dry-run and review the output first.')
        records, normalized = [], []
    except Exception as exc:
        summary.update(status='FAILED_INSERT', validation_status='FAILED_INSERT', error_type=type(exc).__name__)
        records, normalized = [], []
    save_debug(patient_id, summary, records, normalized, root)
    return summary


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None,pet_only=False):
    artifact_directory(patient_id, debug_root)  # Validate path before any operation.
    factory = session_factory or SessionLocal
    # Results and PET classifications are source-derived. Default operation
    # needs neither a model runtime nor network/model metadata requests.
    client = client or SimpleNamespace(model_name='deterministic-examinations',
        model_version=PROMPT_VERSION,runtime='local-deterministic')
    bundle=build_patient_bundle(patient_id, factory)
    inputs = select_pet_inputs(bundle) if pet_only else select_examination_inputs(bundle)
    summary = {'patient_id': patient_id, 'case_id': None, 'resolved_case_id': None,
               'selected_sections': sorted({i.section_id for i in inputs}), 'selected_examinations': len(inputs),
               'extracted_records': 0, 'new_rows_inserted': 0, 'duplicates_skipped': 0,
               'dry_run': dry_run, 'prompt_version': PROMPT_VERSION,
               'verified_artifact_used': False, 'granite_called': False,'scope':'PET_ONLY' if pet_only else 'ALL'}
    if not dry_run: return commit_verified(patient_id, inputs, factory, client, debug_root, summary,pet_only=pet_only)
    try:
        with factory.begin() as s: case_id = resolve_case_id(patient_id, session=s)
        summary.update(case_id=case_id, resolved_case_id=case_id)
    except CaseResolutionError as exc:
        summary.update(status=exc.status, validation_status=exc.status)
        save_debug(patient_id, summary, [], [], debug_root)
        return summary
    client = client or get_client()
    run_ids, normalized = [], []
    failure = None
    for item in inputs:
        with factory.begin() as session:
            run = ExtractionRun(source_document_id=item.source_document_id, model_name=client.model_name,
                model_version=client.model_version, prompt_version=PROMPT_VERSION, status='RUNNING',
                raw_output={'patient_id': patient_id, 'dry_run': True, 'examination': item.context()})
            session.add(run); session.flush(); run_id = run.id
        raw = event = errors = None
        status = 'SUCCESS'
        try:
            raw = source_response(item)
        except Exception as exc: status, errors = 'FAILED_MODEL', [{'type': type(exc).__name__}]
        if status == 'SUCCESS':
            try: parse_json(raw)
            except (ValueError, TypeError): status, errors = 'FAILED_PARSE', [{'type': 'invalid_json'}]
        if status == 'SUCCESS':
            try:
                event = {**validate_examinations(raw, item), 'case_id': case_id}
                normalized.append(event)
            except (ValueError, ValidationError) as exc:
                status = 'FAILED_VALIDATION'
                errors = ([{'type': e['type'], 'loc': list(e['loc'])} for e in exc.errors()]
                          if isinstance(exc, ValidationError) else [{'type': str(exc)}])
        with factory.begin() as session:
            run = session.get(ExtractionRun, run_id)
            run.status = status; run.model_version = client.model_version
            run.completed_at = datetime.now(timezone.utc); run.validation_errors = errors
            run.raw_output = {'patient_id': patient_id, 'dry_run': True, 'extraction_type': 'examinations',
                              'examination': item.context(), 'response': raw, 'normalized_output': event,
                              'resolved_case_id': case_id, 'inference_performed':False,
                              'result_method':'exact_source_subreport_v1'}
        run_ids.append(run_id)
        if status != 'SUCCESS': failure = status
    summary.update(status=failure or ('SUCCESS' if inputs else 'NO_INPUT'),
                   validation_status=failure or ('SUCCESS' if inputs else 'NO_INPUT'), model=client.model_name,
                   extracted_records=sum(len(e['records']) for e in normalized) if failure is None else 0)
    records = []
    with factory.begin() as session:
        for run_id in run_ids:
            run = session.get(ExtractionRun, run_id)
            run.raw_output = {**run.raw_output, 'patient_validation_status': summary['validation_status']}
            records.append(run_debug_record(run))
    if summary['status'] == 'SUCCESS':
        path = save_artifact(patient_id, case_id, inputs, records, client, debug_root,scope='PET_ONLY' if pet_only else 'ALL')
        summary.update(verified_artifact=str(path), artifact_validation_status='VERIFIED')
    save_debug(patient_id, summary, records, normalized, debug_root)
    return summary


def extract_pet_patient(patient_id,**kwargs):
    return extract_patient(patient_id,pet_only=True,**kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--commit', action='store_true')
    parser.add_argument('--pet-only',action='store_true',help='Select only PET studies; do not rerun ultrasound or other examinations')
    args = parser.parse_args(argv)
    try: result = extract_patient(args.patient, dry_run=not args.commit,pet_only=args.pet_only)
    except Exception as exc:
        print(json.dumps({'patient_id': args.patient, 'status': 'FAILED', 'error_type': type(exc).__name__})); return 1
    print(json.dumps(result, ensure_ascii=True))
    return int(result['status'] not in ('SUCCESS','NO_INPUT'))


if __name__ == '__main__': raise SystemExit(main())
