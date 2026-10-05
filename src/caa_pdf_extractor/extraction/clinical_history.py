"""First local Granite extraction pipeline: clinical history only."""
import argparse
from datetime import datetime, timezone
import json
import re

from pydantic import ValidationError
from sqlalchemy import select, text, null

from ..llm.granite import GraniteClient
from ..llm.prompts import PROMPT_VERSION, clinical_history_prompt
from ..preparation import build_patient_bundle
from .models import ClinicalHistoryExtraction
from .validators import parse_json, validate_output, deduplicate, normalize_disease
from .debug_export import run_debug_record, save_dry_run_debug
from .case_resolver import resolve_case_id, CaseResolutionError

RELEVANT = re.compile(r"\b(?:diagnos\w*|anamnese|epikrise|beurteilung|zusammenfassung|"
                      r"krankengeschichte|aufenthaltsverlauf|klinischer verlauf)\b", re.I)
FORMAL = re.compile(r"\bdiagnos\w*", re.I)


def select_sections(bundle):
    result = []
    for document in bundle.documents:
        for section in document.sections:
            opening = section.heading or next((line.strip() for line in section.text.splitlines() if line.strip()), "")
            explicit = bool(RELEVANT.search(opening[:160]))
            if ("clinical_history" in section.candidate_targets or explicit) and section.text.strip():
                if not explicit and set(section.candidate_targets) & {"radiology", "laboratory"}:
                    continue
                result.append(section)
    return sorted(result, key=lambda s: (not bool(FORMAL.search(s.heading or s.text[:160])),
                                         s.source_document_id, s.page_start, s.section_id))


def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None):
    from ..database import SessionLocal
    from ..database.models import ExtractionRun, ClinicalHistory
    factory = session_factory or SessionLocal
    bundle = build_patient_bundle(patient_id, factory)
    sections = select_sections(bundle)
    client = client or GraniteClient()
    if not sections:
        if dry_run:
            save_dry_run_debug(patient_id, PROMPT_VERSION, [], status="NO_INPUT",
                               runtime=client.runtime, model_name=client.model_name,
                               model_version=client.model_version)
        return {"status": "NO_INPUT", "input_sections": 0, "diagnoses": [], "dry_run": dry_run,
                "runtime": client.runtime, "model": client.model_name, "prompt_version": PROMPT_VERSION}
    items = []
    runs = []
    failure = None
    for document in bundle.documents:
        chosen = [s for s in sections if s.source_document_id == document.source_document_id]
        if not chosen:
            continue
        with factory.begin() as session:
            run = ExtractionRun(source_document_id=document.source_document_id,
                                model_name=client.model_name, model_version=client.model_version,
                                prompt_version=PROMPT_VERSION, status="RUNNING",
                                raw_output={"patient_id": patient_id, "dry_run": dry_run,
                                            "section_ids": [s.section_id for s in chosen]})
            session.add(run)
            session.flush()
            run_id = run.id
        raw = None
        errors = None
        status = "SUCCESS"
        try:
            prompt = clinical_history_prompt(patient_id, chosen, ClinicalHistoryExtraction.model_json_schema())
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
                validated = validate_output(raw, patient_id, chosen)
                items.extend(validated.clinical_history)
            except (ValueError, ValidationError) as exc:
                status = "FAILED_VALIDATION"
                errors = ([{"type": e["type"], "loc": list(e["loc"])} for e in exc.errors()]
                          if isinstance(exc, ValidationError) else [{"type": str(exc)}])
        with factory.begin() as session:
            run = session.get(ExtractionRun, run_id)
            run.status = status
            run.model_version = client.model_version
            run.completed_at = datetime.now(timezone.utc)
            run.raw_output = {"patient_id": patient_id, "dry_run": dry_run,
                              "response": raw, "section_ids": [s.section_id for s in chosen]}
            run.validation_errors = errors
        runs.append(run_id)
        if status != "SUCCESS":
            failure = status
    priority = {s.section_id: index for index, s in enumerate(sections)}
    items = deduplicate(sorted(items, key=lambda item: priority[item.section_id])) if failure is None else []
    inserted = 0
    duplicates_skipped = 0
    case_id = None
    case_exists = None
    matched_fallnr = None
    insertion_status = None
    if not dry_run and failure is None:
        with factory.begin() as session:
            try:
                case_id = resolve_case_id(patient_id, session=session)
            except CaseResolutionError as exc:
                insertion_status = exc.status
                case_exists = exc.status == "CASE_AMBIGUOUS"
            else:
                case_exists = True
                matched_fallnr = patient_id
                # Serialize duplicate checks using the resolved internal key.
                session.execute(text("SELECT pg_advisory_xact_lock(:case_id)"), {"case_id": case_id})
                existing = {(normalize_disease(row.disease or ""), row.date_disease)
                            for row in session.scalars(select(ClinicalHistory).where(ClinicalHistory.case_id == case_id))}
                for item in items:
                    key = (normalize_disease(item.disease), item.date_disease)
                    if key not in existing:
                        session.add(ClinicalHistory(case_id=case_id, disease=item.disease,
                                    date_disease=item.date_disease or null(), disease_category=null(),
                                    date_admissioned=null(), date_discharged=null()))
                        existing.add(key)
                        inserted += 1
                    else:
                        duplicates_skipped += 1
    # Keep validated provenance after normalization, including dry-run results.
    debug_runs = []
    with factory.begin() as session:
        for run_id in runs:
            run = session.get(ExtractionRun, run_id)
            output = dict(run.raw_output)
            output["normalized_output"] = [i.model_dump(mode="json") for i in items
                                           if i.source_document_id == run.source_document_id]
            output["clinical_rows_inserted"] = inserted
            output["patient_validation_status"] = failure or "SUCCESS"
            if not dry_run:
                output.update({"case_id": case_id, "matched_fallnr": matched_fallnr, "case_exists": case_exists,
                               "insertion_status": insertion_status or failure or "SUCCESS",
                               "duplicates_skipped": duplicates_skipped})
            run.raw_output = output
            if dry_run:
                debug_runs.append(run_debug_record(run))
    if dry_run:
        save_dry_run_debug(patient_id, PROMPT_VERSION, debug_runs, status=failure or "SUCCESS",
                           runtime=client.runtime, model_name=client.model_name,
                           model_version=client.model_version)
    return {"status": insertion_status or failure or "SUCCESS", "patient_id": patient_id,
            "matched_fallnr": matched_fallnr, "case_id": case_id, "case_exists": case_exists,
            "validated_diagnoses": len(items), "new_rows_inserted": inserted,
            "duplicates_skipped": duplicates_skipped, "runtime": client.runtime,
            "model": client.model_name, "model_version": client.model_version,
            "prompt_version": PROMPT_VERSION, "input_sections": len(sections),
            "diagnosis_count": len(items), "dry_run": dry_run, "inserted": inserted,
            "diagnoses": [{k: v for k, v in item.model_dump(mode="json").items() if k != "source_text"}
                          for item in items]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--commit", "--write", dest="commit", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = extract_patient(args.patient, dry_run=not args.commit)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        return 1
    report = result
    if args.commit:
        report = {key: result.get(key) for key in (
            "patient_id", "matched_fallnr", "case_id", "case_exists",
            "validated_diagnoses", "new_rows_inserted", "duplicates_skipped", "status")}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return int(result["status"] != "SUCCESS")


if __name__ == "__main__":
    raise SystemExit(main())
