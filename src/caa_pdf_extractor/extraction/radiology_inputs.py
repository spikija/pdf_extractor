"""Use prepared event identities unchanged; bound MRI reports before consultations."""
from datetime import date
import re

UNRELATED_HEADING = re.compile(
    r'^\s*(?:Historischer\s+(?:CT|MRI|MRT)[- ]Befund|Vorbefund\s*:\s*(?:CT|MRI|MRT)|'
    r'Internistisches\s+Konsil|\w*\s+Konsil\s*\(|'
    r'ECHOKARDIOGRAPHIE|EKG\b|EEG\b|Laborwerte\b|Zusammenfassung\s+des\s+Aufenthalts|'
    r'Weitere\s+empfohlene\s+Ma.nahmen|Entlassungsmedikation)\b', re.I | re.M)


def procedure_context(procedure):
    return {field: getattr(procedure, field) for field in (
        'patient_id', 'procedure_id', 'modality', 'date', 'studies',
        'source_document_id', 'page_start', 'page_end')}


def report_text(procedure):
    match = UNRELATED_HEADING.search(procedure.result_text)
    return procedure.result_text[:match.start()].rstrip() if match else procedure.result_text.strip()


def examination_date(procedure):
    return date.fromisoformat(procedure.date) if procedure.date else None
