"""Source-qualified diagnosis discovery, assertion chronology and event identity V4.

No model, medication resolver, database or network dependencies.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import date

from ..module_artifacts import digest
from .diagnosis_identity_v3 import RULES, key, qualifiers
from .assertion_scope import validate_assertion, concept_clause, af_observation, resolve_af

VERSION = '4.3'
AUTHORITATIVE = re.compile(r'diagnosen|hauptdiagnos|nebendiagnos|vorerkrank|vordiagnos', re.I)
HEADING = re.compile(r'^(?:diagnose[n]?|vorerkrankungen|vordiagnosen|internistischer befund|untersuchungstechnik|rechtes herz|linkes herz|mrt|pet|eeg|anamnese|beurteilung|zusammenfassung|thrombophilie.screening)\s*:?$', re.I)
THERAPY = re.compile(r'cyclophosphamid|rituximab|infliximab|endoxan|cpap.{0,20}therapie|therapie\s*:|systemische lyse', re.I)
TECHNIQUE = re.compile(r'untersuchungstechnik|diffusionsstudie|adcmap|t1.gewicht|t2 axial|flair axial|swi axial|sequenzbeschreibung', re.I)
NEGATIVE = re.compile(r'\bkein\w*\b|\bohne hinweis|\bnicht nachweisbar|\bno evidence|\bnie\b|\bverneint', re.I)
SUSPICION = re.compile(r'\bdd\b|differentialdiagnos|verdacht|\bv\s*\.\s*a\s*\.|suspekt|wahrscheinlich|vereinbar mit|könnte|können.{0,20}sprechen|hinweisend|fragestellung|mögliche[rns]?\s', re.I)
DAYS = re.compile(r'(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*((?:19|20)\d{2})(?!\d)')
MONTHS = re.compile(r'(?<![\d.])(0?[1-9]|1[0-2])\s*/\s*((?:19|20)\d{2})(?!\d)')
YEARS = re.compile(r'\b(?:19|20)\d{2}\b')

# Regexes locate actual concept spans, not arbitrary whole-sentence labels.
DISEASES = [
 ('CEREBRAL_AMYLOID_ANGIOPATHY', r'(?:[cz]erebrale?\s+)?amyloidangiopathie|\bCAA\b'),
 ('CNS_VASCULITIS', r'(?:[cz]erebrale[nrs]?\s+|cerebrale[nrs]?\s+|cns[- ]?)va[skc]+ulitis|vaskulitis\s+mit\s+cerebraler\s+beteiligung'),
 ('ATRIAL_FIBRILLATION', r'vorhofflimmer\w*(?:[- ]arrhythmie)?|\bVHF\b|atrial fibrillation|\bAF\b'),
 ('DIABETES_MELLITUS_TYPE_2', r'diabetes\s+mellitus\s*(?:typ\s*(?:2|II))?|\bDM\s*(?:typ\s*)?(?:2|II)\b'),
 ('ARTERIAL_HYPERTENSION', r'arterielle?\s*hypertonie|\bhypertonie\b'),
 ('PULMONARY_HYPERTENSION', r'(?:sekundär\s+)?pulmonal(?:arteriell)?e[rns]?\s+hyperton(?:ie|us)'),
 ('HYPOTHYROIDISM', r'hypothyreose|hypothyroidism'),
 ('RESTLESS_LEGS_SYNDROME', r'restless[- ]legs(?:[- ]syndrom)?'),
 ('LICHEN_PLANUS', r'lichen\s+(?:ruber\s+)?planus'),
 ('OBSTRUCTIVE_SLEEP_APNEA', r'obstruktive[snrm]?\s+schlafapnoe(?:[- ]syndrom)?|\bOSAS\b'),
 ('POLYNEUROPATHY', r'polyneuropathie'),
 ('LIPID_DISORDER', r'hyperlipidämie|dyslipidämie|hypercholesterinämie|lipidstoffwechselstörung'),
 ('OSTEOPOROSIS', r'osteoporose'),
 ('CAROTID_ARTERY_STENOSIS', r'\bCAVK\b|(?:stenose\s+der\s+)?(?:arteria\s+)?carotis\s+interna.{0,30}stenose|stenose\s+der\s+arteria[e]?\s+carot[ie]s\s+interna'),
 ('SYMPTOMATIC_EPILEPSY', r'(?:symptomatische[rns]?|symptomatiche)\s+epilepsie'),
 ('HEART_FAILURE', r'herzinsuffizienz'),
 ('CHRONIC_KIDNEY_DISEASE', r'(?:chronisch\w*\s+|dialysepflichtig\w*\s+)?niereninsuffizi?enz|\bCKD\b'),
 ('RENAL_CELL_CARCINOMA', r'nierenzellkarzinom'),
 ('HIGH_GRADE_GLIOMA', r'(?:hochgradig\w*|höhergradig\w*)\s+gliom|high[- ]grade\s+glioma'),
 ('STRUCTURAL_EPILEPSY', r'strukturelle[rns]?\s+epilepsie'),
 ('VESTIBULAR_NEURITIS', r'neuritis\s+vestibularis|vestibularisneuritis'),
 ('DEMENTIA', r'vaskuläre\s+demenz|mischdemenz|\bdemenz\b|\bdementia\b'),
 ('BREAST_CANCER', r'mamma[- ]?ca\b|mammakarzinom|breast\s+carcinoma'),
 ('CORONARY_ARTERY_DISEASE',r'koronare\s+herzkrankheit|\bKHK\b'),
 ('HYDROCEPHALUS',r'hydro[cz]ephalus'),
 ('PARKINSONISM',r'parkinson[- ]?syndrom'),
 ('RECTAL_CARCINOMA',r'rektumkarzinom|rektales?\s+karzinom|\bN\.\s*recti\b'),
 ('PERIPHERAL_ARTERIAL_DISEASE',r'\bpAVK\b|periphere\s+arterielle\s+verschlusskrankheit'),
 ('PULMONARY_SARCOIDOSIS',r'lungensarkoidose|pulmonale\s+sarkoidose'),
]
TAXONOMY = {name: (domain, category) for _, name, domain, category in RULES}
TAXONOMY['LIPID_DISORDER'] = ('ENDOCRINE_METABOLIC', 'LIPID_DISORDER')
TAXONOMY.update(CHRONIC_KIDNEY_DISEASE=('RENAL','CHRONIC_KIDNEY_DISEASE'),
    RENAL_CELL_CARCINOMA=('ONCOLOGIC','RENAL_CELL_CARCINOMA'),HIGH_GRADE_GLIOMA=('NEUROLOGIC','BRAIN_TUMOR'),
    STRUCTURAL_EPILEPSY=('NEUROLOGIC','EPILEPSY'),VESTIBULAR_NEURITIS=('NEUROLOGIC','VESTIBULAR'),
    DEMENTIA=('NEUROLOGIC','COGNITIVE'),BREAST_CANCER=('ONCOLOGIC','BREAST_CANCER'),
    CORONARY_ARTERY_DISEASE=('CARDIOVASCULAR','CORONARY_ARTERY_DISEASE'),HYDROCEPHALUS=('NEUROLOGIC','HYDROCEPHALUS'),
    PARKINSONISM=('NEUROLOGIC','MOVEMENT_DISORDER'),RECTAL_CARCINOMA=('ONCOLOGIC','RECTAL_CARCINOMA'),
    PERIPHERAL_ARTERIAL_DISEASE=('CARDIOVASCULAR','PERIPHERAL_ARTERIAL_DISEASE'),PULMONARY_SARCOIDOSIS=('PULMONARY','SARCOIDOSIS'))


def disease_attributes(name, text):
    """Source attributes only; no staging, mechanism or dates inferred clinically."""
    out={}
    side=re.search(r'\b(links|rechts)\b',text,re.I)
    if side:out['side']={'links':'LEFT','rechts':'RIGHT'}[side[1].lower()]
    if name=='CAROTID_ARTERY_STENOSIS':
        out['side']='BILATERAL' if re.search(r'beidseits|\bbds\.?',text,re.I) else out.get('side')
        out['measurements']=[dict(side={'links':'LEFT','rechts':'RIGHT'}[m[1].lower()],stenosis_percent=int(m[2]),
            measurement_method='ECST' if re.search(r'ECST',text,re.I) else 'UNKNOWN') for m in re.finditer(r'\b(links|rechts)\s+(\d{1,3})\s*%',text,re.I)]
    if name=='PULMONARY_SARCOIDOSIS':
        year=re.search(r'Erstdiagnose\s+((?:19|20)\d{2})',text,re.I)
        out['first_diagnosis_year']=int(year[1]) if year else None
    if name=='CHRONIC_KIDNEY_DISEASE':
        stage=re.search(r'\bG\s*([1-5](?:[ab])?)\b',text,re.I)
        out.update(stage='G'+stage[1].upper() if stage else None,
            dialysis_dependent=True if re.search(r'dialysepflichtig',text,re.I) else None,
            underlying_cause='NEPHROCALCINOSIS' if re.search(r'bei\s+nephrokalzinose',text,re.I) else None)
    if name=='HIGH_GRADE_GLIOMA':
        out.update(location='FRONTAL' if re.search(r'frontal',text,re.I) else None,
            secondary_hemorrhage=True if re.search(r'sek(?:undär\w*)?\.?\s+einblutung',text,re.I) else None)
        ed=re.search(r'\bED\s+(\d{1,2}/\d{4})',text,re.I)
        if ed:out['diagnosis_date'],out['diagnosis_date_precision']=explicit_period(ed[1])
    if name=='DEMENTIA':
        out['subtype']='VASCULAR_DEMENTIA' if re.search(r'vaskuläre\s+demenz',text,re.I) else 'MIXED_DEMENTIA' if re.search(r'mischdemenz',text,re.I) else 'UNSPECIFIED'
    ref=re.search(r'bei\s+Diagnose\s+(\d+)',text,re.I)
    if ref:out['source_diagnosis_reference_number']=int(ref[1])
    return out

EVENTS = [
 ('ISCHEMIC_STROKE', r'(?:ischämisch\w*\s+)?(?:(?:arteria\s+)?cerebri\s+)?(?:media[- ]?(?:teil)?infarkt|mediainfarkt|mediateilinfarkt|ACM[- ]infarkt)|ischämisch\w*\s+(?:arteria\s+cerebri\s+media[- ]infarkt|infarkt\w*|insult|schlaganfall)|ischäm(?:isch\w*)?\.?\s+stammganglieninfarkt|(?:[cz]erebral\w*\s+)?multiinfarktgeschehen|(?:rezente?\w*\s+)(?:(?:kleine|punktförmige|gyrale)\w*\s+)*(?:ischämisch\w*\s*läsion|infarzierung)\w*'),
 ('TIA', r'\bTIA\b|transitorisch\w*\s+ischämisch\w*\s+attacke'),
 ('PRIMARY_ICH', r'intraparenchymal\w*\s+hämatom|intrazerebral\w*\s+blutung|intracerebral\w*\s+blutung|intracraniell\w*\s+hämorrhagie|stammganglienblutung|einblutung|hämorrhagisch\w*\s+(?:hinter\w*\s+)?mediateilinfarkt'),
 ('SAH', r'subarachnoidalblutung|\bSAB\b|\bSAH\b'),
 ('STATUS_EPILEPTICUS', r'status\s+epilepticus'),
 ('SEIZURE', r'(?:symptomatisch\w*\s+)?(?:einfach\s+)?fokal\w*\s+anfall|epileptisch\w*\s+anfall'),
 ('PULMONARY_EMBOLISM', r'pulmonalarterienembolie|pulmonalarteriell\w*\s+embolie|lungenembolie|pulmonalembolie'),
 ('DVT', r'(?:tiefe[rn]?\s+)?beinvenenthrombose|tiefe\s+venenthrombose'),
 ('STROKE_UNSPECIFIED', r'\binsult\b|[cz]erebral\w*\s+infarkt'),
]


def explicit_period(text):
    for m in DAYS.finditer(text):
        try:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat(), 'DAY'
        except ValueError:
            pass
    m = MONTHS.search(text)
    if m:
        return f'{int(m[2]):04}-{int(m[1]):02}', 'MONTH'
    months={'januar':1,'februar':2,'märz':3,'april':4,'mai':5,'juni':6,'juli':7,'august':8,'september':9,'oktober':10,'november':11,'dezember':12}
    m=re.search(r'\b('+'|'.join(months)+r')\s+((?:19|20)\d{2})\b',text,re.I)
    if m:return f'{m[2]}-{months[m[1].lower()]:02}','MONTH'
    m = YEARS.search(text)
    return (m[0], 'YEAR') if m else (None, 'UNKNOWN')


def header_date(document):
    if document.get('document_date'):
        return document['document_date']
    found = set()
    for s in document.get('sections', []):
        if s.get('page_start') != 1:
            continue
        for m in re.finditer(r'(?im)^\s*(?:[A-Za-zÄÖÜäöüß .-]+,\s*am|Datum\s*:)\s*(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})\s*$', s['text']):
            value, precision = explicit_period(m[1])
            if precision == 'DAY':
                found.add(value)
    return next(iter(found)) if len(found) == 1 else None


def source_units(bundle, history):
    """Keep every legacy raw span, then discover qualified narrative concepts anew.

    Existing spans are not authoritative eligibility decisions. Every span receives
    an explicit disposition in the raw audit layer, including rejected spans.
    """
    sections = {s['section_id']: (d, s) for d in bundle['documents'] for s in d['sections']}
    units = []
    seen = set()
    for i, raw in enumerate(history['mentions'] + history.get('audit_only', [])):
        d, s = sections[raw['section_id']]
        text = raw.get('evidence_text') or raw.get('disease', '')
        start = raw.get('start', 0)
        quote = r'\s+'.join(re.escape(word) for word in text.split())
        located = re.search(quote, s['text']) if quote else None
        if located:
            start = located.start()
        # Preserve trailing periods/years omitted by the previous line splitter.
        end = located.end() if located else raw.get('end', start + len(text))
        line_end = s['text'].find('\n', end)
        if line_end < 0:
            line_end = len(s['text'])
        tail = s['text'][end:line_end].strip()
        if located and tail and not text.endswith(tail) and re.fullmatch(r'[\s,;.()\d/-]+', tail):
            text += ' ' + tail
        unit = dict(span_id=raw.get('item_id') or f'legacy:{i}', raw_text=text,
                    original_source=raw, document_id=d['source_document_id'], section_id=s['section_id'],
                    heading=s.get('heading'), page=raw.get('page') or s.get('page_start'),
                    start=start, end=end, document_date=header_date(d), origin='LEGACY_RAW_SPAN')
        units.append(unit)
        seen.add((unit['section_id'], key(text)))
    concept = re.compile('|'.join(p for _, p in DISEASES + EVENTS), re.I)
    for d in bundle['documents']:
        for s in d['sections']:
            # Never discover diagnoses from a medication list or laboratory table.
            if re.search(r'medikation|laborwerte|blutzucker|medizinische validierung', s.get('heading') or '', re.I):
                continue
            # Wrapped prose is joined; bullets, numbered entries and blank lines delimit spans.
            blocks = []
            current, offset, position = [], 0, 0
            for line in s['text'].splitlines(keepends=True):
                stripped = line.strip()
                new = not stripped or re.match(r'^(?:\d{1,2}\.\s|[-–]+\s|(?:Vorerkrankungen|Vordiagnosen|Anamnese|Diagnose|Untersuchungstechnik)\b)', stripped, re.I)
                if new and current:
                    blocks.append((offset, ' '.join(current)))
                    current = []
                if stripped:
                    if not current:
                        offset = position
                    current.append(stripped)
                    if re.search(r'[.!?]$', stripped) or HEADING.fullmatch(stripped):
                        blocks.append((offset, ' '.join(current)))
                        current = []
                position += len(line)
            if current:
                blocks.append((offset, ' '.join(current)))
            for offset, text in blocks:
                if not concept.search(text):
                    continue
                if (s['section_id'], key(text)) in seen:
                    continue
                # Avoid counting a rewrapped version of a legacy source quote twice.
                if any(u['section_id'] == s['section_id'] and abs(u['start'] - offset) < 15
                       and key(u['raw_text']) in key(text) for u in units):
                    continue
                seen.add((s['section_id'], key(text)))
                units.append(dict(span_id=f"{s['section_id']}:v4:{offset}", raw_text=text,
                    document_id=d['source_document_id'], section_id=s['section_id'], heading=s.get('heading'),
                    page=s.get('page_start'), start=offset, end=offset + len(text),
                    document_date=header_date(d), origin='SOURCE_REDISCOVERY'))
    return units, sections


def authoritative(unit, section):
    if AUTHORITATIVE.search(unit.get('heading') or ''):
        return True
    # Medical-history subsections are sometimes embedded in an unheaded continuation.
    prefix = section['text'][:unit['start']]
    match = list(re.finditer(r'(?im)^\s*(Vorerkrankungen|Vordiagnosen)\s*:?\s*$', prefix))
    return bool(match and not re.search(r'(?im)^\s*(?:Befund|Therapie|Procedere|Medikation)\s*:', prefix[match[-1].end():]))


def classify(text, authority=False, concept=False, event=False):
    text = re.sub(r'^\s*(?:\d+\.|[-–]+)\s*', '', text).strip()
    if HEADING.fullmatch(text):
        return 'HEADING', False
    if re.search(r'^(?:(?:cyclophosphamid|rituximab|infliximab|endoxan|cpap)[- ]therapie|einleitung\s+einer\s+cpap|therapie\s*:|beg[n]?inn\s+(?:einer|mit|am))', text, re.I) and not event:
        return 'THERAPY', False
    if TECHNIQUE.search(text) and not event:
        return 'PROCEDURE', False
    matches = concepts(text) if concept or event else []
    scoped = validate_assertion(text, matches[0][0], matches[0][1]) if len(matches) == 1 else None
    if scoped and scoped['assertion_status'] == 'NEGATED':
        return 'NEGATED', False
    if scoped and scoped['assertion_status'] in ('UNCERTAIN', 'SOURCE_ONLY'):
        return 'DIFFERENTIAL_DIAGNOSIS', False
    if scoped is None and NEGATIVE.search(text):
        return 'NEGATED', False
    if scoped is None and SUSPICION.search(text):
        return 'DIFFERENTIAL_DIAGNOSIS', False
    if THERAPY.search(text) and not concept and not event:
        return 'THERAPY', False
    if event:
        return 'ACUTE_EVENT', True
    if concept:
        if re.search(r'microbleed|hämosiderin|km.enhancement|diffusions|tracer|zeichen', text, re.I) and not authority:
            return 'INVESTIGATION_FINDING', False
        return ('HISTORICAL_DISEASE' if re.search(r'z\.\s*n\.|zustand nach|vorbekannt|bekannt seit', text, re.I) else 'ESTABLISHED_DISEASE'), True
    if re.search(r'normal|unauffällig|kompensiert|normal weit', text, re.I):
        return 'INVESTIGATION_FINDING', False
    if THERAPY.search(text):
        return 'THERAPY', False
    if re.search(r'\b(?:mg|mmol|g/l|u/l|antikörper|AHI|INR)\b', text, re.I):
        return 'INVESTIGATION_FINDING', False
    if re.search(r'akutes abdomen|schmerz|subjektive kognitive verschlechterung', text, re.I):
        return 'SYMPTOM', False
    return 'OTHER', False


def concepts(text):
    hits = []
    for name, pattern in DISEASES:
        for m in re.finditer(pattern, text, re.I):
            if name=='RECTAL_CARCINOMA' and re.search(r'\bN\.\s*recti',m[0],re.I) and not re.search(r'\b(?:yp|p|c)?T[1-4]\b|karzinom|malign\w*|adenokarzinom',text[max(0,m.start()-100):m.end()+140],re.I):
                continue
            if name == 'ARTERIAL_HYPERTENSION' and re.search(r'pulmonal|portal|intrakraniell', text[max(0, m.start()-30):m.end()], re.I):
                continue
            # Generic diabetes is not automatically type 2 unless explicitly stated.
            if name == 'DIABETES_MELLITUS_TYPE_2' and not re.search(r'typ\s*(?:2|II)|\bDM\s*2', m[0], re.I):
                continue
            hits.append((m.start(), m.end(), name, False))
    for name, pattern in EVENTS:
        for m in re.finditer(pattern, text, re.I):
            if name=='STROKE_UNSPECIFIED' and any(a<m.end() and b>m.start() and n=='ISCHEMIC_STROKE' for a,b,n,_ in hits):
                continue
            if name == 'ISCHEMIC_STROKE' and re.search(r'hämorrhag\w*\s+(?:hinter\w*\s+)?$', text[max(0, m.start()-55):m.start()], re.I):
                continue
            hits.append((m.start(), m.end(), name, True))
    return sorted(hits)


def local_clause(text, start, end, hits):
    previous = max([b for a, b, _, _ in hits if b <= start] or [0])
    following = min([a for a, b, _, _ in hits if a >= end] or [len(text)])
    prefix = text[previous:start]
    # Sentence/semicolon boundaries restrict assertion scope, without splitting dates.
    bounds = list(re.finditer(r';|[.!?]\s+(?=[A-ZÄÖÜ])|\b(?:wobei|jedoch|aber)\b|\bDD\s*:', prefix, re.I))
    left = previous + (bounds[-1].start() if re.match(r'DD', bounds[-1][0], re.I) else bounds[-1].end()) if bounds else previous
    right_bound = re.search(r'[.!?]\s+(?=[A-ZÄÖÜ])|;', text[end:following])
    right = end + right_bound.start() + 1 if right_bound else following
    next_assertion = re.search(r'\b(?:wobei|jedoch|aber|Differentialdiagnose)\b|\bDD\s*:', text[end:right], re.I)
    if next_assertion and following < len(text):
        right = end + next_assertion.start()
    return text[left:right], left


def classify_concept(source_text, start, end, *, authority=False, event=False, model_assertion=None):
    """Semantic eligibility followed by a mandatory source-bound assertion guard."""
    hits=concepts(source_text)
    context_start=start
    current=next((h for h in hits if h[0]==start and h[1]==end),None)
    # Adjacent expanded term and parenthetical synonym are one assertion owner.
    prior=[h for h in hits if current and h[1]<=start and h[2]==current[2]
        and re.fullmatch(r'\s*\(?\s*',source_text[h[1]:start])]
    if prior:context_start=prior[-1][0]
    text, left = local_clause(source_text, context_start, end, hits)
    classification, promote = classify(text, authority, concept=not event, event=event)
    guard = validate_assertion(text, start-left, end-left, model_assertion=model_assertion)
    outer_guard = validate_assertion(source_text, start, end, model_assertion=model_assertion)
    if outer_guard['rule'] in ('CONDITIONAL_OR_SEARCH_CONTEXT', 'SUGGESTIVE_FINDING_COMPARISON','HYPOTHETICAL_FUTURE_DECISION_TRIGGER'):
        guard = outer_guard
    state = guard['assertion_status']
    if state == 'NEGATED':
        classification, promote = 'NEGATED', False
    elif state in ('UNCERTAIN', 'SOURCE_ONLY','NOT_ASSESSABLE','DIFFERENTIAL','CONDITIONAL'):
        classification, promote = 'DIFFERENTIAL_DIAGNOSIS', False
    elif classification in ('NEGATED', 'DIFFERENTIAL_DIAGNOSIS'):
        # A paragraph-wide label cannot negate a different, explicitly positive concept.
        classification, promote = ('ACUTE_EVENT' if event else 'ESTABLISHED_DISEASE'), True
    return text, left, classification, promote, guard


def imaging_date(unit, section, concept_position=0):
    # Date must belong to the nearest investigation header, never a random old date.
    prefix = section['text'][:unit['start']] + unit['raw_text'][:concept_position]
    headers = list(re.finditer(r'(?im)^.*(?:magnetresonanz|\bMRT\b|\bMRI\b|sequenz|cMRT.Kontrolle).*(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}/\d{4}).*$', prefix))
    if headers:
        value, precision = explicit_period(headers[-1][0])
        return value, precision
    # A report heading can wrap onto the previous line (e.g. "Sequenz (08.03.2021)").
    recent = prefix[-300:]
    if re.search(r'magnetresonanz|sequenz|\bMRT\b|\bMRI\b|cMRT', recent, re.I):
        ds = list(DAYS.finditer(recent))
        if ds:
            return explicit_period(ds[-1][0])
    return None, 'UNKNOWN'


def event_details(unit, text, section, name, concept_offset=0, concept_position=0):
    feature_text = text[concept_offset:]
    event_period_text = re.split(r'zum\s+letzten|vorbefund|vergleich\w*\s+(?:vom|zum)|letzten\s+(?:MRI|MRT)', feature_text, maxsplit=1, flags=re.I)[0]
    value, precision = explicit_period(event_period_text)
    basis = 'EXPLICIT_EVENT_PERIOD' if value else 'UNKNOWN'
    recent = bool(re.search(r'rezent|akut', text, re.I))
    historical = bool(re.search(r'200\d|zustand nach|\bz\s*\.\s*n\s*\.|früher|alt\w*|vorbestehend', text, re.I))
    if not value and re.search(r'^(?:\s|\d+[.)]\s)*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+\d{4}',text,re.I):
        value,precision=explicit_period(text[:concept_offset]);basis='EXPLICIT_EVENT_DATE' if value else basis
    relative=re.search(r'\bvor\s+(\d{1,3})\s+Jahren\b',event_period_text[:100],re.I)
    if not value and relative and unit.get('document_date') and 0<int(relative[1])<=120 and not re.search(r'operation|resektion|\bOP\b',event_period_text[:relative.start()],re.I):
        year=int(unit['document_date'][:4])-int(relative[1])
        value,precision,basis=str(year),'APPROXIMATE_YEAR','RELATIVE_HISTORICAL_INFERENCE'
        historical=True
    if value and unit.get('document_date') and value[:4]<unit['document_date'][:4]:historical=True
    if not value and recent:
        value, precision = imaging_date(unit, section, concept_position)
        if value:
            basis = 'IMAGING_REPORT_DATE_RECENT_LESION'
    if not value and unit.get('original_source', {}).get('temporal'):
        t = unit['original_source']['temporal']
        value = t.get('date_disease') or (str(t['date_disease_year']) if t.get('date_disease_year') else None)
        precision = t.get('date_disease_precision', 'UNKNOWN') if value else 'UNKNOWN'
        if value:
            basis = 'SOURCE_BOUND_TEMPORAL_ASSERTION'
    pre_side=re.search(r'\b(rechts|links)\s+[cz]erebral\w*\s+(?:ischämisch\w*\s+)?$',text[:concept_offset],re.I)
    side_matches = [pre_side] if pre_side else list(re.finditer(r'\b(rechts|links)\b', feature_text, re.I))
    side = {'rechts': 'RIGHT', 'links': 'LEFT'}[side_matches[0][1].lower()] if side_matches else None
    if re.search(r'\bbds\.?|beidseits|bilateral',feature_text,re.I):side='BILATERAL'
    territory = 'MCA' if re.search(r'media|\bACM\b', feature_text, re.I) else None
    location = 'MESIOTEMPORAL' if re.search(r'mesiotemporal', feature_text, re.I) else 'PARIETO_OCCIPITAL' if re.search(r'parieto.?o[ck][cz]ipital|parietookzipital', feature_text, re.I) else None
    if re.search(r'stammganglien|basalganglien',feature_text,re.I):location=(side+'_' if side else '')+'BASAL_GANGLIA'
    elif location is None and re.search(r'parietal',feature_text,re.I):location='PARIETAL'
    if basis == 'IMAGING_REPORT_DATE_RECENT_LESION' and not any((side, territory, location)):
        # An undelimited reference to infarcted tissue cannot establish another
        # stroke on the scan date without anatomical/episode ownership.
        value, precision, basis = None, 'UNKNOWN', 'UNRESOLVED_ANATOMICAL_OWNERSHIP'
    shared_prefix = unit['raw_text'][:concept_position]
    embolism_context = re.search(r'embol\w*[^.;]*?(?:bei|nach)\s*(?:tiefer?\s*)?$', shared_prefix, re.I)
    if name == 'DVT' and not value and embolism_context:
        value, precision = explicit_period(embolism_context[0])
        if value:
            basis = 'ASSOCIATED_THROMBOEMBOLIC_EVENT_PERIOD'
    post_lysis = bool(re.search(r'nach\s+(?:.*?\s)?lyse|nach\s+lysetherapie|post.{0,10}thrombol', text, re.I))
    if name == 'PRIMARY_ICH' and post_lysis:
        name = 'TREATMENT_ASSOCIATED_ICH'
    elif name == 'PRIMARY_ICH' and re.search(r'hämorrhag\w*.*infarkt', text, re.I):
        name = 'HEMORRHAGIC_TRANSFORMATION'
    elif name=='PRIMARY_ICH' and re.search(r'intracraniell\w*\s+hämorrhagie|intracerebral\w*\s+blutung|stammganglienblutung',feature_text,re.I):
        name='INTRACEREBRAL_HEMORRHAGE'
    elif name == 'PRIMARY_ICH' and not re.search(r'spontan|primär', text, re.I):
        name = 'INTRACRANIAL_HEMORRHAGE_UNCLASSIFIED'
    cardio = bool(re.search(r'[ck]ardio.?embol', text, re.I))
    # Treatment is read from this numbered diagnosis block, not the whole section.
    after = section['text'][unit['start']:]
    boundary = re.search(r'\n\s*\d{1,2}\.\s', after[1:])
    block = after[:boundary.start()+1] if boundary else after[:700]
    hemorrhage=name in ('PRIMARY_ICH','INTRACEREBRAL_HEMORRHAGE','INTRACRANIAL_HEMORRHAGE_UNCLASSIFIED','TREATMENT_ASSOCIATED_ICH','HEMORRHAGIC_TRANSFORMATION')
    if not hemorrhage and (name != 'ISCHEMIC_STROKE' or not re.search(r'ischäm|infarkt', block[:200], re.I)):
        block = text
    lysis = bool(re.search(r'systemische lyse|(?:intravenöse|i\.?v\.?)\s*(?:thrombo)?lyse', block, re.I))
    caa_etiology=bool(re.search(r'(?:ätiolog\w*|a\.?\s*e\.?|am\s+ehesten)[^\n]*amyloidangiopathie',block,re.I))
    return dict(event_type=name, event_date=value, event_date_precision=precision, event_date_basis=basis,
        date_source=('EXPLICIT_EVENT_DATE' if authoritative(unit,section) or unit.get('structured_item_ids') else 'EXPLICIT_NARRATIVE_DATE') if basis=='EXPLICIT_EVENT_PERIOD' else basis,
        relative_date_text=relative[0] if basis=='RELATIVE_HISTORICAL_INFERENCE' else None,
        event_date_is_approximate=basis in ('IMAGING_REPORT_DATE_RECENT_LESION','RELATIVE_HISTORICAL_INFERENCE'),
        side=side, territory=territory, location=location,
        etiology='CEREBRAL_AMYLOID_ANGIOPATHY' if caa_etiology else 'CARDIOEMBOLIC' if cardio else 'UNKNOWN', etiology_certainty='PROBABLE' if caa_etiology else 'SOURCE_REPORTED' if cardio else 'UNKNOWN',
        differential_etiologies=['HEMORRHAGIC_INFARCTION'] if re.search(r'\bDD\b[^\n]*hämorrhag\w*\s+infarkt',block,re.I) else [],
        multiplicity='MULTIPLE' if re.search(r'Infarkte\b|multiinfarkt',text,re.I) else 'UNSPECIFIED',
        etiology_context='ATRIAL_FIBRILLATION' if cardio and re.search(r'vorhofflimm', unit['raw_text'], re.I) else None,
        acute_treatment={'IV_THROMBOLYSIS': True if lysis else None},
        complications=['POST_THROMBOLYSIS_HEMORRHAGE'] if post_lysis else [],
        source_type='IMAGING_CONFIRMED' if recent and re.search(r'läsion|infarzierung', text, re.I) else 'CLINICAL_DOCUMENTATION',
        historical=historical, temporal_resolution='DATED' if value else 'UNRESOLVED')


def compatible(a, b):
    if a['event_type'] != b['event_type']:
        return False
    for field in ('side', 'territory', 'location'):
        if a.get(field) and b.get(field) and a[field] != b[field]:
            return False
    x, y = a.get('event_date'), b.get('event_date')
    return not (x and y) or x.startswith(y) or y.startswith(x)


def group_events(patient_id, mentions):
    dated, undated = [], []
    for m in mentions:
        if m['candidate_type'] == 'ACUTE_CLINICAL_EVENT':
            (dated if m['event']['event_date'] else undated).append(m)
    groups = []
    for m in sorted(dated, key=lambda m: (-len(m['event']['event_date']), m['mention_id'])):
        matches = [g for g in groups if all(compatible(m['event'], n['event']) for n in g)]
        if len(matches) == 1:
            matches[0].append(m)
        else:
            groups.append([m])
    unresolved = []
    for m in undated:
        matches = [g for g in groups if all(compatible(m['event'], n['event']) for n in g)]
        # Temporal adjectives distinguish a historical residual from a current event
        # in the same territory. Unknown references cannot bridge separate dated events.
        if len(matches) > 1:
            docdate = m.get('document_date')
            if m['event']['historical']:
                matches = [g for g in matches if g[0]['event']['event_date'] and len(g[0]['event']['event_date']) == 4]
            elif docdate:
                matches = [g for g in matches if g[0]['event']['event_date'] and g[0]['event']['event_date'][:4] == docdate[:4]]
        if len(matches) == 1 and any(m['event'].get(f) for f in ('side', 'territory', 'location')):
            matches[0].append(m)
        elif m['event']['event_type'] == 'TREATMENT_ASSOCIATED_ICH':
            # A unique same-side/site post-treatment complication can be undated.
            same = [g for g in groups if all(compatible(m['event'], n['event']) for n in g)
                    and g[0]['event']['event_type'] == 'TREATMENT_ASSOCIATED_ICH']
            if len(same) == 1:
                same[0].append(m)
            elif not same:
                groups.append([m])
            else:
                unresolved.append(m)
        else:
            unresolved.append(m)
    events = []
    for group in groups:
        first = dict(group[0]['event'])
        for field in ('side', 'territory', 'location', 'etiology_context'):
            first[field] = next((m['event'][field] for m in group if m['event'].get(field)), None)
        if any(m['event']['etiology'] == 'CARDIOEMBOLIC' for m in group):
            first.update(etiology='CARDIOEMBOLIC', etiology_certainty='SOURCE_REPORTED')
        if any(m['event']['etiology']=='CEREBRAL_AMYLOID_ANGIOPATHY' for m in group):
            first.update(etiology='CEREBRAL_AMYLOID_ANGIOPATHY',etiology_certainty='PROBABLE')
        first['etiology_assertions']=[dict(evidence_id=m['evidence_id'],etiology=m['event']['etiology'],certainty=m['event']['etiology_certainty']) for m in group if m['event']['etiology']!='UNKNOWN']
        first['differential_etiologies']=sorted({d for m in group for d in m['event'].get('differential_etiologies',[])})
        first['acute_treatment'] = {'IV_THROMBOLYSIS': True if any(m['event']['acute_treatment']['IV_THROMBOLYSIS'] for m in group) else None}
        first['complications'] = sorted({c for m in group for c in m['event']['complications']})
        if first['event_type'] == 'ISCHEMIC_STROKE' and first['acute_treatment']['IV_THROMBOLYSIS']:
            # Cross-event relation is disclosed as a candidate, not an inferred primary ICH.
            hemorrhages = [g for g in groups if g[0]['event']['event_type'] == 'TREATMENT_ASSOCIATED_ICH']
            if len(hemorrhages) == 1:
                first['complications'].append('POST_THROMBOLYSIS_HEMORRHAGE')
        event_id = f'{patient_id}:EV4:{digest([first["event_type"], first["event_date"], first["side"], first["territory"], first["location"]])[:16]}'
        if any(e['event_id']==event_id for e in events):
            # Distinct incompatible groups can share a partial-date display tuple.
            # Preserve both with deterministic source-qualified identity and review flag.
            event_id+=':'+digest(sorted(m['mention_id'] for m in group))[:10]
            first['grouping_ambiguity']='DISTINCT_SOURCE_GROUPS_SHARE_DISPLAY_TUPLE'
        for m in group:
            m['event_id'] = event_id
        events.append(dict(event_id=event_id, patient_id=patient_id, **first,
            source_evidence_ids=sorted({m['evidence_id'] for m in group}),
            source_document_ids=sorted({m['document_id'] for m in group}),
            mention_ids=[m['mention_id'] for m in group], mention_count=len(group),
            date_evidence=[dict(evidence_id=m['evidence_id'],event_date=m['event']['event_date'],
                precision=m['event']['event_date_precision'],basis=m['event']['event_date_basis'],
                approximate=m['event']['event_date_is_approximate']) for m in group],
            confidence='SOURCE_SUPPORTED_REVIEW_REQUIRED', review_required=True,
            grouping_rule='COMPATIBLE_TYPE_DATE_ANATOMY_UNAMBIGUOUS_REFERENCE'))
    return events, unresolved


def assemble(patient_id, bundle, history):
    units, sections = source_units(bundle, history)
    from .structured_diagnoses import inventory, attach, reconcile
    structured_items=inventory(bundle)
    attach(units,structured_items,sections)
    mentions, rejected, assertions, unresolved, af_observations = [], [], [], [], []
    pending_events=[]
    for unit in units:
        section = sections[unit['section_id']][1]
        authority = bool(unit.get('structured_item_ids')) or authoritative(unit, section)
        hits = concepts(unit['raw_text'])
        if not hits:
            classification, _ = classify(unit['raw_text'], authority)
            rejected.append(dict(**unit, semantic_class=classification, reason='NO_APPROVED_DISEASE_OR_EVENT_CONCEPT'))
            # Unknown structured diagnoses stay visible to the reviewer, never silently discarded.
            if authority and classification == 'OTHER' and re.search(r'infekt|pyelonephritis|herni[ae]|karzinom|syndrom|insuffizienz|erkrankung|\bNINS\b', unit['raw_text'], re.I):
                unresolved.append(dict(**unit, reason='STRUCTURED_ITEM_WITHOUT_APPROVED_ONTOLOGY_MAPPING'))
            continue
        any_promoted = False
        dispositions = []
        for start, end, name, event in hits:
            text, left, classification, promote, guard = classify_concept(unit['raw_text'], start, end, authority=authority, event=event)
            # A disease mentioned only as etiology still has its own assertion, but not its event's onset.
            state = guard['assertion_status'] if guard['assertion_status'] in ('NOT_ASSESSABLE','DIFFERENTIAL','CONDITIONAL') else 'NEGATED' if classification == 'NEGATED' else 'SUSPECTED' if classification == 'DIFFERENTIAL_DIAGNOSIS' else 'ESTABLISHED' if promote else 'FINDING_ONLY'
            period, precision = explicit_period(text)
            # For chronic disease chronology, documentation date records the assertion.
            # Historical periods remain separate and never backdate an established diagnosis.
            assertion = dict(patient_id=patient_id, canonical_name=name, assertion_status=state,
                assertion_date=unit['document_date'], assertion_date_precision='DAY' if unit['document_date'] else 'UNKNOWN',
                referenced_historical_period=period, referenced_historical_period_precision=precision,
                evidence_id=unit['span_id'], document_id=unit['document_id'], section_id=unit['section_id'],
                page=unit['page'], scope=guard['scope'], final_assertion=guard['assertion_status'],
                semantic_relation=guard.get('semantic_relation'),temporal_status=guard.get('temporal_status'),CAA_SUPPORT=guard.get('CAA_SUPPORT'),
                explicit_positive=guard['explicit_positive'],
                assertion_rule_version=guard['rule_version'], candidate_type='ACUTE_CLINICAL_EVENT' if event else 'DIAGNOSIS_DISEASE',
                raw_text=text, source_priority='STRUCTURED_DIAGNOSIS' if authority else 'QUALIFIED_NARRATIVE',
                semantic_class=classification, review_required=True)
            if name == 'ATRIAL_FIBRILLATION':
                observation = af_observation(unit['raw_text'], start, end, observation_date=unit['document_date'])
                if observation['assertion_status'] != guard['assertion_status']:
                    observation.update(guard, AF_PRESENT=False if guard['assertion_status']=='NEGATED' else True if guard['assertion_status']=='ESTABLISHED' else None,
                        AF_ESTABLISHED_FROM_THIS_EVIDENCE=guard['explicit_positive'])
                observation.update(patient_id=patient_id, evidence_id=unit['span_id'], document_id=unit['document_id'],
                    section_id=unit['section_id'], page=unit['page'], raw_text=text, review_required=True)
                af_observations.append(observation)
            assertion['assertion_effective_date'] = unit['document_date']
            if state == 'SUSPECTED' and re.search(r'erstdiagnose|seit\s+\d{4}', text, re.I) and period:
                assertion['assertion_effective_date'] = period
                assertion['effective_date_basis'] = 'EXPLICIT_HISTORICAL_SUSPECTED_DIAGNOSIS_PERIOD'
            else:
                assertion['effective_date_basis'] = 'SOURCE_DOCUMENTATION_DATE'
            assertions.append(assertion)
            dispositions.append(dict(canonical_name=name, semantic_class=classification, promoted=promote))
            if not promote:
                if event and guard['assertion_status'] in ('UNCERTAIN','DIFFERENTIAL'):
                    details=event_details(unit,text,section,name,start-left,start)
                    pending_events.append(dict(candidate_id=f'{patient_id}:EVC:{digest([unit["span_id"],start,name])[:16]}',
                        patient_id=patient_id,document_id=unit['document_id'],document_date=unit['document_date'],page=unit['page'],section_id=unit['section_id'],
                        evidence_id=unit['span_id'],raw_text=text,assertion_status='UNCERTAIN',status='UNCERTAIN',
                        candidate_type='CLINICAL_EVENT_CANDIDATE',review_required=True,**details))
                continue
            any_promoted = True
            m = dict(mention_id=f'{patient_id}:DXM4:{digest([unit["span_id"], start, name])[:16]}',
                patient_id=patient_id, document_id=unit['document_id'], document_date=unit['document_date'],
                page=unit['page'], section_id=unit['section_id'], evidence_id=unit['span_id'],
                raw_text=text, full_source_text=unit['raw_text'], concept_text=unit['raw_text'][start:end],
                canonical_candidate=name, candidate_type='ACUTE_CLINICAL_EVENT' if event else 'DIAGNOSIS_DISEASE',
                semantic_class=classification, assertion_status=state, source_priority=assertion['source_priority'],
                assertion_rule_version=guard['rule_version'],
                explicit_positive=guard['explicit_positive'],
                qualifiers=qualifiers(text), attributes=disease_attributes(name,
                    re.split(r'\n\s*(?:[-•]|\d+[.)])',unit['raw_text'])[0] if name=='HIGH_GRADE_GLIOMA' else text),
                structured_item_ids=unit.get('structured_item_ids',[]),review_required=True)
            if event:
                m['event'] = event_details(unit, text, section, name, start-left, start)
                m['canonical_candidate'] = m['event']['event_type']
            mentions.append(m)
        unit['concept_dispositions'] = dispositions
        if not any_promoted:
            rejected.append(dict(**unit, semantic_class=dispositions[0]['semantic_class'], reason='EVIDENCE_OR_ASSERTION_ONLY'))
    # Corroborate generic strokes only with a unique compatible, same-document,
    # same-year explicit ischemic source. Never infer ischemia from Insult alone.
    for m in mentions:
        if m.get('event',{}).get('event_type')!='STROKE_UNSPECIFIED':continue
        e=m['event']
        linked=[n for n in mentions if n.get('event',{}).get('event_type')=='ISCHEMIC_STROKE'
            and not n['event'].get('subtype_basis')
            and n['document_id']==m['document_id'] and e.get('event_date') and n['event'].get('event_date')
            and e['event_date'][:4]==n['event']['event_date'][:4]
            and all(not e.get(f) or not n['event'].get(f) or e[f]==n['event'][f] for f in ('side','territory','location'))]
        episodes={(n['event']['event_date'],n['event'].get('side'),n['event'].get('territory'),n['event'].get('location')) for n in linked}
        if len(episodes)==1:
            m['canonical_candidate']=e['event_type']='ISCHEMIC_STROKE'
            e['corroboration_evidence_ids']=sorted({n['evidence_id'] for n in linked})
            e['subtype_basis']='SAME_DOCUMENT_COMPATIBLE_YEAR_EXPLICIT_ISCHEMIC_SOURCE'
    events, ambiguous_events = group_events(patient_id, mentions)
    reconciliation,unmapped=reconcile(patient_id,structured_items,units,mentions,assertions,classify)
    # Resolve numeric references only to a unique top-level item in the SAME list.
    for m in mentions:
        number=m.get('attributes',{}).get('source_diagnosis_reference_number')
        if not number:continue
        owners=[i for i in structured_items if i['item_id'] in m.get('structured_item_ids',[])]
        targets=[i for i in reconciliation if any(i['diagnosis_list_id']==o['diagnosis_list_id'] and i['document_id']==o['document_id'] for o in owners)
            and i['diagnosis_item_number']==number]
        if len(targets)==1:
            m['attributes']['causal_reference']=dict(relation='SOURCE_REFERENCED_DIAGNOSIS',target_item_id=targets[0]['item_id'],
                canonical_candidates=targets[0]['canonical_candidates'],review_required=True)
    unresolved.extend(dict(m, reason='AMBIGUOUS_OR_UNDATED_EVENT_REFERENCE') for m in ambiguous_events)
    for m in ambiguous_events:
        pending_events.append(dict(candidate_id=f'{patient_id}:EVC:{digest(m["mention_id"])[:16]}',
            patient_id=patient_id,document_id=m['document_id'],document_date=m.get('document_date'),page=m['page'],section_id=m['section_id'],
            evidence_id=m['evidence_id'],raw_text=m['raw_text'],assertion_status=m['assertion_status'],
            status='AMBIGUOUS_OR_UNDATED_EVENT_REFERENCE',candidate_type='CLINICAL_EVENT_CANDIDATE',review_required=True,**m['event']))
    grouped = defaultdict(list)
    for m in mentions:
        if m['candidate_type'] == 'DIAGNOSIS_DISEASE' and m['assertion_status'] == 'ESTABLISHED' and m.get('explicit_positive'):
            grouped[m['canonical_candidate']].append(m)
    diseases = []
    for name, members in sorted(grouped.items()):
        timeline = [a for a in assertions if a['canonical_name'] == name]
        suspicion = sorted(a['assertion_effective_date'] for a in timeline if a['assertion_status'] == 'SUSPECTED' and a['assertion_effective_date'])
        established = sorted(a['assertion_date'] for a in timeline if a['assertion_status'] == 'ESTABLISHED' and a['assertion_date'])
        domain, category = TAXONOMY.get(name, ('UNKNOWN', 'UNKNOWN'))
        identity = f'{patient_id}:DX:{digest(name)[:14]}'
        for m in members:
            m['diagnosis_identity_id'] = identity
        diseases.append(dict(diagnosis_identity_id=identity, patient_id=patient_id, canonical_name=name,
            domain=domain, category=category, subcategory=name,
            first_suspicion_date=suspicion[0] if suspicion else None,
            first_explicit_diagnosis_date=established[0] if established else None,
            first_documented_date=established[0] if established else None,
            first_documented_date_precision='DAY' if established else 'UNKNOWN',
            last_documented_date=established[-1] if established else None,
            status='SOURCE_ESTABLISHED', onset_date=None,
            source_evidence_ids=sorted({m['evidence_id'] for m in members}),
            source_document_ids=sorted({m['document_id'] for m in members}),
            mention_count=len(members), document_count=len({m['document_id'] for m in members}),
            modifiers={'STEROID_INDUCED': any(m['qualifiers']['STEROID_INDUCED'] for m in members)} if name == 'DIABETES_MELLITUS_TYPE_2' else {},
            source_attributes=[dict(evidence_id=m['evidence_id'],document_id=m['document_id'],assertion_date=m['document_date'],**m.get('attributes',{})) for m in members],
            subtype_assertions=[dict(subtype=m['attributes']['subtype'],assertion_date=m['document_date'],evidence_id=m['evidence_id']) for m in members if m.get('attributes',{}).get('subtype')],
            review_required=True))
    clinical = []
    for d in diseases:
        clinical.append(dict(record_type='CHRONIC_DISEASE', canonical_name=d['canonical_name'],
            event_date_or_first_diagnosis_date=d['first_explicit_diagnosis_date'],
            domain=d['domain'], category=d['category'], subcategory=d['subcategory'], status=d['status'],
            mention_count=d['mention_count'], document_count=d['document_count'], review_required=True,
            record_id=d['diagnosis_identity_id']))
    for e in events:
        clinical.append(dict(record_type='CLINICAL_EVENT', canonical_name=e['event_type'],
            event_date_or_first_diagnosis_date=e['event_date'],
            domain='PULMONARY' if e['event_type'] == 'PULMONARY_EMBOLISM' else 'CARDIOVASCULAR' if e['event_type'] == 'DVT' else 'NEUROLOGIC',
            category='THROMBOEMBOLIC_EVENT' if e['event_type'] in {'PULMONARY_EMBOLISM', 'DVT'} else 'CLINICAL_NEUROLOGICAL_EVENT',
            subcategory=e['event_type'], status='SOURCE_DOCUMENTED_EVENT', mention_count=e['mention_count'],
            document_count=len(e['source_document_ids']), review_required=True, record_id=e['event_id'],
            side=e['side'], territory=e['territory'], location=e['location'], event_date_precision=e['event_date_precision']))
    counts = dict(raw_medical_spans_detected=len(units), diagnosis_event_candidates_after_semantic_filtering=len(mentions),
        promoted_source_spans=len({m['evidence_id'] for m in mentions}), canonical_disease_identities=len(diseases),
        canonical_event_identities=len(events), evidence_only_spans_rejected=len(rejected),
        unresolved_candidates=len(unresolved), legacy_raw_spans_retained=len(history['mentions'])+len(history.get('audit_only', [])))
    candidates={}
    for row in pending_events:
        signature=(row['event_type'],row['event_date'],row.get('side'),row.get('territory'),row.get('location'),row['assertion_status']) if row.get('event_date') else row['candidate_id']
        if signature not in candidates:
            candidates[signature]=dict(row,evidence=[],source_evidence_ids=[],source_document_ids=[],mention_count=0)
        item=candidates[signature];item['evidence'].append(dict(row));item['mention_count']+=1
        item['source_evidence_ids']=sorted(set(item['source_evidence_ids']+[row['evidence_id']]))
        item['source_document_ids']=sorted(set(item['source_document_ids']+[row['document_id']]))
    return dict(raw_source_spans=units, diagnosis_mentions=mentions, canonical_disease_identities=diseases,
        canonical_clinical_events=events, diagnostic_assertion_timeline=sorted(assertions, key=lambda a: (a['assertion_date'] or '', a['canonical_name'], a['evidence_id'])),
        patient_clinical_diagnoses=clinical, diagnosis_rejected_spans=rejected, unresolved_candidates=unresolved,
        clinical_event_candidates=list(candidates.values()),
        structured_diagnosis_items=reconciliation,unmapped_diagnosis_candidates=unmapped,
        structured_diagnosis_reconciliation=dict(source_items=len(structured_items),mapped=sum(r['disposition']=='MAPPED_DISEASE_OR_EVENT' for r in reconciliation),
            non_disease=sum(r['disposition']=='NON_DISEASE_EVENT' for r in reconciliation),unmapped=len(unmapped),lost=len(structured_items)-len(reconciliation)),
        af_observations=af_observations,
        af_state=dict(resolve_af(af_observations), patient_id=patient_id,
                      negative_evidence_count=sum(o['assertion_status'] == 'NEGATED' for o in af_observations),
                      earliest_confirmed_date=resolve_af(af_observations)['af_index_date']), counts=counts)
