"""Versioned-prompt local Granite extraction and source-grounded abstention rules."""
import copy
import hashlib
import json
import re
from .workbench_store import ROOT,sha
from .tfne_schema import validate,defaults,response_schema,TRI_FIELDS
from .tfne_sources import model_sections,span_index,FORBIDDEN
from .tfne_screen import derive

PROMPT_NAME='tfne_neurological_deficit_v0_1'
PROMPT_PATH=ROOT/'prompts'/('tfne_neurological_deficit_v0_1.txt')
UNCERTAIN=re.compile(r'möglicher\w*|möglicherweise|fraglich|eventuell|Verdacht|\bV\.?\s*a\.?|nicht\s+auszuschließen',re.I)
RESOLUTION=re.compile(r'vollständig\w*\s+(?:rückläufig|regredient)|komplett\w*\s+(?:rückläufig|regredient)|(?:wieder|zurück)[^.;]{0,45}Ausgangs(?:zustand|niveau)|(?:nach|innerhalb)[^.;]{0,60}(?:sistiert|abgeklungen|verschwunden)|(?:vollständig|komplett)[^.;]{0,25}(?:erholt|normalisiert)|beschwerdefrei|symptomfrei|keine\s+(?:Beschwerden|Symptome|Defizite)\s+mehr',re.I)
STEREOTYPY=re.compile(r'stereotyp|immer\s+gleich|gleichartig|identisch\w*\s+Symptom',re.I)
RECURRENCE=re.compile(r'wiederholt|rezidivier|mehrfach|wiederkehr|erneut|Episoden|Anfälle|mehrmals|\d+\s*(?:mal|x)|täglich|wöchentlich',re.I)
LABELS={'TFNE':r'\bTFNE\b','TIA':r'\bTIA\b|transitorisch\w*\s+ischämisch','EPILEPSY':r'\bEpilepsie\b','SEIZURE':r'Anfall|Anfälle','MIGRAINE':r'Migräne','ISCHEMIC_STROKE':r'ischämisch\w*\s+(?:Schlaganfall|Infarkt)|Hirninfarkt|Insult','ICH':r'Hirnblutung|intrazerebral\w*\s+Blutung|intracerebral\w*\s+Blutung|\bICH\b'}
POLARITY_PATTERNS=dict(POSITIVE_SENSORY=r'Kribbel|Parästhes|Ameisen|Brenn|Dysästhes|Missempfind',POSITIVE_MOTOR=r'Zuck|klon|Schüttel|tonisch',
    POSITIVE_VISUAL=r'Flimmer|Blitz|Zickzack|Lichtphänomen',NEGATIVE_SENSORY=r'Taub|Hypästhes|Sensibilitätsverlust',NEGATIVE_MOTOR=r'Schwäch|Parese|Plegie|Kraftminder',NEGATIVE_VISUAL=r'Hemianop|Sehverlust|Gesichtsfeldausfall|Amaurose|Abdunkel')


def meaningful(field,value):
    return field not in ('episode_index','evidence','assertion') and value not in (None,'UNKNOWN','NONE_DOCUMENTED',[],{})


def ground(raw,sections):
    raw=validate(raw);index=span_index(sections);episodes=[];flags=[]
    for candidate in raw['episodes']:
        episode=copy.deepcopy(candidate);evidence=[];by_field={};states={}
        for field,group in candidate['evidence'].items():
            if set(group)!={'source_span_ids','assertion'} or group['assertion'] not in ('ESTABLISHED','NEGATED','UNCERTAIN','HISTORICAL','CONDITIONAL','NOT_ASSESSABLE'):raise ValueError('Invalid field evidence')
            if any(s not in index for s in group['source_span_ids']):raise ValueError('Evidence span is outside restricted narrative')
            if field not in candidate or field in ('evidence','episode_index'):raise ValueError('Unknown evidence field')
            for span in group['source_span_ids']:
                source=index[span];eid=source['section_id']+':'+str(source['evidence_start'])+':'+str(source['evidence_end'])
                item=dict(source,evidence_id=eid,field=field,assertion=group['assertion'])
                evidence.append(item);by_field.setdefault(field,[]).append(item);states.setdefault(field,[]).append(group['assertion'])
        # Missing/unsupported fields are never silently accepted as facts.
        def abstain(field,reason):
            value=episode[field];episode[field]=copy.deepcopy(defaults()[field])
            flags.append(dict(episode_index=candidate['episode_index'],field=field,raw_value=value,reason=reason))
        for field,value in list(episode.items()):
            if meaningful(field,value) and field not in by_field:abstain(field,'No field-specific grounded source evidence')
        for field in TRI_FIELDS:
            if episode[field]!='TRUE':continue
            text=' '.join(e['evidence_text'] for e in by_field.get(field,[]))
            if any(s in ('UNCERTAIN','CONDITIONAL','NOT_ASSESSABLE') for s in states.get(field,[])) or UNCERTAIN.search(text):abstain(field,'Uncertain source cannot establish a positive field')
        text_for=lambda field:' '.join(e['evidence_text'] for e in by_field.get(field,[]))
        if any(s in ('UNCERTAIN','CONDITIONAL','NOT_ASSESSABLE') for s in states.get('symptom_description',[])):
            episode['assertion']='UNCERTAIN'
        for field in TRI_FIELDS:
            if episode[field]=='FALSE' and not re.search(r'kein\w*|ohne|verneint|nicht|persistier|anhaltend|verblieb|einmalig|erstmalig|nur\s+ein',text_for(field),re.I):abstain(field,'Negative value requires explicit source support')
        if episode['fully_resolved']=='TRUE' and not RESOLUTION.search(text_for('fully_resolved')):abstain('fully_resolved','Complete resolution not explicitly supported')
        if episode['stereotyped']=='TRUE' and not STEREOTYPY.search(text_for('stereotyped')):abstain('stereotyped','Recurrence alone does not establish stereotypy')
        if episode['recurrent']=='TRUE' and not RECURRENCE.search(text_for('recurrent')):abstain('recurrent','Recurrence lacks explicit source support')
        if episode['spreading_progression']=='TRUE' and not re.search(r'ausbreit|breitet|wander|aufsteigend|fortleit|von[^.;]{0,80}(?:über|bis|zum|zur)',text_for('spreading_progression'),re.I):abstain('spreading_progression','Distribution alone does not establish spreading')
        for field in ('positive_symptoms','negative_symptoms'):
            supported=[]
            for code in episode[field]:
                source_text=text_for(field)
                matches=list(re.finditer(POLARITY_PATTERNS[code],source_text,re.I))
                if any(not re.search(r'kein\w*\s+(?:\w+\s+){0,2}$',source_text[max(0,m.start()-40):m.start()],re.I) for m in matches):supported.append(code)
                else:flags.append(dict(episode_index=candidate['episode_index'],field=field,raw_value=code,reason='Symptom polarity lacks positive source support'))
            episode[field]=supported
        diagnosis=episode['historical_diagnosis']
        if diagnosis in LABELS and not re.search(LABELS[diagnosis],text_for('historical_diagnosis'),re.I):abstain('historical_diagnosis','Historical label not explicitly documented')
        if episode['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and episode['fully_resolved']!='TRUE':abstain('temporal_course','Transient fully-resolved course requires explicit complete resolution')
        for component in episode['symptom_components']:
            if set(component)!={'domain','temporal_course','fully_resolved','source_span_ids'} or any(s not in index for s in component['source_span_ids']):raise ValueError('Invalid component evidence')
        if not episode['symptom_description'] or not evidence:
            flags.append(dict(episode_index=candidate['episode_index'],field='episode',raw_value=candidate,reason='Episode lacks grounded symptom description; retained only in raw model log'))
            continue
        episode['field_evidence']=by_field;episode['evidence']=evidence
        from .tfne_validation import validate_grounded
        episode,source_flags=validate_grounded(episode,index);flags+=source_flags
        episode.update(derive(episode));episodes.append(episode)
    return episodes,flags


def request(sections,client):
    system=PROMPT_PATH.read_text(encoding='utf-8')
    payload=dict(model=client.model_name,messages=[dict(role='system',content=system),dict(role='user',content=json.dumps(dict(source_sections=model_sections(sections)),ensure_ascii=False))],
        stream=False,think=False,format=response_schema(list(span_index(sections))),options=dict(temperature=0,seed=0,num_ctx=32768,num_predict=12288))
    return payload


def extract(sections,client,record):
    if client.model_version is None:client.check_model()
    payload=request(sections,client);record('request',payload)
    response=client._request('/api/chat',payload);record('response',response)
    if not response.get('done') or response.get('done_reason')=='length':raise ValueError('Incomplete local TFNE response; raw response retained')
    raw=json.loads(response['message']['content']);record('raw_episodes',raw)
    episodes,flags=ground(raw,sections)
    return episodes,flags
