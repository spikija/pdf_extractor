"""EEG 1.2 finding and segment contract. No recording-end exam metadata."""
import copy
from . import eeg_schema_v11 as v11

VERSION='1.2'
BOOL_FIELDS=v11.BOOL_FIELDS+('NCSE','continuous','video','mobile','ICU_recording','NCSE_OCCURRED_DURING_RECORDING','clinical_correlate_present')
ENUMS={**v11.ENUMS,'recording_type':('ROUTINE_EEG','CONTINUOUS_EEG','VIDEO_EEG','UNKNOWN'),
    'status_type':('NONCONVULSIVE_STATUS_EPILEPTICUS','STATUS_EPILEPTICUS','UNKNOWN'),
    'NCSE_assertion':('ESTABLISHED','NEGATED','UNCERTAIN','HISTORICAL','UNKNOWN'),
    'status_at_final_segment':('PRESENT','ABSENT','UNKNOWN'),
    'status_side':v11.SIDES,'status_region':v11.v1.REGIONS,
    'clinical_correlate':('APHASIA','RHYTHMIC_SUBTLE_MOVEMENTS','OTHER','UNKNOWN'),
    'response_to_ASM':('PARTIAL','NOT_FULLY_TERMINATED','TERMINATED','NO_RESPONSE','NOT_APPLIED','UNKNOWN'),
    'focal_seizure_interpretation':('PROBABLE','DEFINITE','UNKNOWN'),
    'reactivity_stimulus':v11.ENUMS['reactivity_stimulus']+('PAINFUL',)}
TEXT_FIELDS=v11.TEXT_FIELDS+('periodic_frequency_raw','epileptiform_burden_raw','ASM_trial','clinical_response','EEG_response','video_availability','clinical_testing_performed','posterior_dominant_rhythm','background_rhythm_raw')
OBJECT_LISTS=v11.OBJECT_LISTS+('assessability_periods','evolution')
GROUPS=copy.deepcopy(v11.GROUPS)
GROUPS['NCSE / treatment']=['NCSE','NCSE_assertion','status_type','status_side','status_region','clinical_correlate','response_to_ASM','NCSE_OCCURRED_DURING_RECORDING','status_at_final_segment','focal_seizure_interpretation']
GROUPS['cEEG details']=['periodic_frequency_raw','epileptiform_burden_raw','evolution','ASM_trial','clinical_response','EEG_response','NCSE_rating_scale','video_availability','clinical_testing_performed','assessability_periods']


def defaults():
    return {**v11.defaults(),**dict.fromkeys(BOOL_FIELDS,None),**{k:'UNKNOWN' for k in ENUMS},
        **dict.fromkeys(TEXT_FIELDS,None),'evolution':[],'assessability_periods':[],'NCSE_rating_scale':None}


def validate_value(field,value):
    if field in BOOL_FIELDS:
        if value is not None and type(value) is not bool:raise ValueError('EEG boolean requires true/false/null')
    elif field in ENUMS:
        if value not in ENUMS[field]:raise ValueError('Invalid EEG '+field)
    elif field in TEXT_FIELDS:
        if value is not None and not isinstance(value,str):raise ValueError('EEG text requires string/null')
    elif field=='NCSE_rating_scale':
        if value is not None and (type(value) not in (int,float) or value<0):raise ValueError('Invalid source NCSE rating scale')
    elif field in ('assessability_periods','evolution'):
        if not isinstance(value,list):raise ValueError('EEG temporal fields require a JSON list')
        for row in value:
            if not isinstance(row,dict) or not row.get('evidence_ids'):raise ValueError('Temporal EEG detail needs evidence')
            if field=='assessability_periods':
                if set(row)-{'from_time','assessability','event','evidence_ids','source_text_short'} or row.get('assessability') not in ENUMS['assessability']:raise ValueError('Invalid EEG assessability period')
            else:
                if set(row)-{'frequency','spatial','morphological','evidence_ids'} or any(row.get(k) not in ('PRESENT','ABSENT','UNKNOWN') for k in ('frequency','spatial','morphological')):raise ValueError('Invalid EEG evolution')
    elif field=='reactivity_findings':
        for row in value:
            checked=dict(row)
            if checked.get('stimulus')=='PAINFUL':checked['stimulus']='TACTILE'
            v11.validate_object(field,checked)
    else:v11.validate_value(field,value)
    return value
