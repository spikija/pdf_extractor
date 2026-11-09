"""Bounded medication sections and table continuations; no clinical inference."""
from dataclasses import dataclass, field
import re

VERSION = 'MEDICATION_SOURCE_1.1'
MEDICATION_HEADING = re.compile(r"\b(?:\w*medikation\w*|medikament\w*|medication\w*|therapieempfehlung\w*)\b", re.I)
EMBEDDED = re.compile(r'(?im)^[ \t]*(?P<heading>(?:(?:aktuelle|empfohlene|weitere\s+empfohlene)\s+)?(?:[\wäÄ]*medikation(?:\s+zum\s+Verlegungszeitpunkt)?|Medikamente|Therapieempfehlungen?))\s*:?\s*(?=\n|$)')
STOP = re.compile(r'^(?:allerg\w*|neurologischer\s*befund|vegetative\s+anamnese|sozialanamnese|(?:andere\s+)?vorerkrankungen|vorkrankheiten|anamnese|diagnosen|befund\w*|noxen|procedere|zusammenfassung|weitere\s+empfohlene\s+maßnahmen|kontroll\w*|blutverdünnung|medikamentenerhebung|weitere\s+medikamente\s+nicht)',re.I)
NOISE = re.compile(r'Universitätsklinikum|Gemeinnützige|Paracelsus|Privatuniversität|Landesgericht|UID-Nr|FN\s*\d|Telefon|Telefax|www\.|geboren am|geb\.:',re.I)
QUALIFIER = re.compile(r'^bei\s+(?:Verlegung|Transfer)\b',re.I)
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
    source_lines: list[str] = field(default_factory=list)


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
    if isinstance(bundle,dict):
        from types import SimpleNamespace
        bundle=SimpleNamespace(documents=[SimpleNamespace(sections=[SimpleNamespace(**dict(s,
            tables=[SimpleNamespace(**t) for t in s.get('tables',[])])) for s in d['sections']]) for d in bundle['documents']])
    inputs = []
    for document in bundle.documents:
        context = None
        previous_page = None
        for section in document.sections:
            opening = section.heading or next((x.strip() for x in section.text.splitlines() if x.strip()), "")
            explicit = bool(MEDICATION_HEADING.search(opening[:160]))
            embedded=list(EMBEDDED.finditer(section.text))
            if explicit:context=opening
            elif section.heading and not QUALIFIER.match(section.heading):context=None
            elif previous_page is not None and section.page_start>previous_page+1:context=None
            schedule_header=any(re.search(r'Medikamente|Früh.*Mittag|Mittag.*Abend',render_row(row),re.I)
                for t in section.tables for row in t.rows[:2])
            # A free-standing schedule table is bounded layout evidence, not arbitrary prose.
            relevant=explicit or bool(context) or bool(embedded) or schedule_header
            tables=_medication_tables(section) if relevant else []
            if tables:
                for t in tables:
                    cleaned=[]
                    for row in t['rows']:
                        cells=list(row)
                        cells[0]=re.sub(r'^Medikamente\s*\n','',str(cells[0]),flags=re.I)
                        cells=[re.sub(r'^(?:Früh|Mittag|Abend|Nacht)\s*\n','',str(c or ''),flags=re.I) for c in cells]
                        cleaned.append(cells)
                    t['rows']=cleaned;t['source_rows']=[render_row(r) for r in cleaned]
                inputs.append(MedicationInput(section.section_id,section.patient_id,section.source_document_id,
                    section.page_start,context or (embedded[0]['heading'] if embedded else 'Medication schedule table'),'',tables))
            # Embedded medication blocks are handled even inside an unrelated narrative section.
            regions=[]
            for n,m in enumerate(embedded):
                end=embedded[n+1].start() if n+1<len(embedded) else len(section.text)
                regions.append((m['heading'],section.text[m.end():end]))
            if explicit and not embedded and not tables:
                regions.append((opening,section.text))
            for heading,text in regions:
                if tables and not re.search(r'pr[äa]medikation|dauermedikation|vormedikation|aktuelle|verlegungszeitpunkt',heading,re.I):continue
                lines=bounded_medication_lines(text,heading)
                if lines:inputs.append(MedicationInput(section.section_id,section.patient_id,section.source_document_id,
                    section.page_start,heading,'\n'.join(lines),[],lines))
            if context and not tables and (STOP.match(opening) or any(STOP.match(line.strip()) for line in section.text.splitlines() if line.strip())):
                context=None
            previous_page=section.page_start
    return inputs


def bounded_medication_lines(text,heading):
    lines=[]
    for line in text.splitlines():
        value=line.strip().lstrip('-• ').strip()
        if not value:continue
        if STOP.match(value):break
        if NOISE.search(value):continue
        if re.match(r'^(?:Bitte|Absprache|Termin|Wir\b|Es\b|Empfohlen\b)',value,re.I):continue
        if MEDICATION_HEADING.fullmatch(value.rstrip(':')) or QUALIFIER.match(value):continue
        # Recommendation headings need explicit drug-list layout/strength, not prose.
        if re.search(r'therapieempfehl',heading,re.I) and not re.search(r'\d\s*(?:mg|µg|mcg|IE|gtt)|\d[-/]\d',value,re.I):continue
        if lines and (re.match(r'^(?:mg\b|ml\b|\d|pausiert\b|gesteigert\b|reduziert\b)',value,re.I)):
            lines[-1]+=' '+value
        else:lines.append(value)
    if re.search(r'aktuelle\s+medikation',heading,re.I):
        return [part.strip() for part in re.split(r',\s+(?=[A-ZÄÖÜ])',' '.join(lines)) if part.strip()]
    return lines


def source_rows(bundle,encounters=None):
    """Materialize every selected row before normalization, including unitless products."""
    encounters=encounters or {}
    rows=[]
    for item in select_medication_inputs(bundle):
        lines=[render_row(row) for table in item.tables for row in table['rows']] or item.source_lines
        for n,line in enumerate(lines):
            rows.append(dict(patient_id=item.patient_id,raw_medication=line,source_document_id=item.source_document_id,
                page=item.page_number,section_id=item.section_id,evidence_id=f'{item.section_id}:MED:{len(rows)}',
                medication_context=item.heading,available_date=(encounters.get(item.source_document_id) or encounters.get(str(item.source_document_id),{})).get('discharge_date'),
                source_row_type='TABLE' if item.tables else 'BOUNDED_LIST',source_row_position=n+1,source_selector_version=VERSION))
    return rows
