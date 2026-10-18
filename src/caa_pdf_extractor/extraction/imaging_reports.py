"""Source-first study discovery shared by radiology v2 and PET examinations.

Offsets address document_text(), a versioned concatenation of prepared source
sections. Report fragments retain separate exact offsets; no synthetic quotation.
"""
from dataclasses import dataclass, asdict, field
from datetime import date
import re
from .radiology_artifacts import digest

VERSION='imaging_logical_headers_v2'
DATE=r'(?<!\d)(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\d)'
MRI=r'(?:Kernspintomogra(?:phie|fie)|Magnetresonanztomogra(?:phie|fie)|c?MRT|MRI|MRA|MR)'
CT=r'(?:Computertomogra(?:fie|phie)|CTA|CCT|CT)'
PET=r'(?:(?:18F[- ]*)?(?:FDG|Amyloid|[βß]-?Amyloid)[-\s]*)?PET(?:\s*[-/]\s*CT)?'
TRACER=r'(?:Florbetaben|Florbetapir|Flutemetamol)'
HEADER=re.compile(r'^[ \t]*(?P<label>'+MRI+'|'+PET+'|'+TRACER+'|'+CT+r')\b[^\n]*',re.I|re.M)
STOP=re.compile(r'^[ \t]*(?:Neuroson\w*|Internistisch\w*\s+(?:Konsil|Befund)|ECHOKARDIO\w*|'
    r'Elektroenzephalo\w*|ELEKTROENZEPHALO\w*|EEG\b|Neuropsycholog\w*|VIDEO-EEG|EMG/|'
    r'Laborwerte|Entlassungsmedikation|Weitere\s+empfohlene|Zusammenfassung\s+des\s+Aufenthalts|'
    r'Angiogra\w*\s*:|DSA\b|Röntgen\s*:|SPECT\b|Ultraschall\s*:|Sonogra\w*|'
    r'Abdomensonogra\w*|Thoraxsonogra\w*|NIERENSONOGRAPHIE|Urologisches\s+Konsil|ZVK\b|Beratung\s*\()',re.I|re.M)
PLANNED=re.compile(r'\b(?:geplant\w*|empfohlen\w*|vorgesehen\w*|angefordert\w*|nicht\s+(?:erfolgt|durchgeführt)|scheduled|recommended)\b',re.I)

def document_text(document):return '\n'.join(s.source_text for s in document.sections)
def parse_date(value):
    try:return date.fromisoformat(value).isoformat() if '-' in value else date(*map(int,value.split('.')[::-1])).isoformat()
    except ValueError:return None
def explicit_date(header):
    # Discard comparison clauses, not the legitimate date elsewhere in a header.
    clean=re.split(r'\b(?:Vergleich\w*|Voruntersuch\w*|comparison|previous)\b',header,flags=re.I)[0]
    values={parse_date(m.group()) for m in re.finditer(DATE,clean)}-{None}
    return next(iter(values)) if len(values)==1 else None
def discharge_date(document):
    top='\n'.join(s.source_text for s in document.sections if s.page_start==1)
    values=[]
    for m in re.finditer(r'\bvon\s+('+DATE+r')\s+bis\s+('+DATE+r')\s+zur\s+station[äa]ren',top,re.I):values.append(parse_date(m[2]))
    for m in re.finditer(r'(?im)^\s*(?:Entlassungsdatum|Discharge date)\s*:\s*('+DATE+r')',top):values.append(parse_date(m[1]))
    values=set(values)-{None}
    return next(iter(values)) if len(values)==1 else None
def date_for(header,report,document,allow_fallback=True):
    value=explicit_date(header)
    if not value:
        attached=re.search(r'(?im)^\s*(?:Untersuchungsdatum|Examination date|Untersuchung vom)\s*:?\s*('+DATE+r')',report)
        if attached:value=parse_date(attached[1])
    if value:return value,'REPORT_EXPLICIT'
    if allow_fallback and document.document_date:return document.document_date,'DOCUMENT_DATE'
    if allow_fallback and (value:=discharge_date(document)):return value,'DOCUMENT_DISCHARGE_DATE'
    return None,'UNKNOWN'
def modality_for(header,report):
    if re.search(PET+'|'+TRACER,header,re.I):
        if re.search(r'amyloid|Florbetaben|Florbetapir|Flutemetamol|[βß].?Amyloid',header+'\n'+report,re.I):return 'PET_CT_AMYLOID'
        if re.search(r'FDG|F18\s*FDG',header+'\n'+report,re.I):return 'PET_CT_FDG'
        return 'OTHER'
    if re.match(r'\s*MRA\b',header,re.I):return 'MRA'
    if re.match(MRI,header.strip(),re.I):return 'MRI'
    return 'CTA' if re.match(r'\s*CTA\b',header,re.I) else 'CT'

@dataclass
class ImagingStudy:
    patient_id:str
    source_document_id:int
    procedure_id:str
    modality:str
    exam_date:str|None
    exam_date_source:str
    header:str
    report_text:str
    section_id:str
    page_start:int
    page_end:int
    source_spans:list[dict]=field(default_factory=list)
    discovery_kind:str='FORMAL_REPORT'
    kind:str='RADIOLOGY'
    def context(self):return {k:v for k,v in asdict(self).items() if k!='report_text'}

def _location(document,offset):
    start=0
    for section in document.sections:
        end=start+len(section.source_text)
        if start<=offset<=end:return section.section_id,section.page_start,section.page_end
        start=end+1
    raise ValueError('Imaging source offset outside document')

def discover_document(document,patient_id,allow_fallback=True):
    text=document_text(document);matches=list(HEADER.finditer(text));headers=[];consumed=0
    for m in matches:
        if m.start()<consumed:continue
        line=m.group().strip()
        # A performed report heading has a colon, a parenthesized date, or an
        # explicit labelled date. Narrative MRI comparisons are not procedures.
        if not (re.match(r'\s*(?:'+MRI+'|'+PET+'|'+TRACER+'|'+CT+r')\s*:',line,re.I)
                or re.search(r'\('+DATE+r'\)|\bam\s+'+DATE,line,re.I)
                or re.match(CT+r'\s+vom\s+'+DATE+r'\s*$',line,re.I)
                or re.match(CT+r'\s+(?:Schädel|Cerebrum|Neurokranium)\b',line,re.I)
                or re.fullmatch(r'CCT\s*',line,re.I)
                or re.fullmatch(MRI+'|'+PET+'|'+TRACER,line,re.I)):continue
        if PLANNED.search(line):continue
        end=m.end();header=line
        while not explicit_date(header):
            n=re.match(r'\n[ \t]*(\S[^\n]*)',text[end:])
            if not n or len(header)>1500:break
            following=n[1]
            # Only procedure vocabulary or a standalone attached date may wrap.
            # Never consume a comparison or a result sentence just for its date.
            continuation=re.match(r'(?:'+MRI+'|'+CT+'|'+PET+r')\s*:',following,re.I)
            vocabulary=re.fullmatch(r'(?:arterielle\s+)?(?:intracranielle|intrakranielle)\s+Gefäße\s+(?:nativ|mit KM)(?:\s*\('+DATE+r'\))?\s*,?|'
                r'(?:nativ|SWI/Hämosequenz|Diffusion|Black Blood(?: Sequenz)?|Sequenz)(?:\s*\('+DATE+r'\))?\s*,?|'
                r'peripher venösen Zugangs onA\s*\('+DATE+r'\)|\('+DATE+r'\)',following,re.I)
            wrapped_vessels=re.match(r'(?:arterielle\s+)?intracranielle\s+Gefäße\s+(?:nativ|mit KM),\s*Kernspintomogra',following,re.I)
            protocol_wrap=re.match(r'(?:Sequenz|SWI/Hämosequenz|Diffusion|Black Blood(?: Sequenz)?|Multiplanare Rekonstruktion)(?:,|\s*\()',following,re.I)
            ct_protocol=re.match(CT+r'\b',line,re.I) and re.fullmatch(r'(?:Schädel\s+nativ\s*,?|CT[- ]Angiogra(?:fie|phie)[^.!?\n]*|CTA\s*[^.!?\n]*)',following,re.I)
            if not (continuation or vocabulary or wrapped_vessels or protocol_wrap or ct_protocol):break
            end+=n.end();header=text[m.start():end].strip()
        consumed=end;headers.append((m.start(),end,header))
    studies=[]
    for n,(start,end,header) in enumerate(headers):
        stop=headers[n+1][0] if n+1<len(headers) else len(text)
        boundary=STOP.search(text,end,stop)
        if boundary:stop=boundary.start()
        left=end
        while left<stop and text[left].isspace():left+=1
        right=stop
        while right>left and text[right-1].isspace():right-=1
        report=text[left:right];modality=modality_for(header,report)
        if modality in ('CT','CTA') and re.search(r'PET',header,re.I):continue
        exam_date,date_source=date_for(header,report,document,allow_fallback)
        section,page,_=_location(document,start);_,_,lastpage=_location(document,max(start,right-1))
        identity=digest({'version':VERSION,'doc':document.source_document_id,'section':section,'start':start,'header':header})[:16]
        studies.append(ImagingStudy(patient_id,document.source_document_id,f'doc{document.source_document_id}_img_{identity}',
            modality,exam_date,date_source,header,report,section,page,lastpage,
            [{'role':'header','start':start,'end':end,'text':text[start:end]},
             *[{'role':'header_fragment','start':start+p.start(),'end':start+p.end(),'text':p.group()} for p in re.finditer(r'[^\n]+',text[start:end])],
             {'role':'report','start':left,'end':right,'text':report}],kind='PET' if re.match(r'\s*(?:'+PET+'|'+TRACER+r')\b',header,re.I) else 'RADIOLOGY'))
    # Repeated composite PET/CT headers are explicitly the same source parent
    # only when header/date agree. Preserve every fragment; never merge by result.
    grouped=[]
    for study in studies:
        same=next((s for s in grouped if s.kind=='PET' and study.kind=='PET' and s.exam_date is not None
                   and s.exam_date==study.exam_date and ' '.join(s.header.split())==' '.join(study.header.split())
                   and (s.modality==study.modality or 'OTHER' in (s.modality,study.modality))),None)
        if same:
            same.report_text+='\n'+study.report_text;same.source_spans+=study.source_spans;same.page_end=study.page_end
            if same.modality=='OTHER':same.modality=study.modality
        else:grouped.append(study)
    # Explicit, dated performed-study summaries are source procedures too. Do
    # not promote partial-date histories, plans or comparison-only mentions.
    performed=re.compile(r'\b(?:Es\s+wurde\s+am\s+('+DATE+r')\s+ein\s+Sch[äa]del[- ]MRT\s+durchgeführt|'
                         r'Es\s+erfolgte\s+am\s+('+DATE+r')\s+ein\s+FDG[- ]PET)\b',re.I)
    for m in performed.finditer(text):
        if any(any(span['start']<=m.start()<span['end'] for span in s.source_spans) for s in grouped):continue
        end=re.search(r'\n\s*\n',text[m.end():]);stop=m.end()+end.start() if end else min(len(text),m.end()+1000)
        sentence_end=re.search(r'\.(?:\s+)(?=[A-ZÄÖÜ])',text[m.end():stop])
        if sentence_end:stop=m.end()+sentence_end.start()+1
        raw=text[m.start():stop].strip();exam_date=parse_date(m[1] or m[2]);modality='MRI' if m[1] else 'PET_CT_FDG'
        if any(s.modality==modality and s.exam_date==exam_date for s in grouped):continue
        section,page,lastpage=_location(document,m.start());identity=digest({'doc':document.source_document_id,'start':m.start(),'summary':m.group()})[:16]
        grouped.append(ImagingStudy(patient_id,document.source_document_id,f'doc{document.source_document_id}_summary_{identity}',
            modality,exam_date,'REPORT_EXPLICIT',m.group(),raw,section,page,lastpage,
            [{'role':'report','start':m.start(),'end':m.start()+len(raw),'text':raw}],discovery_kind='PERFORMED_STUDY_SUMMARY',kind='PET' if m[2] else 'RADIOLOGY'))
    return sorted(grouped,key=lambda s:s.source_spans[0]['start'])

def discover_studies(bundle,allow_document_fallback=True):
    return [s for d in bundle.documents for s in discover_document(d,bundle.patient_id,allow_document_fallback)]
