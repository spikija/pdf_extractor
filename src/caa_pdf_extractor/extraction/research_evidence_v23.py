"""Dated facts and laboratory observations from already extracted text."""
import re
from .clinical_scales_v23 import dates
from .research_scores_v23 import as_date,component

FACTS={'C':r'Herzinsuffizienz|heart failure|kardiale\s+Dekompensation',
 'H':r'\bHypertonie\b|\bHypertension\b|\bHTN\b',
 'D':r'Diabetes\s+mellitus|\bDM\s*(?:II|2|I|1)\b',
 'S2':r'isch[äa]m\w*.{0,35}(?:Infarkt|Insult)|transitorisch\s+isch[äa]m\w*\s+Attacke|\bTIA\b',
 'V':r'Myokardinfarkt|myocardial infarction|\bpAVK\b|peripher\w*\s+arteriell\w*\s+Verschluss',
 'anemia_history':r'An[äa]mie|anaemia|anemia',
 'bleeding_history':r'(?:intrazerebral|intracerebral)\w*\s+(?:Blutung|H[äa]matom|H[äa]morrhagie)|Subarachnoidalblutung|GI[- ]Blutung|gastrointestinal\w*\s+Blutung',
 'AF':r'Vorhofflimmern|\bVHF\b|atrial fibrillation',
 'CAA':r'(?:cerebral\w*|zerebral\w*)\s+Amyloidangiopathie|\bCAA\b',
 'PREVIOUS_SEIZURES':r'\bEpilepsie\b|epilepsy'}

def facts_from_section(text,*,patient_id,document_id,section_id,available_date,page=None):
    out=[]
    # Positive disease facts are extracted only from diagnosis sections by caller.
    if not as_date(available_date):return out
    for n,line in enumerate(text.splitlines()):
        for key,rx in FACTS.items():
            m=re.search(rx,line,re.I)
            if not m:continue
            before=line[max(0,m.start()-45):m.start()]
            if key=='H' and re.search(r'pulmonal|portal|intrakraniell',before,re.I):continue
            if re.search(r'Verdacht|\bV\s*\.\s*a\s*\.|DD\s*:|Ausschluss|Risiko',before,re.I):continue
            negative=bool(re.search(r'\b(?:kein\w*|ohne|no|without)\b',before,re.I))
            eid=f'{document_id}:{section_id}:fact:{n}:{key}'
            out.append(dict(patient_id=str(patient_id),component=key,**component(not negative,available_date,[eid]),
                source_document_id=str(document_id),section_id=section_id,page=page,reported_event_dates=dates(line),evidence_quote=line[:160],date_basis='DOCUMENT_AVAILABLE_DATE'))
    return out

def facts_at_index(rows,index):
    """Unresolved contradictory facts remain unknown; absence stays missing."""
    result={};d=as_date(index)
    if not d:return result
    for key in FACTS:
        eligible=[r for r in rows if r['component']==key and as_date(r['evidence_date']) and as_date(r['evidence_date'])<=d]
        values={r['value'] for r in eligible}
        if len(values)==1:
            result[key]=component(next(iter(values)),max(r['evidence_date'] for r in eligible),[x for r in eligible for x in r['source_evidence_ids']])
    return result

LABS=[('HEMOGLOBIN',r'(?<!\w)(?:H[äa]moglobin|hemoglobin|Hb)(?!\w)',r'g/d[lL]|g/[lL]'),
 ('HEMATOCRIT',r'H[äa]matokrit|hematocrit|\bHkt\b|\bHct\b',r'%|[lL]/[lL]'),
 ('EGFR',r'\beGFR\b|glomerul[äa]r\w*\s+Filtrationsrate',r'm[lL]/min(?:/1[.,]73\s*m[²2])?'),
 ('CREATININE',r'Kreatinin|creatinine',r'mg/d[lL]|[µu]mol/[lL]')]

def labs_from_section(text,*,patient_id,document_id,section_id,page=None,admission_date=None):
    out=[]
    for n,line in enumerate(text.splitlines()):
        for analyte,pattern,unit in LABS:
            # Only a value explicitly next to its analyte and unit is accepted.
            # Multi-column laboratory tables need separately attributable cells.
            m=re.search('(?:'+pattern+r')\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*('+unit+r')',line,re.I)
            table=False
            if not m:
                # Cached "reference | unit | first | last | min | max" rows.
                # Recover the source first cell, but never assign the admission
                # date as a phlebotomy date. Urine creatinine is not serum.
                if re.search(r'Kreatinin\s*[/ (]\s*(?:Harn|Urin|HPM)',line,re.I):continue
                m=re.search(r'^\s*(?:'+pattern+r')(?:\s+CKD-EPI|\s*\([^)]*\))?\s+(?:[<>]?\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?\s+)?('+unit+r'|ml/min/)\s+([<>]?\d+(?:[.,]\d+)?)',line,re.I)
                if not m:continue
                # Reorder to the narrative convention used below.
                table=True;raw_value=m[2];raw_unit=m[1]
            else:raw_value=m[1];raw_unit=m[2]
            ds=dates(line);value=float(raw_value.lstrip('<>').replace(',','.'));u=raw_unit
            if analyte=='HEMOGLOBIN':
                if u.lower()=='g/l':value/=10
                u='g/dL'
            elif analyte=='HEMATOCRIT':
                if u.lower()=='l/l':value*=100
                u='%'
            elif analyte=='EGFR':u='mL/min/1.73m2' if re.search(r'1[.,]73',u) else 'mL/min_UNSPECIFIED_NORMALIZATION'
            else:u='mg/dL' if u.lower()=='mg/dl' else 'umol/L'
            out.append(dict(patient_id=str(patient_id),analyte=analyte,value=value,unit=u,sample_date=ds[0] if len(set(ds))==1 else None,
                admission_date=admission_date,source_document_id=str(document_id),section_id=section_id,page=page,
                source_evidence_ids=[f'{document_id}:{section_id}:lab:{n}:{analyte}'],evidence_quote=line[:160],
                validation_status='VALID' if len(set(ds))==1 and raw_value[0] not in '<>' else 'SAMPLE_DATE_UNKNOWN' if len(set(ds))!=1 else 'NONEXACT_RESULT',
                source_column_role='FIRST' if table else 'NARRATIVE',comparator=raw_value[0] if raw_value[0] in '<>' else '=',date_basis='EXPLICIT_ON_ANALYTE_LINE' if len(set(ds))==1 else 'UNKNOWN'))
    return out

def status_components(event,labels,sections,previous_facts):
    """Only event-owned status type/admission consciousness and prior seizures.

    Discharge consciousness does not replace the admission component. Multiple
    competing observations without times remain unresolved.
    """
    day=event.get('event_date');eid=event['id'];typ=None;con=None;prior=None
    joined=' '.join(labels)
    if as_date(day):
        if re.search(r'nicht.?konvulsiv|non.?convuls|\bNCSE\b',joined,re.I):
            if re.search(r'koma|coma',joined,re.I):typ=component('NCSE_IN_COMA',day,[eid])
        elif re.search(r'konvulsiv|convulsiv',joined,re.I):typ=component('GENERALIZED_CONVULSIVE',day,[eid])
        elif re.search(r'fokal|focal|aphasicus',joined,re.I):typ=component('FOCAL',day,[eid])
        prior_epilepsy=[f for f in previous_facts if f.get('component')=='PREVIOUS_SEIZURES' and as_date(f.get('evidence_date')) and as_date(f['evidence_date'])<as_date(day)]
        if prior_epilepsy:prior=component(True,max(f['evidence_date'] for f in prior_epilepsy),[x for f in prior_epilepsy for x in f['source_evidence_ids']])
        observations=[]
        for sec in sections:
            heading=sec.get('heading') or '';text=sec.get('source_text') or sec.get('text') or ''
            if not re.search(r'Aufnahme|Aufnahmestatus',heading,re.I):continue
            if re.search(r'sediert|intubiert',text,re.I):continue
            values=[]
            for rx,val in [(r'komat[öo]s|comatose','COMATOSE'),(r'stupor','STUPOROUS'),(r'somnolent','SOMNOLENT'),(r'verwirrt|confused','CONFUSED'),(r'\bwach\b|awake','AWAKE')]:
                if re.search(rx,text,re.I):values.append(val)
            if len(set(values))==1:observations.append(component(values[0],day,[sec['section_id']]))
            if re.search(r'keine\s+(?:fr[üu]heren|bisherigen|vorherigen)\s+(?:epileptischen\s+)?Anf[äa]lle',text,re.I):
                if prior is None:prior=component(False,day,[sec['section_id']])
        if len({e['value'] for e in observations})==1 and observations:con=observations[0]
    return dict(previous_seizures=prior,seizure_type=typ,consciousness=con)
