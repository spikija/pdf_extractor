"""Deterministic diagnostic headers and report boundaries; no interpretation."""
from bisect import bisect_right
from datetime import date
import re

from .models import DiagnosticProcedure, DiagnosticSubprocedure, PreparedSection


MODALITY = r"(?:Röntgen|Roentgen|Rtg|Computertomogra(?:fie|phie)|CTA|CT|Kernspintomogra(?:fie|phie)|MRT|MRI)"
HEADER = re.compile(r"^[ \t]*(?P<name>" + MODALITY + r")[ \t]*:[ \t]*(?P<body>.*)", re.IGNORECASE)
COMPONENT = re.compile(r"(?P<name>" + MODALITY + r")\s*:\s*", re.IGNORECASE)
HEADER_DATE = re.compile(r"\((\d{1,2})\.(\d{1,2})\.(\d{4})\)")


def normalize_modality(name: str) -> str:
    name = name.casefold()
    if name in ("röntgen", "roentgen", "rtg"):
        return "RTG"
    if name in ("ct", "cta") or name.startswith("computertomogra"):
        return "CT"
    if name in ("mrt", "mri") or name.startswith("kernspintomogra"):
        return "MRI"
    raise ValueError("Unsupported diagnostic modality")


def _date(header: str) -> str | None:
    found = set()
    matches = list(HEADER_DATE.finditer(header))
    for match in matches:
        try:
            found.add(date(int(match[3]), int(match[2]), int(match[1])).isoformat())
        except ValueError:
            return None
    return next(iter(found)) if len(found) == 1 else None


def _studies(header: str) -> list[str]:
    header = HEADER_DATE.sub("", header)
    components = list(COMPONENT.finditer(header))
    result = []
    for i, match in enumerate(components):
        end = components[i + 1].start() if i + 1 < len(components) else len(header)
        study = " ".join(header[match.end():end].strip(" ,\r\n\t").split())
        # A leading CTA label is itself a subtype, even without a CT wrapper.
        if match["name"].casefold() == "cta":
            study = "CTA " + study
        if study:
            result.append(study)
    return result


def _subprocedures(result_text: str, studies: list[str]) -> list[DiagnosticSubprocedure]:
    boundaries = []
    for study in studies:
        pattern = r"(?m)^[ \t]*" + r"\s+".join(re.escape(w) for w in study.split()) + r"[ \t]*:[ \t]*"
        for match in re.finditer(pattern, result_text, re.IGNORECASE):
            boundaries.append((match.start(), match.end(), study))
    boundaries.sort()
    return [DiagnosticSubprocedure(study, result_text[end:boundaries[i + 1][0]
                                                    if i + 1 < len(boundaries) else len(result_text)].strip())
            for i, (_, end, study) in enumerate(boundaries)]


def split_diagnostic_procedures(sections: list[PreparedSection]) -> list[DiagnosticProcedure]:
    """Split one document's ordered sections, retaining exact original slices.

    Page fallback sections can continue an event across pages. A new named,
    nonprocedure section ends the preceding report. Composite header wrapping
    is accepted only after a trailing comma, preventing narrative dates from
    being consumed as examination dates.
    """
    if not sections:
        return []
    identity = (sections[0].patient_id, sections[0].source_document_id)
    if any((s.patient_id, s.source_document_id) != identity for s in sections):
        raise ValueError("Diagnostic splitter requires sections from one document")
    text = "".join(s.source_text for s in sections)
    section_offsets = []
    offset = 0
    for section in sections:
        section_offsets.append(offset)
        offset += len(section.source_text)
    # Page text may lack a trailing newline. Scan each section independently
    # so the next page's first header cannot merge with the previous footer.
    lines = [line for section in sections for line in section.source_text.splitlines(keepends=True)]
    line_offsets = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line)
    headers = []
    i = 0
    while i < len(lines):
        match = HEADER.match(lines[i].rstrip("\r\n"))
        if not match:
            i += 1
            continue
        modality = normalize_modality(match["name"])
        start_line = i
        while (lines[i].rstrip().endswith(",") and not HEADER_DATE.search(lines[i])
               and i + 1 < len(lines)):
            following = lines[i + 1].rstrip("\r\n")
            next_match = HEADER.match(following)
            if not following.strip() or (next_match and normalize_modality(next_match["name"]) != modality):
                break
            i += 1
        end = line_offsets[i] + len(lines[i])
        header_text = text[line_offsets[start_line]:end]
        # Mixed modality components on the same line cannot share one event.
        if all(normalize_modality(m["name"]) == modality for m in COMPONENT.finditer(header_text)):
            headers.append((line_offsets[start_line], end, modality, _date(header_text), _studies(header_text)))
        i += 1
    procedures = []
    for i, (start, header_end, modality, exam_date, studies) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        for section, section_start in zip(sections, section_offsets):
            if header_end <= section_start < end and section.heading:
                # Study labels belong to the current report, even when the
                # typography sectionizer made them separate sections.
                label = section.heading.strip().rstrip(":").casefold()
                if not HEADER.match(section.heading) and label not in {s.casefold() for s in studies}:
                    end = section_start
                    break
        first_section = sections[bisect_right(section_offsets, start) - 1]
        last_section = sections[bisect_right(section_offsets, max(start, end - 1)) - 1]
        result = text[header_end:end]
        procedures.append(DiagnosticProcedure(
            procedure_id=f"doc{identity[1]}_proc{len(procedures) + 1:02d}",
            patient_id=identity[0], source_document_id=identity[1],
            page_start=first_section.page_start, page_end=last_section.page_end,
            modality=modality, date=exam_date, studies=studies,
            subprocedures=_subprocedures(result, studies),
            result_text=result, source_text=text[start:end],
        ))
    return procedures
