"""Exact organ/component ownership; composite PET is not a brain interpretation."""
import re

VERSION='pet_component_ownership_v1'
BRAIN=re.compile(r'\b(?:Hirn\w*|Gehirn\w*|brain|zerebral\w*|cerebral\w*|kortikal\w*|cortical\w*|'
    r'temporoparietal\w*|temporo\s*[- ]\s*mesial\w*|mesiotemporal\w*|Cingul\w*|Zingul\w*|Precuneus|'
    r'Alzheimer\w*|AD[- ]typ\w*|FTD[- ](?:Muster|pattern)|DLB[- ](?:Muster|pattern))\b',re.I)
HEAD_NECK=re.compile(r'\b(?:Zungengrund|Pharynx|Hals|neck|Arteria\s+carotis|ACC|Zervikal\w*|cervical\w*)\b',re.I)
BODY=re.compile(r'\b(?:Magen\w*|Gastritis|gastric|stomach|Lymphknot\w*|lymph\s+node\w*|Knochen\w*|bone|oss[äa]r\w*|Abdomen|Thorax|Becken|Extremit[äa]ten|Skelett|Oberschenkel\w*)\b',re.I)
INTERPRETATION=re.compile(r'metaboli\w*|Mehrspeicher\w*|uptake|Hypometabol\w*|Hypermetabol\w*|Bindung\w*|binding|'
    r'Alzheimer[- ]typ\w*|AD[- ]typ\w*|unauff[äa]llig\w*|unklar\w*|equivocal|grenzwertig\w*|'
    r'beurteilbar|auswertbar|uninterpret\w*|non[- ]diagnostic|uninterpretable|unzureichend',re.I)
DEFERRED=re.compile(r'(?:Hirn|Gehirn|brain)\s+(?:wird|is)\s+(?:separat|separately)|siehe\s+NUK|separat\s+befundet',re.I)

def initial_scope(header):
    if BRAIN.search(header) and not re.search(r'Stamm|K[öo]rper|body|Hals|neck',header,re.I):return 'BRAIN'
    return 'UNASSIGNED'

def partition_pet(text,header=''):
    state=initial_scope(header);ct=False;parts=[];start=0
    split=re.compile(r'\n|;|(?<=[.!?])\s+(?=[A-ZÄÖÜ])|,\s+(?=(?:aber|jedoch|w[äa]hrend)\b)',re.I)
    spans=[]
    for m in split.finditer(text):spans.append((start,m.start()));start=m.end()
    spans.append((start,len(text)))
    for lo,hi in spans:
        while lo<hi and text[lo].isspace():lo+=1
        while hi>lo and text[hi-1].isspace():hi-=1
        if lo==hi:continue
        q=text[lo:hi];reason='INHERITED_COMPONENT_HEADER'
        if re.match(r'(?:Sch[äa]del[- ]CT|CT\s+des\b|.*\bnativ\s*:\s*CT\b|Sch[äa]del\s+nativ\s*:|Stamm\s+nativ\s*:)',q,re.I):
            state='OTHER';ct=True;reason='CT_COMPONENT'
        elif re.match(r'(?:Synopsis\s+PET|Beurteilung\s+der\s+PET)',q,re.I):
            state='UNASSIGNED';ct=False;reason='COMPOSITE_PET_CONCLUSION'
        elif re.match(r'(?:Hirn|Gehirn|Brain|Zerebral)\s*:',q,re.I):
            state='BRAIN';ct=False;reason='EXPLICIT_BRAIN_HEADER'
        elif re.match(r'(?:Kopf\s*,?\s*Hals|Kopf/Hals|Head\s*(?:and|/)\s*neck|Hals)\s*:',q,re.I):
            state='HEAD_NECK';ct=False;reason='EXPLICIT_HEAD_NECK_HEADER'
        elif re.match(r'(?:K[öo]rperstamm|Stamm|Thorax|Abdomen(?:\s+und\s+Becken)?|Magen|Stomach|Body|Skelett(?:\s+und\s+Extremit[äa]ten)?)\s*:',q,re.I):
            state='BODY';ct=False;reason='EXPLICIT_BODY_HEADER'
        scope=state
        if not ct:
            brain=bool(BRAIN.search(q));extracranial=bool(HEAD_NECK.search(q) or BODY.search(q))
            if brain and extracranial:
                scope='UNASSIGNED';reason='MIXED_ORGAN_CLAUSE_UNASSIGNED'
            elif HEAD_NECK.search(q):scope='HEAD_NECK';reason='EXPLICIT_HEAD_NECK_ANATOMY'
            elif BODY.search(q):scope='BODY';reason='EXPLICIT_EXTRACRANIAL_ANATOMY'
            elif brain:scope='BRAIN';reason='EXPLICIT_BRAIN_ANATOMY'
        if DEFERRED.search(q):scope='UNASSIGNED';reason='BRAIN_INTERPRETATION_DEFERRED'
        eligible=scope=='BRAIN' and bool(INTERPRETATION.search(q)) and not DEFERRED.search(q)
        parts.append({'component':scope,'text':q,'start':lo,'end':hi,
            'coordinate_system':'pet_report_text_v1','ownership_reason':reason,'brain_interpretation':bool(eligible)})
    return parts
