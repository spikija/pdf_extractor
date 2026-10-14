"""Focused instrument-based reports inside prepared sections, not bedside status."""
from dataclasses import asdict, dataclass
from datetime import date
import re

HEADER = re.compile(r'^\s*(?:ECHOKARDIOGRAPHIE\b[^\n]*|'
    r'(?:(?:Ven[öo]se|Arterielle|Transkranielle|Transcranielle|Extrakranielle|Extracranielle)\s+)?'
    r'(?:Farbduplex\w*|Duplexsonographie|Duplex|Dopplersonographie|Doppler|Sonographie|Ultraschall|Ultrasound|Neurosonologie|Neurosonographie)\b[^\n]*|'
    r'(?:Carotis|Karotis|Halsgef[äa][ßs]e)[- ]?(?:Duplex|Doppler|Sonographie)\b[^\n]*|'
    r'(?:(?:Amyloid|FDG)[- ]PET(?:[- /]CT)?|PET(?:[- /]CT)?\s+\(?(?:Amyloid|FDG)|SPECT|'
    r'(?:MRT|MRI|Magnetresonanztomographie)\s+(?:Abdomen|K[öo]rper|Ganzk[öo]rper|body))\b[^\n]*)', re.I | re.M)
STOP = re.compile(r'^\s*(?:Ambulanzbrief\b|Therapieempfehlung\b|Beurteilung\s*:|'
    r'Internistisches\s+Konsil|Computertomograf\w*\s*:|Magnetresonanz\w*\s*:|'
    r'Laborwerte\b|Mikrobiologie\b|Bakteriologie\b|Neurologischer\s+Status\b|NIHSS\b|MMSE\b|MoCA\b|'
    r'(?:CT|MRT|MRI)\s+(?:Sch[äa]del|Gehirn|brain)\b|Medikation\b|Mit freundlichen|Universit[äa]tsklinikum\b)', re.I | re.M)
PLANNED = re.compile(r'\b(?:empfohlen|geplant|vorgesehen|angefordert|scheduled|recommended)\b', re.I)


@dataclass
class ExaminationInput:
    patient_id: str
    examination_id: str
    source_document_id: int
    section_id: str
    page_number: int
    header: str
    text: str
    type: str
    location: str | None
    date_examination: str | None

    def context(self):
        return {k: v for k, v in asdict(self).items() if k != 'text'}


def header_date(header):
    if re.search(r'Vergleich|Voruntersuch|document|Dokument|Aufnahme|Entlassung', header, re.I):
        return None
    dates = set()
    for d, m, y in re.findall(r'(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)', header):
        try: dates.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError: return None
    for iso in re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)', header):
        try: dates.add(date.fromisoformat(iso).isoformat())
        except ValueError: return None
    return next(iter(dates)) if len(dates) == 1 else None


def header_mapping(header, body):
    lower = header.casefold()
    if re.search(r'carotis|karotis|halsgef',lower) and re.search(r'transkran|transcran|gehirn|brain',lower):
        raise ValueError('Mixed neck/brain examination header is ambiguous; separate reports first')
    kind = ('pet ct amyloid' if 'amyloid' in lower else 'pet ct fdg' if 'fdg' in lower
            else 'spect' if lower.startswith('spect') else 'mri body' if re.match(r'mrt|mri|magnetresonanz',lower) else 'ultrasound')
    location = ('heart' if 'echokard' in lower else 'neck vessels' if re.search(r'carotis|karotis|halsgef',lower)
                else 'brain' if re.search(r'transkran|transcran|gehirn|brain',lower)
                else 'abdomen' if 'abdomen' in lower
                else 'body' if re.search(r'k[öo]rper|\bbody\b',lower) else None)
    if location is None and kind == 'ultrasound':
        neck = bool(re.search(r'\b(?:ICA|A\.?\s*carotis|Halsgef[äa][ßs]e|carotid)\b',body,re.I))
        brain = bool(re.search(r'\b(?:ACM|MCA|A\.?\s*cerebri|transkran\w*|transcran\w*)\b',body,re.I))
        if neck and brain: raise ValueError('Mixed neck/brain examination anatomy is ambiguous; separate source reports first')
        if neck: location='neck vessels'
        elif brain: location='brain'
    if location is None:
        explicit = {value for pattern,value in ((r'\b(?:Gehirn|brain)\b','brain'),
            (r'\b(?:Herz|heart)\b','heart'),(r'\b(?:Abdomen|abdominal)\b','abdomen'),
            (r'\b(?:Ganzk[öo]rper|whole.body)\b','body')) if re.search(pattern,body,re.I)}
        if len(explicit)>1: raise ValueError('Multiple explicit examination locations; mapping is ambiguous')
        if explicit: location=next(iter(explicit))
    return kind, location


def select_examination_inputs(bundle):
    inputs = []
    for document in bundle.documents:
        for section in document.sections:
            if set(section.candidate_targets) & {'medications', 'laboratory'}:
                continue
            matches = list(HEADER.finditer(section.text))
            for index, match in enumerate(matches):
                header = match.group().strip()
                if PLANNED.search(header): continue
                end = matches[index+1].start() if index+1 < len(matches) else len(section.text)
                boundary = STOP.search(section.text, match.end(), end)
                if boundary: end = boundary.start()
                body = section.text[match.end():end].strip()
                if not body: continue
                text = section.text[match.start():end].strip()
                kind, location = header_mapping(header,body)
                inputs.append(ExaminationInput(bundle.patient_id, f'{section.section_id}_exam{index+1:02d}',
                    document.source_document_id, section.section_id, section.page_start, header, text,
                    kind, location, header_date(header)))
    return inputs
