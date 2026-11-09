"""TFNE 0.1 history-only routing and an auditable input firewall."""
import re
from pathlib import Path
from .workbench_store import ROOT,read
from .module_artifacts import digest
from .section_dictionary import classify_heading,heading_matches,load_dictionary

ALLOWED={'ANAMNESE','ZUSAMMENFASSUNG_DES_AUFENTHALTES'}
FORBIDDEN=re.compile(r'\b(?:EEG|cEEG|vEEG|MRT|MRI|CT|CCT|CTA|CMRT|SWI|FLAIR|DWI|CAA|cSS|cSAH|Boston|Mikroblutungen|Siderose)\b|Elektroen\w*|Amyloidangiopathie|Amyloid[- ]?spells|Kernspintomogra\w*|Computertomogra\w*|Magnetresonanz\w*|superfiziell\w*\s+Siderose|konvexit\w*\s+Subarachnoidal',re.I)



def source_folder(manifest):
    # Prefer an already cleaned snapshot; never invoke another extraction module.
    for context in (manifest.get('eeg_source_context'),manifest.get('source_context')):
        if context and (Path(context['directory'])/'source_bundle.json').exists():return Path(context['directory'])
    return None


def section_type(heading,dictionary=None):
    found=classify_heading(heading,dictionary)
    if not found or found['canonical_section'] not in ('ANAMNESE','HOSPITAL_SUMMARY'):return None
    if found['module_actions'].get('TFNE_CLINICAL_HISTORY')!='INCLUDE':return None
    return 'ANAMNESE' if found['canonical_section']=='ANAMNESE' else 'ZUSAMMENFASSUNG_DES_AUFENTHALTES'


def select_sections(patient,document):
    """Return page-local, losslessly located narrative spans and excluded passages."""
    selected=[];excluded=[];active=None;dictionary=load_dictionary()
    # Match across page joins, then clip headings to each unchanged parent text.
    joined=''.join(s['text']+'\n' for s in document['sections'])
    document_marks=list(heading_matches(joined,dictionary))
    document_offset=0
    for section in document['sections']:
        text=section['text'];heading=section.get('heading') or ''
        if heading:active=section_type(heading,dictionary)
        marks=[(max(0,m['start']-document_offset),min(len(text),m['end']-document_offset),
                section_type(m['source_heading'],dictionary))
               for m in document_marks if m['start']<document_offset+len(text) and m['end']>document_offset]
        document_offset+=len(text)+1
        marks.sort();position=0
        regions=[]
        for start,end,kind in marks:
            if start<position:continue
            if active and start>position:regions.append((position,start,active))
            active=kind;position=end
        if active and position<len(text):regions.append((position,len(text),active))
        for a,b,kind in regions:
            # Sentence/paragraph units retain exact original offsets. Dates,
            # decimals and medical abbreviations are not rewritten.
            pieces=re.finditer(r'.+?(?:\n[ \t]*\n|(?<=[.!?])\s+(?=[A-ZÄÖÜ])|$)',text[a:b],re.S)
            kept=[]
            for piece in pieces:
                lo=a+piece.start();hi=a+piece.end()
                while lo<hi and text[lo].isspace():lo+=1
                while hi>lo and text[hi-1].isspace():hi-=1
                if lo==hi:continue
                quote=text[lo:hi]
                reason='EMBEDDED_EEG_IMAGING_CAA' if FORBIDDEN.search(quote) else None
                if reason:
                    excluded.append(dict(patient_id=patient,document_id=document['source_document_id'],page=section['page_start'],section_id=section['section_id'],section_type=kind,start=lo,end=hi,text=quote,reason=reason));continue
                kept.append(dict(start=lo,end=hi,text=quote))
            if kept:
                sid=section['section_id']+':TFNE:'+digest([a,b,kind])[:10]
                selected.append(dict(patient_id=patient,source_document_id=document['source_document_id'],document_id=document['source_document_id'],filename=document['filename'],
                    page=section['page_start'],section_id=sid,parent_section_id=section['section_id'],section_type=kind,
                    source_text=text,source_start=a,source_end=b,spans=kept))
    return selected,excluded


def model_sections(sections):
    out=[];n=0
    for source in sections:
        spans=[]
        for row in source['spans']:
            n+=1;spans.append(dict(span_id=f'S{n:04}',text=row['text']))
        out.append(dict(section_id=source['section_id'],section_type=source['section_type'],spans=spans))
    if any(s['section_type'] not in ALLOWED or any(FORBIDDEN.search(p['text']) for p in s['spans']) for s in out):raise ValueError('TFNE model-input firewall failed')
    return out


def span_index(sections):
    out={};n=0
    for source in sections:
        for row in source['spans']:
            n+=1;out[f'S{n:04}']=dict(document_id=source['document_id'],source_document_id=source['document_id'],page=source['page'],
                section_id=source['section_id'],parent_section_id=source['parent_section_id'],section_type=source['section_type'],evidence_text=row['text'],
                evidence_start=row['start'],evidence_end=row['end'])
    return out
