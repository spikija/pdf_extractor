"""Readable local exports; never modify audit records or extraction results."""
from copy import deepcopy
import json
from pathlib import Path

DEBUG_ROOT = Path(__file__).resolve().parents[3] / "data" / "debug"


def run_debug_record(run):
    record = deepcopy(run.raw_output)
    response = record.pop("response", None)
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            pass  # Failed-parse runs must retain the original invalid response.
    record["granite_response"] = response
    record.update({"run_id": run.id, "source_document_id": run.source_document_id,
                   "model_name": run.model_name, "model_version": run.model_version,
                   "prompt_version": run.prompt_version, "status": run.status,
                   "started_at": run.started_at.isoformat() if run.started_at else None,
                   "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                   "validation_errors": deepcopy(run.validation_errors)})
    return record


def save_dry_run_debug(patient_id, prompt_version, runs, *, status, runtime,
                       model_name, model_version, debug_root=None):
    for component in (patient_id, prompt_version):
        if component in ("", ".", "..") or any(c in component for c in '/\\:<>"|?*'):
            raise ValueError("Invalid debug export path component")
    records = deepcopy(runs)
    responses = [record["granite_response"] for record in records]
    output = {
        "patient_id": patient_id, "dry_run": True,
        "patient_validation_status": status, "runtime": runtime,
        "model_name": model_name, "model_version": model_version,
        "prompt_version": prompt_version,
        "section_ids": [section_id for record in records for section_id in record.get("section_ids", [])],
        "granite_response": responses[0] if len(responses) == 1 else responses,
        "normalized_output": [item for record in records for item in record.get("normalized_output", [])],
        "clinical_rows_inserted": 0,
        "runs": records,
    }
    path = (debug_root or DEBUG_ROOT) / patient_id / f"clinical_history_{prompt_version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
