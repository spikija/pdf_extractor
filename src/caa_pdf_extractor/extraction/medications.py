"""Local medication extraction with strict validation and atomic, idempotent commits."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, text, null

from ..database import SessionLocal
from ..database.models import ExtractionRun, Medication
from ..llm.backend import get_client
from ..llm.medication_prompts import PROMPT_VERSION, medication_prompt
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT, run_debug_record
from .medication_inputs import select_medication_inputs
from .medication_models import MedicationExtraction
from .medication_validators import validate_medications, deduplicate_medications, medication_key
from .validators import parse_json


def _save_debug(patient_id, summary, records, debug_root):
    if patient_id in ("", ".", "..") or any(c in patient_id for c in '/\\:<>"|?*'):
        raise ValueError("Invalid debug export path component")
    output = dict(summary)
    output['runs'] = records
    output['normalized_output'] = [item for run in records for item in run.get('normalized_output', [])]
    path = Path(debug_root or DEBUG_ROOT) / patient_id / f"medications_{PROMPT_VERSION}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    factory = session_factory or SessionLocal
    bundle = build_patient_bundle(patient_id, factory)
    inputs = select_medication_inputs(bundle)
    client = client or get_client()
    items, runs = [], []
    failure = None
    for document in bundle.documents:
        chosen = [item for item in inputs if item.source_document_id == document.source_document_id]
        if not chosen:
            continue
        with factory.begin() as session:
            run = ExtractionRun(source_document_id=document.source_document_id,
                                model_name=client.model_name, model_version=client.model_version,
                                prompt_version=PROMPT_VERSION, status="RUNNING",
                                raw_output={"patient_id": patient_id, "dry_run": dry_run,
                                            "section_ids": [item.section_id for item in chosen]})
            session.add(run)
            session.flush()
            run_id = run.id
        raw, errors = None, None
        status = "SUCCESS"
        try:
            prompt = medication_prompt(patient_id, chosen, MedicationExtraction.model_json_schema())
            if len(prompt) > 60000:
                raise ValueError("Selected input exceeds configured context budget")
            raw = client.generate_json(prompt)
        except Exception as exc:
            status, errors = "FAILED_MODEL", [{"type": type(exc).__name__}]
        if status == "SUCCESS":
            try:
                parse_json(raw)
            except (ValueError, TypeError):
                status, errors = "FAILED_PARSE", [{"type": "invalid_json"}]
        if status == "SUCCESS":
            try:
                items.extend(validate_medications(raw, patient_id, chosen).medications)
            except (ValueError, ValidationError) as exc:
                status = "FAILED_VALIDATION"
                errors = ([{"type": e['type'], "loc": list(e['loc'])} for e in exc.errors()]
                          if isinstance(exc, ValidationError) else [{"type": str(exc)}])
        with factory.begin() as session:
            run = session.get(ExtractionRun, run_id)
            run.status = status
            run.model_version = client.model_version
            run.completed_at = datetime.now(timezone.utc)
            run.validation_errors = errors
            run.raw_output = {"patient_id": patient_id, "dry_run": dry_run, "response": raw,
                              "section_ids": [item.section_id for item in chosen],
                              "table_ids": [{"section_id": item.section_id, "table_index": t['table_index'],
                                             "page_number": t['page_number']} for item in chosen for t in item.tables]}
        runs.append(run_id)
        if status != "SUCCESS":
            failure = status
    items = deduplicate_medications(items) if failure is None else []
    inserted, skipped = 0, 0
    case_id, matched_fallnr = None, None
    insertion_status = None
    if failure is None and inputs:
        with factory.begin() as session:
            try:
                case_id = resolve_case_id(patient_id, session=session)
            except CaseResolutionError as exc:
                insertion_status = exc.status
            else:
                matched_fallnr = patient_id
                if not dry_run:
                    session.execute(text("SELECT pg_advisory_xact_lock(:case_id)"), {"case_id": case_id})
                    existing = {medication_key(row) for row in session.scalars(
                        select(Medication).where(Medication.case_id == case_id))}
                    for item in items:
                        key = medication_key(item)
                        if key in existing:
                            skipped += 1
                            continue
                        session.add(Medication(case_id=case_id, medication=item.medication,
                                               date_noted=item.date_noted or null(),
                                               date_stopped=item.date_stopped or null(),
                                               medication_category=item.medication_category or null(),
                                               deleted=False))
                        existing.add(key)
                        inserted += 1
    summary = {"patient_id": patient_id, "case_id": case_id, "matched_fallnr": matched_fallnr,
               "sections_used": len(inputs), "section_ids": [item.section_id for item in inputs],
               "tables_used": sum(len(item.tables) for item in inputs),
               "validated_medications": len(items), "new_rows_inserted": inserted,
               "duplicates_skipped": skipped, "dry_run": dry_run,
               "status": failure or insertion_status or ("SUCCESS" if inputs else "NO_INPUT"),
               "validation_status": failure or ("SUCCESS" if inputs else "NO_INPUT"),
               "runtime": client.runtime, "model": client.model_name,
               "prompt_version": PROMPT_VERSION}
    records = []
    with factory.begin() as session:
        for run_id in runs:
            run = session.get(ExtractionRun, run_id)
            output = dict(run.raw_output)
            output.update({"normalized_output": [item.model_dump(mode='json') for item in items
                                                   if item.source_document_id == run.source_document_id],
                           "clinical_rows_inserted": inserted, "duplicates_skipped": skipped,
                           "patient_validation_status": summary['validation_status'],
                           "insertion_status": summary['status'], "case_id": case_id,
                           "matched_fallnr": matched_fallnr})
            run.raw_output = output
            records.append(run_debug_record(run))
    _save_debug(patient_id, summary, records, debug_root)
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
        print(json.dumps({"patient_id": args.patient, "status": "FAILED", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return int(result['status'] not in ('SUCCESS', 'NO_INPUT'))


if __name__ == '__main__':
    raise SystemExit(main())
