"""EEG 1.1: source-scoped localization, relationships and explicit evidence states.

Exam metadata retains eeg_date/eeg_date_precision, with no recording-end field.
"""
import copy
from . import eeg_schema as v1

VERSION='1.1'
REGIONS=('FRONTAL','CENTRAL','TEMPORAL','PARIETAL','OCCIPITAL','HEMISPHERIC')
SIDES=v1.SIDES
EXTENTS=('ANTERIOR','MID','POSTERIOR','MID_TO_ANTERIOR','OTHER','UNKNOWN')
CONTINUITY=('CONTINUOUS','NEAR_CONTINUOUS','INTERMITTENT','UNKNOWN')
ASYMMETRY=('LEFT_PREDOMINANT','RIGHT_PREDOMINANT','SYMMETRIC','UNKNOWN')
RELATIONSHIPS=('PRIMARY','SECONDARY_INDEPENDENT','SPREAD','UNKNOWN')
FREQUENCY=('OCCASIONAL','FREQUENT','ABUNDANT','UNKNOWN')
SEVERITY=('MILD','MODERATE','MARKED','UNKNOWN')
EVIDENCE=('CONFIRMED','EXPLICITLY_ABSENT','NO_DEFINITE_EVIDENCE','UNCERTAIN','CONFLICTING_EVIDENCE','UNKNOWN')
BOOL_FIELDS=v1.BOOL_FIELDS+('epileptiform_confirmed','spread_present','artifact_present','secondary_bilateral_synchrony')
ENUMS={**v1.ENUMS,'assessability':('FULL','LIMITED','NOT_ASSESSABLE','UNKNOWN'),
 'rhythmic_delta_type':('FIRDA','TIRDA','LRDA','GRDA','OTHER','UNKNOWN'),
 'dominant_focus_side':SIDES,'dominant_focus_asymmetry':ASYMMETRY,'spread_side':SIDES,
 'focal_slowing_asymmetry':ASYMMETRY,'pattern_asymmetry':ASYMMETRY,
 'focal_slowing_continuity':CONTINUITY,'background_slowing_continuity':CONTINUITY,
 'reactivity_stimulus':('VERBAL','TACTILE','EXTERNAL','MULTIPLE','UNKNOWN'),
 'artifact_severity':SEVERITY,'epileptiform_evidence_status':EVIDENCE,'ictal_activity_status':EVIDENCE,
 'clinical_signs_of_NCSE':('PRESENT','ABSENT','UNKNOWN'),
 'morphology_modifier':('BROAD','OTHER','UNKNOWN'),'epileptiform_frequency':FREQUENCY,
 'anterior_posterior_extent':EXTENTS,
 'generalized_slowing_maximum':('FRONTAL','BIFRONTAL','TEMPORAL','OTHER','UNKNOWN'),
}
TEXT_FIELDS=v1.TEXT_FIELDS+('extent_description',)
REGION_LISTS=('focal_slowing_regions','dominant_focus_regions','spread_regions','epileptiform_regions')
OBJECT_LISTS=('focal_abnormalities','slow_patterns','rhythmic_patterns','reactivity_findings')
GROUPS=copy.deepcopy(v1.GROUPS)
GROUPS['Focus relationships']=['dominant_focus_side','dominant_focus_regions','dominant_focus_asymmetry','spread_present','spread_side','spread_regions','focal_abnormalities']
GROUPS['Slowing']+=['focal_slowing_regions','focal_slowing_asymmetry','focal_slowing_continuity','background_slowing_continuity','generalized_slowing_maximum','slow_patterns']
GROUPS['Epileptiform findings']+=['epileptiform_confirmed','epileptiform_evidence_status','morphology_modifier','epileptiform_frequency','epileptiform_regions','secondary_bilateral_synchrony']
GROUPS['Periodic / rhythmic patterns']+=['pattern_asymmetry','rhythmic_patterns']
GROUPS['Seizure / status']+=['ictal_activity_status','clinical_signs_of_NCSE']
GROUPS['Reactivity']+=['reactivity_stimulus','reactivity_findings']
GROUPS['Assessability / artifacts']=['assessability','artifact_present','artifact_type','artifact_severity']


def defaults():
    return {**v1.defaults(),**dict.fromkeys(BOOL_FIELDS,None),**{k:'UNKNOWN' for k in ENUMS},
        **dict.fromkeys(TEXT_FIELDS,None),**{k:[] for k in REGION_LISTS+OBJECT_LISTS+('artifact_type',)}}


def _list(value,allowed):
    if not isinstance(value,list) or any(not isinstance(v,str) or v not in allowed for v in value) or len(set(value))!=len(value):raise ValueError('Invalid EEG list')


def validate_value(field,value):
    if field in ENUMS:
        if value not in ENUMS[field]:raise ValueError('Invalid EEG '+field)
    elif field in BOOL_FIELDS:
        if value is not None and type(value) is not bool:raise ValueError('EEG boolean requires true/false/null')
    elif field in REGION_LISTS:_list(value,REGIONS)
    elif field=='artifact_type':_list(value,('MUSCLE','MOVEMENT','ELECTRODE','OTHER'))
    elif field in OBJECT_LISTS:
        if not isinstance(value,list):raise ValueError('EEG detailed fields require a JSON list')
        for row in value:validate_object(field,row)
    elif field in TEXT_FIELDS:
        if value is not None and not isinstance(value,str):raise ValueError('EEG text requires string/null')
    else:v1.validate_value(field,value)
    return value


def validate_object(field,row):
    if not isinstance(row,dict):raise ValueError('Invalid structured EEG finding')
    common={'side':SIDES,'primary_region':REGIONS+('UNKNOWN',),'anterior_posterior_extent':EXTENTS,
        'continuity':CONTINUITY,'frequency':FREQUENCY,'severity':SEVERITY,'asymmetry':ASYMMETRY}
    common['assertion']=('ESTABLISHED','NEGATED','UNCERTAIN','NOT_ASSESSABLE')
    allowed=set(common)|{'regions','extent_description','frequency_hz','evidence_ids','source_text_short'}
    if field=='focal_abnormalities':
        common.update(finding_type=('SLOWING','EPILEPTIFORM'),relationship=RELATIONSHIPS)
        allowed|={'focal_abnormality_id','finding_type','relationship','spread_to','epileptiform_component','secondary_bilateral_synchrony'}
        required={'focal_abnormality_id','finding_type','side','regions','primary_region','anterior_posterior_extent','continuity','frequency','severity','epileptiform_component','relationship','asymmetry','secondary_bilateral_synchrony','spread_to','evidence_ids'}
    elif field in ('slow_patterns','rhythmic_patterns'):
        common.update(kind=('FOCAL_SLOWING','GENERALIZED_SLOWING','RHYTHMIC_DELTA','RHYTHMIC_THETA_DELTA'),distribution=('FOCAL','BILATERAL','GENERALIZED','UNKNOWN'),
            maximum=('FRONTAL','BIFRONTAL','TEMPORAL','OTHER','UNKNOWN'),rhythmic_delta_type=ENUMS['rhythmic_delta_type'],modern_pattern_family=('GRDA','LRDA','UNKNOWN'))
        allowed|={'pattern_id','kind','distribution','maximum','rhythmic_delta_type','modern_pattern_family','source_term'}
        required={'pattern_id','kind','distribution','maximum','rhythmic_delta_type','modern_pattern_family','side','regions','primary_region','asymmetry','evidence_ids'}
    else:
        common={'stimulus':('VERBAL','TACTILE','EXTERNAL','UNKNOWN'),'response':('PRESENT','ABSENT','NOT_TESTED','UNKNOWN'),'assertion':('ESTABLISHED','NEGATED','UNCERTAIN','NOT_ASSESSABLE')}
        allowed=set(common)|{'evidence_ids','source_text_short'}
        required={'stimulus','response','evidence_ids'}
    if set(row)-allowed:raise ValueError('Unknown detailed EEG fields')
    if required-set(row):raise ValueError('Missing required detailed EEG fields')
    for key,options in common.items():
        if key in row and row[key] not in options:raise ValueError('Invalid detailed EEG '+key)
    for key in ('epileptiform_component','secondary_bilateral_synchrony'):
        if key in row and row[key] is not None and type(row[key]) is not bool:raise ValueError('Invalid detailed EEG boolean')
    if 'regions' in row:_list(row['regions'],REGIONS)
    if 'frequency_hz' in row:v1.validate_value('pattern_frequency_hz',row['frequency_hz'])
    for key in ('extent_description','source_text_short','focal_abnormality_id','pattern_id','source_term'):
        if key in row and row[key] is not None and not isinstance(row[key],str):raise ValueError('Invalid detailed EEG text')
    if not isinstance(row.get('evidence_ids'),list) or not row['evidence_ids'] or not all(isinstance(e,str) and e for e in row['evidence_ids']):raise ValueError('Detailed EEG findings require source evidence')
    if 'spread_to' in row:
        if not isinstance(row['spread_to'],list):raise ValueError('Invalid spread links')
        for target in row['spread_to']:
            if set(target)!={'side','regions','evidence_ids'} or target['side'] not in SIDES:raise ValueError('Invalid spread target')
            _list(target['regions'],REGIONS)
            if not target['evidence_ids'] or not all(isinstance(e,str) for e in target['evidence_ids']):raise ValueError('Spread requires evidence')
