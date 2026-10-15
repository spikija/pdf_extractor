"""case_v1: verified patient PATCH workflow; commit performs no inference."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from ..database import SessionLocal
from ..database.models import Case, ExtractionRun, FamilyHistory
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .case_rules import (VERSION, FIELDS, select_inputs, extract, fingerprint,
                         conflict_check, suspicious_defaults)
from .radiology_artifacts import artifact_directory, digest, VerifiedExtractionError


def serial(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def read_case(session, patient_id):
    case_id = resolve_case_id(patient_id, session=session)
    row = session.scalar(select(Case).where(Case.id == case_id).with_for_update())
    if row is None or row.deleted:
        raise CaseResolutionError('CASE_NOT_FOUND')
    return row, {f: serial(getattr(row, f)) for f in FIELDS}


def save_artifact(patient_id, case_id, output, source_fingerprint, run_id, root):
    data = {'artifact_version': 1, 'extraction_type': VERSION, 'patient_id': patient_id,
            'case_id': case_id, 'validation_status': 'VERIFIED',
            'created_at': datetime.now(timezone.utc).isoformat(), 'rule_version': VERSION,
            'prompt_version': VERSION, 'model_name': 'deterministic', 'model_version': VERSION,
            'config': {'unknown': 'omit', 'placeholder_policy': 'review', 'granite': False},
            'source_fingerprint': source_fingerprint, 'source_run_id': run_id,
            **output}
    data['artifact_sha256'] = digest(data)
    folder = artifact_directory(patient_id, root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'case_v1_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}_{uuid4().hex}.json'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return path


def load_artifact(patient_id, case_id, source_fingerprint, output, session, root, source_document_id):
    files = sorted(artifact_directory(patient_id, root).glob('case_v1_*.json'))
    if not files:
        raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        checksum = data.pop('artifact_sha256')
        if digest(data) != checksum:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        data['artifact_sha256'] = checksum
        if (data['patient_id'] != patient_id or data['case_id'] != case_id
                or data['source_fingerprint'] != source_fingerprint
                or data['rule_version'] != VERSION or data['prompt_version'] != VERSION
                or data['model_name'] != 'deterministic' or data['model_version'] != VERSION
                or data['config'] != {'unknown': 'omit', 'placeholder_policy': 'review', 'granite': False}):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        if (data['validation_status'] != 'VERIFIED' or data['artifact_version'] != 1
                or data['extraction_type'] != VERSION or output['source_conflicts']
                or any(data.get(key) != value for key, value in output.items())):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        run = session.get(ExtractionRun, data['source_run_id'])
        if (run is None or run.source_document_id != source_document_id
                or run.status != 'SUCCESS' or run.prompt_version != VERSION
                or run.model_name != 'deterministic' or run.model_version != VERSION
                or run.raw_output != {'patient_id': patient_id, 'case_id': case_id, 'dry_run': True,
                    'extraction_type': VERSION, 'source_fingerprint': source_fingerprint,
                    'normalized_output': output}):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        return data, path
    except VerifiedExtractionError:
        raise
    except (OSError, KeyError, TypeError, ValueError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    # client intentionally unused: explicit deterministic facts need no semantic model.
    factory = session_factory or SessionLocal
    summary = {'patient_id': patient_id, 'case_id': None, 'dry_run': dry_run,
               'granite_called': False, 'fields_changed': 0, 'validation_status': 'FAILED'}
    try:
        artifact_directory(patient_id, debug_root)
        with factory.begin() as session:
            row, current = read_case(session, patient_id)
            case_id = row.id
        bundle = build_patient_bundle(patient_id, factory)
        inputs = select_inputs(bundle)
        output = extract(inputs)
        source_fingerprint = digest({'rules': fingerprint(bundle, inputs),
            'workflow': Path(__file__).read_text(encoding='utf-8')})
        with factory.begin() as session:
            row, current = read_case(session, patient_id)
            if row.id != case_id:
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
            updates, unchanged, conflicts = conflict_check(current, output['update_fields'])
            active_family = session.scalar(select(FamilyHistory.id).where(
                FamilyHistory.case_id == case_id, FamilyHistory.deleted.is_(False)).limit(1))
            summary.update(case_id=case_id, current_values={f: current[f] for f in output['update_fields']},
                proposed_update_fields=output['update_fields'], unchanged_fields=unchanged,
                conflicts=output['source_conflicts'] + conflicts,
                omitted_fields=[f for f in FIELDS if f not in output['update_fields']],
                suspicious_legacy_defaults=suspicious_defaults(current, serial(row.entry_stamp.date()) if row.entry_stamp else None),
                family_history_sync={'safe_to_set_true_later': active_family is not None,
                                     'safe_to_set_false': False, 'current': row.family_history},
                selected_passages=len(inputs))
            if dry_run:
                if not bundle.documents:
                    raise VerifiedExtractionError('NO_SOURCE_DOCUMENTS')
                run = ExtractionRun(source_document_id=bundle.documents[0].source_document_id,
                    model_name='deterministic', model_version=VERSION, prompt_version=VERSION,
                    status='SUCCESS' if not output['source_conflicts'] else 'FAILED_VALIDATION',
                    completed_at=datetime.now(timezone.utc),
                    raw_output={'patient_id': patient_id, 'case_id': case_id, 'dry_run': True,
                        'extraction_type': VERSION, 'source_fingerprint': source_fingerprint,
                        'normalized_output': output})
                session.add(run)
                session.flush()
                run_id = run.id
            else:
                artifact, path = load_artifact(patient_id, case_id, source_fingerprint, output, session,
                                              debug_root, bundle.documents[0].source_document_id)
                if conflicts or output['source_conflicts']:
                    raise VerifiedExtractionError('FIELD_CONFLICT')
                for field, value in updates.items():
                    setattr(row, field, date.fromisoformat(value) if field.endswith('_date') or field == 'date_of_birth' else value)
                session.add(ExtractionRun(source_document_id=bundle.documents[0].source_document_id,
                    model_name='deterministic', model_version=VERSION, prompt_version=VERSION,
                    status='SUCCESS', completed_at=datetime.now(timezone.utc),
                    raw_output={'patient_id': patient_id, 'case_id': case_id, 'dry_run': False,
                        'extraction_type': VERSION, 'operation': 'COMMIT_VERIFIED_EXTRACTION',
                        'artifact_sha256': artifact['artifact_sha256'], 'inference_performed': False,
                        'changed_fields': sorted(updates)}))
                summary.update(verified_artifact=str(path), verified_artifact_used=True)
        if dry_run and not output['source_conflicts']:
            path = save_artifact(patient_id, case_id, output, source_fingerprint, run_id, debug_root)
            summary['verified_artifact'] = str(path)
            summary['artifact_validation_status'] = 'VERIFIED'
        summary.update(status='FIELD_CONFLICT' if summary['conflicts'] else 'SUCCESS',
                       validation_status='REVIEW_REQUIRED' if summary['conflicts'] else 'VERIFIED',
                       fields_changed=0 if dry_run else len(updates))
    except (CaseResolutionError, VerifiedExtractionError) as exc:
        summary.update(status=exc.status, validation_status=exc.status)
    except Exception as exc:
        summary.update(status='FAILED', validation_status='FAILED', error_type=type(exc).__name__)
    # No full patient text or narrative evidence is printed. Evidence is in the artifact.
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    result = extract_patient(args.patient, dry_run=not args.commit)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['status'] == 'SUCCESS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
