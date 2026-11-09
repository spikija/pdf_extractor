"""Native PDF EEG source snapshots and bounded report discovery; no clinical DB dependencies."""
import re
import hashlib
from pathlib import Path
from datetime import date
import pymupdf
from ..workbench_store import ROOT,read,atomic,sha
from ..module_artifacts import digest

LABEL=r'(?:Intensives\s+)?Epilepsiemonitoring|(?:(?:MOBILE|Routine|Video|Langzeit|Schlaf|Schlafentzugs|Ambulant)\s*[- ]\s*)?EEG(?:[- ]Monitoring)?|Elektroen[cz]ephalo(?:gramm|gra(?:ph|f)ie)'
HEAD=re.compile(r'(?im)^[ \t]*(?:'+LABEL+r')\b[^\n]{0,170}')
DATE=re.compile(r'(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*((?:19|20)\d{2})(?!\d)')
PEER=re.compile(r'(?im)^[ \t]*(?:Internist\w*\s+(?:Befund|Konsil|Schlaganfall)|Allgemeines\s+Konsil|Neuropsycholog\w*|Neurologisch\w*\s+Befund|Kernspintomogra\w*|Computertomogra\w*|Magnetresonanz\w*|(?:CT|CTA|MRT|MRI|MRA|PET|EMG|NLG|SPECT|FEES|DSA)\b|Neuroson\w*|Neurograph\w*|Laborwerte|Diagnosen|Dauer\w*medikation|Empfohlene\s+Medikation|Weitere\s+empfohlene|Therapieempfehlung|Mit\s+freundlichen|Beratung/Evaluierung|Transcranielle\s+Magnetstimulation|Röntgen|Ultraschall|Duplex\w*|(?:Evozierte|Motorisch\s+evozierte)\s+Potenziale|EKG[- ]Befund)')
EXTRA_PEER=re.compile(r'(?im)^[ \t]*(?:Iktales\s+EEG\s+vorhanden|Laborbefund|Therapiebericht|Logopäd\w*|Ergotherap\w*|Physiotherap\w*|Procedere|Es\s+wird\s+folgendes\s+Procedere|Zusammenfassung\s*$|Aufnahmestatus|Anamnese\s*\(|Kardio\w*\s+Befund|(?:(?:Ruhe[- ])?EKG|ECG)\s*(?:vom|Befund|\d)|Befund\s+Universitätsklinik|Entlassungsdiagnosen|\w*sonographie\s+Befund)')
RESULT=re.compile(r'normal\w*\s+EEG|Verlangsamung|Erregungsbereitschaft|grundaktivit|grundrhythm|basisrhythm|\d\s*(?:Hz|/sec)|Hirnfunktionsstörung|Funktionsstörung|epileptiform|epilepsietypisch|Normalbefund|Grenzen\s+der\s+Norm|abnorm\w*\s+EEG|Artefakt|unauffällig|sharp[- ]waves?|spike|Delta[- ]|Theta[- ]|elektrographisch|Anfallsmuster|Status[- ]epilepticus',re.I)


def date_from(text):
    short=re.search(r'(?<![\d.])(\d{1,2})\.\s*[-–]\s*(\d{1,2})\.\s*(\d{1,2})\.\s*((?:19|20)\d{2})',text)
    if short:
        try:return date(int(short[4]),int(short[3]),int(short[1])).isoformat(),'DAY'
        except ValueError:pass
    shared=re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*[-–]\s*(\d{1,2})\.\s*(\d{1,2})\.\s*((?:19|20)\d{2})',text)
    if shared and int(shared[2])<=int(shared[4]):
        try:return date(int(shared[5]),int(shared[2]),int(shared[1])).isoformat(),'DAY'
        except ValueError:pass
    m=DATE.search(text)
    if m:
        try:return date(int(m[3]),int(m[2]),int(m[1])).isoformat(),'DAY'
        except ValueError:pass
    m=re.search(r'\b(0?[1-9]|1[0-2])/(20\d{2}|19\d{2})\b',text)
    if m:return f'{m[2]}-{int(m[1]):02}','MONTH'
    m=re.search(r'\b((?:19|20)\d{2})\s*:',text)
    if m:return m[1],'YEAR'
    return None,'UNKNOWN'


def snapshot(patient,output,pdf_root=ROOT/'pdf_db'):
    """Prepare only EEG source text. Preserve saved document IDs when available."""
    known={};context=None
    manifest=ROOT/'data/patient_artifacts'/patient/'module_manifest.json'
    if manifest.exists():
        context=read(manifest).get('source_context')
        if context:
            known={d['filename']:d for d in read(Path(context['directory'])/'source_bundle.json')['documents']}
    if not known:
        frozen=ROOT/'data/debug/full_cohort_20261022T201512+0200/bundles.json'
        if frozen.exists():
            b=next((b for b in read(frozen) if b['patient_id']==patient),None)
            if b:known={d['filename']:d for d in b['documents']}
    documents=[]
    for path in sorted((Path(pdf_root)/patient).glob('*.pdf')):
        checksum=sha(path);prior=known.get(path.name)
        docid=prior['source_document_id'] if prior and prior['sha256']==checksum else int(hashlib.sha256((patient+'/'+path.name).encode()).hexdigest()[:12],16)
        sections=[]
        with pymupdf.open(path) as pdf:
            for n,page in enumerate(pdf,1):
                widgets=[dict(name=w.field_name,value=w.field_value,field_type=w.field_type_string,rect=list(w.rect)) for w in (page.widgets() or [])]
                sections.append(dict(section_id=f'doc{docid}_page{n}',page_start=n,page_end=n,text=page.get_text('text',sort=True),checkbox_fields=widgets))
        documents.append(dict(source_document_id=docid,filename=path.name,sha256=checksum,relative_path=patient+'/'+path.name,sections=sections))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    bundle=dict(patient_id=patient,documents=documents,preparation_version='EEG_SOURCE_1.0')
    atomic(output/'source_bundle.json',bundle)
    summary=read(Path(context['directory'])/'patient_summary.json') if context else dict(patient_id=patient,demographics={'update_fields':{}})
    atomic(output/'patient_summary.json',summary)
    encounters=read(Path(context['directory'])/'encounters.json') if context else []
    atomic(output/'encounters.json',encounters)
    return bundle


def flatten(document):
    text='';ranges=[]
    for section in document['sections']:
        start=len(text);text+=section['text']+'\n'
        ranges.append(dict(start=start,end=len(text),section=section))
    return text,ranges


def locate(ranges,position):
    return next(r['section'] for r in ranges if r['start']<=position<r['end'])


def discover(bundle):
    performed=[];candidates=[]
    for doc in bundle['documents']:
        text,ranges=flatten(doc);heads=[]
        for m in HEAD.finditer(text):
            header=m[0].strip();d,precision=date_from(header)
            valid=bool(d or re.search(r'Befund|Monitoring',header,re.I) or re.fullmatch(LABEL+r'\s*:?\s*',header,re.I))
            if not valid:continue
            # Duration/end-of-pattern lines are not examination headers.
            if re.match(r'EEG[- ](?:Ende|Beginn|Dauer|Muster)',header,re.I):continue
            heads.append(m)
        for i,m in enumerate(heads):
            end=heads[i+1].start() if i+1<len(heads) else len(text)
            peer=PEER.search(text,m.end(),end)
            if peer:end=peer.start()
            peer=EXTRA_PEER.search(text,m.end(),end)
            if peer:end=peer.start()
            # A brief interpretation can be repeated under a second EEG heading;
            # each source occurrence is retained and exact duplicate reports merge later.
            header=m[0].strip();body=text[m.end():end].strip();value,precision=date_from(header)
            if not value:
                value,precision=date_from(body[:80]) if re.match(r'^\s*\(?\d',body) else (None,'UNKNOWN')
            status='EEG_PLANNED' if re.search(r'geplant|empfohlen|angefordert|nicht\s+durchgeführt|soll\s+.*?erfolg',header,re.I) else 'EEG_PERFORMED' if body and RESULT.search(body) else 'EEG_REFERENCED' if value else 'EEG_UNKNOWN'
            start=m.end()
            while start<end and text[start].isspace():start+=1
            while end>start and text[end-1].isspace():end-=1
            first=locate(ranges,m.start());last=locate(ranges,max(start,end-1))
            row=dict(patient_id=bundle['patient_id'],document_id=doc['source_document_id'],source_document=doc['filename'],
                eeg_date=value,eeg_date_precision=precision,source_page_start=first['page_start'],source_page_end=last['page_start'],
                section_ids=[r['section']['section_id'] for r in ranges if r['start']<end and r['end']>m.start()],
                header=header,eeg_report_text=text[start:end],report_start=start,report_end=end,header_start=m.start(),
                report_occurrence_id=f'{bundle["patient_id"]}:EEG_SOURCE:{digest([doc["source_document_id"],m.start()])[:16]}',
                screening_status=status,ranges=ranges)
            candidates.append(row)
            if status=='EEG_PERFORMED':performed.append(row)
    return performed,candidates
