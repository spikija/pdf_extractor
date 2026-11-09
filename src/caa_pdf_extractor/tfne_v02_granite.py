"""Two local semantic passes over frozen v0.1 spans, with full request logs."""
import json
import re
from datetime import datetime, timezone

from .workbench_store import ROOT
from .tfne_sources import model_sections, span_index, FORBIDDEN
from .tfne_v02_schema import detection_schema, phenotype_schema, validate_phenotype
from .tfne_v02_validation import DOMAIN, domains, has, ground,exact_quote

PROMPT_NAME='tfne_neurological_deficit_v0_2'
PROMPT=ROOT/'prompts/tfne_neurological_deficit_v0_2.txt'
DETECTION_PROMPT=ROOT/'prompts/tfne_neurological_deficit_v0_2_detection.txt'


def detect(raw,index):
    if not isinstance(raw,dict) or set(raw)!={'episodes'} or not isinstance(raw['episodes'],list):raise ValueError('Invalid detection response')
    kept=[];flags=[];used=set()
    for n,d in enumerate(raw['episodes'],1):
        if set(d)!={'episode_index','source_span_ids','short_exact_description','episode_date_text'}:raise ValueError('Detection schema mismatch')
        ids=d['source_span_ids'];quote=d['short_exact_description'];reason=None
        if not isinstance(ids,list) or not ids or any(s not in index for s in ids):reason='Detection cites a span outside the frozen history'
        elif not isinstance(quote,str) or not quote.strip() or not any(exact_quote(quote,index[s]['evidence_text']) is not None for s in ids):reason='Detection description is not an exact source substring'
        elif not domains(quote):reason='No explicitly present neurological symptom in the exact detection'
        elif re.search(r'\b(?:Migräneanamnese|bekannt\w*\s+Migräne)\b',quote,re.I) and not re.search(r'kam\s+es|aufgetreten|entwickelt|erlitt',quote,re.I):reason='Background migraine history is not a current distinct spell'
        elif re.search(r'\b(?:empfohlen|geplant|sollte|künftig|bei\s+erneutem)\b',quote,re.I) and not re.search(r'kam\s+es|aufgetreten|entwickelt|erlitt|klagte|bestehend',quote,re.I):reason='Recommendation or conditional plan, not an actual episode'
        key=(tuple(sorted(ids)),quote)
        if key in used:reason='Exact duplicate detection'
        if reason:
            flags.append(dict(episode_index=n,field='episode',code='REVIEW_REQUIRED',raw_value=d,reason=reason));continue
        used.add(key);d=dict(d,episode_index=n)
        source_quote=next(exact_quote(quote,index[s]['evidence_text']) for s in ids if exact_quote(quote,index[s]['evidence_text']) is not None)
        if source_quote!=quote:
            d['model_description_before_whitespace_resolution']=quote
            d['short_exact_description']=source_quote
            d['source_quote_resolution']='WHITESPACE_ONLY_EXACT_SOURCE_SUBSTRING'
        date=d['episode_date_text']
        if date and (not isinstance(date,str) or not any(date in index[s]['evidence_text'] for s in ids)):
            flags.append(dict(episode_index=n,field='episode_date_text',code='DATE_NOT_IN_SOURCE',raw_value=date,reason='Detection date absent from cited episode spans'));d['episode_date_text']=None
        kept.append(d)
    return kept,flags


def payload(client,prompt,data,schema,predict=8192):
    return dict(model=client.model_name,messages=[dict(role='system',content=prompt.read_text(encoding='utf-8')),
        dict(role='user',content=json.dumps(data,ensure_ascii=False))],stream=False,think=False,format=schema,
        options=dict(temperature=0,seed=0,num_ctx=32768,num_predict=predict))


def anchor_options(index):
    options=[]
    for sid,source in index.items():
        text=source['evidence_text'];pieces=[text]
        pieces += [m[0].strip() for m in re.finditer(r'.+?(?:(?<=[.!?])\s+(?=[A-ZÄÖÜ])|$)',text,re.S) if m[0].strip()]
        for quote in dict.fromkeys(pieces):options.append(dict(source_span_id=sid,text=quote))
    return options


def call(client,request,record,prefix,replay=None):
    if replay is not None:
        from pathlib import Path
        from .workbench_store import sha
        replay=Path(replay);old=replay/(prefix+'_raw.json');req=replay/(prefix+'_request.json')
        if old.exists() and req.exists():
            prior=json.loads(req.read_text(encoding='utf-8'));raw=json.loads(old.read_text(encoding='utf-8'))
            compatible=prior['messages']==request['messages'] and (prefix=='pass1' or len(raw.get('episodes',[]))==1)
            if compatible:
                record(prefix+'_request',prior);record(prefix+'_raw',raw)
                record(prefix+'_response',json.loads((replay/(prefix+'_response.json')).read_text(encoding='utf-8')))
                record(prefix+'_replayed',dict(source_directory=str(replay),source_raw_sha256=sha(old),actual_local_call=False,
                    reason='Saved response satisfies the same source/prompt and the final episode cardinality contract'))
                return raw
    record(prefix+'_request',request)
    record(prefix+'_started',dict(timestamp=datetime.now(timezone.utc).isoformat()))
    response=client._request('/api/chat',request);record(prefix+'_response',response)
    if not response.get('done') or response.get('done_reason')=='length':raise ValueError('Incomplete local response; no candidate produced')
    raw=json.loads(response['message']['content']);record(prefix+'_raw',raw)
    return raw


def extract(sections,client,record,replay=None):
    model=model_sections(sections);index=span_index(sections)
    if any(FORBIDDEN.search(s['evidence_text']) for s in index.values()):raise ValueError('History-only firewall failed')
    anchors=anchor_options(index)
    raw=call(client,payload(client,DETECTION_PROMPT,dict(source_sections=model,exact_episode_anchor_options=anchors),detection_schema(list(index),anchors)),record,'pass1',replay)
    detections,flags=detect(raw,index);rejected_count=len(raw['episodes'])-len(detections)
    record('validated_detections',dict(episodes=detections,validation_flags=flags))
    episodes=[];proposals=[]
    for d in detections:
        ids=d['source_span_ids']
        # Original global IDs remain stable; never renumber an episode subset.
        spans=[dict(span_id=s,text=index[s]['evidence_text']) for s in ids]
        request=payload(client,PROMPT,dict(detected_episode=d,episode_source_spans=spans),phenotype_schema(ids))
        prefix=f'pass2_episode_{d["episode_index"]:03d}'
        raw=call(client,request,record,prefix,replay);validate_phenotype(raw)
        if len(raw['episodes'])!=1:raise ValueError('Pass 2 must return exactly its detected episode')
        proposal=raw['episodes'][0];proposals.append(dict(detection=d,proposal=proposal))
        e,f=ground(proposal,d,index);episodes.append(e);flags+=f
        record(prefix+'_validated',dict(episode=e,validation_flags=f))
    record('raw_phenotype_proposals',proposals)
    return episodes,flags,dict(pass1_calls=1,pass2_calls=len(detections),detected_episodes=len(detections),rejected_detections=rejected_count)
