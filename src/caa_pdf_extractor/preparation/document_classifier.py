"""Conservative document identity and labelled date rules, without inference."""
import re
from datetime import date


TYPE_PATTERNS = {
    "discharge_letter": r"\b(?:entlassungsbrief|entlassungsbericht|arztbrief\s+bei\s+entlassung|discharge\s+(?:letter|summary))\b",
    "outpatient_letter": r"\b(?:ambulanzbrief|ambulanzbericht|ambulanter\s+arztbrief|outpatient\s+(?:letter|report))\b",
    "radiology_report": r"\b(?:radiologischer\s+befund|radiologischer\s+bericht|radiology\s+report|(?:ct|mrt|mr|computertomograf\w*|magnetresonanztomograf\w*)[ -]+befund)\b",
    "laboratory_report": r"\b(?:laborbefund|laborbericht|laboratory\s+report)\b",
    "consultation": r"\b(?:konsiliarbericht|konsiliarbefund|konsilbericht|konsilbefund|consultation\s+(?:report|letter))\b",
}


def classify_document(pages: list[dict]) -> str:
    # Titles near the beginning provide document identity. Later mentions of
    # other reports are commonly embedded findings, not document titles.
    if not pages:
        return "unknown"
    first = pages[0]
    title_lines = first.get("text", "").splitlines()[:40]
    titles = [h for h in first.get("headings", []) if h and len(h) <= 140]
    evidence = "\n".join(line for line in title_lines + titles if len(line) <= 140)
    matches = [kind for kind, pattern in TYPE_PATTERNS.items()
               if re.search(pattern, evidence, re.IGNORECASE)]
    return matches[0] if len(matches) == 1 else "unknown"


DATE_PATTERN = re.compile(
    r"^\s*(?:Dokumentdatum|Briefdatum|Berichtsdatum|Befunddatum|"
    r"Ausstellungsdatum|Datum|Document date|Report date)\s*:\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\s*$",
    re.IGNORECASE,
)


def detect_document_date(pages: list[dict]) -> str | None:
    dates = set()
    for page in pages:
        for line in page.get("text", "").splitlines():
            match = DATE_PATTERN.fullmatch(line)
            if not match:
                continue
            value = match[1]
            try:
                if "." in value:
                    day, month, year = map(int, value.split("."))
                    parsed = date(year, month, day)
                else:
                    parsed = date.fromisoformat(value)
            except ValueError:
                continue
            dates.add(parsed.isoformat())
    return next(iter(dates)) if len(dates) == 1 else None
