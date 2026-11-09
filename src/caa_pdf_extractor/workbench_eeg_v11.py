"""EEG 1.1 review validation and per-object overlays; all raw artifacts stay immutable."""
import copy
import re
from datetime import date
from .extraction.eeg_schema_v11 import defaults,validate_value,OBJECT_LISTS,GROUPS
from .extraction.eeg_extraction_v11 import derive,clinical_summary

DERIVED_FOCUS=('focal_slowing_present','focal_slowing_side','focal_slowing_region','focal_slowing_regions','focal_slowing_continuity','focal_slowing_asymmetry','dominant_focus_side','dominant_focus_regions','dominant_focus_asymmetry','spread_present','spread_side','spread_regions','anterior_posterior_extent','extent_description','lateralized_abnormality','lateralized_side','lateralized_abnormality_type','epileptiform_side','epileptiform_regions','epileptiform_region','epileptiform_distribution')


def identity(row):return row.get('focal_abnormality_id',row.get('pattern_id',tuple(row['evidence_ids'])))


def validate_edits(raw,edits):
    if not isinstance(edits,dict):raise ValueError('Corrections must be a JSON object')
    if 'finding_id' in raw:
        if set(edits)!={'value'}:raise ValueError('Finding corrections require only value')
        values={raw['field']:edits['value']}
    else:
        values=edits
        if set(values)&(set(DERIVED_FOCUS)|{'rhythmic_patterns'}):raise ValueError('Edit focal_abnormalities or slow_patterns to change their derived summaries')
    for field,value in values.items():
        if field=='eeg_date':
            if value is not None:
                if not isinstance(value,str) or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',value):raise ValueError('EEG dates require ISO year/month/day or null')
                date.fromisoformat(value+'-01-01' if len(value)==4 else value+'-01' if len(value)==7 else value)
        elif field=='eeg_date_precision':
            if value not in ('DAY','MONTH','YEAR','UNKNOWN'):raise ValueError('Invalid date precision')
        else:validate_value(field,value)
        if field in OBJECT_LISTS:
            original=raw['value'] if 'finding_id' in raw else raw[field]
            lookup={identity(r):r for r in original}
            if len(value)!=len(lookup) or {identity(r) for r in value}!=set(lookup):raise ValueError('Keep detailed finding identities; use REJECT or UNCERTAIN for removal')
            if any(row['evidence_ids']!=lookup[identity(row)]['evidence_ids'] for row in value):raise ValueError('Source evidence identities are immutable')
    return edits


def _refresh(result,changed,raw):
    unknown=defaults()
    if changed=='focal_abnormalities':
        explicit_absence=result.get('focal_slowing_present') is False
        generalized_ep=result.get('epileptiform_distribution')=='GENERALIZED'
        for field in DERIVED_FOCUS:result[field]=copy.deepcopy(unknown[field])
        if explicit_absence:result['focal_slowing_present']=False
        if generalized_ep:result['epileptiform_distribution']='GENERALIZED'
        available={e for f in result['focal_abnormalities'] if f['relationship']=='SPREAD' for e in f['evidence_ids']}
        for f in result['focal_abnormalities']:f['spread_to']=[t for t in f['spread_to'] if set(t['evidence_ids'])&available]
    if changed=='slow_patterns':
        result['generalized_slowing_present']=False if result.get('generalized_slowing_present') is False else None
        result['rhythmic_delta_activity_present']=False if result.get('rhythmic_delta_activity_present') is False else None
        result['rhythmic_delta_type']='UNKNOWN';result['pattern_asymmetry']='UNKNOWN'
        result['rhythmic_patterns']=[p for p in result['slow_patterns'] if p['kind'].startswith('RHYTHMIC')]
        types={p['rhythmic_delta_type'] for p in result['rhythmic_patterns'] if p['rhythmic_delta_type']!='UNKNOWN'}
        if len(types)==1:result['rhythmic_delta_type']=next(iter(types))
    if changed=='reactivity_findings':result['reactivity_stimulus']='UNKNOWN';result['reactivity_to_external_stimuli']='UNKNOWN'
    derive(result)


def reviewed_exam(exam,findings,records,fingerprint):
    result=copy.deepcopy(exam);unknown=defaults()
    result.pop('eeg_date_end',None)  # Ignore legacy metadata without rewriting artifacts.
    entities={exam['eeg_exam_id']:exam,**{f['finding_id']:f for f in findings}}
    actions=[r for eid,r in records.items() if eid in entities and r.get('source_artifact_hash')==fingerprint and r['action']!='RESET']
    for record in sorted(actions,key=lambda r:r['updated_at']):
        raw=entities[record['entity_id']];action=record['action']
        if 'finding_id' in raw:
            field=raw['field']
            if action=='EDIT':value=validate_edits(raw,record['physician_value'])['value']
            else:value=copy.deepcopy(raw['value']) if action=='ACCEPT' else copy.deepcopy(unknown[field])
            if field in OBJECT_LISTS:
                target={identity(r) for r in raw['value']};others=[r for r in result[field] if identity(r) not in target]
                result[field]=others+value
                _refresh(result,field,exam)
            else:result[field]=value
        else:
            if action=='EDIT':updates=validate_edits(raw,{k:v for k,v in record['physician_value'].items() if k!='eeg_date_end'})
            elif action=='ACCEPT':updates={field:copy.deepcopy(raw[field]) for field in unknown}
            else:updates=copy.deepcopy(unknown)
            result.update(updates)
            for field in ('focal_abnormalities','slow_patterns','reactivity_findings'):
                if field in updates:_refresh(result,field,exam)
    summary=clinical_summary(result)
    result.update(eeg_primary_finding=summary['eeg_primary_finding'],clinical_phenotype=summary['clinical_phenotype'])
    return result
