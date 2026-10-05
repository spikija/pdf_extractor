"""Strict JSON, provenance checks and conservative deduplication."""
import json
import re
from .models import ClinicalHistoryExtraction


def normalize_disease(text: str) -> str:
    return " ".join(text.split()).casefold()


def parse_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def validate_output(raw, patient_id, sections):
    parsed = parse_json(raw)
    # Strict JSON validation accepts ISO dates while rejecting coerced IDs.
    result = ClinicalHistoryExtraction.model_validate_json(json.dumps(parsed))
    if result.patient_id != patient_id:
        raise ValueError("Patient provenance mismatch")
    by_id = {s.section_id: s for s in sections}
    for item in result.clinical_history:
        section = by_id.get(item.section_id)
        if (section is None or section.patient_id != patient_id
                or item.source_document_id != section.source_document_id
                or item.page_number != section.page_start):
            raise ValueError("Section provenance mismatch")
        snippet = normalize_disease(item.source_text)
        disease = normalize_disease(item.disease)
        if not disease or not snippet or snippet not in normalize_disease(section.text) or disease not in snippet:
            raise ValueError("Unsupported source quotation")
        if item.date_disease:
            d = item.date_disease
            pattern = rf"(?<!\d)(?:{d.isoformat()}|0?{d.day}\.0?{d.month}\.{d.year})(?!\d)"
            if not re.search(pattern, item.source_text):
                raise ValueError("Disease date missing from source quotation")
    return result


def deduplicate(items):
    seen = set()
    result = []
    for item in items:
        key = (normalize_disease(item.disease), item.date_disease)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result
