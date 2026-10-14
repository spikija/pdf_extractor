"""Focused laboratory, exact evidence and inference-free verified commits."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from sqlalchemy import select, text, null
from ..database import SessionLocal
from ..database.models import Laboratory, ExtractionRun
from ..preparation import build_patient_bundle
from ..llm.backend import get_client
from ..llm.laboratory_prompts import PROMPT_VERSION, laboratory_prompt
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT, run_debug_record
from .laboratory_inputs import select_laboratory_inputs, source_manifest
from .laboratory_models import validate_laboratory, signature, FIELDS
from .laboratory_artifacts import save_artifact, load_artifact, verify_sources, fingerprint
from .radiology_artifacts import artifact_directory, VerifiedExtractionError
from .validators import parse_json


class LaboratoryPersistenceError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('PERSISTENCE_BLOCKED')


def save_debug(patient_id, summary, records, normalized, root):
    path = Path(root or DEBUG_ROOT) / patient_id / f'laboratory_{PROMPT_VERSION}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**summary, 'runs': records, 'normalized_output': normalized},
                              ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _extract_text_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    artifact_directory(patient_id, debug_root)
    factory = session_factory or SessionLocal
    summary = {'patient_id': patient_id, 'resolved_case_id': None, 'dry_run': dry_run,
               'prompt_version': PROMPT_VERSION, 'granite_called': False,
               'verified_artifact_used': False, 'new_rows_inserted': 0, 'duplicates_skipped': 0,
               'extracted_records': 0}
    records, normalized = [], None
    try:
        with factory.begin() as session:
            case_id = resolve_case_id(patient_id, session=session)
        summary.update(case_id=case_id, resolved_case_id=case_id)
        bundle = build_patient_bundle(patient_id, factory)
        inputs, manifest = select_laboratory_inputs(bundle), source_manifest(bundle)
        summary['selected_sections'] = sorted({i.section_id for i in inputs})
        summary['selected_passages'] = len(inputs)
        if not dry_run:
            artifact, path = load_artifact(patient_id, inputs, manifest, client, debug_root)
            with factory.begin() as session:
                locked_case_id = resolve_case_id(patient_id, session=session)
                session.execute(text('SELECT pg_advisory_xact_lock(:case_id)'), {'case_id': locked_case_id})
                normalized, source = verify_sources(artifact, patient_id, locked_case_id, inputs, manifest, session)
                rows = list(session.scalars(select(Laboratory).where(
                    Laboratory.case_id == locked_case_id, Laboratory.deleted.is_(False))))
                if not normalized['persistence_allowed']:
                    raise LaboratoryPersistenceError(normalized['persistence_issues'])
                existing = {signature({'date_of_exam': r.date_of_exam.isoformat() if r.date_of_exam else None,
                                       **{name: getattr(r, name) for name in FIELDS}}) for r in rows}
                inserted = skipped = 0
                for record in normalized['laboratory']:
                    key = signature(record)
                    if key in existing:
                        skipped += 1
                        continue
                    session.add(Laboratory(case_id=locked_case_id,
                        date_of_exam=date.fromisoformat(record['date_of_exam']) if record['date_of_exam'] else null(),
                        **{name: Decimal(record[name]) if record[name] is not None else null() for name in FIELDS},
                        deleted=False))
                    existing.add(key)
                    inserted += 1
                run = ExtractionRun(source_document_id=source.source_document_id,
                    model_name=artifact['model_name'], model_version=artifact['model_version'],
                    prompt_version=PROMPT_VERSION, status='SUCCESS', completed_at=datetime.now(timezone.utc),
                    raw_output={'patient_id': patient_id, 'resolved_case_id': locked_case_id,
                        'dry_run': False, 'extraction_type': 'laboratory', 'operation': 'COMMIT_VERIFIED_EXTRACTION',
                        'inference_performed': False, 'verified_artifact': str(path),
                        'artifact_sha256': artifact['artifact_sha256'], 'source_run_id': source.id,
                        'source_fingerprint': artifact['source_fingerprint'], 'normalized_output': normalized,
                        'inserted': inserted, 'duplicates_skipped': skipped})
                session.add(run)
                session.flush()
                records.append(run_debug_record(run))
            summary.update(status='SUCCESS', validation_status='VERIFIED', verified_artifact_used=True,
                verified_artifact=str(path), new_rows_inserted=inserted, duplicates_skipped=skipped,
                extracted_records=len(normalized['laboratory']), model=artifact['model_name'])
        else:
            client = client or get_client()
            prompt = laboratory_prompt(patient_id, inputs)
            if len(prompt) > 60000:
                raise ValueError('Laboratory focused input exceeds context budget')
            source_id = inputs[0].source_document_id if inputs else (bundle.documents[0].source_document_id if bundle.documents else None)
            with factory.begin() as session:
                run = ExtractionRun(source_document_id=source_id, model_name=client.model_name,
                    model_version=client.model_version, prompt_version=PROMPT_VERSION, status='RUNNING',
                    raw_output={'patient_id': patient_id, 'dry_run': True, 'extraction_type': 'laboratory'})
                session.add(run)
                session.flush()
                run_id = run.id
            raw, error, status = None, None, 'SUCCESS'
            try:
                summary['granite_called'] = client.runtime != 'mock'
                raw = client.generate_json(prompt)
            except Exception as exc:
                status, error = 'FAILED_MODEL', type(exc).__name__
            if status == 'SUCCESS':
                try:
                    parse_json(raw)
                except (ValueError, TypeError):
                    status, error = 'FAILED_PARSE', 'invalid_json'
            if status == 'SUCCESS':
                try:
                    normalized = validate_laboratory(raw, patient_id, inputs)
                except ValueError as exc:
                    status, error = 'FAILED_VALIDATION', type(exc).__name__
            with factory.begin() as session:
                run = session.get(ExtractionRun, run_id)
                run.status, run.model_version = status, client.model_version
                run.completed_at = datetime.now(timezone.utc)
                run.validation_errors = [{'type': error}] if error else None
                run.raw_output = {'patient_id': patient_id, 'resolved_case_id': case_id, 'dry_run': True,
                    'extraction_type': 'laboratory', 'selected_passages': [i.context() for i in inputs],
                    'empty_source_document_id': source_id, 'source_manifest': manifest,
                    'source_fingerprint': fingerprint(patient_id, inputs, manifest), 'response': raw,
                    'normalized_output': normalized, 'patient_validation_status': status}
                records.append(run_debug_record(run))
            summary.update(status=status, validation_status=status, model=client.model_name,
                           extracted_records=len(normalized['laboratory']) if normalized else 0)
            if normalized:
                summary.update(persistence_allowed=normalized['persistence_allowed'],
                               persistence_issues=normalized['persistence_issues'])
            if status == 'SUCCESS':
                path = save_artifact(patient_id, case_id, inputs, manifest, run, client, debug_root)
                summary.update(verified_artifact=str(path), artifact_validation_status='VERIFIED')
    except LaboratoryPersistenceError as exc:
        summary.update(status='PERSISTENCE_BLOCKED', validation_status='VERIFIED',
                       persistence_allowed=False, persistence_issues=exc.issues)
        records = []
    except (CaseResolutionError, VerifiedExtractionError) as exc:
        summary.update(status=exc.status, validation_status=exc.status)
    except Exception as exc:
        summary.update(status='FAILED', validation_status='FAILED', error_type=type(exc).__name__)
    save_debug(patient_id, summary, records, normalized, debug_root)
    return summary


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    """Approved geometry-only workflow; no model client is created or used."""
    from .laboratory_layout_workflow import extract_layout_patient
    return extract_layout_patient(patient_id, dry_run=dry_run,
        session_factory=session_factory, debug_root=debug_root)


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
