"""Source-bound EEG phenotyping for candidate review; no diagnosis or database writer."""
import re
from collections import defaultdict
from ..module_artifacts import digest
from .eeg_schema import defaults,validate_value,BOOL_FIELDS,ENUMS,TEXT_FIELDS
from .eeg_sources import discover,locate

FURNITURE=re.compile(r'Universitätsklinikum|Gemeinnützige|Privatuniversität|Paracelsus|Landesgericht|UID-Nr|Telefon|Telefax|www\.|geb\.:.*Seite',re.I)
UNCERTAIN=re.compile(r'kein\w*\s+sicher\w*\s+(?:Hinweis|Zeichen)|ohne\s+sicher\w*\s+Hinweis|fraglich|möglich\w*|\bV\.?\s*a\.?\b|nicht\s+(?:sicher\s+)?auszuschließen|Verdacht',re.I)
NEGATIVE=re.compile(r'\b(?:kein\w*|ohne|keinerlei|nicht|no)\b',re.I)
EP_PATTERN=r'epilepsietypisch\w*\s+(?:Potential\w*|Potenzial\w*)|epileptiform\w*\s+(?:Potential\w*|Potenzial\w*|Entladung\w*|Aktivität)|\bETP\b|(?:Poly)?spike(?:[- ]wave)?\w*|sharp[- ]waves?|Zeichen\s+erhöht\w*\s+(?:[cz]erebraler?\s+)?Erregungsbereitschaft'
FOCAL=r'(?:lokal\w*|fokal\w*|regional\w*)\s+(?:Hirn)?funktionsstörung|fokal\w*\s+Verlangsamung|Herd(?:störung|verlangsamung)|(?:Delta|Theta)[-\s]*(?:(?:Delta|Theta)[-\s]*)?(?:Aktivität|Gruppen|Wellen)[^.;]{0,100}(?:links|rechts|bifrontal|bitemporal)|(?:links|rechts)[-\s]*(?:temporal|frontal)[^.;]{0,70}(?:Theta|Delta|Verlangsamung)'
GENERAL=r'diffus\w*\s+(?:Hirn)?funktionsstörung|generalisiert\w*\s+Verlangsamung|diffus\w*\s+Verlangsamung|(?:verlangsam\w*\s+Basisrhythmus|Verlangsamung\s+des\s+Basisrhythmus)|diffus\w*\s+(?:eingelagert\w*\s+)?(?:Theta|Delta)[-\s]*(?:(?:Theta|Delta)[-\s]*)?Aktivität'
REGION_PATTERNS=[('FRONTOTEMPORAL',r'fronto[-\s]?temporal|bifronto[-\s]?temporal|frontal[^.;]{0,30}temporal|temporal[^.;]{0,35}frontal'),
 ('FRONTOCENTRAL',r'fronto[-\s]?(?:zentral|central)'),('TEMPOROPARIETAL',r'temporo[-\s]?parietal'),
 ('HEMISPHERIC',r'hemisphär|hemispheric'),('MULTIFOCAL',r'multifokal|multifocal'),
 ('TEMPORAL',r'temporal'),('FRONTAL',r'frontal'),('PARIETAL',r'parietal'),('OCCIPITAL',r'o[ck][cz]ipital|occipital'),('CENTRAL',r'zentral|central')]


def clauses(report):
    """Normalized analysis with original character offsets for every token."""
    text=report['eeg_report_text'];chars=[];positions=[];role='FINDING'
    role_ranges=[];offset=0
    for line in text.splitlines(keepends=True):
        value=line.strip()
        if re.match(r'^(?:Indikation|Fragestellung|Zuweisungsdiagnose|Anamnese)\s*:',value,re.I):role='INDICATION'
        elif not value:role='FINDING'
        elif re.match(r'^(?:Befund|Interpretation|Beurteilung|Grundaktivität|Basisrhythmus)\s*:',value,re.I):role='FINDING'
        role_ranges.append((offset,offset+len(line),role))
        if not FURNITURE.search(value):
            for m in re.finditer(r'\S+',line):
                if chars:chars.append(' ');positions.append(offset+m.start())
                chars.extend(m[0]);positions.extend(range(offset+m.start(),offset+m.end()))
        offset+=len(line)
    clean=''.join(chars)
    for m in re.finditer(r'.+?(?:;|(?<=[.!?])\s+(?=[A-ZÄÖÜ])|(?=\b(?:Indikation|Fragestellung|Befund|Interpretation|Beurteilung)\s*:)|$)',clean):
        if not m[0].strip():continue
        a,b=positions[m.start()],positions[m.end()-1]+1
        source=text[a:b]
        indication=next((role=='INDICATION' for x,y,role in role_ranges if x<=a<y),False)
        yield m[0],a,b,'INDICATION' if indication else 'FINDING'


def polarity(text,match):
    before=text[:match.start()];after=text[match.end():]
    before=re.split(r',|\b(?:aber|jedoch|hingegen|bei|mit)\b',before,flags=re.I)[-1]
    before=re.sub(r'nicht\s+bewusst\s+erlebt\w*','',before,flags=re.I)
    local=before[-100:]+match[0]+after[:100]
    if UNCERTAIN.search(local) or re.search(r'broad[- ]*$',before,re.I):return None,'UNCERTAIN'
    if NEGATIVE.search(before[-100:]) or re.search(r'^\s*(?:(?:sind|ist|war|waren)\s+)?nicht\s+(?:nachweisbar|vorhanden|erkennbar|abgrenzbar|zur\s+Darstellung)|^\s*(?:zeigt|zeigte|zeigen|zeigten)\s+sich\s+nicht|^\s*(?:kam|kamen|kommt|kommen)\s+nicht\s+zur\s+Darstellung',after,re.I):return False,'NEGATED'
    return True,'ESTABLISHED'


def anatomy(text):
    left=bool(re.search(r'\blinks\b|links[- ]',text,re.I));right=bool(re.search(r'\brechts\b|rechts[- ]',text,re.I))
    side='BILATERAL' if left and right or re.search(r'beidseits|bilateral|bifrontal|bitemporal|bifronto',text,re.I) else 'LEFT' if left else 'RIGHT' if right else 'UNKNOWN'
    region=next((value for value,pattern in REGION_PATTERNS if re.search(pattern,text,re.I)),'UNKNOWN')
    return side,region


def frequency(text):
    m=re.search(r'(\d+(?:[,.]\d+)?)\s*(?:[-–]\s*(\d+(?:[,.]\d+)?))?\s*(?:Hz|/\s*sec\.?|/s\b)',text,re.I)
    if not m:return None
    low=float(m[1].replace(',','.'));high=float(m[2].replace(',','.')) if m[2] else None
    if not 0<low<=200 or high is not None and not low<=high<=200:return None
    return dict(min=low,max=high) if high is not None else low


class Extractor:
    def __init__(self,report):
        self.report=report;self.values=defaults();self.findings=[];self._values=defaultdict(list)
    def add(self,field,value,q,start,pattern=None,assertion=None,source_type='FREE_TEXT_SOURCE',method='DETERMINISTIC'):
        validate_value(field,value)
        target=re.search(pattern,q,re.I) if pattern else None
        absolute=self.report['report_start']+start+(target.start() if target else 0)
        section=locate(self.report['ranges'],min(absolute,self.report['report_end']-1))
        excerpt=' '.join(q.split())[:450]
        row=dict(patient_id=self.report['patient_id'],eeg_exam_id=None,document_id=self.report['document_id'],page=section['page_start'],
            section_id=section['section_id'],evidence_id=f"{self.report['report_occurrence_id']}:{field}:{start}",
            source_text_short=excerpt,source_type=source_type,source_start=absolute,field=field,value=value,
            assertion=assertion or ('NEGATED' if value is False or value in ('ABSENT','NOT_PERFORMED') else 'UNCERTAIN' if value is None else 'ESTABLISHED'),
            method=method,review_required=True)
        row['finding_id']='EEGF:'+digest([row['evidence_id'],field,value])[:20]
        row['temporal_scope']='INTERICTAL' if re.search(r'\binteriktal',q,re.I) else 'ICTAL' if re.search(r'\biktal',q,re.I) else 'REPORT'
        if row['finding_id'] not in {f['finding_id'] for f in self.findings}:
            self.findings.append(row);self._values[field].append(value)
    def boolean(self,field,pattern,q,start):
        for m in re.finditer(pattern,q,re.I):
            value,state=polarity(q,m);self.add(field,value,q,start,pattern,state)
            return value,state
        return None,None
    def finish(self):
        for field,values in self._values.items():
            unique={str(v):v for v in values if v is not None and v!='UNKNOWN'}
            if field in TEXT_FIELDS and unique:self.values[field]='\n'.join(unique.values())
            elif len(unique)==1:self.values[field]=next(iter(unique.values()))
            elif len(unique)>1:
                if field in BOOL_FIELDS and set(unique)=={'True','False'}:
                    supporting=[f for f in self.findings if f['field']==field]
                    if any(f['value'] is True and f['temporal_scope']=='ICTAL' for f in supporting) and all(f['temporal_scope']=='INTERICTAL' for f in supporting if f['value'] is False):self.values[field]=True
                if field.endswith('_side') and set(unique)<=set(('LEFT','RIGHT','BILATERAL')):self.values[field]='BILATERAL'
                elif field.endswith('_region'):
                    regions=set(unique)
                    self.values[field]='FRONTOTEMPORAL' if regions<={'FRONTAL','TEMPORAL','FRONTOTEMPORAL'} else 'MULTIFOCAL'
        return self.values,self.findings


def checked_form(ex):
    offset=0
    aliases={'modulation':r'Modulation','amplitude':r'Amplitude','frequency_stability':r'Frequenzstabilität|Konstanz',
        'symmetry':r'Symmetrie','eye_opening_blocking':r'Augenöffn|Blockier','focal_slowing_side':r'Herdseite|fokale\s+Verlangsamung',
        'epileptiform_discharge_present':r'epileptiform|epilepsietypisch','focal_slowing_present':r'fokale\s+Verlangsamung|Herdstörung',
        'generalized_slowing_present':r'generalisierte\s+Verlangsamung','background_continuity':r'Kontinuität',
        'hyperventilation_performed':r'Hyperventilation','photic_stimulation_performed':r'Photostimulation',
        'sleep_spindles':r'Schlafspindel','k_complexes':r'K[- ]Komplex','vertex_transients':r'Vertex',
        'source_reported_abnormality_grade':r'Gesamtbeurteilung|Abnormitätsgrad'}
    translations={'gut':'GOOD','ausreichend':'SUFFICIENT','mäßig':'MODERATE','gering':'LOW','fehlend':'ABSENT',
        'symmetrisch':'SYMMETRIC','asymmetrisch':'ASYMMETRIC','links':'LEFT','rechts':'RIGHT','beidseits':'BILATERAL',
        'stabil':'STABLE','konstant':'STABLE','instabil':'UNSTABLE','kontinuierlich':'CONTINUOUS','diskontinuierlich':'DISCONTINUOUS',
        'normal':'NORMAL','leicht abnorm':'MILDLY_ABNORMAL','mäßig abnorm':'MODERATELY_ABNORMAL'}
    for line in ex.report['eeg_report_text'].splitlines(keepends=True):
        for m in re.finditer(r'(?:\[[xX]\]|☒|☑)\s*([^\[\]☐☒☑\n]+)',line):
            selected=m[1].strip(' ;,.');context=line.split('[')[0].split('☒')[0].split('☐')[0]
            combined=context+' '+selected
            for field,pattern in aliases.items():
                if not re.search(pattern,combined,re.I):continue
                negative=bool(re.match(r'nein|keine?|nicht|absent',selected,re.I))
                if field in BOOL_FIELDS:value=not negative
                elif field in ('sleep_spindles','k_complexes','vertex_transients'):value='ABSENT' if negative else 'PRESENT'
                else:value=translations.get(selected.lower(),selected.upper())
                if field in ENUMS and value not in ENUMS[field]:continue
                ex.add(field,value,line.strip(),offset,source_type='STRUCTURED_FORM',assertion='NEGATED' if negative else 'ESTABLISHED')
        offset+=len(line)


def extract(report):
    ex=Extractor(report);body=report['eeg_report_text'];is_form=bool(re.search(r'\[[ xX]\]|☒|☑|☐',body))
    interpretation=re.search(r'(?is)Interpretation\s*:\s*(.*)',body)
    ex.values['original_interpretation_text']=interpretation[1].strip() if interpretation else None
    if is_form:checked_form(ex)
    for q,start,end,role in clauses(report):
        if role=='INDICATION':
            ex.add('clinical_indication_text',q,q,start)
            category=next((c for c,p in [('EPILEPSY',r'epilepsie'),('SEIZURE',r'Anfall|Anfälle|Status'),('TFNE',r'TFNE'),('ALTERED_CONSCIOUSNESS',r'Bewusst|Koma'),('ENCEPHALOPATHY',r'Enzephalopath'),('STROKE',r'Infarkt|Schlaganfall'),('ICH',r'Blutung'),('CAA',r'Amyloid|CAA'),('SYNCOPE',r'Synkop')] if re.search(p,q,re.I)),'OTHER')
            ex.add('clinical_indication_category',category,q,start);continue
        # Empty/unselected form options are not clinical assertions.
        if is_form and re.search(r'\[[ xX]*\]|☐|☒|☑',q):continue
        src='STRUCTURED_FORM' if is_form and re.search(r'\[[xX]\]|☒|☑',q) else 'FREE_TEXT_SOURCE'
        focal,fs=ex.boolean('focal_slowing_present',FOCAL,q,start)
        general,gs=ex.boolean('generalized_slowing_present',GENERAL,q,start)
        ep,es=ex.boolean('epileptiform_discharge_present',EP_PATTERN,q,start)
        if focal is True:
            side,region=anatomy(q)
            if side!='UNKNOWN':ex.add('focal_slowing_side',side,q,start,source_type=src);ex.add('lateralized_abnormality',True,q,start);ex.add('lateralized_side',side,q,start);ex.add('lateralized_abnormality_type','SLOWING',q,start)
            if region!='UNKNOWN':ex.add('focal_slowing_region',region,q,start,source_type=src)
            ex.add('slow_activity_description_text',q,q,start)
        if general is True:ex.add('slow_activity_description_text',q,q,start)
        if re.search(r'Grundaktivität|Basisrhythmus|Grundrhythmus|Hintergrundaktivit',q,re.I):ex.add('background_description_text',q,q,start)
        if ep is True:
            side,region=anatomy(q)
            if side!='UNKNOWN':ex.add('epileptiform_side',side,q,start);ex.add('epileptiform_distribution','LATERALIZED',q,start)
            if region!='UNKNOWN':ex.add('epileptiform_region',region,q,start)
            if re.search(r'generalisier',q,re.I):ex.add('epileptiform_distribution','GENERALIZED',q,start)
            typ=next((v for v,p in [('POLYSPIKE_WAVE',r'polyspike[- ]wave'),('POLYSPIKE',r'polyspike'),('SPIKE_WAVE',r'spike[- ]wave'),('SPIKE',r'\bspikes?\b'),('SHARP_WAVE',r'sharp[- ]waves?')] if re.search(p,q,re.I)),None)
            if typ:ex.add('epileptiform_discharge_type',typ,q,start)
            ex.add('epileptiform_description_text',q,q,start)
        for field,pattern in [('periodic_discharge_present',r'periodisch\w*\s+Entladung|\b(?:LPD|GPD|BIPD|PLED|GPED)s?\b'),
            ('rhythmic_delta_activity_present',r'rhythmisch\w*\s+(?:generalisierte\w*\s+)?Delta[- ]Aktivität|\b(?:LRDA|GRDA|FIRDA|TIRDA)\b'),
            ('rhythmic_periodic_pattern_present',r'rhythmisch[- ]periodisch\w*\s+Muster|pseudoperiodisch\w*\s+Ablauf'),
            ('seizure_recorded',r'elektro(?:gra[fp]hisch|klinisch)\w*\s+(?:Anfall|Anfälle|Anfallsaktivität)|\biktal\w*\s+(?:EEG|Muster)|Anfallsmuster|(?:Anfall|Anfälle)[^.;]{0,45}(?:aufgezeichnet|erfasst)|(?:Anfälle|Anfall)\s+(?:während|in)\s+(?:der|dieser)\s+(?:Ableitung|Aufzeichnung)'),
            ('encephalopathy_pattern',r'En[cz]ephalopathie|triphasisch\w*\s+Wellen|burst[- ]suppression'),
            ('reduced_vigilance_phases',r'(?:reduziert\w*|vermindert\w*)\s+Vigilanz|subvigil|Müdigkeit|Schläfrigkeit'),
            ('alpha_dropout',r'Alpha[- ](?:dropout|Ausfall)|Alphaausfall'),('subvigil_beta_activity',r'subvigil\w*\s+Beta'),
            ('sleep_architecture_present',r'(?:erhalten\w*|normal\w*)\s+Schlafarchitektur'),
            ('ventricular_extrasystoles',r'(?<!supra)ventrikulär\w*\s+Extrasystol'),('supraventricular_extrasystoles',r'supraventrikulär\w*\s+Extrasystol'),
            ('eeg_clinical_event_captured',r'(?:klinisch\w*\s+|habituell\w*\s+)?(?:Ereignis\w*|Episode\w*|Attacke\w*)[^.;]{0,250}(?:aufgezeichnet|erfasst|captured)')]:ex.boolean(field,pattern,q,start)
        m=re.search(r'(?:non[- ]?konvulsiv\w*\s+)?Status\s+epilepticus(?:[- ]?Muster)?|Status[- ]epilepticus[- ]Muster',q,re.I)
        if m:
            value,state=polarity(q,m)
            history=bool(re.search(r'Zustand\s+nach|\bZ\.?\s*n\.?|anamnestisch|nach\s+(?:behandelt\w*\s+)?$',q[:m.start()],re.I))
            if value is False and re.search(r'keine\s+klinischen\s+Zeichen',q,re.I):value,state=None,'UNCERTAIN'
            if value is not True or (not history and re.search(r'EEG|elektrogra[fp]h|elektroklin|non[- ]?konvulsiv|Muster',q,re.I)):
                ex.add('electrographic_status_epilepticus',value,q,start,r'Status',state)
        for field,pattern in [('vertex_transients',r'Vertex[- ](?:Transienten|Zacken|Wellen)'),('k_complexes',r'K[- ]Komplex'),('sleep_spindles',r'Schlafspindel'),('high_slow_waves',r'hoch\w*\s+langsame\s+Wellen')]:
            m=re.search(pattern,q,re.I)
            if m:
                value,state=polarity(q,m);ex.add(field,'PRESENT' if value else 'ABSENT' if value is False else 'UNKNOWN',q,start,pattern,state)
        for field,pattern,typ in [('periodic_discharge_type',r'\bLPDs?\b','LPD'),('periodic_discharge_type',r'\bGPDs?\b','GPD'),('periodic_discharge_type',r'\bBIPDs?\b','BIPD'),('rhythmic_delta_type',r'\bLRDA\b','LRDA'),('rhythmic_delta_type',r'\bGRDA\b','GRDA')]:
            m=re.search(pattern,q,re.I)
            if m and polarity(q,m)[0] is True:ex.add(field,typ,q,start,pattern)
        if re.search(r'\b(?:PLED|GPED)s?\b',q,re.I):ex.add('periodic_discharge_type','OTHER',q,start)
        if re.search(r'periodisch|rhythmisch\w*\s+Delta|\b(?:LPD|GPD|BIPD|LRDA|GRDA|FIRDA|TIRDA)\b',q,re.I):
            side,region=anatomy(q)
            if side!='UNKNOWN':ex.add('pattern_side',side,q,start)
            if region!='UNKNOWN':ex.add('pattern_region',region,q,start)
        if re.search(r'En[cz]ephalopathie|triphasisch|burst[- ]suppression',q,re.I):ex.add('encephalopathy_description_text',q,q,start)
        if re.search(r'Status\s+epilepticus(?:[- ]?Muster)?|Status[- ]epilepticus[- ]Muster',q,re.I):ex.add('status_epilepticus_description_text',q,q,start)
        if re.search(r'Schlaf|Vigilanz|Müdigkeit|subvigil|Vertex|K[- ]Komplex|Spindel',q,re.I):ex.add('sleep_description_text',q,q,start)
        if re.search(r'Hyperventilation|Photostimulation|Fotostimulation|photic\s+driving',q,re.I):ex.add('activation_description_text',q,q,start)
        if re.search(r'Blockier|Reaktivität|reaktiv|Augenöffnen',q,re.I):ex.add('reactivity_description_text',q,q,start)
        if re.search(r'EKG|ECG',q,re.I) and re.search(r'Schenkelblock|Blockbild|Sonstiges|Auffälligkeit',q,re.I):ex.add('other_ecg_findings',q,q,start)
        if re.search(r'EEG[- ]Korrelat|elektroklinisch\w*\s+Korrelation|klinisch[- ]elektrographisch',q,re.I):ex.add('clinical_eeg_correlation',q,q,start)
        if re.search(r'(?:Ereignis\w*|Episode\w*|Attacke\w*)[^.;]{0,250}(?:aufgezeichnet|erfasst|captured)',q,re.I):ex.add('event_description',q,q,start)
        if re.search(r'schnelle\s+Aktivität|Beta[- ]Aktivität|schnelle\s+Frequenzen',q,re.I):
            for field,band in [('fast_activity_13_18hz',r'13\s*[-–]\s*18\s*Hz'),('fast_activity_18_25hz',r'18\s*[-–]\s*25\s*Hz')]:
                m=re.search(band,q,re.I)
                if m:
                    value,state=polarity(q,m);ex.add(field,'PRESENT' if value else 'ABSENT' if value is False else 'UNKNOWN',q,start,band,state)
            amount='ABSENT' if re.search(r'keine\s+(?:schnelle|Beta)',q,re.I) else 'ABUNDANT' if re.search(r'reichlich|häufig',q,re.I) else 'MODERATE' if re.search(r'mäßig',q,re.I) else 'LITTLE' if re.search(r'wenig|gering',q,re.I) else None
            if amount:ex.add('fast_activity_amount',amount,q,start)
            distribution='FRONTOCENTRAL' if re.search(r'fronto[- ]?(?:zentral|central)',q,re.I) else 'DIFFUSE' if re.search(r'diffus',q,re.I) else 'FOCAL' if re.search(r'fokal',q,re.I) else None
            if distribution:ex.add('fast_activity_distribution',distribution,q,start)
            side,_=anatomy(q)
            if side!='UNKNOWN':ex.add('fast_activity_side',side,q,start)
            ex.add('fast_activity_description_text',q,q,start)
        if re.search(r'Grundaktivität|Basisrhythmus|Grundrhythmus',q,re.I):
            location='POSTERIOR' if re.search(r'posterior|hintere|o[ck][cz]ipital',q,re.I) else 'FRONTOCENTRAL' if re.search(r'fronto[- ]?(?:zentral|central)',q,re.I) else 'FRONTAL' if re.search(r'frontal',q,re.I) else 'TEMPORAL' if re.search(r'temporal',q,re.I) else 'CENTRAL' if re.search(r'zentral',q,re.I) else 'DIFFUSE' if re.search(r'diffus',q,re.I) else None
            if location:ex.add('predominant_location',location,q,start)
        if re.search(r'Hintergrundreaktivität|Grundaktivität[^.;]{0,30}reaktiv',q,re.I):
            ex.add('background_reactivity','ABSENT' if re.search(r'fehlend|keine|nicht',q,re.I) else 'PRESENT',q,start)
        if re.search(r'Artefakt',q,re.I):ex.boolean('artifact_limited',r'Artefakt(?:überlagerung|e|belastung)?',q,start)
        if re.search(r'elektroklinisch',q,re.I) and ex._values.get('seizure_recorded') and ex._values['seizure_recorded'][-1] is True:ex.add('seizure_type_eeg','ELECTROCLINICAL',q,start)
        elif re.search(r'elektrogra[fp]hisch|\biktal\w*\s+(?:EEG|Muster)|Anfallsmuster',q,re.I) and ex._values.get('seizure_recorded') and ex._values['seizure_recorded'][-1] is True:ex.add('seizure_type_eeg','ELECTROGRAPHIC',q,start)
        hz=frequency(q)
        if hz is not None:
            if re.search(r'Basisrhythm|Grundrhythm|Grundaktivität|Hintergrundaktivit',q,re.I):ex.add('background_frequency_hz',hz,q,start);ex.add('basic_rhythm_hz',hz,q,start)
            if re.search(r'diffus[^.;]*Theta|Theta[- ]Aktivität',q,re.I):ex.add('diffuse_theta_frequency_hz',hz,q,start)
            if re.search(r'periodisch|rhythmisch',q,re.I):ex.add('pattern_frequency_hz',hz,q,start)
        for field,pattern,options in [
            ('frequency_stability',r'konstant|stabil|instabil|labil',[('SUFFICIENTLY_STABLE',r'ausreichend'),('MODERATELY_STABLE',r'mäßig'),('UNSTABLE',r'instabil|labil'),('STABLE',r'konstant|stabil')]),
            ('modulation',r'modulier|Modulation',[('GOOD',r'gut'),('SUFFICIENT',r'ausreichend'),('MODERATE',r'mäßig'),('LOW',r'gering'),('ABSENT',r'fehlend|keine')]),
            ('amplitude',r'Amplitude|amplitudig',[('LOW_MEDIUM',r'niedrig[^.;]{0,20}mittel'),('MEDIUM_HIGH',r'mittel[^.;]{0,20}hoch'),('LOW',r'niedrig'),('HIGH',r'hoch'),('MEDIUM',r'mittel')]),
            ('eye_opening_blocking',r'Blockier|Augenöffn',[('NOT_PERFORMED',r'nicht\s+durchgeführt'),('GOOD',r'gut'),('MODERATE',r'mäßig'),('LOW',r'gering'),('ABSENT',r'nicht\s+blockier|keine\s+Blockier')]),
            ('background_continuity',r'kontinuier|diskontinuier|burst[- ]suppression|supprimier',[('BURST_SUPPRESSION',r'burst[- ]suppression'),('DISCONTINUOUS',r'diskontinuier'),('NEAR_CONTINUOUS',r'nahezu\s+kontinuier'),('SUPPRESSED',r'supprimier'),('CONTINUOUS',r'kontinuier')]),
            ('symmetry',r'asymmetr|symmetr',[('ASYMMETRIC',r'asymmetr'),('SYMMETRIC',r'symmetr')]),
            ('ecg_rhythm',r'EKG|ECG|Sinusrhythm',[('SINUS',r'Sinusrhythm'),('ARRHYTHMIA',r'Arrhythm|Vorhofflimm')]),
            ('heart_rate_category',r'EKG|ECG|Sinusrhythm',[('BRADYCARDIC',r'bradykard'),('TACHYCARDIC',r'tachykard'),('NORMAL',r'normofrequent')]),
            ('slow_activity_amount',r'Theta|Delta|langsame\s+Aktivität',[('ABUNDANT',r'reichlich|häufig'),('MODERATE',r'mäßig'),('LITTLE',r'gering|wenig|diskret')]),
            ('encephalopathy_pattern_type',r'triphasisch|burst[- ]suppression|low[- ]voltage',[('TRIPHASIC_MORPHOLOGY',r'triphasisch'),('BURST_SUPPRESSION',r'burst[- ]suppression'),('LOW_VOLTAGE',r'low[- ]voltage')]),
        ]:
            if re.search(pattern,q,re.I):
                value=next((value for value,p in options if re.search(p,q,re.I)),None)
                if value:ex.add(field,value,q,start,source_type=src)
        for field,key in [('hyperventilation_performed',r'Hyperventilation|\bHV\b'),('photic_stimulation_performed',r'Photostimulation|Fotostimulation|photisch\w*\s+Stimulation')]:
            m=re.search(key,q,re.I)
            if m and re.search(r'durchgeführt|erfolgte|unter|bei|keine\s+Veränderung|ohne\s+Änderung|nicht|kein',q,re.I):
                done=not bool(re.search(r'nicht\s+durchgeführt|nicht\s+erfolgt|keine\s+(?:Hyperventilation|Photo|Foto)',q,re.I));ex.add(field,done,q,start,key)
                if field=='hyperventilation_performed':
                    response='NOT_PERFORMED' if not done else 'NO_CHANGE' if re.search(r'keine\s+Veränderung|ohne\s+Änderung',q,re.I) else 'INCREASE_SLOW_ACTIVITY' if re.search(r'Zunahme[^.;]*(?:Theta|Delta|langsam)',q,re.I) else None
                    if response:ex.add('hyperventilation_response',response,q,start)
        if re.search(r'photisch\w*\s+driving|photic\s+driving|Fotostimulationsantwort',q,re.I):
            value='ABSENT' if re.search(r'kein|fehlend',q,re.I) else 'GOOD' if re.search(r'gut',q,re.I) else 'MODERATE' if re.search(r'mäßig',q,re.I) else 'LOW' if re.search(r'gering',q,re.I) else None
            if value:ex.add('photic_driving',value,q,start)
        if re.search(r'äußer\w*\s+Reiz|extern\w*\s+Reiz',q,re.I):
            value='NOT_TESTED' if re.search(r'nicht\s+(?:getestet|geprüft)',q,re.I) else 'ABSENT' if re.search(r'keine\s+Reaktion|nicht\s+reaktiv',q,re.I) else 'PRESENT' if re.search(r'Reaktion|reagib|reaktiv',q,re.I) else None
            if value:ex.add('reactivity_to_external_stimuli',value,q,start)
        grade=None
        for value,pattern in [('BROADLY_NORMAL',r'Grenzen\s+der\s+Norm|weitgehend\s+normal'),('SEVERELY_ABNORMAL',r'schwer\w*\s+abnorm|hochgradig\s+abnorm'),('MODERATELY_ABNORMAL',r'mäßig\w*\s+abnorm'),('MILDLY_ABNORMAL',r'leicht\w*\s+abnorm'),('MINIMALLY_ABNORMAL',r'minimal\w*\s+abnorm'),('ABNORMAL',r'\babnorm\w*\s+EEG|pathologisch\w*\s+EEG'),('NORMAL',r'Normalbefund|normales?\s+EEG|unauffällig\w*\s+EEG')]:
            m=re.search(pattern,q,re.I)
            if m and polarity(q,m)[0] is True:grade=value;break
        if grade:ex.add('source_reported_abnormality_grade',grade,q,start)
        if re.search(r'interiktal|interictal',q,re.I):ex.add('eeg_tfne_timing','INTERICTAL',q,start)
        elif re.search(r'während\s+(?:der|einer)\s+(?:Episode|Attacke|TFNE)',q,re.I):ex.add('eeg_tfne_timing','DURING_EPISODE',q,start)
        elif re.search(r'nach\s+(?:der|einer)\s+(?:Episode|Attacke|TFNE)',q,re.I):ex.add('eeg_tfne_timing','POST_EPISODE',q,start)
    quality='NOT_ASSESSABLE' if re.search(r'(?:EEG|Ableitung)[^.!]{0,100}nicht\s+(?:beurteilbar|auswertbar)|(?:aufgrund|wegen)[^.!]{0,70}Artefakt[^.!]{0,50}nicht\s+(?:beurteilbar|auswertbar)|unverwertbares\s+EEG',body,re.I) else 'ESTABLISHED'
    if quality=='NOT_ASSESSABLE':ex.add('assessability',quality,' '.join(body.split())[:450],0,assertion='NOT_ASSESSABLE')
    values,findings=ex.finish()
    values['source_form_detected']=is_form
    return values,findings


def clinical_summary(exam):
    flags=[]
    for field,flag in [('focal_slowing_present','FOCAL_SLOWING'),('generalized_slowing_present','GENERALIZED_SLOWING'),
        ('periodic_discharge_present','PERIODIC_DISCHARGES'),('rhythmic_delta_activity_present','RHYTHMIC_DELTA_ACTIVITY'),
        ('electrographic_status_epilepticus','STATUS_EPILEPTICUS'),('encephalopathy_pattern','ENCEPHALOPATHY_PATTERN')]:
        if exam.get(field) is True:flags.append(flag)
    if exam.get('epileptiform_discharge_present') is True:
        distribution=exam.get('epileptiform_distribution','UNKNOWN')
        flags.append('GENERALIZED_EPILEPTIFORM_ACTIVITY' if distribution=='GENERALIZED' else 'MULTIFOCAL_EPILEPTIFORM_ACTIVITY' if distribution=='MULTIFOCAL' else 'FOCAL_EPILEPTIFORM_ACTIVITY' if distribution in ('FOCAL','LATERALIZED') else 'OTHER')
    if exam.get('seizure_recorded') is True:
        if exam.get('seizure_type_eeg')=='ELECTROGRAPHIC':flags.append('ELECTROGRAPHIC_SEIZURE')
        elif exam.get('seizure_type_eeg')=='ELECTROCLINICAL':flags.append('ELECTROCLINICAL_SEIZURE')
    normal=exam.get('source_reported_abnormality_grade') in ('NORMAL','BROADLY_NORMAL')
    if not flags and normal:flags.append('NORMAL')
    if exam.get('assessability')=='NOT_ASSESSABLE':primary='UNINTERPRETABLE'
    elif any(f in flags for f in ('ELECTROGRAPHIC_SEIZURE','ELECTROCLINICAL_SEIZURE')):primary='ELECTROGRAPHIC_SEIZURE'
    elif any(f in flags for f in ('PERIODIC_DISCHARGES','RHYTHMIC_DELTA_ACTIVITY')) or exam.get('rhythmic_periodic_pattern_present') is True:primary='RHYTHMIC_PERIODIC_PATTERN'
    elif exam.get('epileptiform_discharge_present') is True:primary='EPILEPTIFORM_DISCHARGES'
    elif 'FOCAL_SLOWING' in flags:primary='FOCAL_SLOWING'
    elif 'GENERALIZED_SLOWING' in flags:primary='GENERALIZED_SLOWING'
    elif 'NORMAL' in flags:primary='NORMAL'
    elif exam.get('source_reported_abnormality_grade') not in ('UNKNOWN',None):primary='OTHER_ABNORMALITY';flags.append('OTHER')
    else:primary='UNKNOWN'
    return dict(eeg_exam_id=exam['eeg_exam_id'],patient_id=exam['patient_id'],eeg_primary_finding=primary,
        clinical_phenotype=sorted(set(flags)),laterality=exam.get('focal_slowing_side') if exam.get('focal_slowing_present') else exam.get('epileptiform_side','UNKNOWN'),
        region=exam.get('focal_slowing_region') if exam.get('focal_slowing_present') else exam.get('epileptiform_region','UNKNOWN'),
        seizure_recorded=exam.get('seizure_recorded'),electrographic_status_epilepticus=exam.get('electrographic_status_epilepticus'),
        eeg_clinical_event_captured=exam.get('eeg_clinical_event_captured'),source_type='DERIVED',
        evidence_ids=exam.get('evidence_ids',[]),review_required=True)
