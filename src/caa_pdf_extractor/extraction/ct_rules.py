"""CT/CTA-specific additions; MRI and PET normalization remain independent."""
import re
from dataclasses import replace
from .radiology_v2_rules import SUPPORT,extract_findings,clauses,source_offset
from .radiology_assertions import candidate_state,qualified_target,study_scope,compartment,NEGATIVE_TARGETS,local_context
from .imaging_reports import DATE,parse_date,document_text,date_for
VERSION='ct_atomic_findings_v2_owned_date_fallback'

def complete_ct_header(study,document,allow_document_fallback=True):
    """Own a wrapped protocol date before considering any report comparisons."""
    if study.modality not in ('CT','CTA'):return
    # Current acquisition dates precede comparison/history clauses. If no
    # acquisition date exists, retain the approved document-date/discharge
    # fallback with its explicit provenance, never a historical image date.
    header=re.split(r'Letzte\w*\s+(?:CCT|CT)|Vergleich|Voruntersuch|Historisch\w*',study.header,flags=re.I)[0]
    first=re.match(r'(?P<protocol>(?:2E\s*)?\(?'+DATE+r'\))\s*',study.report_text)
    if not first or not (study.header.rstrip().endswith('(') or re.search(r'Handgelenk\s+li\s*$',study.header,re.I)):
        study.exam_date,study.exam_date_source=date_for(header,study.report_text,document,allow_document_fallback)
        return
    span=next(s for s in study.source_spans if s['role']=='report')
    header_span=next(s for s in study.source_spans if s['role']=='header')
    raw=document_text(document);end=span['start']+first.end('protocol')
    study.header=raw[header_span['start']:end].strip()
    study.exam_date=parse_date(re.search(DATE,first.group()).group());study.exam_date_source='REPORT_EXPLICIT'
    start=span['start']+first.end();study.report_text=raw[start:span['end']]
    study.source_spans=[{'role':'header','start':header_span['start'],'end':end,'text':raw[header_span['start']:end]},
        {'role':'report','start':start,'end':span['end'],'text':study.report_text}]
EXTRA={
 'EDEMA':re.compile(r'\b(?:Ödem\w*|Perifokalödem\w*|edema)\b',re.I),
 'MASS_EFFECT':re.compile(r'\b(?:Mittellinienverlagerung\w*|Mittellinienshift|raumfordernde\w*\s+Wirkung|Raumforderungszeichen|mass\s+effect)\b',re.I),
 'HYDROCEPHALUS':re.compile(r'\b(?:Hydro[cz]ephalus|Liquoraufstau|Ventrikelaufstau)\b',re.I),
 'CALCIFICATION':re.compile(r'\b(?:Verkalkung\w*|Kalzifikation\w*|calcification\w*)\b',re.I)}
def extract_ct(study,document,studies):
    original=study
    # Page furniture can split a governing negator from its target. Mask only
    # explicit letterhead blocks, preserving coordinates and source quotations.
    furniture=re.compile(r'(?im)^\s*Universitätsklinikum[^\n]*\n(?:[^\n]*\n){0,7}?[^\n]*geb\.:\s*[^\n]*Seite\s+\d+[^\n]*')
    cleaned=furniture.sub(lambda m:' '*len(m.group()),study.report_text)
    study=replace(study,report_text=cleaned)
    support={**SUPPORT,**EXTRA,'ICH':re.compile(SUPPORT['ICH'].pattern+r'|\b(?:Parenchymblutung|Blutungsareal)\w*\b',re.I)}
    result=extract_findings(study,document,studies,support_patterns=support,compartment_resolver=ct_compartment,
        exact_evidence=lambda start,end,_:original.report_text[start:end])
    result['negative_findings']=[n for n in result['negative_findings'] if not (n['finding_type'] in ('NO_ACUTE_HEMORRHAGE','NO_ICH') and re.search(r'kein\w*\s+(?:frisch\w*\s+)?groß\w*\s+Blutung',n['evidence_text'],re.I))]
    for start,end,q in clauses(study.report_text):
        patterns={**EXTRA,'TERRITORIAL_INFARCT':re.compile(r'territorial\w*\s+Infarkt\w*',re.I)}
        for kind,pat in patterns.items():
            for m in pat.finditer(q):
                state=candidate_state(q,m)
                if state=='ABSENT' and not qualified_target(q,m):
                    if m.group().casefold().startswith('perifokal'):continue
                    if kind=='TERRITORIAL_INFARCT':
                        result['negative_findings']=[n for n in result['negative_findings'] if not (n['evidence_text']==q and n['finding_type']=='NO_ACUTE_ISCHEMIA')]
                    result['negative_findings'].append({'finding_type':'NO_'+kind,'location':None,'evidence_text':q,'source_offsets':source_offset(study,start,end)})
    result['ct_rule_version']=VERSION
    accepted=[]
    for n in result['negative_findings']:
        q=n['evidence_text']
        target_type='NO_ACUTE_HEMORRHAGE' if n['finding_type']=='NO_ICH' else n['finding_type']
        absent=[m for m in re.finditer(NEGATIVE_TARGETS.get(target_type,r'(?!)'),q,re.I) if candidate_state(q,m)=='ABSENT']
        scoped_qualified=absent and all(re.search(r'\b(?:groß\w*|grösser\w*|größer\w*|signifikant\w*|kortikal\w*|cortical\w*|territorial\w*|intraventrikulär\w*|abgrenzbar\w*|Früh)\b',local_context(q,m)[0],re.I) or re.search(r'frühzeichen',m.group(),re.I) for m in absent)
        if n['finding_type'] in ('NO_ACUTE_ISCHEMIA','NO_ACUTE_HEMORRHAGE','NO_ICH','NO_SAH','NO_MASS_LESION') and scoped_qualified:
            result['other_assertions'].append({**n,'reason':'CT_QUALIFIED_ABSENCE_NOT_BROADENED','assertion_state':'ABSENT','qualified':True});continue
        if n['finding_type']=='NO_MASS_EFFECT' and re.search(r'Mittellinie',q,re.I):n={**n,'finding_type':'OTHER'}
        if n not in accepted:accepted.append(n)
    result['negative_findings']=accepted
    findings=[]
    for f in result['findings']:
        if f['finding_type']=='CALCIFICATION' and re.search(r'Aorten|Aorta|Hilus|Kieferhöhle|Bukbus|Bulbus|Karotisbifurkation',f['evidence_text'],re.I):
            result['other_assertions'].append({**f,'reason':'NON_BRAIN_COMPONENT','assertion_state':'PRESENT'});continue
        findings.append(f)
    result['findings']=findings
    for values in result.values():
        if not isinstance(values,list):continue
        for record in values:
            q=record.get('evidence_text') if isinstance(record,dict) else None
            if q:
                offsets=record.get('source_offsets')
                if offsets:record['evidence_text']=document_text(document)[offsets['start']:offsets['end']]
                else:
                    start=cleaned.find(q)
                    if start>=0:record['evidence_text']=original.report_text[start:start+len(q)]
    return result

def ct_compartment(kind,text,match):
    # An explicitly lobar/parenchymal component owns its compartment even when
    # the same sentence separately describes adjacent subarachnoid blood.
    if kind=='ICH' and re.search(r'(?:lobär\w*\s+Blutung\w*|Parenchymblutung\w*)$',text[max(0,match.start()-35):match.end()],re.I):return 'ICH'
    if kind=='ICH' and re.match(r'Blutungsareal',match.group(),re.I) and re.search(r'frontal|parietal|temporal|okzipital|occipital|Stammganglien|Thalamus',text,re.I) and not re.search(r'subdural|epidural|subarachnoid|\bSAB\b|Sulc|Hirnfurch',text,re.I):return 'ICH'
    return compartment(kind,text,match)

def ct_modality(study):
    # A combined native CT/CTA protocol remains CT; an explicit CTA-only
    # procedure is not mislabeled solely because its billing prefix says CT.
    if study.modality not in ('CT','CTA'):return study.modality
    h=study.header
    if re.fullmatch(r'Computertomogra\w*\s+vom\s+\d[\d.]+',h,re.I):h+=' '+study.report_text[:250]
    return 'CTA' if re.search(r'\bCTA\b',h,re.I) and not re.search(r'Schädel\s+nativ|Thorax\s+nativ|Schädel\s+craniell\s+nativ',h,re.I) else study.modality

def ct_scope(study):
    h=study.header
    if re.fullmatch(r'Computertomogra\w*\s+vom\s+\d[\d.]+',h,re.I):
        h+=' '+study.report_text[:250]
        if re.search(r'Seitenventrikel|Circulus\s+arteriosus|frontotemporal|intracran|intrakran|intra-\s*und\s+extrakran',study.report_text,re.I):return 'BRAIN'
    if re.search(r'Schädel|craniell|intracran|intrakran|Hirn|brain|Cerebrum|Neurokranium|\bCCT\b',h,re.I):return 'BRAIN'
    return study_scope(study)
