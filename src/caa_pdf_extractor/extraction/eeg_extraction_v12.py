"""1.2 source-scoped explicit status, cEEG template facts and temporal quality."""
import copy
import re
from . import eeg_extraction_v11 as v11
from .eeg_schema_v12 import defaults,validate_value
from .eeg_sources import locate
from ..module_artifacts import digest

NCSE=re.compile(r'nicht[-\s]+konvulsiv\w*\s+Status\s+epilepticus|\bNCSE\b(?!\s*Rating)|Status\s+aphasicus',re.I)
STATUS=re.compile(NCSE.pattern+r'|Status[-\s]+epilepticus',re.I)


def assertion(text,match,role='FINDING'):
    prefix=text[max(0,match.start()-100):match.start()]
    local=prefix+text[match.start():match.end()+80]
    if role=='INDICATION':return 'UNCERTAIN'
    if re.search(r'anamnestisch|Z\.?\s*n\.?\s*|früher|damals|historisch|Erstmanifestation',local,re.I):return 'HISTORICAL'
    # The "nicht" inside non-convulsive and "nicht vollständig durchbrochen"
    # are not negation of the diagnosis.
    if re.search(r'kein\w*|ohne\s+(?:Hinweis|Zeichen)',prefix,re.I) or re.match(r'\s*(?:liegt\s+)?nicht\s+vor',text[match.end():],re.I):return 'NEGATED'
    if re.search(r'Fragestellung|Indikation|V\.?\s*a\.?\s*|Verdacht|fraglich|wahrscheinlich',prefix,re.I):return 'UNCERTAIN'
    return 'ESTABLISHED'


def clinical_summary(exam):
    summary=v11.clinical_summary(exam)
    if exam.get('NCSE') is True or exam.get('NCSE_OCCURRED_DURING_RECORDING') is True:
        summary['eeg_primary_finding']='STATUS_EPILEPTICUS'
        summary['clinical_phenotype']=sorted(set(summary['clinical_phenotype'])|{'STATUS_EPILEPTICUS'})
    return summary


def extract(report):
    # Temporally bounded loss of signal must not invalidate the preceding EEG.
    quality=[];masked=copy.deepcopy(report)
    pattern=re.compile(r'(?im)(?:Ab\s+(?:ca\.?\s*)?\d{1,2}:\d{2}[^.]{0,250}(?:nicht\s+beurteilbar)[^.]*\.?|Um\s+(?:ca\.?\s*)?\d{1,2}:\d{2}[^.]{0,200}Elektroden[^.]*\.?)')
    for match in pattern.finditer(report['eeg_report_text']):quality.append(match)
    chars=list(report['eeg_report_text'])
    for match in quality:
        for i in range(match.start(),match.end()):
            if chars[i]!='\n':chars[i]=' '
    masked['eeg_report_text']=''.join(chars)
    old,findings=v11.extract(masked);values={**defaults(),**old,'eeg_schema_version':'1.2'}
    # Status in v1.2 is determined from bounded current EEG assertions, never
    # from the inherited broad pattern heuristic.
    status_fields={'electrographic_status_epilepticus','NCSE','NCSE_assertion','status_type'}
    findings=[f for f in findings if f['field'] not in status_fields]
    values.update(electrographic_status_epilepticus=None,NCSE=None)

    def add(field,value,text,offset,assertion_value='ESTABLISHED'):
        validate_value(field,value);values[field]=copy.deepcopy(value)
        absolute=report['report_start']+offset;section=locate(report['ranges'],min(absolute,max(report['report_start'],report['report_end']-1)))
        eid=f'{report["report_occurrence_id"]}:v12:{field}:{absolute}'
        findings.append(dict(patient_id=report['patient_id'],eeg_exam_id=None,document_id=report['document_id'],page=section['page_start'],section_id=section['section_id'],
            evidence_id=eid,finding_id='EEGF12:'+digest([eid,value])[:20],field=field,value=copy.deepcopy(value),assertion=assertion_value,
            source_text_short=text[:450],source_clause_text=text,source_type='FREE_TEXT_SOURCE',source_start=absolute,source_end=absolute+len(text),
            source_layer='EEG_BOUNDED_SOURCE_TEXT',method='DETERMINISTIC_EEG_1.2',eeg_schema_version='1.2',review_required=True,temporal_scope='REPORT'))
        return eid

    states=[];ncse_states=[]
    for q in v11.clauses(report):
        text=q.text;offset=q.positions[0] if q.positions else 0
        for match in STATUS.finditer(text):
            if re.search(r'Rating\s+Scale',text[max(0,match.start()-5):match.end()+30],re.I):continue
            preceding=report['eeg_report_text'][max(0,offset-20):offset]
            role='INDICATION' if re.search(r'V\.?\s*a\.\s*$',preceding,re.I) else q.role
            state=assertion(text,match,role)
            clinical_only=bool(re.search(r'keine\s+klinischen\s+Zeichen',text[:match.start()],re.I))
            if clinical_only:
                add('clinical_signs_of_NCSE','ABSENT',text,offset,'NEGATED');continue
            states.append((state,text,offset))
            if NCSE.fullmatch(match[0]):ncse_states.append((state,text,offset))
        if q.role!='FINDING':continue
        # Explicit abbreviations retain their original frequency spelling.
        periodic=re.search(r'\b(LPD|GPD|BIPD)(?:[’\x27]?s)?\b',text,re.I)
        periodic_scope=text[text.rfind(':',0,periodic.start())+1:periodic.end()] if periodic else ''
        if periodic and not re.search(r'(?:kein\w*|ohne)\s+(?:periodisch\w*\s+)?(?:Entladungen|Muster|LPD|GPD|BIPD)',periodic_scope,re.I):
            add('periodic_discharge_present',True,text,offset);add('periodic_discharge_type',periodic[1].upper(),text,offset)
            freq=re.search(r'\d+(?:[,.]\d+)?(?:\s*[-–]\s*\d+(?:[,.]\d+)?)?\s*(?:pro\s*10\s*Sekunden|/\s*s|Hz)',text,re.I)
            if freq:
                add('periodic_frequency_raw',freq[0],text,offset)
                if re.fullmatch(r'\d+(?:[,.]\d+)?\s*pro\s*10\s*Sekunden',freq[0],re.I):hz=float(re.search(r'\d+(?:[,.]\d+)?',freq[0])[0].replace(',','.'))/10
                else:hz=v11.v1.frequency(freq[0])
                if hz is None and '/s' in freq[0]:hz=float(re.search(r'\d+(?:[,.]\d+)?',freq[0])[0].replace(',','.'))
                if hz is not None:add('pattern_frequency_hz',hz,text,offset)
        if re.search(r'assoziiert\w*\s+rhythmisch\w*\s+Delta|\+\s*RDA',text,re.I):add('rhythmic_delta_activity_present',True,text,offset)
        if re.search(r'Reaktion|Reagibilität|Reagibilit',text,re.I) and re.search(r'Schmerzreiz|extern\w*\s+Stimuli',text,re.I):
            stimulus='PAINFUL' if re.search(r'Schmerzreiz',text,re.I) else 'EXTERNAL'
            response='ABSENT' if re.search(r'fehlend|keine\s+Reaktion',text,re.I) else 'PRESENT'
            eid=add('reactivity_to_external_stimuli',response,text,offset)
            row=dict(stimulus=stimulus,response=response,evidence_ids=[eid],source_text_short=text[:450])
            add('reactivity_findings',values['reactivity_findings']+[row],text,offset);add('reactivity_stimulus',stimulus,text,offset)
        if re.search(r'En[cz]ephalopath',text,re.I) and not re.search(r'kein\w*\s+En[cz]ephalopath',text,re.I):
            add('encephalopathy_pattern',True,text,offset)
            if re.search(r'diffus|verlangsam',text,re.I):add('generalized_slowing_present',True,text,offset);add('encephalopathy_pattern_type','GENERALIZED_SLOWING',text,offset)
        if re.search(r'fokal\w*\s+Anfälle\s+wahrscheinlich',text,re.I):
            add('focal_seizure_interpretation','PROBABLE',text,offset,'UNCERTAIN')
            # A probable interpretation itself cannot support a definite seizure.
            if not any(f['field']=='seizure_recorded' and f['value'] is True and 'wahrscheinlich' not in f['source_text_short'] for f in findings):
                values['seizure_recorded']=None;findings=[f for f in findings if f['field']!='seizure_recorded']
        if re.search(r'\bAphasi',text,re.I):
            add('clinical_correlate','APHASIA',text,offset,'UNCERTAIN' if re.search(r'Anamnese|\?',text,re.I) else 'ESTABLISHED')
            if re.search(r'Klinisch\s+Aphasie',text,re.I):add('clinical_correlate_present',True,text,offset)
        if re.search(r'klinisch\s+rhythmisch\w*\s+Zuckung|assoziiert\w*\s+subtil\w*\s+Entäußerung',text,re.I):
            add('clinical_correlate','RHYTHMIC_SUBTLE_MOVEMENTS',text,offset);add('clinical_correlate_present',True,text,offset)
        if re.search(r'nicht\s+vollständig\s+durchbrochen|geringgradig\w*\s+Attenuierung',text,re.I):add('response_to_ASM','NOT_FULLY_TERMINATED' if 'durchbrochen' in text else 'PARTIAL',text,offset)
    for target,rows in [('electrographic_status_epilepticus',states),('NCSE',ncse_states)]:
        current=next((r for r in rows if r[0]=='ESTABLISHED'),None) or next((r for r in rows if r[0]=='NEGATED'),None) or next(iter(rows),None)
        if current:
            state,text,offset=current
            add(target,True if state=='ESTABLISHED' else False if state=='NEGATED' else None,text,offset,state)
            if target=='NCSE':
                add('NCSE_assertion',state,text,offset,state)
                if state=='ESTABLISHED':
                    add('status_type','NONCONVULSIVE_STATUS_EPILEPTICUS',text,offset)
                    add('status_side',v11.side(text),text,offset)
                    add('status_region',v11.compact_region(v11.regions(text)),text,offset)
        elif target=='NCSE' and any(r[0]=='NEGATED' for r in states):
            _,text,offset=next(r for r in states if r[0]=='NEGATED');add('NCSE',False,text,offset,'NEGATED');add('NCSE_assertion','NEGATED',text,offset,'NEGATED')
    if values['NCSE'] is True:
        values['NCSE_OCCURRED_DURING_RECORDING']=True
        values['status_at_final_segment']='PRESENT'
    elif values['electrographic_status_epilepticus'] is False:values['status_at_final_segment']='ABSENT'
    # Preserve structured template content verbatim alongside typed source facts.
    text=report['eeg_report_text']
    for field,pattern in {
        'epileptiform_burden_raw':r'Epileptiforme\s+Aktivität[^:\n]*:\s*([^\n]+)',
        'ASM_trial':r'EEG\s+und\s+klinische\s+Verbesserung:\s*([^\n]+)',
        'clinical_response':r'Klinische\s+(?:Antwort|Verbesserung):\s*([^\n]+)',
        'EEG_response':r'EEG[- ](?:Antwort|Verbesserung):\s*([^\n]+)',
        'video_availability':r'Video:\s*([^\n]+)',
        'clinical_testing_performed':r'Klinische\s+Testung:\s*([^\n]+)',
        'posterior_dominant_rhythm':r'([^\n]*posterior\w*\s+Lokalisationsschwerpunkt[^\n]*)',
        'background_rhythm_raw':r'(Basisrhythmus[^;\n]*)',
    }.items():
        m=re.search(pattern,text,re.I)
        if m:add(field,m[1].strip(),m[0],m.start())
    m=re.search(r'NCSE\s+Rating\s+Scale[^:\n]*:\s*(\d+(?:[,.]\d+)?)',text,re.I)
    if m:add('NCSE_rating_scale',float(m[1].replace(',','.')),m[0],m.start())
    m=re.search(r'Evolutionsmuster[^:\n]*:\s*([^\n]+)',text,re.I)
    if m:
        parts=m[1].split('/');states3=['ABSENT' if re.search(r'keine|nein',p,re.I) else 'PRESENT' if re.search(r'ja|vorhanden',p,re.I) else 'UNKNOWN' for p in parts]
        states3=(states3+['UNKNOWN']*3)[:3];eid=f'{report["report_occurrence_id"]}:v12:evolution:{report["report_start"]+m.start()}'
        add('evolution',[dict(zip(('frequency','spatial','morphological'),states3),evidence_ids=[eid])],m[0],m.start())
    periods=[]
    for m in quality:
        time=re.search(r'\d{1,2}:\d{2}',m[0])[0];eid=f'{report["report_occurrence_id"]}:v12:assessability_periods:{report["report_start"]+m.start()}'
        row=dict(from_time=time,assessability='NOT_ASSESSABLE',event='ELECTRODES_REMOVED' if 'Elektroden' in m[0] else 'ARTIFACT_LIMITATION',evidence_ids=[eid],source_text_short=m[0])
        periods.append(row)
        add('assessability_periods',[row],m[0],m.start(),'NOT_ASSESSABLE')
    if quality:
        # Record all periods in one editable finding, retaining per-period links.
        values['assessability_periods']=periods
    corrected={f['field'] for f in findings if f.get('method')=='DETERMINISTIC_EEG_1.2'}&{'periodic_discharge_present','periodic_discharge_type','pattern_frequency_hz'}
    findings=[f for f in findings if f['field'] not in corrected or f.get('method')=='DETERMINISTIC_EEG_1.2']
    for finding in findings:finding['eeg_schema_version']='1.2'
    return values,findings
