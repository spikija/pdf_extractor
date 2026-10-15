"""First local Granite extraction pipeline: clinical history only."""
import argparse
from datetime import datetime, timezone
import json
import re

from pydantic import ValidationError
from sqlalchemy import select, text, null

from ..llm.backend import get_client
from ..llm.prompts import PROMPT_VERSION, clinical_history_prompt
from ..preparation import build_patient_bundle
from .models import ClinicalHistoryExtraction
from .diagnosis_items import validate_independent
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


def _extract_dry_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    if not dry_run:
        raise ValueError("Use verified artifact commit")
    from ..database import SessionLocal
    from ..database.models import ExtractionRun, ClinicalHistory
    factory = session_factory or SessionLocal
    bundle = build_patient_bundle(patient_id, factory)
    sections = select_sections(bundle)
    client = client or get_client()
    if not sections:
        if dry_run:
            save_dry_run_debug(patient_id, PROMPT_VERSION, [], status="NO_INPUT",
                               runtime=client.runtime, model_name=client.model_name,
                               model_version=client.model_version, debug_root=debug_root)
        return {"status": "NO_INPUT", "input_sections": 0, "diagnoses": [], "dry_run": dry_run,
                "runtime": client.runtime, "model": client.model_name, "prompt_version": PROMPT_VERSION}
    items = []
    runs = []
    failure = None
    rejected_all = []
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
        rejected, evidence_metadata = [], []
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
                validated, rejected, evidence_metadata = validate_independent(raw, patient_id, chosen)
                items.extend(validated.clinical_history)
                rejected_all.extend(rejected)
                if rejected:
                    status = 'PARTIAL_VALID' if validated.clinical_history else 'FAILED_VALIDATION'
                    errors = [{'type': r['rejection_reason'], 'index':r['index']} for r in rejected]
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
                              "response": raw, "section_ids": [s.section_id for s in chosen],
                              "rejected_records": rejected, "evidence_metadata": evidence_metadata,
                              "independent_validation": True}
            run.validation_errors = errors
        runs.append(run_id)
        if status not in ("SUCCESS", "PARTIAL_VALID") and not rejected:
            failure = status
    priority = {s.section_id: index for index, s in enumerate(sections)}
    items = deduplicate(sorted(items, key=lambda item: priority[item.section_id])) if failure is None else []
    patient_status = failure or ("PARTIAL_VALID" if items and rejected_all else "FAILED_VALIDATION" if rejected_all else "SUCCESS")
    inserted = 0
    duplicates_skipped = 0
    case_id = None
    case_exists = None
    matched_fallnr = None
    insertion_status = None
    # Keep validated provenance after normalization, including dry-run results.
    debug_runs = []
    with factory.begin() as session:
        for run_id in runs:
            run = session.get(ExtractionRun, run_id)
            output = dict(run.raw_output)
            output["normalized_output"] = [i.model_dump(mode="json") for i in items
                                           if i.source_document_id == run.source_document_id]
            output["clinical_rows_inserted"] = inserted
            output["patient_validation_status"] = patient_status
            if not dry_run:
                output.update({"case_id": case_id, "matched_fallnr": matched_fallnr, "case_exists": case_exists,
                               "insertion_status": insertion_status or patient_status,
                               "duplicates_skipped": duplicates_skipped})
            run.raw_output = output
            if dry_run:
                debug_runs.append(run_debug_record(run))
    if dry_run:
        save_dry_run_debug(patient_id, PROMPT_VERSION, debug_runs, status=patient_status,
                           runtime=client.runtime, model_name=client.model_name,
                           model_version=client.model_version, debug_root=debug_root)
    return {"status": insertion_status or patient_status, "patient_id": patient_id,
            "matched_fallnr": matched_fallnr, "case_id": case_id, "case_exists": case_exists,
            "validated_diagnoses": len(items), "new_rows_inserted": inserted,
            "duplicates_skipped": duplicates_skipped, "runtime": client.runtime,
            "model": client.model_name, "model_version": client.model_version,
            "prompt_version": PROMPT_VERSION, "input_sections": len(sections),
            "diagnosis_count": len(items), "dry_run": dry_run, "inserted": inserted,
            "_run_ids": runs, "_bundle": bundle, "diagnoses": [{k: v for k, v in item.model_dump(mode="json").items() if k != "source_text"}
                          for item in items]}



def extract_patient(patient_id, *, dry_run=True, client=None, session_factory=None, debug_root=None):
    from .document_artifacts import create, commit
    from ..database import SessionLocal
    from ..database.models import ExtractionRun
    factory = session_factory or SessionLocal
    if not dry_run:
        return commit('clinical_history', patient_id, build_patient_bundle(patient_id, factory),
                      client=client, session_factory=factory, debug_root=debug_root)
    client = client or get_client()
    result = _extract_dry_patient(patient_id, dry_run=True, client=client, session_factory=factory, debug_root=debug_root)
    result['granite_called'] = client.runtime != 'mock' and result.get('input_sections',0) > 0
    bundle = result.pop('_bundle', None)
    run_ids = result.pop('_run_ids', [])
    if result['status'] not in ('SUCCESS', 'PARTIAL_VALID'): return result
    with factory.begin() as session:
        try: case_id = resolve_case_id(patient_id, session=session)
        except CaseResolutionError as exc:
            result.update(status=exc.status, validation_status=exc.status)
            return result
        # IDs from this dry-run, captured directly rather than selecting older audits.
        runs = [session.get(ExtractionRun, run_id) for run_id in run_ids]
        for run in runs:
            run.raw_output = {**run.raw_output, 'case_id':case_id, 'matched_fallnr':patient_id}
        path = create('clinical_history',bundle,case_id,runs,client,debug_root)
    result.update(case_id=case_id,resolved_case_id=case_id,matched_fallnr=patient_id,
                  case_exists=True,verified_artifact=str(path),artifact_validation_status='PARTIAL_VALID' if result['status']=='PARTIAL_VALID' else ('VERIFIED' if result['diagnosis_count'] else 'VERIFIED_EMPTY'))
    return result

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
