"""Strict provenance and date checks, without inferred drug classification."""
import json
import re
from .medication_models import MedicationExtraction
from .medication_inputs import normalize_medication, NON_MEDICATION
from .validators import parse_json


def medication_key(item):
    return (normalize_medication(item.medication), item.date_noted,
            item.date_stopped, item.medication_category)


def deduplicate_medications(items):
    result, seen = [], set()
    for item in items:
        key = medication_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _date_supported(value, snippet, kind):
    if value is None:
        return True
    if re.search(r"\b(?:aufnahme\w*|entlass\w*|geburt\w*|dokument\w*|admission|discharge|birth)\b", snippet, re.I):
        return False
    d = value
    date_pattern = rf"(?:{d.isoformat()}|0?{d.day}\.0?{d.month}\.{d.year})"
    label = (r"(?:seit|ab|beginn|begonnen|verordnet|start|date_noted)" if kind == "start"
             else r"(?:bis|abgesetzt|gestoppt|beendet|ende|stop|date_stopped)")
    # Dates must be attached to a start/stop label in the supporting medication
    # line. Admission/discharge dates cannot satisfy this check.
    return bool(re.search(rf"\b{label}\b\s*(?:am|zum|:)?\s*{date_pattern}(?!\d)", snippet, re.I))


def validate_medications(raw, patient_id, inputs):
    parsed = parse_json(raw)
    result = MedicationExtraction.model_validate_json(json.dumps(parsed))
    if result.patient_id != patient_id:
        raise ValueError("Patient provenance mismatch")
    by_id = {item.section_id: item for item in inputs}
    for item in result.medications:
        source = by_id.get(item.section_id)
        if (source is None or source.patient_id != patient_id
                or item.source_document_id != source.source_document_id):
            raise ValueError("Section provenance mismatch")
        snippet = normalize_medication(item.source_text)
        medication = normalize_medication(item.medication)
        if not medication or not snippet or NON_MEDICATION.search(medication):
            raise ValueError("Unsupported medication")
        if item.source_table_index is not None:
            table = next((t for t in source.tables if t['table_index'] == item.source_table_index
                          and t['page_number'] == item.page_number), None)
            if table is None or snippet not in table['source_rows'] or medication != snippet:
                raise ValueError("Medication table quotation mismatch")
        else:
            # Require a single supporting source line, not a concatenation of
            # unrelated lines that could attach another medication's date.
            lines = [normalize_medication(line) for line in source.text.splitlines()]
            if item.page_number != source.page_number or snippet not in lines or medication not in snippet:
                raise ValueError("Medication source quotation mismatch")
            # Do not allow strength or regimen to be truncated from a quoted line.
            if not snippet.startswith(medication):
                raise ValueError("Medication wording mismatch")
            suffix = snippet[len(medication):].strip()
            if suffix and not re.match(r"^(?:[;,]\s*)?(?:seit|ab|beginn|begonnen|verordnet|start|bis|abgesetzt|gestoppt|beendet|ende|stop|category|kategorie)\b", suffix, re.I):
                raise ValueError("Medication instructions omitted")
        if item.medication_category is not None and item.medication_category not in snippet:
            raise ValueError("Medication category not explicit in source")
        if (not _date_supported(item.date_noted, snippet, "start")
                or not _date_supported(item.date_stopped, snippet, "stop")):
            raise ValueError("Medication date not explicitly associated")
        if item.date_noted and item.date_stopped and item.date_stopped < item.date_noted:
            raise ValueError("Medication stop date precedes start date")
        item.medication = medication
    return result
