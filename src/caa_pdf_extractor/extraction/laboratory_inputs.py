"""Six-target candidate search; ranges and summary columns remain explicit context."""
from dataclasses import asdict, dataclass
from datetime import date
import re
from .radiology_artifacts import digest

PATTERNS = {
    'csf_beta4240_ratio': r'(?:A\s*(?:β|beta)\s*42\s*[/：:]\s*40|Amyloid\s*beta\s*42\s*[/：:]\s*40|Beta[- ]Amyloid\s*Quotient)',
    'csf_beta42': r'(?:A\s*(?:β|beta)\s*42|Beta[- ]Amyloid\s*1[- ]42|Amyloid\s*beta\s*42)',
    'csf_tau_total': r'(?<!\w)(?:Tau\s+gesamt|Gesamt[-\s]+Tau|total[-\s]+tau|t[-\s]?Tau)(?!\w)',
    'csf_ptau_total': r'(?<!\w)(?:p[-\s]?Tau|phospho[-\s]+Tau|phosphoryliertes\s+Tau)(?!\w)',
    'crp': r'(?:\bCRP\b|C[- ]reaktives\s*Protein)',
    'hba1c': r'(?:HbA1c\b|H[äa]moglobin\s*A1c)',
}
MATCHERS = {name: re.compile(pattern, re.I) for name, pattern in PATTERNS.items()}
DATE = re.compile(r'(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})(?!\d)')
DATE_CONTEXT = re.compile(r'Labor|sample|sampling|Entnahme|Liquor|CSF|examination', re.I)
FORBIDDEN_DATE = re.compile(r'Aufnahme|Entlassung|admission|discharge|document|Dokument|geboren|\bgeb\b|birth|born|\bbis\b|\bto\b', re.I)
SUMMARY = re.compile(r'Erster\s+Wert.*Letzter\s+Wert|first\s+value.*last\s+value', re.I)


@dataclass(frozen=True)
class LaboratoryInput:
    candidate_id: str
    biomarker: str
    source_document_id: int
    section_id: str
    page_number: int
    text: str
    context_text: str
    associated_date: str | None
    summary_layout: bool
    structured_table_available: bool

    def context(self):
        return asdict(self)


def explicit_date(text):
    if FORBIDDEN_DATE.search(text):
        return None
    values = DATE.findall(text)
    if len(set(values)) != 1:
        return None
    value = values[0]
    try:
        return (date.fromisoformat(value) if '-' in value else date(*map(int, value.split('.')[::-1]))).isoformat()
    except ValueError:
        return None


def select_laboratory_inputs(bundle):
    inputs = []
    for document in bundle.documents:
        for section in document.sections:
            rows = [(section.page_start, line, False) for line in section.text.splitlines()]
            for table in section.tables:
                rows.extend((table.page_number, '  '.join(str(cell or '') for cell in row), True) for row in table.rows)
            headers = [line for _, line, _ in rows if SUMMARY.search(line)]
            date_header = section.heading if section.heading and DATE_CONTEXT.search(section.heading) else ''
            for index, (page, line, structured) in enumerate(rows):
                if DATE_CONTEXT.search(line) and DATE.search(line) and not any(m.search(line) for m in MATCHERS.values()):
                    date_header = line
                marker = next((name for name, regex in MATCHERS.items() if regex.search(line)), None)
                if marker is None or re.search(r'HbA1c[- ]Wert', line, re.I):
                    continue
                if len(line) > 2000:
                    raise ValueError('Laboratory candidate exceeds focused input limit')
                continuation = ''
                if marker == 'hba1c' and 'IFCC' in line.upper() and index + 1 < len(rows):
                    following = rows[index + 1][1]
                    if re.search(r'\bol\b', following):
                        continuation = following
                context = '\n'.join(filter(None, [date_header, *headers, line, continuation]))
                associated = explicit_date(line) if DATE.search(line) else explicit_date(date_header)
                inputs.append(LaboratoryInput(f'{section.section_id}_lab{index+1:03d}', marker,
                    document.source_document_id, section.section_id, page, line, context,
                    associated, bool(headers), structured))
    return inputs


def source_manifest(bundle):
    return digest(bundle.to_dict())
