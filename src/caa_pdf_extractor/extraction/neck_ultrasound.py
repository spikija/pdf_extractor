"""Conservative secondary cervical stenosis marker; full text is authoritative."""
import re
VERSION='cervical_threshold_summary_v1'
def summarize_neck(text):
    text=re.split(r'Gray-Weale|Transkran|Transcran',text,flags=re.I)[0]
    cervical=bool(re.search(r'Carotis|Karotis|\b[AI]CI\b|\bICA\b|\bCCA\b|Vertebralis|Halsgef|Extrakran',text,re.I))
    if not cervical:return {'summary':'INDETERMINATE','evidence':[],'reason':'NO_CERVICAL_THRESHOLD_EVIDENCE','rule_version':VERSION}
    if re.search(r'\bECST\b',text,re.I) and re.search(r'\bNASCET\b',text,re.I):
        return {'summary':'INDETERMINATE','evidence':[q for q in text.splitlines() if re.search(r'ECST|NASCET|%',q,re.I)],'reason':'COMPETING_STENOSIS_REFERENCE_METHODS','rule_version':VERSION}
    found=[]
    for line in re.split(r'\n|;|(?<=[.!])\s+(?=[A-Z])',text):
        if re.search(r'\b(?:ACM|MCA|Basilaris|cerebri)\b',line,re.I):continue
        if re.search(r'Verschluss|Okklusion|occlusion',line,re.I) and not re.search(r'kein\w*|ohne|Ausschluss|Verdacht|fraglich|\?',line,re.I):found.append(('OCCLUSION',line))
        for m in re.finditer(r'(?P<op><=?|>=?|≤|≥)?\s*(?P<lo>\d{1,3})(?:\s*[-–]\s*(?P<hi>\d{1,3}))?\s*%',line):
            if re.search(r'fraglich|Verdacht|\?|kein\w*\s+(?:\w+\s+){0,3}Stenos|ohne\s+Stenos',line,re.I):continue
            if not re.search(r'Stenos|Carotis|Karotis|\b[AI]CI\b|\bICA\b|Vertebralis',line,re.I):continue
            lo=int(m['lo']);hi=int(m['hi'] or lo);op=m['op'] or ''
            if not 0<=lo<=hi<=100:continue
            value='INDETERMINATE'
            if op=='<' and lo<=50:value='STENOSIS_LT_50'
            elif op in ('>=','≥') and lo>=50 or op=='>' and lo>=50:value='STENOSIS_GE_50'
            elif not op:
                if lo==hi==0:value='NO_STENOSIS'
                elif hi<50:value='STENOSIS_LT_50'
                elif lo>=50:value='STENOSIS_GE_50'
            found.append((value,line))
    priority={'NO_STENOSIS':0,'STENOSIS_LT_50':1,'STENOSIS_GE_50':2,'OCCLUSION':3}
    clear=[(v,q) for v,q in found if v in priority]
    # An unresolved threshold can conceal a worse lesion; only an explicit
    # occlusion dominates every unresolved severity.
    if any(v=='INDETERMINATE' for v,_ in found) and not any(v=='OCCLUSION' for v,_ in clear):value='INDETERMINATE'
    elif clear:value=max(clear,key=lambda x:priority[x[0]])[0]
    elif re.search(r'keine\s+(?:zervikalen\s+)?Stenosen\b|ohne\s+Stenosen\b',text,re.I) and not re.search(r'Stenose\s*\d|Plaque',text,re.I):value='NO_STENOSIS'
    else:value='INDETERMINATE'
    return {'summary':value,'evidence':[q for v,q in found if v==value],
        'reason':'WORST_EXPLICIT_CERVICAL_THRESHOLD' if value!='INDETERMINATE' else 'THRESHOLD_NOT_EXPLICIT','rule_version':VERSION}


def extend_cervical_reports(inputs,bundle):
    """Retain an explicit carotid continuation across page furniture only."""
    from .imaging_reports import document_text
    docs={d.source_document_id:d for d in bundle.documents}
    for item in inputs:
        if item.type!='ultrasound' or item.location!='neck vessels':continue
        raw=document_text(docs[item.source_document_id])
        if not re.match(r'Neuroson',item.header,re.I) or raw.count(item.header)!=1:continue
        start=raw.index(item.header)
        furniture=re.search(r'\n\s*Universitätsklinikum',raw[start:start+2000],re.I)
        if not furniture:continue
        end=start+furniture.start()
        continuation=re.match(r'\s*Universitätsklinikum[^\n]*\n(?:[^\n]*\n){0,7}?[^\n]*geb\.:\s*[^\n]*Seite\s+\d+[^\n]*\s*(?=A\.\s*carotis)',raw[end:],re.I)
        if not continuation:continue
        remaining=raw[end+continuation.end():]
        stop=re.search(r'(?im)^\s*(?:Gray-Weale|Internistisch|Neuroson|EEG|Computertom|Kernspintom|Transtemporal|Transkran|Transcran)',remaining)
        if not stop:continue
        new_end=end+continuation.end()+stop.start()
        while new_end>end and raw[new_end-1].isspace():new_end-=1
        item.text=raw[start:new_end]
        item.source_spans=[{'role':'parent_anatomy_span','coordinate_system':'document_text_v1','start':start,'end':new_end,'text':item.text}]
    return inputs
