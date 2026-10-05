"""Lossless source segments with separately cleaned extraction text."""
from collections import defaultdict
from copy import deepcopy
from math import ceil
import re

from .models import PreparedSection, PreparedTable


TARGET_PATTERNS = {
    "case": r"\b(?:stammdaten|patientendaten|demograph\w*|demographic\w*)\b",
    "clinical_history": r"\b(?:diagnos\w*|anamnese|krankengeschichte|verlauf|epikrise|clinical history)\b",
    "medications": r"\b(?:medikation\w*|medikament\w*|medication\w*|arzneimittel\w*)\b",
    "examinations": r"\b(?:untersuchung\w*|sonograph\w*|ultraschall\w*|duplex\w*|pet|spect|examination\w*)\b",
    "laboratory": r"\b(?:labor\w*|liquor\w*|laboratory|blutbild)\b",
    "radiology": r"\b(?:radiolog\w*|computertomograf\w*|magnetresonanztomograf\w*|ct|mrt|mri|cct|cmrt)\b",
    "family_history": r"\b(?:familienanamnese|family history)\b",
}

# Only unmistakable administrative/contact lines can be removed. Identical
# diagnosis/medication headings at page edges must remain in prepared text.
CONTACT_PATTERN = re.compile(
    r"(?:\b(?:tel(?:efon)?|fax|telefonnummer)\s*[:.+]|https?://|www\.|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|\bseite\s+\d+\s+(?:von|/)\s*\d+)",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def candidate_targets(heading: str | None, text: str) -> list[str]:
    # A short opening line is usable evidence for a page fallback. Searching
    # whole pages would incorrectly route incidental mentions of drugs/tests.
    evidence = heading or next((line for line in text.splitlines() if line.strip()), "")
    if len(evidence) > 160:
        return []
    return [target for target, pattern in TARGET_PATTERNS.items()
            if re.search(pattern, evidence, re.IGNORECASE)]


def detect_noise(pages: list[dict]) -> set[str]:
    if len(pages) < 3:
        return set()
    occurrences = defaultdict(set)
    for index, page in enumerate(pages):
        lines = [line for line in page.get("text", "").splitlines() if line.strip()]
        for line in lines[:3] + lines[-3:]:
            if CONTACT_PATTERN.search(line):
                occurrences[normalize(line)].add(index)
    threshold = max(3, ceil(len(pages) * 0.7))
    return {line for line, page_indices in occurrences.items() if len(page_indices) >= threshold}


def _heading_matches(text: str, headings: list[str], noise: set[str]) -> list[tuple]:
    found = {}
    for heading in headings:
        clean = heading.strip()
        if not clean or len(clean) > 120 or normalize(clean) in noise:
            continue
        # Resolve the detected heading against actual text; never synthesize or
        # replace source text. Match whole lines, including multiline headings.
        pattern = r"(?m)^[ \t]*" + r"\s+".join(re.escape(w) for w in clean.split()) + r"[ \t]*(?=\r?$)"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            found.setdefault(match.start(), (match.start(), match.end(), clean))
    # Ignore overlapping detected heading spans.
    selected = []
    for item in sorted(found.values()):
        if not selected or item[0] >= selected[-1][1]:
            selected.append(item)
    return selected


def sectionize(pages: list[dict], patient_id: str, source_document_id: int
               ) -> tuple[list[PreparedSection], list[dict]]:
    noise = detect_noise(pages)
    sections = []
    excluded = []
    for index, page in enumerate(pages, 1):
        page_number = page.get("page_number", index)
        original = page.get("text", "")
        lines = original.splitlines(keepends=True)
        nonempty_indices = [i for i, line in enumerate(lines) if line.strip()]
        edge_indices = set(nonempty_indices[:3] + nonempty_indices[-3:])
        removed_indices = {i for i in edge_indices if normalize(lines[i]) in noise}
        removed_offsets = set()
        offset = 0
        for line_index, line in enumerate(lines):
            if line_index in removed_indices:
                removed_offsets.add(offset)
                excluded.append({"page_number": page_number, "line_number": line_index + 1,
                                 "text": line, "reason": "repeated_contact_header_footer"})
            offset += len(line)
        matches = _heading_matches(original, page.get("headings", []), noise)
        boundaries = [(start, heading) for start, _, heading in matches]
        if not boundaries or boundaries[0][0] != 0:
            boundaries.insert(0, (0, None))
        page_sections = []
        for boundary_index, (start, heading) in enumerate(boundaries):
            end = boundaries[boundary_index + 1][0] if boundary_index + 1 < len(boundaries) else len(original)
            source_text = original[start:end]
            local_offset = start
            kept = []
            for line in source_text.splitlines(keepends=True):
                if local_offset not in removed_offsets:
                    kept.append(line)
                local_offset += len(line)
            cleaned = "".join(kept)
            section = PreparedSection(
                section_id=f"doc{source_document_id}_sec{len(sections) + 1:02d}",
                patient_id=patient_id, source_document_id=source_document_id,
                heading=heading, page_start=page_number, page_end=page_number,
                text=cleaned, source_text=source_text,
                candidate_targets=candidate_targets(heading, cleaned),
            )
            sections.append(section)
            page_sections.append(section)
        for table_index, table in enumerate(page.get("tables", []), 1):
            rows = deepcopy(table.get("rows", []))
            cells = [normalize(str(cell)) for row in rows for cell in (row or [])
                     if cell is not None and len(normalize(str(cell))) >= 3]
            scores = [sum(cell in normalize(section.source_text) for cell in cells)
                      for section in page_sections]
            best = max(scores, default=0)
            # Compact parsed tables lack coordinates. Use matching cell content
            # if unique; otherwise explicitly record only page-level association.
            unique = best > 0 and scores.count(best) == 1
            chosen = page_sections[scores.index(best)] if unique else page_sections[0]
            chosen.tables.append(PreparedTable(page_number, table_index, rows,
                                              "cell_text_match" if unique else "page_fallback"))
    return sections, excluded
