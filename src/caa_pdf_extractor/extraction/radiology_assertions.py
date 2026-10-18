"""Clause-local assertion ownership; negatives are never positive phenotypes."""
import re
VERSION='radiology_concept_ownership_v3_morphology'
UNCERTAIN=re.compile(r'\b(?:fraglich\w*|unsicher\w*|möglicherweise|eventuell|könnte\w*|Verdacht\s+auf|V\.\s*a\.|DD\b|Differenzialdiagnose|Differentialdiagnose|nicht\s+(?:sicher\s+)?auszuschließen|kein\w*\s+sicher\w*)',re.I)
NEGATOR=re.compile(r'\b(?:kein\w*|ohne|Ausschluss|negative\s+for|absence\s+of)\b',re.I)
CONCEPT_PATTERNS={
 'ENHANCEMENT':r'Aufladung\w*|(?:Kontrastmittel[- ]?|KM[- ])?(?:enhancement|Aufnahme)\w*|Kontrastmittel\s+aufnehm\w*',
 'VASCULITIS_ETIOLOGY':r'vaskulitisch\w*\s+Genese|Vaskulitis|Vasculitis',
 'CAA_ETIOLOGY':r'Amyloid\w*|\bCAA\b',
 'TUMOR':r'tumorbedingt\w*|neoplastisch\w*|Tumor\w*|tumorverdächtig\w*',
 'MASS_EFFECT':r'raumfordernde\s+Wirkung|Raumforderung\w*|Raumforderungsnachweis|Mittellinienverlagerung\w*|mass\s+effect',
 'EDEMA':r'Ödem\w*|Perifokalödem\w*',
 'ASSESSABILITY':r'(?:suffizient\w*|sicher\w*)\s+Aussage|Beurteilbarkeit',
 'INTERVAL_CHANGE':r'Änderung\w*|Dynamik|Progredienz|Progression|Zunahme',
 'TEMPORAL_PREDICTION':r'Resorption|resorbieren',
 'ISCHEMIC_LESION':r'Ischämie\w*|Infarzierung\w*|\w*[Ii]nfarkt\w*|ischämisch\w*\s+(?:Läsion\w*|Defekt\w*)',
 'HEMORRHAGE':r'\b(?:Blutung\w*|Hämatom\w*|ICB|ICH|Hämorrhagie\w*)\b',
 'SAH':r'\b(?:SAB|SAH|cSAH|Subarachnoidalblutung\w*|Sulcusblutung\w*|(?:subarachnoidal\w*|sul[ck]al\w*|konvexitätsnah\w*)\s+Blutung\w*)\b',
 'CMB':r'Mikroblutung\w*|Microbleeds?|Mikro-Hämosiderin\w*',
 'CSS':r'(?:oberflächlich\w*|superfiziell\w*|kortikal\w*|sulkal\w*)\s*(?:Hämosideros\w*|Sideros\w*)',
 'VASCULAR_CALIBER_ABNORMALITY':r'Kaliberirregularität\w*'}
QUALIFIED=re.compile(r'größer\w*|gros[sz]\w*|raumfordernd\w*|relevant\w*|wesentlich\w*|aktiv\w*|demarkiert\w*|flächig\w*|neoplastisch\w*|tumorverdächtig\w*|hämorrhagisch\w*\s+Transformation',re.I)

def qualified_target(text,match):
    prefix,_=local_context(text,match)
    if QUALIFIED.search(prefix):return True
    # Bare coordination inherits the first target's qualification; a separately
    # introduced target ("oder einer ...") keeps its own modifiers.
    if re.fullmatch(r'\s*[-,]?\s*(?:oder|bzw\.?|und)\s*',prefix,re.I):
        negs=list(NEGATOR.finditer(text[:match.start()]))
        if negs and QUALIFIED.search(text[negs[-1].end():match.start()]):return True
    return False

def concepts(text):
    return sorted([{'concept':k,'start':m.start(),'end':m.end(),'text':m.group()}
        for k,pat in CONCEPT_PATTERNS.items() for m in re.finditer(pat,text,re.I)],key=lambda c:(c['start'],-c['end']))

def local_context(text,match):
    """Bound age/compartment context to this morphology, before another target."""
    cs=concepts(text)
    prev=[c['end'] for c in cs if c['end']<=match.start() and c['concept'] not in ('TEMPORAL_PREDICTION',)]
    nxt=[c['start'] for c in cs if c['start']>=match.end()]
    lo=max(prev,default=0);hi=min(nxt,default=len(text))
    # Retain modifiers and anatomical terms adjacent to this exact morphology.
    return text[lo:match.start()],text[match.end():hi]

def candidate_state(text,match):
    """Identify the negated concept before assigning an assertion state."""
    prefix=text[:match.start()];suffix=text[match.end():]
    prefix=re.split(r'\n\s*--\s*',prefix)[-1]
    local_prefix,local_suffix=local_context(text,match)
    if re.search(r'\b(?:Fragestellung|Indikation|Kontrolle\s+nach|MR\s+bei\s+Z\.n\.)\b',prefix,re.I):return 'CONTEXT_ONLY'
    if re.fullmatch(r'\s*(?:Intrakranielle\s+Blutung|Ischämie)\s*',text,re.I):return 'CONTEXT_ONLY'
    if re.search(r'\b(?:falls|wenn|bei\s+parenchymaler\s+Komponente)\b',local_prefix,re.I):return 'CONDITIONAL'
    if re.search(r'\b(?:DD|Differenzialdiagnose|Differentialdiagnose)\b',prefix,re.I):return 'UNCERTAIN'
    if re.search(r'empfohlen|geplant|zu\s+erwarten',text,re.I) and re.search(r'nach\s+Resorption\s+(?:der|einer)\s*$',prefix,re.I):return 'CONDITIONAL'
    # Scope uncertainty to the target, rather than a later etiology/morphology.
    if UNCERTAIN.search(local_prefix) or re.search(r'kein\w*\s+(?:eindeutig\w*|sicher\w*)',local_prefix,re.I):return 'UNCERTAIN'
    if re.search(r'nicht\s+(?:sicher\s+)?(?:auszuschließen|abgrenzbar)',local_suffix,re.I):return 'UNCERTAIN'
    if '?' in local_suffix and not re.search(r'Genese|bedingt|posttraumatisch',local_suffix,re.I):return 'UNCERTAIN'
    negs=list(NEGATOR.finditer(prefix))
    if negs:
        neg=negs[-1]
        owners=[c for c in concepts(text) if neg.end()<=c['start']<match.start()]
        if not owners:return 'ABSENT'
        owner=owners[0]
        # A coordinated list shares its governing negator; relational phrases
        # such as "around/of/within the hemorrhage" do not.
        bridge=text[owner['end']:match.start()]
        if re.fullmatch(r'\s*[-,]?\s*(?:oder|bzw\.?|und)\s+(?:(?:einer|eine|eines|einem|rezenten|akuten|frischen)\s+)*',bridge,re.I):return 'ABSENT'
    if re.search(r'\bnicht\s+(?:mehr\s+)?(?:nachweisbar|sichtbar)\b',local_suffix,re.I):return 'ABSENT'
    # Postposed denial can govern a coordinated list, through its final target.
    if re.search(r'\bsind\s+nicht\s+sichtbar\b',suffix,re.I) and not re.search(r'[.;]',suffix.rstrip().rstrip('.')):return 'ABSENT'
    return 'PRESENT'

def assertion(text,match):
    return {'PRESENT':'ASSERTED','ABSENT':'NEGATED'}.get(candidate_state(text,match),candidate_state(text,match))

def compartment(kind,text,match=None):
    if kind!='ICH':return kind
    if match is not None and any(s.start()<=match.start() and match.end()<=s.end() for s in re.finditer(CONCEPT_PATTERNS['SAH'],text,re.I)):return None
    if match is not None:
        prefix,suffix=local_context(text,match)
        text=prefix+match.group()+suffix
    if re.search(r'subdural|epidural|extradural|Blutungsquelle|Tumorblutung',text,re.I):return None
    if re.search(r'Sideros|Hämosideros|Mikroblutung|Microbleed',text,re.I) and not re.search(r'intra[cz]erebral|intraparench|parenchymal\w*\s+Blutung|atypisch\w*\s+Blutung|nach\s+Resorption\s+einer\s+Blutung',text,re.I):return None
    if re.search(r'subarachnoidal|\bSAB\b|\bSAH\b|\bcSAH\b',text,re.I):return None
    if re.search(r'intra[cz]erebral|intraparenchymal|parenchymal\w*\s+(?:Blutung|Hämorrhag)|Parenchymblutung|lobär\w*\s+(?:Blutung|Hämatom)|\bICB\b|\bICH\b',text,re.I):return 'ICH'
    # Explicit brain-lobar location supplies the compartment for common terse
    # report-result sentences, but not sulcal/superficial or generic bleeding.
    if re.search(r'\b(?:hochfront[oa]\w*|front[oa]\w*|temporal\w*|parietal\w*|okzipital\w*|occipital\w*|postzentral|suprainsulär|Mediastromgebiet)\b',text,re.I) and not re.search(r'Sulc|Hirnfurch|sulkal|subdural',text,re.I):return 'ICH'
    return None

NEGATIVE_TARGETS={
 'NO_ACUTE_ISCHEMIA':r'\b(?:Ischämie\w*|ischemi\w*|Infarkt\w*)\b',
 'NO_ACUTE_HEMORRHAGE':r'\b(?:Blutung\w*|hemorrhag\w*|Hämatom\w*)\b',
 'NO_SAH':r'\b(?:SAB|SAH|cSAH|Subarachnoidalblutung\w*|Sulcusblutung\w*|(?:subarachnoidal\w*|sul[ck]al\w*|konvexitätsnah\w*)\s+Blutung\w*)\b',
 'NO_NEW_CMB':r'\b(?:Mikroblutung\w*|Microbleeds?)\b',
 'NO_CSS':r'(?:oberflächlich\w*|superfiziell\w*|kortikal\w*)\s*(?:Hämosideros\w*|Sideros\w*)',
 'NO_ENHANCEMENT':r'\b(?:Aufladung|Enhancement|Kontrastmittel\w*|KM[- ](?:Aufnahme|Enhancement))\b',
 'NO_MASS_LESION':r'\b(?:Raumforderung\w*|Raumforderungsnachweis|mass\s+lesion)\b',
 'NO_VASCULAR_CALIBER_ABNORMALITY':r'\bKaliberirregularität\w*\b'}

def negative_candidates(text):
    values=[]
    for kind,pat in NEGATIVE_TARGETS.items():
        for m in re.finditer(pat,text,re.I):
            if assertion(text,m)!='NEGATED':continue
            prefix,suffix=local_context(text,m)
            if qualified_target(text,m):continue
            value=kind
            if kind=='NO_NEW_CMB' and not re.search(r'\bneu\w*\b',text[:m.start()],re.I):
                value='OTHER' # Explicit absence of all CMB is not merely no new CMB.
            if kind=='NO_ACUTE_HEMORRHAGE':
                if any(s.start()<=m.start() and m.end()<=s.end() for s in re.finditer(CONCEPT_PATTERNS['SAH'],text,re.I)):continue
                if re.search(r'Blutungsquelle|Nachblutung|Tumorblutung',m.group(),re.I):continue
                if re.search(r'intra[cz]erebral|intraparenchymal|\bICH\b|\bICB\b',text,re.I):value='NO_ICH'
                elif re.search(r'Blutung\w*\s+(?:jeden|jeglichen)\s+Alters',text,re.I):value='OTHER'
            if kind=='NO_ENHANCEMENT' and re.search(r'wandständig|Vaskulitis|vaskulitis.typisch',text,re.I):value='NO_VASCULITIS_ENHANCEMENT'
            if value not in values:values.append(value)
    return values

def concept_assertions(text):
    """Retain modifier and qualified concepts without coercing phenotype enums."""
    rows=[]
    for c in concepts(text):
        # Match objects keep all ownership functions on identical coordinates.
        m=re.compile(re.escape(c['text'])).match(text,c['start'])
        state=candidate_state(text,m);prefix,_=local_context(text,m)
        concept=c['concept'];qualified=state=='ABSENT' and qualified_target(text,m)
        if qualified:concept='QUALIFIED_'+concept
        rows.append({**c,'concept':concept,'assertion_state':state,'qualified':qualified})
    return rows

def study_scope(study):
    header=study.header
    if re.search(r'Wirbelsäule|Lendenwirbel|Brustwirbel|Halswirbel|\bLWS\b|\bBWS\b',header,re.I):return 'SPINE'
    if re.search(r'Schädel|craniell|Hirn|brain|Schlaganfall|Epilepsie|\bMRT\b|\bMRI\b',header,re.I):return 'BRAIN'
    if re.search(r'Hals|neck',header,re.I):return 'HEAD_NECK'
    return 'OTHER'

def study_performed(study):
    return not bool(re.search(r'bricht\s+die\s+Patientin|abgebrochen|nicht\s+durchgeführt|nicht\s+erfolgt|not\s+performed',study.report_text,re.I))
