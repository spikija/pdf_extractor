"""Mechanical support checks for numeric durations and component resolution."""
import copy
import re

WORDS={'ein':1,'eine':1,'einer':1,'einem':1,'einen':1,'zwei':2,'drei':3,'vier':4,'fünf':5,'sechs':6,'zehn':10,'fünfzehn':15,'halbe':.5,'halben':.5}
NUMBER=r'(?:\d+(?:[,.]\d+)?|eine[rnm]?|ein|zwei|drei|vier|fünf|sechs|zehn|fünfzehn|halbe[n]?)'
UNIT=r'(?:Sekunden?|Minuten?|Min\.?|Stunden?|Std\.?|Tage[n]?)'
EXPLICIT=re.compile(r'\b('+NUMBER+r')(?:\s*(?:bis|[-–])\s*('+NUMBER+r'))?\s*('+UNIT+r')\b',re.I)
FEATURES=dict(impaired_awareness=r'Bewusstseinsstörung|Bewusstseinstrüb|nicht\s+ansprechbar|eingeschränkt\w*\s+Bewusstsein',
    loss_of_consciousness=r'Bewusstseinsverlust|bewusstlos|Synkope',automatisms=r'Automatism',tonic_posturing=r'tonisch',clonic_jerking=r'Zuck|klon',
    forced_head_eye_deviation=r'(?:Kopf|Blick|Augen)[^.;]{0,35}(?:wendung|drehung|deviation)|Versiv',
    speech_arrest=r'Sprachblock|Sprechblock|Spracharrest|Sprachstopp|konnte[^.;]{0,20}nicht\s+sprechen',
    post_event_confusion=r'postiktal\w*[^.;]{0,30}Verwirr|Verwirr\w*[^.;]{0,30}postiktal|nach[^.;]{0,60}Verwirr',
    post_event_aphasia=r'postiktal\w*[^.;]{0,30}Aphasi|Aphasi\w*[^.;]{0,30}postiktal|nach[^.;]{0,60}Aphasi',todd_paresis=r'Todd|postiktal\w*[^.;]{0,25}Parese')


def positive_match(pattern,text):
    return any(not re.search(r'\b(?:kein\w*|nicht|ohne)\s+(?:\w+\s+){0,3}$',text[max(0,m.start()-70):m.start()],re.I) for m in re.finditer(pattern,text,re.I))


def supported_resolution(text):
    from .tfne_granite import RESOLUTION
    return any(not re.search(r'(?:nicht|unvollständig|nur\s+teilweise)\s*$',text[max(0,m.start()-45):m.start()],re.I) for m in RESOLUTION.finditer(text))


def duration_values(text):
    values=[]
    for match in EXPLICIT.finditer(text):
        def number(word):
            word=word.casefold()
            return WORDS[word] if word in WORDS else float(word.replace(',','.'))
        multiplier=1/60 if match[3].casefold().startswith('sek') else 60 if match[3].casefold().startswith(('st','std')) else 1440 if match[3].casefold().startswith('tag') else 1
        values.append((number(match[1])*multiplier,number(match[2] or match[1])*multiplier))
    return values


def validate_grounded(episode,index):
    episode=copy.deepcopy(episode);flags=[]
    def text(field):return ' '.join(e['evidence_text'] for e in episode['field_evidence'].get(field,[]))
    def abstain(field,reason):
        from .tfne_schema import NUMBERS,TEXT_FIELDS
        old=episode[field];episode[field]=None if field in NUMBERS+TEXT_FIELDS else 'UNKNOWN'
        flags.append(dict(episode_index=episode['episode_index'],field=field,raw_value=old,reason=reason))
    from .tfne_schema import TRI_FIELDS
    for field in TRI_FIELDS:
        if episode[field]=='TRUE' and any(e['assertion']=='NEGATED' for e in episode['field_evidence'].get(field,[])):abstain(field,'Negated source evidence cannot establish a positive field')
    for field,pattern in FEATURES.items():
        if episode[field]=='TRUE' and not positive_match(pattern,text(field)):abstain(field,'Seizure-like feature requires explicit nonnegated phenomenology, not a diagnostic label')
    from .tfne_granite import POLARITY_PATTERNS
    for field in ('positive_symptoms','negative_symptoms'):
        supported=[]
        for code in episode[field]:
            uncertain=any(e['assertion'] in ('NEGATED','UNCERTAIN','CONDITIONAL','NOT_ASSESSABLE') for e in episode['field_evidence'].get(field,[]))
            if not uncertain and positive_match(POLARITY_PATTERNS[code],text(field)):supported.append(code)
            else:flags.append(dict(episode_index=episode['episode_index'],field=field,raw_value=code,reason='Negated/uncertain symptom evidence cannot establish present positive/negative symptom polarity'))
        episode[field]=supported
    for field in ('temporal_course','focality','side','onset_type'):
        if episode[field]!='UNKNOWN' and any(e['assertion'] in ('UNCERTAIN','CONDITIONAL','NOT_ASSESSABLE') for e in episode['field_evidence'].get(field,[])):abstain(field,'Field assertion is uncertain; not an established clinical value')
    for field,position in [('duration_min_minutes',0),('duration_max_minutes',1)]:
        if episode[field] is not None:
            values=duration_values(text(field))
            if not any(abs(episode[field]-pair[position])<1e-6 for pair in values):abstain(field,'Numeric duration not supported by an explicit source quantity/unit; vague duration retained as text')
    for field in ('episode_date_text','duration_text','episode_frequency_text'):
        if episode[field] and episode[field]!='UNKNOWN':
            if ' '.join(episode[field].split()).casefold() not in ' '.join(text(field).split()).casefold():abstain(field,'Original date/duration/frequency wording must occur in its exact source evidence')
    if episode['fully_resolved']=='TRUE' and not supported_resolution(text('fully_resolved')):abstain('fully_resolved','Resolution wording is negated or lacks explicit complete resolution')
    if episode['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and episode['fully_resolved']!='TRUE':abstain('temporal_course','Fully resolved course requires validated resolution evidence')
    if episode['duration_min_minutes'] is None and episode['duration_max_minutes'] is None and episode['duration_precision'] in ('EXACT','RANGE'):
        abstain('duration_precision','Exact/range precision lacks a validated numeric duration')
    if episode['episode_count'] is not None:
        value=episode['episode_count'];source=text('episode_count')
        matches=re.findall(r'('+NUMBER+r')\s*(?:mal|x|Episoden|Anfälle|Attacken)',source,re.I)
        supported=[WORDS[m.casefold()] if m.casefold() in WORDS else float(m.replace(',','.')) for m in matches]
        if value not in supported:abstain('episode_count','Episode count is not explicitly supported')
    for component in episode['symptom_components']:
        source=' '.join(index[s]['evidence_text'] for s in component['source_span_ids'])
        if component['fully_resolved']=='TRUE' and not supported_resolution(source):
            component['fully_resolved']='UNKNOWN';flags.append(dict(episode_index=episode['episode_index'],field='symptom_components',raw_value='TRUE',reason='Component resolution lacks explicit positive source support'))
        if component['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and component['fully_resolved']!='TRUE':component['temporal_course']='UNKNOWN'
    for alias,field in [('postictal_confusion','post_event_confusion'),('postictal_aphasia','post_event_aphasia')]:
        episode[alias]=episode[field] if re.search(r'postiktal',text(field),re.I) else 'UNKNOWN'
        if episode[alias]!='UNKNOWN':episode['field_evidence'][alias]=copy.deepcopy(episode['field_evidence'].get(field,[]))
    return episode,flags
