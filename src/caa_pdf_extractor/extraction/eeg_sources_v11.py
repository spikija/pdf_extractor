"""EEG 1.1 source boundaries; existing 1.0 discovery stays unchanged."""
import copy
import re
from .eeg_sources import discover as discover_v1,locate,flatten

MODALITY=re.compile(r'(?im)^[ \t]*(?:(?:24|48|72)\s*[-– ]?\s*(?:STUNDEN|H)\s*[-– ]?\s*(?:EKG|ECG)|(?:LANGZEIT|HOLTER|RUHE|BELASTUNGS)[- ]?(?:EKG|ECG)|(?:EKG|ECG)\s*(?:\(\d|Befund|vom\s+\d))[^\n]*')
INLINE_OTHER_MODALITY=re.compile(r'[,;]\s*(?:24\s*[- ]?Stunden\s*[- ]?EKG|Holter[- ]?EKG)\b',re.I)


def discover(bundle):
    _,decisions=discover_v1(bundle);out=[]
    docs={d['source_document_id']:flatten(d) for d in bundle['documents']}
    for original in decisions:
        row=copy.deepcopy(original);body=row['eeg_report_text']
        boundaries=[m for pattern in (MODALITY,INLINE_OTHER_MODALITY) if (m:=pattern.search(body))]
        boundary=min(boundaries,key=lambda m:m.start()) if boundaries else None
        if boundary:
            row['excluded_modality_heading']=boundary[0].strip();row['report_end']=row['report_start']+boundary.start()
            row['eeg_report_text']=body[:boundary.start()].rstrip();row['report_end']=row['report_start']+len(row['eeg_report_text'])
            row['source_page_end']=locate(row['ranges'],max(row['report_start'],row['report_end']-1))['page_start']
            row['section_ids']=[r['section']['section_id'] for r in row['ranges'] if r['start']<row['report_end'] and r['end']>row['header_start']]
        # Header lines can include an explicit inline finding after the date/colon.
        # Preserve that source text rather than dropping it as part of the heading.
        inline=re.search(r':\s*(?=\S)',row['header'])
        if inline and row['header'][inline.end():].strip():
            text,_=docs[row['document_id']]
            header_start=text.find(row['header'],row['header_start'],row['report_start'])
            if header_start>=0:
                row['report_start']=header_start+inline.end();row['eeg_report_text']=text[row['report_start']:row['report_end']].strip()
        out.append(row)
    return [r for r in out if r['screening_status']=='EEG_PERFORMED'],out
