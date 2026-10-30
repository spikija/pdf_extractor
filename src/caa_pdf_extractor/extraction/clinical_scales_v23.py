"""Explicit scale observations from cached sections; no numeric reconstruction."""
from dataclasses import dataclass, asdict
import re
from datetime import datetime

VERSION='CLINICAL_SCALES_V2.3'
DATE=re.compile(r'(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)')
SCORE=re.compile(r'(?<!\w)(?P<scale>NIHSS|(?:(?:pr[äae]+|pre)[ -]*)?m\s*RS|STESS|CFS|MoCA)\s*(?:(?:bei\s+)?(?:Aufnahme|Entlassung|vor\s+(?:Schlaganfall|Ereignis|Aufnahme)|nach\s+3\s+Monaten)\s*)?[:=]?\s*(?:von\s*)?(?P<value>\d{1,2})(?!\d)',re.I)
STROKE=re.compile(r'isch[äa]m\w*.*(?:infarkt|insult|stroke)|(?:intrazerebral|intracerebral|intra[ -]?cerebral)\w*\s+(?:blutung|h[äa]morrhagie)|\bTIA\b|transitorisch.*attacke',re.I)
TEMP=re.compile(r'pr[äae]+[ -]*m\s*RS|pre[ -]*m\s*RS|premorbid|baseline|vor\s+(?:Schlaganfall|Ereignis|Aufnahme)|(?:bei\s+)?Aufnahme|(?:bei\s+)?Entlassung(?:s-)?|initial|admission|discharge|Verlegung|verlegen|follow[ -]?up|Nachkontrolle|Kontrolle|90-Tage-|3-Monats-|nach\s+3\s+Monaten',re.I)

def temporal(marker):
    if re.search(r'pr[äae]|pre|baseline|vor\s',marker,re.I):return 'PRE_EVENT'
    if re.search(r'Entlass|discharge|verleg',marker,re.I):return 'DISCHARGE'
    if re.search(r'Aufnahme|initial|admission',marker,re.I):return 'ADMISSION'
    return 'FOLLOWUP'

def dates(text):
    result=[]
    for m in DATE.finditer(text):
        try:result.append(datetime.strptime(m[0],'%d.%m.%Y').date().isoformat())
        except ValueError:pass
    return result

@dataclass
class ClinicalScaleAssessment:
    assessment_id:str
    patient_id:str
    case_id:str|None
    encounter_id:str|None
    event_id:str|None
    scale_name:str
    timepoint:str
    assessment_date:str|None
    value:int|float|None
    source:str
    assessability:str
    source_document_id:str
    page:int|None
    section_id:str
    evidence_id:str
    evidence_quote:str
    confidence:str='HIGH'
    review_required:bool=False
    rule_version:str=VERSION

def extract_section(text,*,patient_id,document_id,section_id,page=None,admission_date=None,discharge_date=None,case_id=None):
    """Preserve every explicit observation, including unlinked/untimed ones.

    A new stroke heading resets temporal context. A temporal qualifier can
    propagate only to following lines in the same section and stroke block.
    Date of a historical stroke is not used as admission/discharge date.
    """
    rows=[];episodes=[];event=None;timepoint=None;offset=0
    for line in text.splitlines():
        if STROKE.search(line):
            timepoint=None;ds=dates(line)
            event=dict(event_id=f'V23:{patient_id}:{document_id}:{section_id}:{offset}',event_date=ds[0] if ds else None,
                event_type='ISCHEMIC_STROKE' if re.search(r'isch[äa]m',line,re.I) else 'TIA' if re.search(r'TIA|attacke',line,re.I) else 'INTRACEREBRAL_HEMORRHAGE',
                patient_id=str(patient_id),source_document_id=str(document_id),section_id=section_id,page=page,
                vascular_territory='MCA' if re.search(r'media|\bACM\b|\bMCA\b',line,re.I) else 'UNKNOWN',
                side='LEFT' if re.search(r'links|sinistra|\bleft\b',line,re.I) else 'RIGHT' if re.search(r'rechts|dextra|\bright\b',line,re.I) else 'UNKNOWN',
                etiology='UNKNOWN',mechanical_thrombectomy=None,iv_thrombolysis=None,tici=None,treatment_date=None,evidence_id=f'{document_id}:{section_id}:{offset}')
            episodes.append(event)
        if event and re.search(r'[ÄäAa]tiologie\s*:',line):
            cause=line.split(':',1)[-1].strip()
            event['etiology']='UNKNOWN' if re.search(r'offen|unklar|unknown|nicht\s+klassifiziert',cause,re.I) else cause[:80]
        if event and re.search(r'Thrombolyse|thrombolysis|\bLyse\b',line,re.I):
            event['iv_thrombolysis']=not bool(re.search(r'kein|nicht|ohne|no ',line,re.I))
        if event and re.search(r'thrombektomie|thrombectomy',line,re.I):
            if not re.search(r'kein|nicht|ohne|no ',line,re.I):event['mechanical_thrombectomy']=True
            tici=re.search(r'TICI\s*[:=]?\s*([0-3](?:[abc])?)',line,re.I)
            if tici:event['tici']=tici[1]
            ds=dates(line)
            if ds:event['treatment_date']=ds[0]
        markers=list(TEMP.finditer(line))
        for m in SCORE.finditer(line):
            raw=m['scale'];scale='mRS' if re.search(r'm\s*rs',raw,re.I) else raw.upper()
            val=int(m['value']);limit={'NIHSS':42,'mRS':6,'STESS':6,'CFS':9,'MOCA':30}[scale]
            local=line[max(0,m.start()-45):m.end()+20]
            preceding=[t for t in markers if t.start()<=m.start() and not (t.end()<m.start() and re.search(r'pr[äae]+[ -]*m\s*RS|pre[ -]*m\s*RS',t[0],re.I))]
            # Postfix "NIHSS bei Aufnahme: 10" is owned by this score.
            owned=next((t for t in markers if m.start()<=t.start()<m.end()),None)
            marker=owned or (preceding[-1] if preceding else None)
            if re.search(r'pr[äae]+|pre',raw,re.I):tp='PRE_EVENT'
            elif marker:tp=temporal(marker[0])
            else:tp=timepoint or 'OTHER'
            # A normal pre-mRS marker does not imply the next NIHSS is pre-event.
            if tp=='PRE_EVENT' and not re.search(r'pr[äae]+|pre',raw,re.I):
                # The pre-mRS qualifier belongs only to its own score.
                if marker and re.search(r'm\s*RS',marker[0],re.I) and marker.start()<m.start():tp='OTHER'
            if tp=='PRE_EVENT' and scale!='mRS':tp='OTHER'
            day=admission_date if tp in ('PRE_EVENT','ADMISSION') else discharge_date if tp=='DISCHARGE' else None
            owned_dates=dates(line)
            if len(set(owned_dates))==1 and tp!='PRE_EVENT':day=owned_dates[0]
            if tp=='ADMISSION' and day and admission_date and day>admission_date:
                tp='OTHER'  # Explicit later observation is not an admission score.
            invalid=val>limit or (scale=='CFS' and val<1)
            eid=f'{document_id}:{section_id}:{offset+m.start()}'
            row=ClinicalScaleAssessment(eid,str(patient_id),case_id,f'{patient_id}:{admission_date}' if admission_date else None,
                event['event_id'] if event else None,scale,tp,day,None if invalid else val,'EXPLICIT',
                'INVALID_SOURCE_VALUE' if invalid else 'ASSESSABLE',str(document_id),page,section_id,eid,local[:160],review_required=invalid or tp=='OTHER' or not event)
            record=asdict(row);record['event_date']=event['event_date'] if event else None;record['source_offset']=offset+m.start()
            rows.append(record)
        if markers:
            context_markers=[t for t in markers if temporal(t[0])!='PRE_EVENT']
            last=context_markers[-1][0] if context_markers else markers[-1][0]
            timepoint=temporal(last) if temporal(last)!='PRE_EVENT' else None
        offset+=len(line)+1
    for e in episodes:
        e.update(stroke_date=e['event_date'],stroke_type=e['event_type'],acute_treatment='BOTH' if e['mechanical_thrombectomy'] and e['iv_thrombolysis'] else 'MECHANICAL_THROMBECTOMY' if e['mechanical_thrombectomy'] else 'IV_THROMBOLYSIS' if e['iv_thrombolysis'] else 'NONE' if e['mechanical_thrombectomy'] is False and e['iv_thrombolysis'] is False else 'UNKNOWN',
            reperfusion_grade_system='TICI' if e['tici'] else None,reperfusion_grade=e['tici'],procedure_date=e['treatment_date'])
    return rows,episodes

def resolve_observations(rows):
    """No inferred value may overwrite an explicit value; conflicts stay null."""
    groups={}
    for r in rows:
        # Unlinked, undated observations from different documents cannot conflict
        # with each other or be merged as one unknown historical stroke.
        scope=r.get('encounter_id') or 'DOC:'+str(r.get('source_document_id'))
        groups.setdefault((r.get('patient_id'),r.get('event_id'),scope,r['scale_name'],r['timepoint']),[]).append(r)
    out=[]
    for key,items in groups.items():
        chosen=[r for r in items if r['source']=='EXPLICIT'] or items
        values={r['value'] for r in chosen if r['value'] is not None}
        out.append(dict(patient_id=key[0],event_id=key[1],encounter_id=items[0].get('encounter_id'),scope_id=key[2],scale_name=key[3],timepoint=key[4],
            value=next(iter(values)) if len(values)==1 else None,source=chosen[0]['source'],conflict=len(values)>1,
            source_evidence_ids=[r['evidence_id'] for r in chosen],review_required=len(values)!=1 or any(r.get('review_required') for r in chosen)))
    return out
