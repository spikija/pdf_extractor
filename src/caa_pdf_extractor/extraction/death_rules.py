"""Explicit patient death dates only; no document/current-date inference."""
import re
from datetime import date
VERSION='explicit_patient_death_v2'
DATE=r'(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})'
RELATIVE=re.compile(r'\b(?:Mutter|Vater|Bruder|Brüder|Schwester|Tochter|Sohn|Ehemann|Ehefrau|Gattin|Gatte|Großmutter|Großvater|Nachbar|mother|father|son|daughter|husband|wife)\b',re.I)
def explicit_death_dates(text):
    patterns=[r'\b(?:Exitus\s+letalis\s+am|verstorben\s+am|verstarb\s+am|Tod\s+am|Todesdatum\s*:|death date\s*:|died\s+on)\s*'+DATE,
        r'\bAm\s+'+DATE+r'(?:\s+um\s+\d{1,2}:\d{2}\s*(?:Uhr)?)?\s+verstarb\b']
    values=[]
    for pattern in patterns:
        for m in re.finditer(pattern,text,re.I):
            # Attribution belongs to this sentence, including the subject after
            # a date-before-verb formulation. Relative deaths are not case facts.
            lo=max(text.rfind('\n',0,m.start()),text.rfind('. ',0,m.start()))+1
            context=text[lo:min(len(text),m.end()+100)]
            if RELATIVE.search(context) or re.search(r'\b(?:falls|wenn|möglicherweise|kein\w*|nicht|erwartet|droht|prognostiziert)\b|\?',context,re.I):continue
            try:
                raw=m[1];value=date(*map(int,raw.split('.')[::-1])).isoformat() if '.' in raw else date.fromisoformat(raw).isoformat()
            except ValueError:continue
            values.append({'value':value,'evidence':m.group(),'start':m.start(),'end':m.end()})
    return values
