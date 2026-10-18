"""Focused instrument-based reports inside prepared sections, not bedside status."""
from dataclasses import asdict, dataclass, field
from datetime import date
import re

HEADER = re.compile(r'^\s*(?:ECHOKARDIOGRAPHIE\b[^\n]*|'
    r'(?:(?:Ven[öo]se|Arterielle|Transkranielle|Transcranielle|Extrakranielle|Extracranielle)\s+)?'
    r'(?:Farbduplex\w*|Duplexsonographie|Duplex|Dopplersonographie|Doppler|Sonographie|Ultraschall|Ultrasound|Neurosonologie|Neurosonographie)\b[^\n]*|'
    r'(?:Carotis|Karotis|Halsgef[äa][ßs]e)[- ]?(?:Duplex|Doppler|Sonographie)\b[^\n]*|'
    r'(?:(?:Amyloid|FDG)[- ]PET(?:[- /]CT)?|PET(?:[- /]CT)?\s+\(?(?:Amyloid|FDG)|SPECT|'
    r'(?:MRT|MRI|Magnetresonanztomographie)\s+(?:Abdomen|K[öo]rper|Ganzk[öo]rper|body))\b[^\n]*)', re.I | re.M)
STOP = re.compile(r'^\s*(?:Ambulanzbrief\b|Therapieempfehlung\b|Beurteilung\s*:|'
    r'Internistisches[^\n]*Konsil|Gray-Weale-Klassifikation\s*:|Computertomograf\w*\s*:|Magnetresonanz\w*\s*:|'
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
    parent_examination_id: str | None = None
    source_spans: list[dict] = field(default_factory=list)
    introductory_metadata: list[dict] = field(default_factory=list)
    excluded_subreports: list[dict] = field(default_factory=list)
    imaging_modality: str | None = None
    exam_date_source: str | None = None
    pet_report_text: str | None = None

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
        neck = bool(re.search(r'\b(?:ICA|A\.?\s*carotis|Extrakran\w*\s+Carotisabschnitte|Extracran\w*\s+Carotisabschnitte|Halsgef[äa][ßs]e|carotid)\b',body,re.I))
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


SUBREPORT = re.compile(r'^[ \t]*(?P<label>(?:Extrakran\w*|Extracran\w*|Transkran\w*|Transcran\w*|Transtemporal)[^\n]*)', re.I | re.M)
SIDE = re.compile(r'^[ \t]*(?:Links|Rechts|Left|Right)\s*:', re.I | re.M)


def split_subreports(body):
    """Return exact, explicit anatomy spans; never guess a mixed boundary."""
    matches = list(SUBREPORT.finditer(body))
    if not matches:
        return []
    groups = []
    for match in matches:
        location = 'neck vessels' if re.match(r'extra', match['label'], re.I) else 'brain'
        if not groups or groups[-1][1] != location:
            groups.append((match.start(), location))
    if len({location for _, location in groups}) < 2:
        return []
    # Do not silently discard findings preceding the first explicit subreport.
    prefix=body[:groups[0][0]]
    if prefix.strip():
        lines=[line.strip() for line in prefix.splitlines() if line.strip()]
        if all(re.match(r'(?:A\.?\s*carotis\b|Extrakran\w*|Extracran\w*)',line,re.I) for line in lines):
            if groups[0][1]=='neck vessels':groups[0]=(0,'neck vessels')
            else:groups.insert(0,(0,'neck vessels'))
        elif (not all(re.match(r'(?:Untersuchungstechnik|Technik|Datum|Administrativer Hinweis|Dokumentationshinweis)\s*:',line,re.I) for line in lines)
              or re.search(r'Stenose|Blutung|Läsion|pathologisch|auffällig|Thrombus|Verschluss|Infarkt',prefix,re.I)):
            raise ValueError('Mixed anatomy has unassigned introductory findings')
    spans = []
    for index, (start, location) in enumerate(groups):
        end = groups[index+1][0] if index+1 < len(groups) else len(body)
        span = body[start:end].strip()
        # Side-labelled fragments are children of this exact parent study/anatomy
        # span. Keep its contiguous source order and internal labels in one result.
        spans.append((location, span, f'sub{index+1:02d}'))
    return spans


def select_examination_inputs(bundle):
    inputs = []
    for document in bundle.documents:
        for section in document.sections:
            if set(section.candidate_targets) & {'medications', 'laboratory'}:
                continue
            matches = list(HEADER.finditer(section.text))
            for index, match in enumerate(matches):
                header = match.group().strip()
                # PET is independently discovered from source reports below;
                # the old anchored Amyloid/FDG selector must not duplicate it.
                if re.search(r'PET',header,re.I):continue
                if PLANNED.search(header): continue
                end = matches[index+1].start() if index+1 < len(matches) else len(section.text)
                boundary = STOP.search(section.text, match.end(), end)
                if boundary: end = boundary.start()
                body = section.text[match.end():end].strip()
                if not body: continue
                text = section.text[match.start():end].strip()
                split = split_subreports(body) if re.search(r'neuroson|duplex|doppler|ultras', header, re.I) else []
                if split:
                    excluded=[{'classification':'NECK_VESSEL','reason':'NOT_PERFORMED',
                               'source_spans':source_spans(section.text,span,match.end(),end)}
                              for location,span,suffix in split if location=='neck vessels' and not_performed(span)]
                    for location, span, suffix in split:
                        if location=='neck vessels' and not_performed(span):continue
                        # Both header and result are literal source spans. Parent date is explicit.
                        first_line = span.splitlines()[0].strip()
                        # A label is the header; the finding after ':' remains result evidence.
                        subheader = first_line.split(':', 1)[0] + ':' if ':' in first_line else first_line
                        inputs.append(ExaminationInput(bundle.patient_id,
                            f'{section.section_id}_exam{index+1:02d}_{suffix}', document.source_document_id,
                            section.section_id, section.page_start, subheader, span, 'ultrasound',
                            location, header_date(header),
                            parent_examination_id=f'{section.section_id}_exam{index+1:02d}',
                            source_spans=source_spans(section.text,span,match.end(),end),
                            introductory_metadata=introductory_metadata(body),excluded_subreports=excluded))
                    continue
                kind, location = header_mapping(header,body)
                inputs.append(ExaminationInput(bundle.patient_id, f'{section.section_id}_exam{index+1:02d}',
                    document.source_document_id, section.section_id, section.page_start, header, text,
                    kind, location, header_date(header),parent_examination_id=f'{section.section_id}_exam{index+1:02d}',
                    source_spans=source_spans(section.text,text,match.start(),end)))
    from .neck_ultrasound import extend_cervical_reports
    return extend_cervical_reports(inputs,bundle)+select_pet_inputs(bundle)


def select_pet_inputs(bundle):
    """PET discovery is independent of unrelated anatomy/ultrasound selectors."""
    inputs=[]
    from .imaging_reports import discover_studies, document_text
    docs={d.source_document_id:d for d in bundle.documents}
    for study in discover_studies(bundle):
        if study.kind!='PET':continue
        source=document_text(docs[study.source_document_id])
        start=min(s['start'] for s in study.source_spans);end=max(s['end'] for s in study.source_spans)
        text=source[start:end]
        kind={'PET_CT_AMYLOID':'pet ct amyloid','PET_CT_FDG':'pet ct fdg'}.get(study.modality,'other')
        location='body' if re.search(r'(?:Kopf|Kopfes)\s+bis\s+zu\s+den\s+proximalen\s+Oberschenkeln',study.report_text,re.I) else 'brain' if re.search(r'Hirn:\s*Aktivit[äa]t|vom\s+Sch[äa]del|\bGehirn\b|\bbrain\b',study.report_text,re.I) else None
        inputs.append(ExaminationInput(bundle.patient_id,study.procedure_id,study.source_document_id,
            study.section_id,study.page_start,study.header,text,kind,location,study.exam_date,
            parent_examination_id=study.procedure_id,source_spans=study.source_spans,
            imaging_modality=study.modality,exam_date_source=study.exam_date_source,pet_report_text=study.report_text))
    return inputs


def source_spans(section_text,span,search_start=0,search_end=None):
    start=section_text.find(span,search_start,len(section_text) if search_end is None else search_end)
    if start<0:raise ValueError('Examination span absent from exact source')
    sides=list(SIDE.finditer(span))
    result=[{'role':'parent_anatomy_span','start':start,'end':start+len(span),'text':span}]
    for n,side in enumerate(sides):
        end=sides[n+1].start() if n+1<len(sides) else len(span)
        result.append({'role':'side_subreport','start':start+side.start(),'end':start+end,'text':span[side.start():end]})
    return result


def introductory_metadata(body):
    first=SUBREPORT.search(body)
    if not first:return []
    text=body[:first.start()]
    if not text.strip():return []
    classification='NECK_VESSEL' if re.search(r'A\.?\s*carotis',text,re.I) else 'GLOBAL_EXAM_METADATA' if re.search(r'Untersuchungstechnik|Technik|Datum',text,re.I) else 'OTHER'
    return [{'classification':classification,'text':text,'persisted_as_result':classification=='NECK_VESSEL' and not not_performed(text)}]


def not_performed(span):
    """Explicit unmeasured neck table, audit-only; never discard actual findings."""
    lines=[line.strip() for line in span.splitlines() if line.strip()]
    for line in lines:
        match=re.fullmatch(r'(?:A\.\s*carotis\s+(?:communis|interna|externa)|Extrakran\w*\s+Vertebralisabschnitte)\s*:(.*)',line,re.I)
        if not match or not re.search(r'nicht untersucht',match[1],re.I):return False
        remainder=re.sub(r'nicht untersucht','',match[1],flags=re.I)
        if re.search(r'[^\s|\-]',remainder):return False
    return bool(lines)
