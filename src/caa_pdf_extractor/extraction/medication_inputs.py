"""Bounded medication sections and table continuations; no clinical inference."""
from dataclasses import dataclass, field
import re

MEDICATION_HEADING = re.compile(r"\b(?:\w*medikation\w*|medikament\w*|medication\w*)\b", re.I)
TABLE_HEADER = re.compile(r"^(?:medikament\w*|medication|pr[äa]parat|wirkstoff|arzneimittel)$", re.I)
NON_MEDICATION = re.compile(r"\b(?:kompressions\w*|bandag\w*|strumpf\w*|physiotherap\w*)\b", re.I)


def normalize_medication(value):
    # Do not case-fold or change punctuation/doses; preserve distinct regimens.
    return " ".join(value.split())


def render_row(row):
    cells = [normalize_medication(str(cell or "")) for cell in row]
    return " ".join(cell for cell in cells if cell)


@dataclass
class MedicationInput:
    section_id: str
    patient_id: str
    source_document_id: int
    page_number: int
    heading: str | None
    text: str
    tables: list[dict] = field(default_factory=list)


def _medication_tables(section):
    tables = []
    for table in section.tables:
        # A medication list needs names, not a laboratory/date matrix. Context
        # is required separately by selection; cell values alone do not route it.
        rows = [row for row in table.rows if row and row[0]
                and not TABLE_HEADER.fullmatch(normalize_medication(str(row[0])))
                and not NON_MEDICATION.search(str(row[0]))]
        if rows:
            tables.append({"table_index": table.table_index, "page_number": table.page_number,
                           "rows": rows, "source_rows": [render_row(row) for row in rows]})
    return tables


def select_medication_inputs(bundle):
    inputs = []
    for document in bundle.documents:
        previous = None
        for section in document.sections:
            opening = section.heading or next((x.strip() for x in section.text.splitlines() if x.strip()), "")
            explicit = bool(MEDICATION_HEADING.search(opening[:160]))
            continuation = (previous is not None and section.heading is None
                            and section.page_start in (previous.page_number, previous.page_number + 1)
                            and bool(section.tables) and bool(previous.tables))
            relevant = explicit or "medications" in section.candidate_targets or continuation
            if not relevant or (not explicit and set(section.candidate_targets) & {"radiology", "laboratory"}):
                previous = None
                continue
            tables = _medication_tables(section)
            # Prefer rows; page text includes unrelated headers, footers and
            # duplicated table text. Free-text medication sections stay bounded.
            text = "" if tables else section.text.strip()
            if not tables and not text:
                previous = None
                continue
            item = MedicationInput(section.section_id, section.patient_id, section.source_document_id,
                                   section.page_start, section.heading, text, tables)
            inputs.append(item)
            previous = item
    return inputs
