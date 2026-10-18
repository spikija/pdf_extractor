"""Conservative PET interpretation with exact report quotation evidence."""
import re
VERSION='pet_brain_scope_v2'
PATTERNS={
 'AD_TYPICAL':r'\b(?:AD[- ]typisch\w*|Alzheimer[- ]typisch\w*|typisch\w*\s+(?:Alzheimer|AD)[- ](?:Muster|pattern))\b',
 'AD_ATYPICAL':r'\b(?:atypisch\w*\s+Alzheimer|AD[- ]atypisch\w*)\b',
 'FTD_PATTERN':r'\b(?:FTD[- ](?:Muster|pattern)|frontotemporal\w*\s+Demenz[- ](?:Muster|pattern))\b',
 'DLB_PATTERN':r'\b(?:DLB[- ](?:Muster|pattern)|Lewy[- ](?:body|K[öo]rper)[- ](?:Muster|pattern))\b',
 'PCA_PATTERN':r'\b(?:PCA[- ](?:Muster|pattern)|posteriore\s+kortikale\s+Atrophie[- ](?:Muster|pattern))\b',
 'PPA_PATTERN':r'\bPPA[- ](?:Muster|pattern)\b',
 'VASCULAR_PATTERN':r'\b(?:vaskul[äa]r\w*\s+(?:Muster|pattern)|vascular\s+pattern)\b',
 'MIXED_PATTERN':r'\b(?:gemischt\w*\s+(?:Muster|pattern)|mixed\s+pattern)\b',
}

def pet_text(text):
    """Ignore low-dose CT findings when classifying the PET component."""
    lines=[];ct=False
    for line in text.splitlines(keepends=True):
        if re.search(r'^\s*(?:Sch[äa]del-CT|CT\s+des\b|.*\bnativ\s*:\s*CT\b)',line,re.I):ct=True
        if re.search(r'^\s*(?:Synopsis\s+PET|Beurteilung\s+der\s+PET|(?:Stamm|Hirn):\s*Aktivit[äa]t|K[öo]rperstamm:)',line,re.I):ct=False
        if not ct:lines.append(line)
    return ''.join(lines)

def _normalize_owned(kind,text):
    source=pet_text(text);evidence=[]
    def hits(pattern):
        values=[]
        for m in re.finditer(pattern,source,re.I):
            # A classification is supported by a literal substring, never a
            # generated diagnostic summary. Keep the complete source line.
            start=source.rfind('\n',0,m.start())+1;end=source.find('\n',m.end())
            q=source[start:end if end>=0 else len(source)].strip()
            if q:values.append(q)
        return values
    technical=hits(r'nicht\s+(?:diagnostisch\s+)?(?:beurteilbar|auswertbar)|uninterpretierbar|non[- ]diagnostic|uninterpretable|technisch\s+unzureichend')
    uncertain=hits(r'\b(?:unklar\w*|fraglich\w*|equivocal|indeterminate|grenzwertig\w*)\b')
    amyloid=kind in ('PET_CT_AMYLOID','pet ct amyloid')
    fields={'amyloid_pet_result':None,'fdg_pet_result':None,'fdg_pet_pattern':None}
    if kind not in ('PET_CT_AMYLOID','pet ct amyloid','PET_CT_FDG','pet ct fdg'):
        return {**fields,'classification_evidence':[],'classification_status':'INDETERMINATE_UNKNOWN_TRACER','rule_version':VERSION}
    if amyloid:
        negative=hits(r'(?:keine?|ohne|no)\s+(?:(?:Nachweis|Hinweis)\s+(?:auf\s+)?)?(?:pathologisch\w*\s+)?(?:kortikal\w*\s+)?(?:Amyloid[- ]?Bindung|kortikal\w*\s+(?:Bindung|Anreicherung))|amyloid[- ]negativ\w*|negativ\w*\s+Amyloid[- ]PET|no\s+(?:pathological\s+)?cortical\s+amyloid\s+binding')
        positive=hits(r'amyloid[- ]positiv\w*|positiv\w*\s+Amyloid[- ]PET|(?:pathologisch\w*|erh[öo]ht\w*)\s+kortikal\w*\s+(?:Amyloid[- ]?)?(?:Bindung|Anreicherung)|kortikal\w*\s+Amyloid[- ]Bindung')
        positive=[q for q in positive if not re.search(r'\b(?:kein\w*|ohne|no|nicht)\b',q,re.I)]
        value='NON_DIAGNOSTIC' if technical else 'INDETERMINATE' if uncertain or (positive and negative) else 'POSITIVE' if positive else 'NEGATIVE' if negative else 'INDETERMINATE'
        fields['amyloid_pet_result']=value;evidence=technical or uncertain or positive or negative
    else:
        abnormal=hits(r'\bHypometaboli\w*|abnorm\w*\s+(?:(?:Glukose|glucose)[- ]?)?Metabolis(?:mus|m)|pathologisch\w*\s+(?:Mehrspeicherung|Metabolismus)|gesteigert\w*\s+Traceranreicherung|Mehrspeicherungen[^\n]*Gastritis')
        abnormal=[q for q in abnormal if not re.search(r'\b(?:kein\w*|ohne|no|nicht)\b',q,re.I)]
        normal=hits(r'(?:keine|ohne|no)\s+(?:pathologisch\w*\s+)?Mehrspeicherung\w*|(?:physiologisch\w*|normal\w*|unauff[äa]llig\w*)\s+(?:(?:Glukose|glucose|Hirn|brain|cerebral)[- ]?)?Metabolis(?:mus|m)')
        value='NON_DIAGNOSTIC' if technical else 'INDETERMINATE' if uncertain else 'ABNORMAL' if abnormal else 'NORMAL' if normal else 'INDETERMINATE'
        fields['fdg_pet_result']=value;evidence=technical or uncertain or abnormal or normal
        supported={name:qs for name,pat in PATTERNS.items() if (qs:=hits(pat)) and all(not re.search(r'\b(?:kein\w*|ohne|no|nicht)\b',q,re.I) for q in qs)}
        # The classic bilateral temporoparietal/posterior-cingulate distribution
        # must be explicitly hypometabolic; generic abnormality is insufficient.
        if (not supported and value=='ABNORMAL' and any(re.search(r'temporoparietal',q,re.I)
                and re.search(r'posterior\w*\s+(?:Cingul\w*|Zingul\w*)|Precuneus',q,re.I)
                and re.search(r'beidseits|bilateral',q,re.I) for q in abnormal)):
            supported['AD_TYPICAL']=abnormal
        if value=='ABNORMAL':fields['fdg_pet_pattern']=next(iter(supported)) if len(supported)==1 else 'MIXED_PATTERN' if len(supported)>1 else 'NONSPECIFIC'
        elif value=='NORMAL':fields['fdg_pet_pattern']='NO_SPECIFIC_PATTERN'
        if supported:evidence+= [q for qs in supported.values() for q in qs]
    evidence=list(dict.fromkeys(evidence))
    assert all(q in text for q in evidence)
    return {**fields,'classification_evidence':evidence,'rule_version':VERSION}


def normalize_pet(kind,text,header=''):
    from .pet_components import partition_pet,initial_scope,VERSION as component_version
    components=partition_pet(text,header)
    brain=[c for c in components if c['brain_interpretation']]
    brain_source='\n'.join(c['text'] for c in brain)
    amyloid=kind in ('PET_CT_AMYLOID','pet ct amyloid')
    if kind in ('PET_CT_FDG','pet ct fdg') and not brain:
        result={'amyloid_pet_result':None,'fdg_pet_result':'INDETERMINATE','fdg_pet_pattern':None,
                'classification_evidence':[],'rule_version':VERSION}
        reason='BRAIN_INTERPRETATION_NOT_REPORTED'
    else:
        result=_normalize_owned(kind,text if amyloid else brain_source)
        reason='BRAIN_COMPONENT_ONLY' if not amyloid else 'AMYLOID_TRACER_INTERPRETATION'
    assert all(c['text']==text[c['start']:c['end']] for c in components)
    assert all(q in text for q in result['classification_evidence'])
    return {**result,'components':components,'brain_component_present':bool(brain),
        'brain_header_detected':initial_scope(header)=='BRAIN' or any(re.match(r'(?:Hirn|Gehirn|Brain|Zerebral)\s*:',c['text'],re.I) for c in components),
        'exact_brain_evidence':[c['text'] for c in brain],
        'extracranial_findings':[c for c in components if c['component'] in ('HEAD_NECK','BODY')],
        'reason':reason,'component_rule_version':component_version}
