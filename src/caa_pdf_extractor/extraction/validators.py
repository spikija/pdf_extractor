"""Strict JSON, provenance checks and conservative deduplication."""
import json
import re
from .models import ClinicalHistoryExtraction


def normalize_disease(text: str) -> str:
    return " ".join(text.split()).casefold()


def supported_disease_text(text):
    """Only approved spelling/abbreviation normalization, never semantic inference."""
    value = normalize_disease(text)
    value = re.sub(r'\bz\.\s*n\b\.?', 'zustand nach', value)
    value = re.sub(r'\bicb\b', 'intracerebrale blutung', value)
    return value


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
        snippet = ' '.join(item.source_text.split())
        from .diagnosis_items import normalized_label
        disease = normalized_label(item.disease)
        if not disease or not snippet or snippet not in ' '.join(section.text.split()) or disease not in normalized_label(snippet):
            raise ValueError("Unsupported source quotation")
        if re.search(r'\n\s*\d+[.)](?:[ \t]+|\r?\n)', item.disease) or re.search(r'\n\s*\d+[.)](?:[ \t]+|\r?\n)', item.source_text):
            raise ValueError('Merged diagnosis evidence')
        if re.search(r'\bsowie\b', item.disease, re.I) and len(re.findall(r'\b(?:Blutung|Hämorrhagie|Infarkt)\b', item.disease, re.I)) > 1:
            raise ValueError('Merged diagnosis events')
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
