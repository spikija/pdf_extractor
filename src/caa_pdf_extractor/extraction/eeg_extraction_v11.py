"""Deterministic, clause-scoped EEG 1.1 semantics over preserved 1.0 source primitives."""
import copy
import re
from collections import defaultdict
from dataclasses import dataclass
from ..module_artifacts import digest
from . import eeg_extraction_v1 as v1
from .eeg_schema_v11 import defaults,validate_value,BOOL_FIELDS,ENUMS,TEXT_FIELDS,REGIONS
from .eeg_sources import locate

EP=v1.EP_PATTERN+r'|Zeichen\s+lokal\s+erhöht\w*\s+(?:[cz]erebraler?\s+)?Erregungsbereitschaft'
SLOW=r'Theta|Delta|Verlangsam|(?:Hirn)?funktion(?:s)?störung|Herd(?:befund|störung|verlangsamung|hinweis)'
FOCAL=r'(?:lokal\w*|fokal\w*|regional\w*)\s+(?:Hirn)?funktion(?:s)?störung|Herd(?:befund|störung|verlangsamung|hinweis)|fokal\w*\s+Verlangsam'
GLOBAL=r'diffus|generalisier|global\w*\s+(?:Theta|Delta|Verlangsam)|(?:verlangsam\w*\s+(?:Basisrhythmus|Grundaktivität)|Verlangsamung\s+des\s+Basisrhythmus)|diffuse\s+Allgemeinveränderung'
NDE=r'\b(?:kein\w*\s+(?:eindeutig\w*|sicher\w*)|ohne\s+(?:eindeutig\w*|sicher\w*))\s+(?:(?:Hinweis|Zeichen)\s+(?:auf\s+)?)?'
UNCERTAIN=r'fraglich|möglich\w*|Verdacht|\bV\.?\s*a\.?\b|nicht\s+(?:sicher\s+)?auszuschließen'
RELATION=re.compile(r'(?:(?:\bhäufig|\bmeist|\bgelegentlich|\bteils|\bseltener)\s+(?:auch\s+)?(?:mit\s+)?|\bmit\s+)?(?:\bAusbreitung\b|\bPropag\w*|\b(?:breitet|breiten)\s+sich\b)|(?:(?:\bseltener|\bhäufiger|\bselten|\bhäufig)\s+(?:(?:zeigt|zeigen)\s+sich\s+)?)?\bunabhängig\s+(?:davon|hiervon)\b',re.I)
SPLIT=re.compile(r'\b(?:sowie|zudem|plus)\b|,\s*(?=(?:intermittierend\w*|generalisier\w*|häufig\w*\s+(?:intermittier\w*|mit)|reichlich\w*\s+Gehalt))|\bund\s+(?=(?:(?:eine?|einer|Zeichen)\s+)?(?:(?:leicht\w*|mäßig\w*|ausgeprägt\w*)\s+)*(?:lokal|fokal|diffus|generalisier))',re.I)
REBUILT=set(('focal_slowing_present focal_slowing_side focal_slowing_region lateralized_abnormality lateralized_side lateralized_abnormality_type generalized_slowing_present diffuse_theta_frequency_hz epileptiform_discharge_present epileptiform_discharge_type epileptiform_distribution epileptiform_side epileptiform_region rhythmic_delta_activity_present rhythmic_delta_type pattern_side pattern_region pattern_frequency_hz background_continuity reactivity_to_external_stimuli background_reactivity assessability artifact_limited source_reported_abnormality_grade').split())


@dataclass
class Clause:
    text:str
    positions:list
    role:str='FINDING'
    interpretation:bool=False

    def part(self,a,b=None):
        b=len(self.text) if b is None else b
        while a<b and self.text[a].isspace():a+=1
        while b>a and self.text[b-1].isspace():b-=1
        return Clause(self.text[a:b],self.positions[a:b],self.role,self.interpretation)


def clauses(report):
    chars=[];positions=[];roles=[];phases=[];role='FINDING';interpretation=False;offset=0
    for line in report['eeg_report_text'].splitlines(keepends=True):
        value=line.strip()
        if re.match(r'^(?:Indikation|Fragestellung|Zuweisungsdiagnose|Anamnese)\s*:',value,re.I):role='INDICATION'
        elif re.match(r'^(?:Befund|Grundaktivität|Basisrhythmus)\s*:',value,re.I):role='FINDING'
        elif re.match(r'^(?:Interpretation|Beurteilung)\s*:',value,re.I):role='FINDING';interpretation=True
        elif not value:role='FINDING'
        if not v1.FURNITURE.search(value):
            for m in re.finditer(r'\S+',line):
                if chars:chars.append(' ');positions.append(offset+m.start());roles.append(role);phases.append(interpretation)
                chars.extend(m[0]);positions.extend(range(offset+m.start(),offset+m.end()));roles.extend([role]*len(m[0]));phases.extend([interpretation]*len(m[0]))
        offset+=len(line)
    clean=''.join(chars)
    for m in re.finditer(r'.+?(?:;|(?<!\bz\.)(?<!\bZ\.)(?<=[.!?])\s+(?=[A-ZÄÖÜ])|(?=\b(?:Indikation|Fragestellung|Befund|Interpretation|Beurteilung)\s*:)|$)',clean):
        if not m[0].strip():continue
        q=Clause(m[0],positions[m.start():m.end()],roles[m.start()],phases[m.start()]).part(0)
        if q.text:yield q


def parts(q):
    start=0
    for m in SPLIT.finditer(q.text):
        if m.start()>start:yield q.part(start,m.start())
        start=m.end()
    if start<len(q.text):yield q.part(start)


def regions(text):
    patterns={'FRONTAL':r'frontal|fronto','CENTRAL':r'zentral|zentro|central|centro','TEMPORAL':r'temporal|temporo','PARIETAL':r'parietal|parieto','OCCIPITAL':r'o[ck][cz]ipital|occipito','HEMISPHERIC':r'hemisphär|hemispher'}
    found=[region for region in REGIONS if re.search(patterns[region],text,re.I)]
    if len(found)>1 and 'HEMISPHERIC' in found and not re.search(r'(?:gesamte|ganze)\w*[^.;]{0,20}Hemisph',text,re.I):found.remove('HEMISPHERIC')
    return found


def side(text):
    text=re.sub(r'(?:sekundär\w*[, ]+)?bilateral\w*\s+Synchron\w*','',text,flags=re.I)
    left=bool(re.search(r'links|linke\w*|\bleft\b',text,re.I));right=bool(re.search(r'rechts|rechte\w*|\bright\b',text,re.I))
    return 'BILATERAL' if left and right or re.search(r'beidseits|bilateral|bifrontal|bitemporal|bifronto',text,re.I) else 'LEFT' if left else 'RIGHT' if right else 'UNKNOWN'


def asymmetry(text):
    if re.search(r'links\s*>\s*rechts|links[^.;]{0,25}(?:mehr\s+als\s+rechts|betont|akzent)|Links(?:akzent|beton)',text,re.I):return 'LEFT_PREDOMINANT'
    if re.search(r'rechts\s*>\s*links|rechts[^.;]{0,25}(?:mehr\s+als\s+links|betont|akzent)|Rechts(?:akzent|beton)',text,re.I):return 'RIGHT_PREDOMINANT'
    return 'SYMMETRIC' if re.search(r'(?<!a)symmetrisch',text,re.I) else 'UNKNOWN'


def extent(text):
    anterior=bool(re.search(r'vorne|anterior',text,re.I));mid=bool(re.search(r'\bMitte\b|\bmid\b',text,re.I))
    return 'MID_TO_ANTERIOR' if anterior and mid else 'ANTERIOR' if anterior else 'MID' if mid else 'POSTERIOR' if re.search(r'hinten|posterior',text,re.I) else 'UNKNOWN'


def continuity(text):
    return 'NEAR_CONTINUOUS' if re.search(r'nahezu\s+kontinuier',text,re.I) else 'CONTINUOUS' if re.search(r'(?<!dis)kontinuier',text,re.I) else 'INTERMITTENT' if re.search(r'intermittier',text,re.I) else 'UNKNOWN'


def occurrence(text):
    return 'OCCASIONAL' if re.search(r'vereinzelt|gelegentlich|seltener|\bselten\b',text,re.I) else 'ABUNDANT' if re.search(r'reichlich|zahlreich',text,re.I) else 'FREQUENT' if re.search(r'häufig',text,re.I) else 'UNKNOWN'


def severity(text):
    return 'MARKED' if re.search(r'ausgeprägt|stark|massiv|hochgrad',text,re.I) else 'MODERATE' if re.search(r'mäßig|mittelgrad|mittelschwer',text,re.I) else 'MILD' if re.search(r'leicht|gering|diskret',text,re.I) else 'UNKNOWN'


def primary_region(text,rs):
    maximum=re.search(r'(?:Maximum|Schwerpunkt|Scherpunkt)[^.;]{0,35}',text,re.I)
    mr=regions(maximum[0]) if maximum else []
    return mr[0] if len(mr)==1 else rs[0] if len(rs)==1 else 'UNKNOWN'


def compact_region(rs):
    found=set(rs)
    pairs={frozenset(('FRONTAL','TEMPORAL')):'FRONTOTEMPORAL',frozenset(('FRONTAL','CENTRAL')):'FRONTOCENTRAL',frozenset(('TEMPORAL','PARIETAL')):'TEMPOROPARIETAL'}
    return next(iter(found)) if len(found)==1 else pairs.get(frozenset(found),'OTHER') if found else 'UNKNOWN'


def evidence_state(text,pattern):
    m=re.search(pattern,text,re.I)
    if not m:return None,'UNKNOWN'
    before=text[:m.start()]
    before=re.split(r'\b(?:aber|jedoch|hingegen|sowie)\b|,',before,flags=re.I)[-1]
    after=text[m.end():]
    if re.search(NDE,before[-100:],re.I):return None,'NO_DEFINITE_EVIDENCE'
    if re.search(UNCERTAIN,before[-100:]+m[0]+after[:70],re.I):return None,'UNCERTAIN'
    # Broad is morphology, not an uncertainty operator in 1.1.
    sanitized=re.sub(r'\bbroad[- ]*','',text,flags=re.I)
    target=re.search(pattern,sanitized,re.I)
    value,state=v1.polarity(sanitized,target) if target else (None,'UNCERTAIN')
    return value,'EXPLICITLY_ABSENT' if value is False else 'CONFIRMED' if value is True else 'UNCERTAIN'


class Extraction:
    def __init__(self,report):
        self.report=report;self.values=defaults();self.findings=[];self.facts=defaultdict(list)
        old,findings=v1.extract(report)
        for field,value in old.items():
            if field not in REBUILT:self.values[field]=value
        for finding in findings:
            if finding['field'] not in REBUILT:self.findings.append(dict(finding,eeg_schema_version='1.1'))
        self.focus=[];self.patterns=[];self.reactivities=[];self.anchor=None
        self.form_fields={f['field']:f for f in findings if f['source_type']=='STRUCTURED_FORM'}
        for field,f in self.form_fields.items():
            if field in REBUILT:
                validate_value(field,f['value']);self.values[field]=copy.deepcopy(f['value']);self.findings.append(dict(f,eeg_schema_version='1.1'))
        if 'epileptiform_discharge_present' in self.form_fields:
            value=self.form_fields['epileptiform_discharge_present']['value']
            self.values.update(epileptiform_confirmed=value,epileptiform_evidence_status='CONFIRMED' if value is True else 'EXPLICITLY_ABSENT' if value is False else 'UNKNOWN')

    def add(self,field,value,q,assertion=None,source_type='FREE_TEXT_SOURCE',eid=None):
        validate_value(field,value)
        absolute=self.report['report_start']+(q.positions[0] if q.positions else 0)
        section=locate(self.report['ranges'],min(absolute,self.report['report_end']-1))
        eid=eid or f"{self.report['report_occurrence_id']}:v11:{field}:{absolute}"
        item=dict(patient_id=self.report['patient_id'],eeg_exam_id=None,document_id=self.report['document_id'],page=section['page_start'],
            section_id=section['section_id'],evidence_id=eid,source_text_short=q.text[:450],source_clause_text=q.text,
            source_type=source_type,source_start=absolute,source_end=self.report['report_start']+(q.positions[-1]+1 if q.positions else 0),
            field=field,value=copy.deepcopy(value),assertion=assertion or ('NEGATED' if value is False or value in ('ABSENT','NOT_PERFORMED','EXPLICITLY_ABSENT') else 'UNCERTAIN' if value is None else 'ESTABLISHED'),
            method='DETERMINISTIC_EEG_1.1',review_required=True,eeg_schema_version='1.1',temporal_scope='INTERICTAL' if re.search(r'\binteriktal',q.text,re.I) else 'ICTAL' if re.search(r'\biktal',q.text,re.I) else 'REPORT')
        item['finding_id']='EEGF11:'+digest([eid,value])[:20]
        if item['finding_id'] not in {f['finding_id'] for f in self.findings}:self.findings.append(item);self.facts[field].append(value)
        return eid

    def focus_row(self,q,relationship,kind,ep):
        t=q.text;rs=regions(t);s=side(t)
        if relationship=='UNKNOWN':
            if not self.focus:relationship='PRIMARY'
            elif self.anchor and s==self.anchor['side'] and (not rs or set(rs)&set(self.anchor['regions'])) and not q.interpretation:relationship=self.anchor['relationship']
            elif q.interpretation:
                existing=next((f for f in self.focus if f['side']==s and set(rs)==set(f['regions']) and f['relationship']!='SPREAD'),None)
                if existing:relationship=existing['relationship']
        eid=f"{self.report['report_occurrence_id']}:v11:focal:{q.positions[0]}"
        row=dict(focal_abnormality_id='EEGFA:'+digest(eid)[:18],finding_type=kind,side=s,regions=rs,primary_region=primary_region(t,rs),
            anterior_posterior_extent=extent(t),extent_description=t,continuity=continuity(t),frequency=occurrence(t),frequency_hz=v1.frequency(t),severity=severity(t),
            epileptiform_component=ep,relationship=relationship,asymmetry=asymmetry(t),secondary_bilateral_synchrony=True if re.search(r'sekundär\w*[^.;]{0,20}bilateral\w*\s+Synchron',t,re.I) else None,
            spread_to=[],evidence_ids=[eid],source_text_short=t[:450],assertion='ESTABLISHED')
        self.focus.append(row)
        self.add('focal_abnormalities',[row],q,eid=eid)
        if relationship=='SPREAD' and self.anchor:
            target=dict(side=s,regions=rs,evidence_ids=[eid]);self.anchor['spread_to'].append(target)
            if row['secondary_bilateral_synchrony'] is True:self.anchor['secondary_bilateral_synchrony']=True
        elif relationship in ('PRIMARY','SECONDARY_INDEPENDENT'):self.anchor=row
        return row

    def pattern(self,q,kind,distribution,typ='UNKNOWN'):
        t=q.text;rs=regions(t);eid=f"{self.report['report_occurrence_id']}:v11:pattern:{q.positions[0]}:{kind}"
        maximum='BIFRONTAL' if re.search(r'bifrontal',t,re.I) else 'FRONTAL' if re.search(r'frontal\w*\s+Maximum|Maximum[^.;]{0,15}frontal',t,re.I) or typ=='FIRDA' else 'TEMPORAL' if re.search(r'temporal\w*\s+Maximum|Maximum[^.;]{0,15}temporal',t,re.I) else 'UNKNOWN'
        row=dict(pattern_id='EEGP:'+digest(eid)[:18],kind=kind,distribution=distribution,side=side(t),regions=rs,primary_region=primary_region(t,rs),
            anterior_posterior_extent=extent(t),extent_description=t,continuity=continuity(t),frequency=occurrence(t),frequency_hz=v1.frequency(t),severity=severity(t),asymmetry=asymmetry(t),
            maximum=maximum,rhythmic_delta_type=typ,modern_pattern_family='GRDA' if typ in ('FIRDA','GRDA') else 'LRDA' if typ=='LRDA' else 'UNKNOWN',source_term=typ if typ!='UNKNOWN' else None,
            evidence_ids=[eid],source_text_short=t[:450],assertion='ESTABLISHED')
        self.patterns.append(row);self.add('slow_patterns',[row],q,eid=eid)
        return row

    def quality(self,q):
        t=q.text
        if re.search(r'(?i)^\s*(?:Im\s+)?(?:EKG|ECG)\b',t):return
        if re.search(r'Artefakt',t,re.I):
            negative=bool(re.search(r'artefaktfrei|keine\s+Artefakt',t,re.I))
            self.add('artifact_present',not negative,q)
            if not negative:
                types=[v for v,p in [('MUSCLE',r'Muskel'),('MOVEMENT',r'Bewegung'),('ELECTRODE',r'Elektrod')] if re.search(p,t,re.I)] or ['OTHER']
                self.add('artifact_type',types,q);self.add('artifact_severity',severity(t),q)
        not_assessable=bool(re.search(r'(?:EEG|Ableitung)[^.;]{0,100}nicht\s+(?:beurteilbar|auswertbar)|(?:aufgrund|wegen)[^.;]{0,70}Artefakt[^.;]{0,40}nicht\s+(?:beurteilbar|auswertbar)|unverwertbar\w*\s+EEG',t,re.I))
        limited=bool(re.search(r'soweit[^.;]{0,100}beurteilbar|eingeschränkt\w*[^.;]{0,30}beurteilbar|bei\s+(?:stark\w*|massiv\w*)[^.;]{0,60}Artefakt',t,re.I))
        if not_assessable:self.add('assessability','NOT_ASSESSABLE',q,assertion='NOT_ASSESSABLE');self.add('artifact_limited',True,q)
        elif limited:self.add('assessability','LIMITED',q);self.add('artifact_limited',True,q)
        elif re.search(r'vollständig\s+beurteilbar|uneingeschränkt\s+beurteilbar|gute\s+Ableitqualität',t,re.I):self.add('assessability','FULL',q)

    def reactivity(self,q):
        t=q.text
        if re.search(r'\b(?:EKG|ECG)\b',t,re.I):return
        if re.search(r'Reaktion|Reagibilität|Reagibilit|reaktiv|Reaktivität',t,re.I):
            if re.search(r'Ansprache|verbal|taktil|tactil|äußer\w*\s+Reiz|extern\w*\s+Reiz|exogen\w*\s+Reiz',t,re.I):
                stimulus='VERBAL' if re.search(r'Ansprache|verbal',t,re.I) else 'TACTILE' if re.search(r'taktil|tactil',t,re.I) else 'EXTERNAL'
                response='NOT_TESTED' if re.search(r'nicht\s+(?:getestet|geprüft)',t,re.I) else 'ABSENT' if re.search(r'fehlend|keine\s+Reaktion|nicht\s+reaktiv',t,re.I) else 'PRESENT' if re.search(r'erhalten\w*\s+Reaktion|reagib|reaktiv|Reaktion',t,re.I) else 'UNKNOWN'
                eid=f"{self.report['report_occurrence_id']}:v11:reactivity:{q.positions[0]}"
                row=dict(stimulus=stimulus,response=response,evidence_ids=[eid],source_text_short=t[:450]);self.reactivities.append(row);self.add('reactivity_findings',[row],q,eid=eid)
            if re.search(r'Hintergrundreaktivität|Grundaktivität[^.;]{0,35}reaktiv|Hintergrund[^.;]{0,35}reaktiv',t,re.I):
                self.add('background_reactivity','ABSENT' if re.search(r'fehlend|keine|nicht',t,re.I) else 'PRESENT',q)

    def grade(self,q):
        t=q.text
        for value,pattern in [('BROADLY_NORMAL',r'Grenzen\s+der\s+Norm|weitgehend\s+normal'),('SEVERELY_ABNORMAL',r'(?:schwer\w*|hochgradig)\s+(?:abnorm|pathologisch)'),('MODERATELY_ABNORMAL',r'(?:mäßig\w*|mittelschwer\w*)\s+(?:abnorm|pathologisch)'),('MILDLY_ABNORMAL',r'leicht\w*\s+(?:abnorm|pathologisch)'),('MINIMALLY_ABNORMAL',r'minimal\w*\s+(?:abnorm|pathologisch)'),('ABNORMAL',r'\babnorm\w*\s+EEG|pathologisch\w*\s+EEG'),('NORMAL',r'Normalbefund|normales?\s+EEG|unauffällig\w*\s+EEG')]:
            m=re.search(pattern,t,re.I)
            if m and (re.search(r'EEG',t[max(0,m.start()-25):m.end()+25],re.I) or value in ('BROADLY_NORMAL','NORMAL') or m.start()<5) and evidence_state(t,pattern)[0] is True:
                self.add('source_reported_abnormality_grade',value,q);break

    def domains(self,q):
        t=q.text
        if q.role=='INDICATION' or re.search(r'(?i)^\s*(?:Im\s+)?(?:EKG|ECG)\b',t):return
        self.quality(q);self.reactivity(q);self.grade(q)
        if re.search(r'keine\s+klinischen\s+Zeichen[^.;]{0,55}Status\s+epilepticus',t,re.I):self.add('clinical_signs_of_NCSE','ABSENT',q)
        ep,state=evidence_state(t,EP)
        if state!='UNKNOWN':
            self.add('epileptiform_discharge_present',ep,q,assertion='NEGATED' if ep is False else 'UNCERTAIN' if ep is None else 'ESTABLISHED')
            self.add('epileptiform_evidence_status',state,q)
            self.add('epileptiform_confirmed',False if state in ('NO_DEFINITE_EVIDENCE','EXPLICITLY_ABSENT') else ep,q,assertion='UNCERTAIN' if state=='NO_DEFINITE_EVIDENCE' else None)
            typ=next((v for v,p in [('POLYSPIKE_WAVE',r'polyspike[- ]wave'),('POLYSPIKE',r'polyspike'),('SPIKE_WAVE',r'spike[- ]wave'),('SHARP_WAVE',r'sharp[- ]waves?'),('SPIKE',r'\bspikes?\b')] if re.search(p,t,re.I)),None)
            if typ:self.add('epileptiform_discharge_type',typ,q)
            if re.search(r'broad\s+sharp',t,re.I):self.add('morphology_modifier','BROAD',q)
            if ep is True:self.add('epileptiform_frequency',occurrence(t),q)
        _,ictal=evidence_state(t,r'iktal\w*\s+(?:Aktivität|EEG|Muster)|Anfallsmuster')
        if ictal=='NO_DEFINITE_EVIDENCE':self.add('ictal_activity_status',ictal,q)
        if re.search(r'sekundär\w*[^.;]{0,25}bilateral\w*\s+Synchron',t,re.I):
            self.add('secondary_bilateral_synchrony',True,q)
            if self.anchor:self.anchor['secondary_bilateral_synchrony']=True
        # Rebuild continuity only in its explicitly named EEG domain.
        cont=continuity(t)
        bg_slow=re.search(r'(?:nahezu\s+)?(?:kontinuierlich\w*|intermittierend\w*)\s+verlangsam\w*\s+(?:Basisrhythmus|Grundaktivität)|(?:Basisrhythmus|Grundaktivität)\s+(?:ist\s+)?(?:nahezu\s+)?(?:kontinuierlich\w*|intermittierend\w*)\s+verlangsam\w*',t,re.I)
        if bg_slow:self.add('background_slowing_continuity',continuity(bg_slow[0]),q)
        if re.search(r'(?:Hintergrund(?:aktivität)?|Grundaktivität)\s+(?:(?:ist|erscheint)\s+)?(?:nahezu\s+)?(?:kontinuierlich|diskontinuierlich)|(?:kontinuierlich\w*|diskontinuierlich\w*)\s+(?:Hintergrund(?:aktivität)?|Grundaktivität)|burst[- ]suppression|supprimiert\w*\s+(?:Hintergrund|Grundaktivität)',t,re.I) and not re.search(r'kontinuierlich\w*\s+verlangsam',t,re.I):
            value='BURST_SUPPRESSION' if re.search(r'burst[- ]suppression',t,re.I) else 'DISCONTINUOUS' if re.search(r'diskontinuier',t,re.I) else 'SUPPRESSED' if re.search(r'supprimier',t,re.I) else cont
            if value!='UNKNOWN':self.add('background_continuity',value,q)
        # Localize each activity, stopping at spread/independence operators.
        for part in parts(q):
            global_=bool(re.search(GLOBAL,part.text,re.I)) and bool(re.search(SLOW,part.text,re.I))
            firda=bool(re.search(r'\bFIRDA\b',part.text,re.I));grda=bool(re.search(r'\bGRDA\b',part.text,re.I))
            rhythm=bool(re.search(r'rhythmisch|\b(?:FIRDA|TIRDA|GRDA|LRDA)\b',part.text,re.I)) and bool(re.search(r'Delta|\b(?:FIRDA|TIRDA|GRDA|LRDA)\b',part.text,re.I))
            if firda or grda:global_=True
            if global_:
                slow_value,_=evidence_state(part.text,SLOW)
                if slow_value is True:
                    self.add('generalized_slowing_present',True,part)
                    self.pattern(part,'GENERALIZED_SLOWING','GENERALIZED')
                    hz=v1.frequency(part.text)
                    if hz is not None and re.search(r'Theta',part.text,re.I):self.add('diffuse_theta_frequency_hz',hz,part)
            if rhythm:
                present,_=evidence_state(part.text,r'Delta|\b(?:FIRDA|TIRDA|GRDA|LRDA)\b')
                self.add('rhythmic_delta_activity_present',present,part)
                if present is True:
                    typ=next((p for p in ('FIRDA','TIRDA','GRDA','LRDA') if re.search(r'\b'+p+r'\b',part.text,re.I)),'OTHER')
                    self.add('rhythmic_delta_type',typ,part)
                    self.pattern(part,'RHYTHMIC_THETA_DELTA' if re.search(r'Theta',part.text,re.I) else 'RHYTHMIC_DELTA','GENERALIZED' if global_ else 'BILATERAL' if side(part.text)=='BILATERAL' else 'FOCAL' if side(part.text) in ('LEFT','RIGHT') else 'UNKNOWN',typ)
            marks=list(RELATION.finditer(part.text));segments=[];start=0;relation='UNKNOWN'
            for mark in marks:
                if mark.start()>start:segments.append((part.part(start,mark.start()),relation))
                start=mark.start();relation='SECONDARY_INDEPENDENT' if re.search(r'unabhängig',mark[0],re.I) else 'SPREAD'
                operator,state=evidence_state(part.text,re.escape(mark[0]))
                if operator is not True:
                    if relation=='SPREAD':self.add('spread_present',operator,part,assertion='NEGATED' if operator is False else 'UNCERTAIN')
                    relation='NONPOSITIVE_RELATION'
            segments.append((part.part(start),relation))
            for segment,relation in segments:
                txt=segment.text
                if not txt:continue
                if relation=='NONPOSITIVE_RELATION':continue
                rs=regions(txt);local=bool(re.search(FOCAL,txt,re.I)) or bool(rs and side(txt)!='UNKNOWN' and re.search(r'Theta|Delta|Verlangsam',txt,re.I))
                ep_local,ep_state=evidence_state(txt,EP)
                if relation=='SPREAD' and rs and self.anchor:
                    self.focus_row(segment,'SPREAD','EPILEPTIFORM' if ep_local is True and not re.search(r'Theta|Delta|Verlangsam',txt,re.I) else self.anchor['finding_type'],ep_local)
                    continue
                if global_ and not re.search(FOCAL,txt,re.I):continue
                if local:
                    positive,status=evidence_state(txt,SLOW)
                    if positive is False:self.add('focal_slowing_present',False,segment);continue
                    if positive is True:
                        row=self.focus_row(segment,relation,'SLOWING',ep_local)
                        if relation=='SECONDARY_INDEPENDENT' and row['frequency']=='UNKNOWN':
                            row['frequency']=occurrence(part.text[:part.text.find(segment.text)])
                        self.pattern(segment,'FOCAL_SLOWING','BILATERAL' if side(txt)=='BILATERAL' else 'FOCAL')
                elif ep_local is True and rs:
                    self.focus_row(segment,relation,'EPILEPTIFORM',True)
                elif relation=='SECONDARY_INDEPENDENT' and rs and self.anchor:
                    # Explicit elliptical references ("sowie unabhängig davon über
                    # links ...") inherit their named antecedent, not its location.
                    row=self.focus_row(segment,relation,self.anchor['finding_type'],True if self.anchor['finding_type']=='EPILEPTIFORM' else None)
                    if row['frequency']=='UNKNOWN':row['frequency']=occurrence(part.text[:part.text.find(segment.text)])
                elif ep_local is True and self.anchor and re.search(r'superponiert|eingelagert|ebendort|eben\s+dort',txt,re.I):
                    self.anchor['epileptiform_component']=True
                    self.anchor['evidence_ids'].append(self.add('epileptiform_discharge_present',True,segment))
            if ep is True and re.search(r'generalisier',part.text,re.I) and not re.search(r'sekundär\w*[^.;]{0,25}bilateral',part.text,re.I):
                self.add('epileptiform_distribution','GENERALIZED',part)

    def finish(self):
        for field,values in self.facts.items():
            if field in ('focal_abnormalities','slow_patterns','reactivity_findings'):continue
            unique={str(v):v for v in values if v is not None and v!='UNKNOWN'}
            if field=='artifact_type':self.values[field]=sorted({v for vs in values for v in vs})
            elif len(unique)==1:self.values[field]=next(iter(unique.values()))
            elif field=='assessability' and unique:self.values[field]=next(v for v in ('NOT_ASSESSABLE','LIMITED','FULL') if v in unique)
            elif field in ('epileptiform_evidence_status','ictal_activity_status') and unique:self.values[field]='CONFLICTING_EVIDENCE'
        if self.values['epileptiform_evidence_status']=='NO_DEFINITE_EVIDENCE':self.values['epileptiform_discharge_present']=None
        if self.values['ictal_activity_status']=='NO_DEFINITE_EVIDENCE':
            self.values['seizure_recorded']=None
            self.findings=[f for f in self.findings if f['field']!='seizure_recorded']
        elif self.values['ictal_activity_status']=='UNKNOWN' and self.values.get('seizure_recorded') is not None:self.values['ictal_activity_status']='CONFIRMED' if self.values['seizure_recorded'] else 'EXPLICITLY_ABSENT'
        # An ictal positive and an explicit interictal negative are different scopes.
        ep_f=[f for f in self.findings if f['field']=='epileptiform_discharge_present']
        if any(f['value'] is True and f.get('temporal_scope')=='ICTAL' for f in ep_f) and all(f.get('temporal_scope')=='INTERICTAL' for f in ep_f if f['value'] is False):
            self.values.update(epileptiform_discharge_present=True,epileptiform_confirmed=True,epileptiform_evidence_status='CONFIRMED')
        # Repeated interpretation sentences corroborate the same explicitly located
        # focus; keep every source link without counting a second identical focus.
        merged=[];aliases={}
        for row in self.focus:
            same=next((r for r in merged if all(r[k]==row[k] for k in ('finding_type','side','regions','relationship')) and (r['anterior_posterior_extent']==row['anterior_posterior_extent'] or 'UNKNOWN' in (r['anterior_posterior_extent'],row['anterior_posterior_extent']))),None)
            if same:
                aliases[row['focal_abnormality_id']]=same['focal_abnormality_id']
                for key in ('continuity','frequency','severity','asymmetry','primary_region','anterior_posterior_extent'):
                    if same[key]=='UNKNOWN' and row[key]!='UNKNOWN':same[key]=row[key]
                if row['epileptiform_component'] is True:same['epileptiform_component']=True
                if row['secondary_bilateral_synchrony'] is True:same['secondary_bilateral_synchrony']=True
                same['evidence_ids']=list(dict.fromkeys(same['evidence_ids']+row['evidence_ids']))
                same['spread_to']+=row['spread_to']
            else:merged.append(row)
        self.focus=merged
        self.values.update(focal_abnormalities=self.focus,slow_patterns=self.patterns,rhythmic_patterns=[p for p in self.patterns if p['kind'].startswith('RHYTHMIC')],reactivity_findings=self.reactivities,eeg_schema_version='1.1')
        derive(self.values)
        # Keep each saved detailed finding aligned with the final relationship links.
        for f in self.findings:
            if f['field']=='focal_abnormalities':
                ids={aliases.get(r['focal_abnormality_id'],r['focal_abnormality_id']) for r in f['value']}
                f['value']=copy.deepcopy([r for r in self.focus if r['focal_abnormality_id'] in ids])
        return self.values,self.findings


def derive(values):
    """Compact geometry comes from independent source foci, never from spread targets."""
    foci=[f for f in values.get('focal_abnormalities',[]) if f.get('assertion','ESTABLISHED')=='ESTABLISHED']
    mains=[f for f in foci if f['relationship'] in ('PRIMARY','SECONDARY_INDEPENDENT')]
    dominant=next((f for f in mains if f['relationship']=='PRIMARY'),None)
    slowing=[f for f in mains if f['finding_type']=='SLOWING']
    if not slowing:slowing=[f for f in foci if f['finding_type']=='SLOWING' and f['relationship']!='SPREAD']
    spread=[f for f in foci if f['relationship']=='SPREAD']
    if slowing:
        sides={f['side'] for f in slowing if f['side']!='UNKNOWN'};rs=[r for r in REGIONS if any(r in f['regions'] for f in slowing)]
        values.update(focal_slowing_present=True,focal_slowing_side='BILATERAL' if 'BILATERAL' in sides or {'LEFT','RIGHT'}<=sides else next(iter(sides)) if sides else 'UNKNOWN',focal_slowing_regions=rs,focal_slowing_region=compact_region(rs))
        values['focal_slowing_continuity']=next((f['continuity'] for f in slowing if f['continuity']!='UNKNOWN'),'UNKNOWN')
        values['focal_slowing_asymmetry']=next((f['asymmetry'] for f in slowing if f['asymmetry']!='UNKNOWN'),'UNKNOWN')
    if dominant:
        values.update(dominant_focus_side=dominant['side'],dominant_focus_regions=dominant['regions'],dominant_focus_asymmetry=dominant['asymmetry'],anterior_posterior_extent=dominant['anterior_posterior_extent'],extent_description=dominant['extent_description'])
    if spread:
        sides={f['side'] for f in spread if f['side']!='UNKNOWN'}
        values.update(spread_present=True,spread_side='BILATERAL' if 'BILATERAL' in sides or {'LEFT','RIGHT'}<=sides else next(iter(sides)) if sides else 'UNKNOWN',spread_regions=[r for r in REGIONS if any(r in f['regions'] for f in spread)])
    lateral=[f for f in mains if f['side'] in ('LEFT','RIGHT') or f['asymmetry'] in ('LEFT_PREDOMINANT','RIGHT_PREDOMINANT')]
    if lateral:
        values.update(lateralized_abnormality=True,lateralized_side=values['focal_slowing_side'] if slowing else lateral[0]['side'],lateralized_abnormality_type='SLOWING' if slowing else 'EPILEPTIFORM')
    ep=[f for f in foci if (f['finding_type']=='EPILEPTIFORM' or f['epileptiform_component'] is True) and f['relationship']!='SPREAD']
    if ep and values.get('epileptiform_discharge_present') is True:
        sides={f['side'] for f in ep if f['side']!='UNKNOWN'};rs=[r for r in REGIONS if any(r in f['regions'] for f in ep)]
        values.update(epileptiform_side='BILATERAL' if 'BILATERAL' in sides or {'LEFT','RIGHT'}<=sides else next(iter(sides)) if sides else 'UNKNOWN',epileptiform_regions=rs,epileptiform_region=compact_region(rs))
        if values.get('epileptiform_distribution')!='GENERALIZED':values['epileptiform_distribution']='MULTIFOCAL' if any(f['relationship']=='SECONDARY_INDEPENDENT' for f in ep) else 'FOCAL'
    patterns=[p for p in values.get('slow_patterns',[]) if p.get('assertion','ESTABLISHED')=='ESTABLISHED']
    if any(p['kind']=='GENERALIZED_SLOWING' and p.get('assertion','ESTABLISHED')=='ESTABLISHED' for p in patterns):values['generalized_slowing_present']=True
    values['generalized_slowing_maximum']=next((p['maximum'] for p in patterns if p['distribution']=='GENERALIZED' and p['maximum']!='UNKNOWN'),'UNKNOWN')
    rhythmic=[p for p in patterns if p['kind'].startswith('RHYTHMIC') and p.get('assertion','ESTABLISHED')=='ESTABLISHED']
    if rhythmic:
        values['rhythmic_delta_activity_present']=True
        values['pattern_asymmetry']=next((p['asymmetry'] for p in rhythmic if p['asymmetry']!='UNKNOWN'),'UNKNOWN')
    reactivities=[r for r in values.get('reactivity_findings',[]) if r.get('assertion','ESTABLISHED')=='ESTABLISHED']
    if reactivities:
        stimuli={r['stimulus'] for r in reactivities};responses={r['response'] for r in reactivities}
        values['reactivity_stimulus']=next(iter(stimuli)) if len(stimuli)==1 else 'MULTIPLE'
        values['reactivity_to_external_stimuli']=next(iter(responses)) if len(responses)==1 else 'UNKNOWN'
    return values


def extract(report):
    ex=Extraction(report)
    for q in clauses(report):
        if q.role!='FINDING':continue
        if re.search(r'\[[ xX]*\]|☐|☒|☑',q.text):
            if ex.form_fields.get('focal_slowing_present',{}).get('value') is True and not ex.focus:
                if re.search(r'Fokale\s+Verlangsamung|Herdstörung',q.text,re.I):ex.focus_row(q,'PRIMARY','SLOWING',None)
            continue
        ex.domains(q)
    return ex.finish()


def clinical_summary(exam):return v1.clinical_summary(exam)
