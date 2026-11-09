"""Strict episode contract for Granite; research screens are deliberately absent."""
import copy

VERSION='0.1'
TRI=['TRUE','FALSE','UNKNOWN']
ASSERTIONS=['ESTABLISHED','NEGATED','UNCERTAIN','HISTORICAL','CONDITIONAL','NOT_ASSESSABLE']
COURSES=['TRANSIENT_FULLY_RESOLVED','PERSISTENT','PROGRESSIVE','MIXED','FLUCTUATING_WITHOUT_CLEAR_RESOLUTION','UNKNOWN']
DOMAINS=['SENSORY','MOTOR','LANGUAGE','VISUAL','COGNITIVE','GAIT_ATAXIA','CONSCIOUSNESS','DIZZINESS','OTHER']
TRI_FIELDS='fully_resolved spreading_progression recurrent stereotyped impaired_awareness loss_of_consciousness automatisms tonic_posturing clonic_jerking forced_head_eye_deviation speech_arrest post_event_confusion post_event_aphasia todd_paresis'.split()
ENUMS=dict(assertion=ASSERTIONS,temporal_course=COURSES,duration_precision=['EXACT','RANGE','APPROXIMATE','UNKNOWN'],focality=['FOCAL','NONFOCAL','MIXED','UNKNOWN'],
    side=['LEFT','RIGHT','BILATERAL','MIDLINE','UNKNOWN'],onset_type=['ABRUPT','GRADUAL','UNKNOWN'],
    spread_type=['SOMATOTOPIC_CONTIGUOUS','SEQUENTIAL_NONCONTIGUOUS','VISUAL_SPREAD','OTHER','UNKNOWN'],
    persistent_course=['STABLE_RESIDUAL','PROGRESSIVE','UNKNOWN'],historical_diagnosis=['TFNE','TIA','ISCHEMIC_STROKE','SEIZURE','EPILEPSY','MIGRAINE','ICH','UNCLEAR','OTHER','NONE_DOCUMENTED'])
LIST_ENUMS=dict(symptom_domains=DOMAINS,positive_symptoms=['POSITIVE_SENSORY','POSITIVE_MOTOR','POSITIVE_VISUAL'],negative_symptoms=['NEGATIVE_SENSORY','NEGATIVE_MOTOR','NEGATIVE_VISUAL'],
    body_regions=['FACE','MOUTH','TONGUE','HAND','FINGERS','ARM','LEG','FOOT','HEMISOMA','OTHER'],persistent_deficit_domains=[d for d in DOMAINS if d not in ('CONSCIOUSNESS','DIZZINESS')])
TEXT_FIELDS=['symptom_description','episode_date_text','duration_text','episode_frequency_text','ictal_behavior_description']
NUMBERS=['duration_min_minutes','duration_max_minutes','episode_count']


def defaults():
    return _defaults()


def _defaults():
    out={**{k:'UNKNOWN' for k in ENUMS},**dict.fromkeys(TRI_FIELDS,'UNKNOWN'),**{k:[] for k in LIST_ENUMS},**dict.fromkeys(TEXT_FIELDS,None),**dict.fromkeys(NUMBERS,None)}
    out.update(episode_index=1,assertion='ESTABLISHED',historical_diagnosis='NONE_DOCUMENTED',spread_sequence=[],symptom_components=[],evidence={})
    return out


def response_schema(span_ids):
    props={k:{'enum':v} for k,v in ENUMS.items()}
    props.update({k:{'enum':TRI} for k in TRI_FIELDS})
    props.update({k:dict(type='array',items={'enum':v}) for k,v in LIST_ENUMS.items()})
    props.update({k:dict(type=['string','null']) for k in TEXT_FIELDS})
    props.update({k:dict(type=['number','null'],minimum=0) for k in NUMBERS})
    props.update(episode_index=dict(type='integer',minimum=1),spread_sequence=dict(type='array',items=dict(type='string')),
        symptom_components=dict(type='array',items=dict(type='object',additionalProperties=False,required=['domain','temporal_course','fully_resolved','source_span_ids'],properties=dict(
            domain={'enum':DOMAINS},temporal_course={'enum':COURSES},fully_resolved={'enum':TRI},source_span_ids=dict(type='array',minItems=1,items={'enum':span_ids})))),
        evidence=dict(type='object',additionalProperties=False,required=sorted(_defaults().keys()-{'episode_index','evidence'}),properties={
            field:dict(type='object',additionalProperties=False,required=['source_span_ids','assertion'],properties=dict(
                source_span_ids=dict(type='array',items={'enum':span_ids}),assertion={'enum':ASSERTIONS}))
            for field in sorted(_defaults().keys()-{'episode_index','evidence'})}))
    episode=dict(type='object',additionalProperties=False,required=list(props),properties=props)
    return dict(type='object',additionalProperties=False,required=['episodes'],properties=dict(episodes=dict(type='array',items=episode)))


def validate(raw):
    if not isinstance(raw,dict) or set(raw)!={'episodes'} or not isinstance(raw['episodes'],list):raise ValueError('Expected only episodes array; Granite must not output TFNE screens')
    for episode in raw['episodes']:
        if not isinstance(episode,dict) or set(episode)!=set(_defaults()):raise ValueError('Episode schema mismatch')
        for field,allowed in {**ENUMS,**{k:TRI for k in TRI_FIELDS}}.items():
            if episode[field] not in allowed:raise ValueError('Invalid '+field)
        for field,allowed in LIST_ENUMS.items():
            if not isinstance(episode[field],list) or any(v not in allowed for v in episode[field]):raise ValueError('Invalid '+field)
        for field in TEXT_FIELDS:
            if episode[field] is not None and not isinstance(episode[field],str):raise ValueError('Invalid text '+field)
        for field in NUMBERS:
            value=episode[field]
            if value is not None and (type(value) not in (int,float) or value<0):raise ValueError('Invalid number '+field)
        if type(episode['episode_index']) is not int or episode['episode_index']<1:raise ValueError('Invalid episode index')
        lo,hi=episode['duration_min_minutes'],episode['duration_max_minutes']
        if lo is not None and hi is not None and lo>hi:raise ValueError('Reversed duration')
        if not isinstance(episode['spread_sequence'],list) or any(not isinstance(v,str) for v in episode['spread_sequence']):raise ValueError('Invalid spread sequence')
        if not isinstance(episode['evidence'],dict) or not isinstance(episode['symptom_components'],list):raise ValueError('Invalid evidence/components')
    return copy.deepcopy(raw)
