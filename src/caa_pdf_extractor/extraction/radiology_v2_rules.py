"""Atomic, source-exact imaging findings. No exam is discarded by these rules."""
import re
from .imaging_reports import document_text

VERSION='radiology_v2_concept_atomic_v5_morphology'
from .radiology_assertions import assertion, compartment, negative_candidates, UNCERTAIN, concept_assertions, local_context
CMB=re.compile(r'\b(?:mikro[-\s]?blutung\w*|microbleeds?|mikro[-\s]h[äa]mosiderinresidu\w*|'
    r'(?:infratentoriellen|supratentoriellen)Mikroblutung\w*|'
    r'(?:punktf[öo]rmig\w*|stecknadelkopfgro[ßs]\w*|kleinst\w*)\s+h[äa]mosiderin(?:ablagerung|auflagerung)\w*|'
    r'(?:multiple|zahlreich\w*)\s+klein\w*\s+h[äa]mosiderinherd\w*|multiple\s+h[äa]mosiderinablagerung\w*|'
    r'parenchymat[öo]s\w*\s+h[äa]mosiderinresidu\w*|'
    r'suszeptibilit[äa]tsbedingte\w*\s+punktf[öo]rmige\w*\s+L[äa]sion\w*)',re.I)
SUSCEPTIBILITY=re.compile(r'\bsuszeptib\w*artefakt\w*',re.I)
SUPPORT={
 'CMB':CMB,
 'CSS':re.compile(r'(?:oberfl[äa]ch\w*|superfiziell\w*|kortikal\w*|sulk[au]l\w*)\s+(?:(?:punktförmig\w*|stecknadelkopfgro[ßs]\w*)\s+)?(?:H[äa]mosideros\w*|Sideros\w*|H[äa]mosiderin(?:ablagerung|auflagerung)\w*)',re.I),
 'ICH':re.compile(r'\b(?:Blutung\w*|H[äa]matom\w*|ICB|ICH|intrazerebral\w*\s+H[äa]morrhag\w*)\b',re.I),
 'SAH':re.compile(r'\b(?:SAB|SAH|cSAH|Subarachnoidalblutung\w*|Sulcusblutung\w*|subarachnoidal\w*\s+(?:Blutung\w*|H[äa]morrhag\w*)|(?:sul[ck]al\w*|konvexitätsnah\w*)\s+Blutung\w*|Blut\s+in\s+(?:den\s+)?Sulci)\b',re.I),
 'ISCHEMIC_LESION':re.compile(r'\b(?:isch[äa]misch\w*\s+(?:(?:punktf[öo]rmig\w*|rezent\w*|alt\w*)\s+){0,2}(?:L[äa]sion\w*|Defekt\w*)|rezent\w*\s+(?:punktförmig\w*\s+)?diffusionsgestört\w*\s+(?:Areal|Läsion)\w*|ischemic\s+lesion\w*|infarct\w*|Infarzierung\w*|Infarkt\w*|Mediateilinfarkt\w*|Grenzzoneninfarkt\w*|Isch[äa]mie\w*)\b',re.I),
 'WMH':re.compile(r'\b(?:Fazekas(?:\s+Grad)?\s+(?:III|II|I|[123])\b|white[-\s]+matter\s+hyperintensit\w*|hyperintens\w*\s+Marklagerveränderung\w*|Marklagerl[äa]sion\w*|microangiopathic\s+white[-\s]matter\s+lesion\w*)',re.I),
 'MICROANGIOPATHY':re.compile(r'\b(?:[Mm]ikroangiopath\w*|[Mm]icroangiopath\w*|Leukenzephalopath\w*)',re.I),
}
OLD=re.compile(r'\b(?:alt\w*|vorbestehend\w*|vorbekannt\w*|bekannt\w*|chronisch\w*|unver[äa]ndert\w*|station[äa]r\w*|resorbiert\w*|residu\w*|stattgehabt\w*|posth[äa]morrhag\w*)\b|\bnach\s+Resorption\b|\bZustand\s+nach\b',re.I)
INTERVAL=re.compile(r'\b(?:neu(?:e[nrms]?)?|neugetreten\w*|new)\b',re.I)
ACUTE=re.compile(r'\b(?:akut\w*|frisch\w*|rezent\w*|acute|fresh|recent)\b',re.I)
SUBACUTE=re.compile(r'\b(?:subakut\w*|subacute)\b',re.I)
RESOLVED=re.compile(r'\b(?:Defekt\w*|H[äa]mosiderinresidu\w*)\s+nach\s+(?:einer\s+)?Blutung\w*|\b(?:old|chronic|residual|pre[- ]existing|unchanged)\b',re.I)

def clauses(text):
    start=0
    # Preserve literal whitespace and coordinates; abbreviations/dates do not
    # terminate clauses unless followed by a new sentence.
    for m in re.finditer(r';|(?<!bzw)(?<!ca)(?<!chron)(?<!\ba)[.!?]\s+(?=[A-ZÄÖÜ])|\n[ \t]*(?=(?:[-–]\s|Keine?\b|Im\s+Intervall\b|Vorbestehende\b|Die\b|Ansonsten\b|Mikroangiopathien\b|Einzelne\b|Hämosiderin/alte\b))|,\s+(?=weiters\b)|\bund\s+(?=(?:(?:etwa\s+)?unveränderte?\b|primär\b|alte\b|frische\b|akute\b|neue\b|vorbestehende\b))',text):
        if m.group().startswith('.') and re.search(r'\bZ\.\s*n$',text[:m.start()],re.I):continue
        if m.group().startswith('und') and text[start:m.start()].strip().casefold() in ('neue','alte'):continue
        end=m.start()+1 if text[m.start()] in '.!?' else m.start()
        lo=start
        while lo<end and text[lo].isspace():lo+=1
        hi=end
        while hi>lo and text[hi-1].isspace():hi-=1
        if hi>lo:yield lo,hi,text[lo:hi]
        start=m.end()
    lo=start
    while lo<len(text) and text[lo].isspace():lo+=1
    if text[lo:].strip():yield lo,len(text.rstrip()),text[lo:].rstrip()

def negated(text,match):
    return assertion(text,match)=='NEGATED'

def temporal_semantics(kind,text,match):
    """Interval occurrence and biological lesion age are independent facts."""
    stable_context=kind=='CMB' and bool(re.search(r'\bwie\s+zuletzt\b',text[max(0,match.start()-100):match.start()],re.I))
    prefix=re.split(r',|;|\b(?:neben|sowie|und|bei|mit)\b',text[:match.start()],flags=re.I)[-1]
    suffix=re.split(r';|\b(?:neben|sowie|und|bei|mit)\b',text[match.end():],flags=re.I)[0]
    strip_comparison=lambda value:re.sub(r'\b(?:alt\w*|old|previous|fr[üu]her\w*)\s+(?:MRT|MRI|CT|Voruntersuch\w*)\b','',value,flags=re.I)
    prefix=strip_comparison(prefix)
    text=prefix+match.group()+strip_comparison(suffix)
    interval=(True if INTERVAL.search(text) else False if
        (kind=='CMB' and (OLD.search(text) or RESOLVED.search(text) or stable_context))
        or re.search(r'\b(?:vorbestehend\w*|vorbekannt\w*|unver[äa]ndert\w*|pre[- ]existing|unchanged)\b',prefix,re.I) else None)
    if kind=='CSS':status='CHRONIC';acute=False
    elif SUBACUTE.search(prefix) or SUBACUTE.search(text):status='SUBACUTE';acute=None
    elif kind=='CMB':
        if ACUTE.search(prefix):status='ACUTE';acute=True
        elif interval is True:status='INTERVAL_NEW';acute=None
        elif re.search(r'h[äa]mosiderin',match.group(),re.I):status='CHRONIC';acute=False
        elif OLD.search(text) or RESOLVED.search(text) or stable_context:status='CHRONIC';acute=False
        else:status='INDETERMINATE';acute=None
    elif kind=='SAH' and re.search(r'\bneu\s+aufgetreten\w*',text,re.I):status='ACUTE';acute=True
    elif kind=='SAH' and re.search(r'\bZ\.\s*n\.|Zustand\s+nach',prefix,re.I):status='CHRONIC';acute=False
    elif ACUTE.search(prefix) and not (re.search(r'\b(?:alt\w*|chronisch\w*|residu\w*|resorbiert\w*)\b',prefix,re.I) or RESOLVED.search(prefix)):
        status='ACUTE';acute=True
    elif re.search(r'\b(?:alt\w*|chronisch\w*|residu\w*|resorbiert\w*|posthämorrhag\w*|old|chronic|residual)\b',prefix,re.I) or RESOLVED.search(text) or re.search(r'\bnach\s+Resorption\b',prefix,re.I):status='CHRONIC';acute=False
    elif ACUTE.search(text) and not OLD.search(text):status='ACUTE';acute=True
    elif re.search(r'\b(?:alt\w*|chronisch\w*|residu\w*|resorbiert\w*|old|chronic|residual)\b',text,re.I):status='CHRONIC';acute=False
    elif interval is True:status='INTERVAL_NEW';acute=None
    elif kind=='WMH':status='CHRONIC';acute=False
    else:status='INDETERMINATE';acute=None
    return {'temporal_status':status,'interval_new':interval,'acute_lesion':acute}

def acuity(kind,text,match):
    return temporal_semantics(kind,text,match)['acute_lesion']

def pattern(findings):
    if not findings:return None
    types={r['finding_type'] for r in findings};hem=types & {'CMB','CSS','ICH','SAH'}
    if 'ISCHEMIC_LESION' in types and hem:return 'ISCHEMIC_AND_HEMORRHAGIC_FINDINGS'
    if len(findings)==1:return 'SINGLE_FINDING'
    if hem and types<= {'CMB','CSS','ICH','SAH'}:return 'MULTIPLE_HEMORRHAGIC_FINDINGS'
    return 'MULTIPLE_MIXED_FINDINGS'

def source_offset(study,start,end):
    cursor=0
    for span in study.source_spans:
        if span['role']!='report':continue
        length=len(span['text'])
        if cursor<=start and end<=cursor+length:return {'coordinate_system':'document_text_v1','start':span['start']+start-cursor,'end':span['start']+end-cursor}
        cursor+=length+1
    raise ValueError('Finding crosses independent source fragments')

def susceptibility_context(study,document,all_studies):
    strong=next((q for _,_,q in clauses(study.report_text) if (m:=CMB.search(q)) and not negated(q,m)),None)
    if strong:return {'rule':'SAME_REPORT_EXPLICIT_CMB','evidence_text':strong}
    # A matching MRI month in this document's diagnosis timeline is a bounded,
    # explicit link only when there is exactly one corresponding MRI study.
    if not study.exam_date:return None
    year,month=study.exam_date[:4],str(int(study.exam_date[5:7]))
    matches=[s for s in all_studies if s.source_document_id==study.source_document_id and s.modality=='MRI' and s.exam_date and s.exam_date[:7]==study.exam_date[:7]]
    if len(matches)!=1:return None
    text=document_text(document)
    for m in re.finditer(r'(?im)^\s*[-\s]*c?MRT[- ]Kontrolle\s+0?'+month+'/'+year+r'\s*:[^\n]*(?:\n[^\n]*){0,3}',text):
        q=m.group();cm=CMB.search(q)
        if cm and not negated(q,cm):return {'rule':'UNIQUE_SAME_DOCUMENT_MRI_MONTH_LINK','evidence_text':q,'source_offsets':{'start':m.start(),'end':m.end(),'coordinate_system':'document_text_v1'}}
    return None

def extract_findings(study,document,all_studies,*,support_patterns=None,compartment_resolver=None,exact_evidence=None):
    patterns=SUPPORT if support_patterns is None else support_patterns
    findings=[];rejected=[];negatives=[];uncertain=[];other=[];seen=set();context=susceptibility_context(study,document,all_studies)
    summary=re.search(r'(?im)^\s*(?:Einzelbeurteilung|Beurteilung)\s*:',study.report_text)
    for start,end,text in clauses(study.report_text):
        offsets=source_offset(study,start,end)
        for concept in concept_assertions(text):
            row={**concept,'evidence_text':text,'source_offsets':offsets}
            if concept['assertion_state'] in ('UNCERTAIN','CONDITIONAL'):uncertain.append(row)
            if concept['concept'] not in ('ISCHEMIC_LESION','HEMORRHAGE','SAH','CMB','CSS') or concept['qualified']:
                other.append(row)
        if UNCERTAIN.search(text) and not any(assertion(text,m)=='ASSERTED' for regex in patterns.values() if (m:=regex.search(text))):
            uncertain.append({'evidence_text':text,'source_offsets':offsets,'assertion_status':'UNCERTAIN','reason':'UNCERTAIN_CLAUSE'})
        for kind in negative_candidates(text):
            negatives.append({'finding_type':kind,'location':None,'evidence_text':text,'source_offsets':offsets})
        if re.search(r'\b(?:empfohlen|geplant)\b',text,re.I):
            rejected.append({'evidence_text':text,'reason':'FUTURE_OR_RECOMMENDATION','assertion_status':'CONTEXT_ONLY'});continue
        candidates=[]
        for kind,regex in patterns.items():
            matches=list(regex.finditer(text))
            if kind=='CMB' and not matches and (m:=SUSCEPTIBILITY.search(text)):
                if context:candidates.append((kind,m,context))
                else:rejected.append({'finding_type':'CMB','evidence_text':text,'reason':'SUSCEPTIBILITY_WITHOUT_MICROHEMORRHAGIC_CONTEXT'})
            candidates.extend((kind,m,None) for m in matches)
        for kind,m,support in candidates:
            category=re.match(r'[^\n:]{0,100}:',text) if kind=='CMB' else None
            same_kind=[cm for ck,cm,_ in candidates if ck==kind]
            if category and len(same_kind)>1 and m.end()<=category.end():
                rejected.append({'finding_type':kind,'evidence_text':text,'reason':'CATEGORY_LABEL_CONTEXT_ONLY','assertion_status':'CONTEXT_ONLY'});continue
            state=assertion(text,m)
            if state=='NEGATED':
                rejected.append({'finding_type':kind,'evidence_text':text,'reason':'NEGATED_FINDING'});continue
            if state!='ASSERTED':
                record={'finding_type':kind,'evidence_text':text,'source_offsets':offsets,'reason':state+'_FINDING','assertion_status':state}
                rejected.append(record)
                if state in ('UNCERTAIN','CONDITIONAL','DIFFERENTIAL'):uncertain.append(record)
                continue
            if kind=='CMB' and re.search(r'h[äa]mosiderin',m.group(),re.I) and re.search(r'oberflächlich\w*|superfiziell\w*|sul[ck]al\w*',text[:m.start()],re.I):
                rejected.append({'finding_type':kind,'evidence_text':text,'reason':'SUPERFICIAL_SIDEROSIS_COMPARTMENT'});continue
            owner=(compartment_resolver or compartment)(kind,text,m)
            if kind=='ICH' and owner is None:
                rejected.append({'finding_type':kind,'evidence_text':text,'reason':'COMPARTMENT_NOT_INTRACEREBRAL','assertion_status':'UNASSIGNED'});continue
            if kind=='MICROANGIOPATHY' and SUPPORT['WMH'].search(study.report_text):continue
            prefix,suffix=local_context(text,m)
            owned=prefix+m.group()+suffix
            owned_match=re.compile(re.escape(m.group())).match(owned,len(prefix))
            temporal=temporal_semantics(kind,owned,owned_match)
            if category and kind=='CMB' and temporal['temporal_status']=='INDETERMINATE' and OLD.search(category.group()):
                temporal={'temporal_status':'CHRONIC','interval_new':False,'acute_lesion':False}
            locations=re.findall(r'\b(?:frontal\w*|fronto[-\w]*|parietal\w*|temporal\w*|mesiotemporal\w*|okzipital\w*|occipital\w*|kortik\w*|subkortik\w*|supratentoriell\w*|infratentoriell\w*|Thalam\w*|zerebell\w*|insul[äa]r\w*)\b',text,re.I)
            left=bool(re.search(r'\blinks\b',text,re.I));right=bool(re.search(r'\brechts\b',text,re.I))
            bilateral=bool(re.search(r'bihemisph[äa]r\w*|beidseits|\bbds\.',text,re.I)) or (left and right)
            laterality='bilateral' if bilateral else 'left' if left else 'right' if right else None
            location=', '.join(dict.fromkeys(locations)) or None
            if kind=='CMB' and re.search(r'bihemisph[äa]r',text,re.I) and not locations:location='bihemispheric'
            if kind=='SAH' or kind=='ISCHEMIC_LESION' and re.match(r'Infarzierung',m.group(),re.I):
                pairs=re.findall(r'\b(rechts|links|rechten|linken)\s+(frontal\w*|parietal\w*|temporal\w*|okzipital\w*|occipital\w*)(?:\s+(lateral))?',text,re.I)
                if pairs:
                    names=[]
                    for side,region,lateral in pairs:
                        area=next(a for a in ('frontal','parietal','temporal','okzipital','occipital') if region.casefold().startswith(a))
                        names.append(('right' if side.casefold().startswith('recht') else 'left')+' '+area+(' lateral' if lateral else ''))
                    location=' and '.join(dict.fromkeys(names))
                    sides={side.casefold().startswith('recht') for side,_,_ in pairs}
                    laterality='bilateral' if len(sides)>1 else 'right' if True in sides else 'left'
            if summary and start>summary.start():
                tokens={t.casefold() for t in locations}
                repeated=any(f['finding_type']==kind and all(f[k]==v for k,v in temporal.items())
                    and (laterality is None or f['laterality']==laterality)
                    and (not tokens or tokens<={t.strip().casefold() for t in (f['location'] or '').split(',')}) for f in findings)
                if repeated:
                    rejected.append({'finding_type':kind,'evidence_text':text,'reason':'REPEATED_REPORT_SUMMARY_COMPONENT'});continue
            signature=(kind,tuple(temporal.items()),location,laterality)
            if signature in seen:continue
            seen.add(signature)
            grade=re.search(r'Fazekas(?:\s+Grad)?\s+(III|II|I|[123])\b',text,re.I)
            attrs={'fazekas_grade':{'I':1,'II':2,'III':3}.get(grade[1].upper(),int(grade[1]) if grade[1].isdigit() else None)} if grade else {}
            record={'finding_type':kind,'assertion_state':'PRESENT','compartment':'subarachnoid' if kind=='SAH' else owner,**temporal,'location':location,'laterality':laterality,
                'evidence_text':exact_evidence(start,end,text) if exact_evidence else text,'source_offsets':source_offset(study,start,end),'attributes':attrs}
            if support:record['supporting_context']=support
            offsets=record['source_offsets'];assert document_text(document)[offsets['start']:offsets['end']]==record['evidence_text']
            findings.append(record)
            # Explicit shared-noun age coordination supports two components,
            # each retaining the exact full clause as its evidence.
            if kind=='CMB' and re.search(r'\bneue\s+und\s+alte\s*$',text[:m.start()],re.I):
                record.update(temporal_status='INTERVAL_NEW',interval_new=True,acute_lesion=None)
                findings.append({**record,'temporal_status':'CHRONIC','interval_new':False,'acute_lesion':False})
    return {'findings':findings,'negative_findings':negatives,'uncertain_findings':uncertain,'other_assertions':other,'overall_pattern':pattern(findings),'rejected_candidates':rejected,
            'status':'VERIFIED' if findings else 'VERIFIED_EMPTY','rule_version':VERSION}
