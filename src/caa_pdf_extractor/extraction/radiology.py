"""MRI phenotype extraction; CT/RTG are audited without clinical inserts."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, text, null

from ..database import SessionLocal
from ..database.models import ExtractionRun, Radiology
from ..llm.backend import get_client
from ..llm.radiology_prompts import PROMPT_VERSION, radiology_prompt
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT, run_debug_record
from .radiology_inputs import procedure_context, report_text
from .radiology_models import RadiologyExtraction
from .radiology_validators import validate_radiology, validate_independent_radiology
from .validators import parse_json
from .radiology_artifacts import (create_artifact, load_latest_artifact,
                                 verify_audit_anchor, VerifiedExtractionError)


def _save_debug(patient_id, summary, records, normalized, debug_root):
    output = {**summary, 'procedures_used': [r['procedure'] for r in records],
              'granite_response': [r['granite_response'] for r in records if r['status'] != 'SKIPPED_NON_MRI'],
              'normalized_output': normalized, 'runs': records}
    path = Path(debug_root or DEBUG_ROOT) / patient_id / f'radiology_{PROMPT_VERSION}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _commit_verified(patient_id, procedures, factory, client, debug_root):
    counts = Counter(p.modality for p in procedures)
    summary = {'patient_id': patient_id, 'case_id': None, 'resolved_case_id': None,
               'diagnostic_procedures_selected': len(procedures), 'rtg_count': counts['RTG'],
               'ct_count': counts['CT'], 'mri_count': counts['MRI'], 'dry_run': False,
               'prompt_version': PROMPT_VERSION, 'radiology_records_validated': 0,
               'new_rows_inserted': 0, 'duplicates_skipped': 0,
               'verified_artifact_used': False, 'granite_called': False}
    records, normalized = [], []
    try:
        artifact, path = load_latest_artifact(patient_id, procedures, client=client, debug_root=debug_root)
        with factory.begin() as session:
            case_id = resolve_case_id(patient_id, session=session)
            summary.update(case_id=case_id, resolved_case_id=case_id)
            session.execute(text('SELECT pg_advisory_xact_lock(:case_id)'), {'case_id': case_id})
            normalized, sources = verify_audit_anchor(artifact, procedures, session, case_id)
            existing_rows = list(session.scalars(
                select(Radiology).where(Radiology.case_id == case_id, Radiology.deleted.is_(False))))
            existing = {(r.mri_date, r.type, r.acute_lesion) for r in existing_rows}
            existing_counts = Counter((r.mri_date, r.type, r.acute_lesion) for r in existing_rows)
            by_id = {e['procedure']['procedure_id']: e for e in artifact['procedures']}
            # Plan the entire source set before writes. Database identity alone
            # cannot prove that two distinct reports describe the same study.
            intended = {}
            for p in procedures:
                if p.modality != 'MRI': continue
                event = by_id[p.procedure_id]['normalized_output']
                for finding in event['findings']:
                    identity = (event['mri_date'], finding['type'], finding['acute_lesion'])
                    db_identity = (date.fromisoformat(identity[0]) if identity[0] else None, *identity[1:])
                    if existing_counts[db_identity] > 1:
                        raise VerifiedExtractionError('DUPLICATE_IDENTITY_AMBIGUOUS')
                    owner = (p.source_document_id, p.procedure_id)
                    if identity in intended and intended[identity] != owner:
                        raise VerifiedExtractionError('DUPLICATE_IDENTITY_AMBIGUOUS')
                    intended[identity] = owner
            inserted, skipped = 0, 0
            for p in procedures:
                entry = by_id[p.procedure_id]
                event = entry['normalized_output']
                event_inserted, event_skipped = 0, 0
                if p.modality == 'MRI':
                    d = date.fromisoformat(event['mri_date']) if event['mri_date'] else None
                    for finding in event['findings']:
                        key = (d, finding['type'], finding['acute_lesion'])
                        if key in existing:
                            skipped += 1
                            event_skipped += 1
                            continue
                        session.add(Radiology(case_id=case_id, pid=patient_id, mri_date=d or null(),
                                              type=finding['type'],
                                              acute_lesion=finding['acute_lesion'] if finding['acute_lesion'] is not None else null(),
                                              deleted=False, saved_on_hdd=False))
                        existing.add(key)
                        inserted += 1
                        event_inserted += 1
                source = sources[p.procedure_id]
                run = ExtractionRun(source_document_id=p.source_document_id, model_name=entry['model_name'],
                                    model_version=entry['model_version'], prompt_version=PROMPT_VERSION,
                                    status='SUCCESS' if p.modality == 'MRI' else 'SKIPPED_NON_MRI',
                                    completed_at=datetime.now(timezone.utc),
                                    raw_output={'patient_id': patient_id, 'dry_run': False,
                                        'operation': 'COMMIT_VERIFIED_EXTRACTION', 'inference_performed': False,
                                        'verified_artifact': str(path), 'artifact_sha256': artifact['artifact_sha256'],
                                        'source_fingerprint': entry['source_fingerprint'],
                                        'source_run_id': source.id, 'procedure': entry['procedure'],
                                        'response': source.raw_output['response'], 'normalized_output': event,
                                        'resolved_case_id': case_id, 'patient_validation_status': 'SUCCESS',
                                        'insertion_status': 'SUCCESS', 'inserted': event_inserted,
                                        'duplicates_skipped': event_skipped})
                session.add(run)
                session.flush()
                records.append(run_debug_record(run))
        summary.update(status='SUCCESS', validation_status=artifact['validation_status'], verified_artifact_used=True,
                       verified_artifact=str(path), artifact_sha256=artifact['artifact_sha256'],
                       model=artifact['model_name'], model_version=artifact['model_version'],
                       runtime=artifact['runtime'], new_rows_inserted=inserted, duplicates_skipped=skipped,
                       radiology_records_validated=sum(len(e['findings']) for e in normalized))
    except (VerifiedExtractionError, CaseResolutionError) as exc:
        summary.update(status=exc.status, validation_status=exc.status,
                       next_step='Run --dry-run and review a successful extraction before --commit.')
        records, normalized = [], []
    except Exception as exc:
        summary.update(status='FAILED_INSERT', validation_status='FAILED_INSERT', error_type=type(exc).__name__)
        records, normalized = [], []
    _save_debug(patient_id, summary, records, normalized, debug_root)
    return summary


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    if not isinstance(patient_id, str) or patient_id in ('', '.', '..') or any(c in patient_id for c in '/\\:<>"|?*'):
        raise ValueError('Invalid patient identifier for debug export')
    factory = session_factory or SessionLocal
    bundle = build_patient_bundle(patient_id, factory)
    procedures = [p for d in bundle.documents for p in d.diagnostic_procedures]
    if len({p.procedure_id for p in procedures}) != len(procedures):
        raise ValueError('Duplicate prepared procedure identity')
    if not dry_run:
        # No backend factory or inference is reached by commit, even on failure.
        return _commit_verified(patient_id, procedures, factory, client, debug_root)
    client = client or get_client()
    runs, normalized = [], []
    failure = None
    for procedure in procedures:
        context = procedure_context(procedure)
        with factory.begin() as session:
            run = ExtractionRun(source_document_id=procedure.source_document_id,
                                model_name=client.model_name, model_version=client.model_version,
                                prompt_version=PROMPT_VERSION, status='RUNNING',
                                raw_output={'patient_id': patient_id, 'dry_run': dry_run, 'procedure': context})
            session.add(run)
            session.flush()
            run_id = run.id
        raw, errors, event = None, None, None
        status = 'SKIPPED_NON_MRI' if procedure.modality != 'MRI' else 'SUCCESS'
        if status == 'SUCCESS':
            try:
                prompt = radiology_prompt(procedure, RadiologyExtraction.model_json_schema())
                if len(prompt) > 60000:
                    raise ValueError('Selected MRI input exceeds configured context budget')
                raw = client.generate_json(prompt)
            except Exception as exc:
                status, errors = 'FAILED_MODEL', [{'type': type(exc).__name__}]
            if status == 'SUCCESS':
                try:
                    parse_json(raw)
                except (ValueError, TypeError):
                    status, errors = 'FAILED_PARSE', [{'type': 'invalid_json'}]
            if status == 'SUCCESS':
                try:
                    event = validate_independent_radiology(raw, patient_id, procedure)
                    status = event['validation_status']
                    errors = [{'type':r['rejection_reason'],'field':r['field']} for r in event['rejected_records']] or None
                    normalized.append(event)
                except (ValueError, ValidationError) as exc:
                    status = 'FAILED_VALIDATION'
                    errors = ([{'type': e['type'], 'loc': list(e['loc'])} for e in exc.errors()]
                              if isinstance(exc, ValidationError) else [{'type': str(exc)}])
        with factory.begin() as session:
            run = session.get(ExtractionRun, run_id)
            run.status = status
            run.model_version = client.model_version
            run.completed_at = datetime.now(timezone.utc)
            run.validation_errors = errors
            run.raw_output = {'patient_id': patient_id, 'dry_run': dry_run, 'procedure': context,
                              'response': raw, 'normalized_output': event,
                              'prepared_result_characters': len(procedure.result_text),
                              'input_report_characters': len(report_text(procedure)) if procedure.modality == 'MRI' else 0,
                              'selection_reason': 'MRI_PHENOTYPE' if procedure.modality == 'MRI' else 'NON_MRI_AUDIT_ONLY'}
        runs.append(run_id)
        if status.startswith('FAILED'):
            failure = status
    case_id, insertion_status = None, None
    inserted, skipped = 0, 0
    per_event = {p.procedure_id: {'inserted': 0, 'duplicates_skipped': 0} for p in procedures}
    if procedures:
        try:
            with factory.begin() as session:
                case_id = resolve_case_id(patient_id, session=session)
        except CaseResolutionError as exc:
            insertion_status = exc.status
        except Exception as exc:
            # Finalize independent inference audits if case lookup fails.
            insertion_status = 'FAILED_INSERT'
            inserted = skipped = 0
            per_event = {p.procedure_id: {'inserted': 0, 'duplicates_skipped': 0} for p in procedures}
            insertion_error = type(exc).__name__
    if len(normalized) == sum(p.modality=='MRI' for p in procedures):
        rejected = any(e.get('rejected_records') for e in normalized)
        accepted = any(e.get('validation_status') in ('SUCCESS','PARTIAL_VALID') for e in normalized)
        failure = ('PARTIAL_VALID' if accepted else 'FAILED_VALIDATION') if rejected else None
    counts = Counter(p.modality for p in procedures)
    summary = {'patient_id': patient_id, 'case_id': case_id, 'resolved_case_id': case_id,
               'diagnostic_procedures_selected': len(procedures),
               'rtg_count': counts['RTG'], 'ct_count': counts['CT'], 'mri_count': counts['MRI'],
               'radiology_records_validated': sum(len(e['findings']) for e in normalized) if failure in (None,'PARTIAL_VALID') else 0,
               'validation_status': insertion_status or failure or ('SUCCESS' if counts['MRI'] else 'NO_MRI_INPUT'),
               'status': insertion_status or failure or ('SUCCESS' if procedures else 'NO_INPUT'),
               'new_rows_inserted': inserted, 'duplicates_skipped': skipped, 'dry_run': dry_run,
               'prompt_version': PROMPT_VERSION, 'model': client.model_name, 'runtime': client.runtime}
    records = []
    with factory.begin() as session:
        for run_id in runs:
            run = session.get(ExtractionRun, run_id)
            output = dict(run.raw_output)
            output.update({'resolved_case_id': case_id, 'patient_validation_status': summary['validation_status'],
                           'insertion_status': summary['status'], **per_event[output['procedure']['procedure_id']]})
            if insertion_status == 'FAILED_INSERT':
                output['insertion_error_type'] = insertion_error
            if output['normalized_output'] is not None:
                output['normalized_output'] = {**output['normalized_output'], 'case_id': case_id}
            run.raw_output = output
            records.append(run_debug_record(run))
    normalized = [{**event, 'case_id': case_id} for event in normalized]
    if summary['status'] in ('SUCCESS','PARTIAL_VALID') and counts['MRI']:
        path = create_artifact(patient_id, case_id, procedures, records,
                               model_name=client.model_name, model_version=client.model_version,
                               runtime=client.runtime, debug_root=debug_root)
        summary.update(verified_artifact=str(path), artifact_validation_status='PARTIAL_VALID' if summary['status']=='PARTIAL_VALID' else ('VERIFIED' if summary['radiology_records_validated'] else 'VERIFIED_EMPTY'))
    _save_debug(patient_id, summary, records, normalized, debug_root)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--commit', '--write', dest='commit', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = extract_patient(args.patient, dry_run=not args.commit)
    except Exception as exc:
        print(json.dumps({'patient_id': args.patient, 'status': 'FAILED', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return int(result['status'] not in ('SUCCESS', 'NO_INPUT'))


if __name__ == '__main__':
    raise SystemExit(main())
