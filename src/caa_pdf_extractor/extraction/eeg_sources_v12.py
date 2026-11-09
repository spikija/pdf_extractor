"""Bounded EEG examinations and continuous report intervals; no non-EEG extraction."""
import copy
import re
from datetime import date,datetime
from .eeg_sources import flatten,locate,date_from,RESULT
from ..module_artifacts import digest
from .eeg_section_headings_v12 import classify_heading,section_headings
from ..section_dictionary import eeg_action,load_dictionary

SHORT_DATE=re.compile(r'(?<![\d.])(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4}|\d{2})(?!\d)')
INTERVAL=re.compile(r'(\d{1,2}\.\d{1,2}\.\d{2,4})\s+(\d{1,2}:\d{2})\s*(?:Uhr)?\s*[-–]\s*(\d{1,2}\.\d{1,2}\.\d{2,4})\s+(\d{1,2}:\d{2})',re.I)
# Template control fields are internal EEG structure, not clinical peer headings.
NON_EEG=re.compile(r'(?im)^[ \t]*(?:Besonderheiten\s*:|Prächirurgisches\s*Setting|Interiktales\s+EEG\s*:\s*(?:Ja|Nein)|Iktales\s+EEG\s*:\s*(?:Ja|Nein)|Ätiologie/ERN|Zusätzliche\s+ERN)[^\n]*')
PLAN=re.compile(r'geplant|empfohlen|angefordert|nicht\s+durchgeführt|soll(?:te)?\b|:\s*(?:Nein|Keines)\b',re.I)


def eeg_date(text):
    # Keep the established range-start parser; extend only two-digit years.
    value,precision=date_from(text)
    if value:return value,precision
    match=SHORT_DATE.search(text)
    if match:
        day,month,year=map(int,match.groups());year+=2000 if year<30 else 1900 if year<100 else 0
        try:return date(year,month,day).isoformat(),'DAY'
        except ValueError:pass
    return None,'UNKNOWN'


def interval(text):
    match=INTERVAL.search(text)
    if not match:return None,None
    return tuple(eeg_date(match[i])[0]+'T'+match[j].zfill(5) for i,j in ((1,2),(3,4)))


def attributes(header,body='',heading_info=None):
    video=bool((heading_info or {}).get('canonical_section')=='VIDEO_EEG' or re.search(r'video|vEEG',header,re.I))
    continuous=bool(re.search(r'cEEG|kontinuierliches\s+EEG|continuous\s+EEG|Langzeit',header+'\n'+body,re.I))
    mobile=bool(re.search(r'mobil',header,re.I));icu=bool(re.search(r'Intensiv|\bICU\b',header,re.I))
    routine=bool(re.match(r'\s*(?:EEG|Routine|Standard|Elektroen)',header,re.I))
    return dict(recording_type='VIDEO_EEG' if video else 'CONTINUOUS_EEG' if continuous else 'ROUTINE_EEG' if routine else 'UNKNOWN',
        continuous=True if continuous else False if routine else None,video=True if video else None,mobile=True if mobile else None,ICU_recording=True if icu else None)


def boundary(body,structured=False,dictionary=None):
    matches=list(NON_EEG.finditer(body))
    matches.extend(match for match,heading in section_headings(body,structured,dictionary)
        if eeg_action(heading,body[:match.start()],body[match.end():match.end()+500],structured)=='STOP')
    inline=re.search(r'[,;]\s*(?:24[- ]Stunden[- ]EKG|Holter[- ]EKG)\b',body,re.I)
    if inline:matches.append(inline)
    if structured:
        matches=[m for m in matches if not re.match(r'\s*(?:Besonderheiten|Medikation)',m[0],re.I)]
    return min(matches,key=lambda m:m.start()) if matches else None


def discover(bundle):
    performed=[];decisions=[]
    dictionary=load_dictionary()
    for doc in bundle['documents']:
        text,ranges=flatten(doc);headers=[]
        for match,kind in section_headings(text,dictionary=dictionary):
            if kind['section_family']!='EEG':continue
            heading=match[0].strip();dt,precision=eeg_date(heading)
            if re.match(r'EEG[- ](?:Ende|Beginn|Dauer|Muster)',heading,re.I):continue
            if not (dt or re.search(r'Kurzbefund|Befund|Monitoring\b',heading,re.I) or re.fullmatch(r'cEEG\s*',heading,re.I)):continue
            # Template yes/no labels are boundaries, never performed recordings.
            headers.append((match,dt,precision,kind))
        rows=[]
        for i,(match,dt,precision,heading_info) in enumerate(headers):
            start=match.end();end=headers[i+1][0].start() if i+1<len(headers) else len(text)
            heading=match[0].strip();body=text[start:end];structured=bool(re.search(r'cEEG\s+Kurzbefund|MOBILE\s+Video',heading,re.I))
            stop=boundary(body,structured,dictionary)
            excluded=stop[0].strip() if stop else None
            excluded_type=classify_heading(excluded) if excluded else None
            if stop:end=start+stop.start()
            inline=re.search(r':\s*(?=\S)',heading)
            if inline and not re.search(r'Kurzbefund',heading,re.I):start=match.start()+match[0].find(heading)+inline.end()
            while start<end and text[start].isspace():start+=1
            while end>start and text[end-1].isspace():end-=1
            body=text[start:end]
            if not dt:dt,precision=eeg_date(body[:120])
            seg_start,seg_end=interval(heading+'\n'+body[:140])
            status='EEG_PLANNED' if PLAN.search(heading) or re.match(r'\s*(?:Nein|Keines)\b',body,re.I) else 'EEG_PERFORMED' if RESULT.search(body) or re.search(r'LPD|GPD|Hirnfunktions|Status\s+epilepticus',body,re.I) else 'EEG_REFERENCED'
            first=locate(ranges,match.start());last=locate(ranges,max(start,end-1))
            row=dict(patient_id=bundle['patient_id'],document_id=doc['source_document_id'],source_document=doc['filename'],
                eeg_date=dt,eeg_date_precision=precision,source_page_start=first['page_start'],source_page_end=last['page_start'],
                section_ids=[r['section']['section_id'] for r in ranges if r['start']<end and r['end']>match.start()],
                header=heading,eeg_report_text=body,report_start=start,report_end=end,header_start=match.start(),ranges=ranges,
                report_occurrence_id=f'{bundle["patient_id"]}:EEG12_SOURCE:{digest([doc["source_document_id"],match.start()])[:16]}',
                screening_status=status,excluded_section_heading=excluded,excluded_section_type=excluded_type['section_type'] if excluded_type else None,
                excluded_heading_core=excluded_type['heading_core'] if excluded_type else None,segment_start=seg_start,segment_end=seg_end,
                **attributes(heading,body,heading_info))
            row['segments']=[]
            # Adjacent timed summaries may join only an explicit continuous/video
            # parent, with compatible intervals and no intervening routine exam.
            parent=rows[-1] if rows else None
            is_segment=bool(re.search(r'cEEG\s+Kurzbefund',heading,re.I))
            parent_end=(parent['segments'][-1]['segment_end'] if parent and parent['segments'] else parent['segment_end'] if parent else None)
            gap=(datetime.fromisoformat(seg_start)-datetime.fromisoformat(parent_end)).total_seconds() if seg_start and parent_end else None
            compatible=parent and parent['recording_type'] in ('VIDEO_EEG','CONTINUOUS_EEG') and (not parent_end or gap is not None and 0<=gap<=300)
            if is_segment and compatible:
                if not parent['segments'] and parent['segment_start'] and re.search(r'cEEG\s+Kurzbefund',parent['header'],re.I):
                    parent['segments'].append(copy.deepcopy(parent))
                parent['segments'].append(row);parent['continuous']=True
                parent['source_page_end']=row['source_page_end'];parent['section_ids']=list(dict.fromkeys(parent['section_ids']+row['section_ids']))
                if status=='EEG_PERFORMED':parent['screening_status']='EEG_PERFORMED'
            else:rows.append(row)
            decisions.append(row)
        performed.extend(r for r in rows if r['screening_status']=='EEG_PERFORMED')
    return performed,decisions
