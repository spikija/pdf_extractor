"""Evidence-first v0.2 validation. No model, I/O, diagnosis or screen inference."""
import copy
import re

from .tfne_schema import defaults, NUMBERS, TEXT_FIELDS, TRI_FIELDS
from .tfne_screen import derive
from .tfne_validation import EXPLICIT, WORDS, FEATURES

UNCERTAIN=r'\b(?:möglicherweise|fraglich\w*|eventuell|Verdacht|vermutlich|evtl)\b|\bV\.?\s*a\.?'
NEGATION=r'\b(?:kein\w*|ohne|nicht|verneint\w*)\s+(?:\w+\s+){0,6}$'
PERSIST=r'persistier\w*|verblieb\w*|residuell\w*|anhaltend\w*|weiter\s+bestehend\w*|nicht\s+(?:rückläufig|regredient)|bleibend\w*'
PROGRESS=r'progred\w*|progressiv\w*|fortschreit\w*|zunehmend\w*'
RESOLUTION=r'(?:vollständig\w*|komplett\w*)\s+(?:rückläufig|regredient|sistiert|abgeklungen|verschwunden|erholt)|(?:wieder|zurück)[^.;]{0,35}Ausgangs(?:zustand|niveau)|(?:nach|innerhalb)\s+[^.;]{1,45}(?:sistiert|abgeklungen|verschwunden)|(?:wieder\s+)?(?:beschwerde|symptom)frei|keine\s+(?:Beschwerden|Symptome|Defizite)\s+mehr'
POLARITY=dict(POSITIVE_SENSORY=r'Kribbel\w*|Parästhes\w*|Ameisenlauf\w*|Brennen|brennend\w*',
    POSITIVE_MOTOR=r'Zuck\w*|Klon\w*|rhythmisch\w*\s+(?:Beweg\w*|Schüttel\w*)|tonisch\w*\s+(?:Halt\w*|Verkrampf\w*)',
    POSITIVE_VISUAL=r'Flimmer\w*|Blitz\w*|Zickzack\w*|Lichtphänomen\w*|visuell\w*\s+Aura',
    NEGATIVE_SENSORY=r'Taub\w*|Hypästhes\w*|Sensibilitätsverlust\w*',
    NEGATIVE_MOTOR=r'Schwäch\w*|Pares\w*|Plegi\w*|Kraftminder\w*|Lähm\w*',
    NEGATIVE_VISUAL=r'Hemianop\w*|Sehverlust\w*|Gesichtsfeldausfall\w*|Amaurose\w*|Abdunkel\w*')
DOMAIN=dict(LANGUAGE=r'Aphasi\w*|Dysphasi\w*|Wortfind\w*|Sprachstör\w*|Sprachblock\w*|Sprechblock\w*|Spracharrest\w*|Sprachstopp\w*|Wörter\s+nicht\s+gefunden|(?:Wörter|Sätze)\s+falsch\s+(?:aussprach|ausgesprochen)|falsche[nr]?\s+(?:Wörter|Sätze)',
    COGNITIVE=r'Verwirr\w*|Desorient\w*|Orientierungsstör\w*|kognitiv\w*\s+(?:Abbau|Stör\w*|Defizit\w*|Einbuß\w*)|Gedächtnis\w*|Demenz\w*|Delir\w*',
    SENSORY=POLARITY['POSITIVE_SENSORY']+'|'+POLARITY['NEGATIVE_SENSORY'],
    MOTOR=POLARITY['POSITIVE_MOTOR']+'|'+POLARITY['NEGATIVE_MOTOR']+r'|Pronier\w*|motorisch\w*\s+Symptom\w*|Muskelatroph\w*|Muskelartroph\w*',
    VISUAL=POLARITY['POSITIVE_VISUAL']+'|'+POLARITY['NEGATIVE_VISUAL']+r'|Sehstör\w*|Doppelbild\w*|Diplopie',
    GAIT_ATAXIA=r'Ataxi\w*|Gangstör\w*|Gangunsicher\w*|Gleichgewichtsstör\w*',
    CONSCIOUSNESS=r'Bewusst\w*|bewusstlos|Synkope|nicht\s+ansprechbar',
    DIZZINESS=r'Schwindel\w*',OTHER=r'Kopfschmerz\w*|Unwohlsein|Übelkeit|Erbrechen|Malaise|Schluckschwierig\w*|Schwierigkeiten\s+beim\s+Schlucken|Dysphagi\w*|Hemisymptom\w*')
DOMAIN['LANGUAGE']+=r'|Dysarthr\w*|Sprechschwierig\w*|Schwierigkeiten\s+beim\s+Sprechen'
BODY=dict(FACE=r'Gesicht\w*|Fazial\w*',MOUTH=r'Mund\w*',TONGUE=r'Zung\w*',HAND=r'Hand|Hände[n]?',
    FINGERS=r'Finger\w*',ARM=r'Arm(?:e[sn]?)?\b',LEG=r'Bein\w*',FOOT=r'Fuß\w*|Füß\w*',HEMISOMA=r'Halbseit\w*|Hemikörper|Körperhälfte',OTHER=r'Rumpf\w*|Schulter\w*')
SIDE=dict(LEFT=r'\blink\w*|\bli\.',RIGHT=r'\brecht\w*|\bre\.',BILATERAL=r'beidseit\w*|beiderseits|bilateral',MIDLINE=r'mittig|Mittellinie')
SPREAD=r'aufsteigend\w*|wander\w*|ausbreit\w*|breitet\w*[^.;]{0,25}aus|fortleit\w*|von[^.;]{0,80}(?:über|bis|zum|zur|nach)'
RECURRENCE=r'wiederholt\w*|rezidivier\w*|mehrfach|wiederkehr\w*|erneut|mehrmals|\d+\s*(?:mal|x)|täglich|wöchentlich'
STEREOTYPY=r'stereotyp\w*|immer\s+(?:gleich|dieselbe)|gleichartig\w*|identisch\w*\s+Symptom\w*'
LABELS=dict(TFNE=r'\bTFNE\b',TIA=r'\bTIA\b|transitorisch\w*\s+ischämisch',EPILEPSY=r'\bEpilepsie\b',
    SEIZURE=r'Anfall|Anfälle',MIGRAINE=r'Migräne',ISCHEMIC_STROKE=r'ischämisch\w*\s+(?:Schlaganfall|Infarkt)|Hirninfarkt|Insult',ICH=r'Hirnblutung|intrazerebral\w*\s+Blutung|\bICH\b',UNCLEAR=r'unklar',OTHER=r'diagnos\w*|beurteilt|gewertet')


def populated(value):return value not in (None,'UNKNOWN','NONE_DOCUMENTED',[],{})


def exact_quote(quote,text):
    """Resolve whitespace-only PDF wrapping differences back to source bytes.

    Never fuzzy-match, remove negation, change spelling or accept paraphrases.
    The returned substring, not the model's normalized wording, is persisted.
    """
    if not isinstance(quote,str) or not quote.strip():return None
    if quote in text:return quote
    pattern=r'\s+'.join(re.escape(word) for word in quote.strip().split())
    match=re.search(pattern,text)
    return match[0] if match else None


def positive(pattern,text):
    for match in re.finditer(pattern,text,re.I):
        before=re.split(r'[;!?]|\b(?:aber|jedoch|während|danach|zuvor)\b',text[:match.start()],flags=re.I)[-1]
        if not re.search(NEGATION,before[-100:],re.I) and not re.search(UNCERTAIN,before[-80:],re.I):
            yield match


def has(pattern,text):return next(positive(pattern,text),None) is not None


def resolution(text):
    for m in re.finditer(RESOLUTION,text,re.I):
        prefix=text[max(0,m.start()-50):m.start()]
        if not re.search(r'(?:nicht|unvollständig|nur\s+teilweise)\s*$',prefix,re.I) and not re.search(UNCERTAIN,prefix,re.I):
            return True
    return False


def domains(text):return [code for code,pattern in DOMAIN.items() if has(pattern,text)]


def duration(text):
    """An exact quantity/unit, never an inferred bin. 'Seit' is a lower bound."""
    matches=list(EXPLICIT.finditer(text))
    if len(matches)!=1:return None
    m=matches[0]
    def number(word):return WORDS[word.casefold()] if word.casefold() in WORDS else float(word.replace(',','.'))
    unit=m[3].casefold();scale=1/60 if unit.startswith('sek') else 60 if unit.startswith(('st','std')) else 1440 if unit.startswith('tag') else 1
    lo=number(m[1])*scale;hi=number(m[2])*scale if m[2] else lo
    if hi<lo:return None
    prefix=text[:m.start()].strip()
    if re.search(r'\bseit\b|mindestens|mehr\s+als|>\s*$',prefix,re.I):hi=None
    elif re.search(r'weniger\s+als|unter|bis\s+zu|<\s*$',prefix,re.I):hi=lo;lo=None
    return lo,hi,'RANGE' if m[2] else 'APPROXIMATE' if lo is None or hi is None or re.search(r'ca\.?|etwa|ungefähr',prefix,re.I) else 'EXACT'


def support(field,value,text,episode):
    """Return a named rejection code, or None when explicit support exists."""
    if not populated(value):return None
    if field in ('positive_symptoms','negative_symptoms'):
        return None if all(has(POLARITY[c],text) for c in value) else 'DOMAIN_POLARITY_CONFLICT'
    if field=='symptom_domains':return None if all(has(DOMAIN[d],text) for d in value) else 'DOMAIN_POLARITY_CONFLICT'
    if field=='persistent_deficit_domains':
        explicit=has(PERSIST,text) or set(value)=={'COGNITIVE'} and has(PROGRESS,text)
        return None if explicit and all(has(DOMAIN[d],text) for d in value) else 'PERSISTENCE_UNSUPPORTED'
    if field=='persistent_course':
        ok=has(PROGRESS,text) and (has(PERSIST,text) or has(DOMAIN['COGNITIVE'],text)) if value=='PROGRESSIVE' else has(PERSIST,text) and has(r'stabil|residuell|verblieben|unverändert',text)
        return None if ok else 'PERSISTENCE_UNSUPPORTED'
    if field=='temporal_course':
        ok={'TRANSIENT_FULLY_RESOLVED':episode['fully_resolved']=='TRUE' and resolution(text),
            'PERSISTENT':has(PERSIST,text),'PROGRESSIVE':has(PROGRESS,text),
            'FLUCTUATING_WITHOUT_CLEAR_RESOLUTION':has(r'fluktuier\w*|wechselnd\w*',text) and not resolution(text),
            'MIXED':len({c['temporal_course'] for c in episode['symptom_components'] if c['temporal_course']!='UNKNOWN'})>=2}.get(value,False)
        return None if ok else 'TRANSIENT_WITHOUT_RESOLUTION_EVIDENCE' if value=='TRANSIENT_FULLY_RESOLVED' else 'TEMPORAL_COURSE_CONFLICT'
    if field=='fully_resolved':return None if (resolution(text) if value=='TRUE' else has(PERSIST,text)) else 'TEMPORAL_COURSE_CONFLICT'
    if field=='focality':
        ds=set(domains(text));focal=bool(ds&{'LANGUAGE'}) or has(r'Hemianop\w*|\bfokal\w*',text) or (bool(ds&{'SENSORY','MOTOR','VISUAL'}) and any(has(p,text) for p in SIDE.values()) and any(has(p,text) for p in BODY.values()))
        nonfocal=bool(ds&{'COGNITIVE','DIZZINESS','OTHER','CONSCIOUSNESS'})
        ok=focal if value=='FOCAL' else nonfocal and not focal if value=='NONFOCAL' else focal and nonfocal
        return None if ok else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='side':return None if has(SIDE[value],text) else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='body_regions':return None if all(has(BODY[c],text) for c in value) else 'UNSUPPORTED_FIELD_REMOVED'
    if field in ('episode_date_text','duration_text','episode_frequency_text','symptom_description','ictal_behavior_description'):
        return None if value in text else 'DATE_NOT_IN_SOURCE' if field=='episode_date_text' else 'UNSUPPORTED_FIELD_REMOVED'
    if field in ('duration_min_minutes','duration_max_minutes','duration_precision'):
        d=duration(text)
        if field=='duration_precision' and value=='APPROXIMATE' and re.search(r'wenige|einige|ca\.?|etwa|ungefähr',text,re.I):return None
        target=None if not d else d[0 if field=='duration_min_minutes' else 1 if field=='duration_max_minutes' else 2]
        return None if target==value else 'DURATION_UNSUPPORTED'
    if field=='episode_count':
        from .tfne_validation import NUMBER
        values=[WORDS[t.casefold()] if t.casefold() in WORDS else float(t.replace(',','.')) for t in re.findall('('+NUMBER+r')\s*(?:mal|x|Episoden|Anfälle|Attacken)',text,re.I)]
        return None if value in values else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='onset_type':return None if has(r'plötzlich|akut|abrupt|schlagartig' if value=='ABRUPT' else r'langsam|allmählich|schleichend|graduell',text) else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='spread_sequence':
        if not has(SPREAD,text) or len(value)<2:return 'UNSUPPORTED_FIELD_REMOVED'
        last=-1
        for item in value:
            pattern=BODY.get(item,re.escape(item));matches=list(positive(pattern,text))
            later=next((m for m in matches if m.start()>last),None)
            if later is None:return 'UNSUPPORTED_FIELD_REMOVED'
            last=later.start()
        return None
    if field=='spread_type':
        ok=episode['spreading_progression']=='TRUE' and (value=='VISUAL_SPREAD' and has(DOMAIN['VISUAL'],text) or value in ('SOMATOTOPIC_CONTIGUOUS','SEQUENTIAL_NONCONTIGUOUS') and len(episode['spread_sequence'])>=2 or value=='OTHER' and has(SPREAD,text))
        return None if ok else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='historical_diagnosis':
        # A background label cannot replace the clinician's label of this event.
        ok=has(LABELS[value],text) and bool(re.search(r'als[^.;]{0,80}(?:gewertet|beurteilt|diagnostiziert)|(?:Diagnos\w*|beurteilt|gewertet)[^.;]{0,80}|(?:Episode|Symptomatik|Ereignis)[^.;]{0,50}(?:entspr|interpretiert)',text,re.I))
        return None if ok else 'UNSUPPORTED_FIELD_REMOVED'
    if field=='assertion':
        if value=='NEGATED':return 'REVIEW_REQUIRED'
        return None if domains(text) or has(r'kam\s+es|berichtet|besteht|aufgetreten\w*|entwickelt\w*|erlitt|beklagte|klagte',text) else 'UNSUPPORTED_FIELD_REMOVED'
    if field in TRI_FIELDS:
        pattern={'recurrent':RECURRENCE,'stereotyped':STEREOTYPY,'spreading_progression':SPREAD,**FEATURES}.get(field)
        if value=='TRUE':return None if pattern and has(pattern,text) else 'UNSUPPORTED_FIELD_REMOVED'
        if re.search(UNCERTAIN,text,re.I):return 'UNSUPPORTED_FIELD_REMOVED'
        if pattern:
            for m in re.finditer(pattern,text,re.I):
                if re.search(NEGATION,text[max(0,m.start()-100):m.start()],re.I):return None
        if field=='recurrent' and has(r'einmalig|erstmalig|einzige\w*\s+Episode',text):return None
        return 'UNSUPPORTED_FIELD_REMOVED'
    return None


def ground(candidate,detection,index):
    """Validate one pass-2 proposal; preserve all rejected values as visible flags."""
    episode=copy.deepcopy(candidate);flags=[];evidence={};status={};base=defaults()
    def flag(field,code,old,reason):
        flags.append(dict(episode_index=detection['episode_index'],field=field,code=code,raw_value=copy.deepcopy(old),reason=reason))
    def remove(field,code,reason):
        old=episode[field];episode[field]=copy.deepcopy(base[field])
        if field=='assertion':episode[field]='UNCERTAIN'
        evidence.pop(field,None);status[field]='REMOVED_UNSUPPORTED'
        flag(field,code,old,reason)
    for field in base.keys()-{'episode_index','evidence'}:
        group=candidate['evidence'].get(field,{})
        ids=group.get('source_span_ids',[]);quote=group.get('quote');state=group.get('assertion','NOT_ASSESSABLE')
        valid=(isinstance(ids,list) and bool(ids) and isinstance(quote,str) and bool(quote.strip())
               and all(s in index and s in detection['source_span_ids'] and exact_quote(quote,index[s]['evidence_text']) is not None for s in ids))
        if valid:
            rows=[]
            for sid in ids:
                source=index[sid];resolved=exact_quote(quote,source['evidence_text']);lo=source['evidence_start']+source['evidence_text'].index(resolved)
                # A copied word can still omit a preceding "keine" or "V.a.".
                # Restore that exact local source context before judging support.
                local=lo-source['evidence_start'];prefix=source['evidence_text'][:local]
                clauses=list(re.finditer(r'[;!?]|\b(?:aber|jedoch|während|danach|zuvor)\b',prefix,re.I))
                scope_start=clauses[-1].end() if clauses else 0
                context=prefix[scope_start:];neg=re.search(NEGATION,context[-100:],re.I);uncertain=re.search(UNCERTAIN,context[-80:],re.I)
                source_state=state
                if neg or uncertain:
                    tail_start=max(scope_start,local-(100 if neg else 80))
                    source_state='NEGATED' if neg else 'UNCERTAIN'
                    expanded=source['evidence_text'][tail_start:local+len(resolved)]
                    lo=source['evidence_start']+tail_start;resolved=expanded
                rows.append(dict(source,evidence_text=resolved,evidence_start=lo,evidence_end=lo+len(resolved),
                    source_span_id=sid,evidence_id=source['section_id']+f':{lo}:{lo+len(resolved)}',field=field,assertion=source_state))
            evidence[field]=rows
        if populated(episode[field]) and not valid:
            remove(field,'UNSUPPORTED_FIELD_REMOVED','A populated field requires its own exact source quote and allowed span IDs')
        else:status[field]='SUPPORTED' if populated(episode[field]) else 'UNKNOWN_OR_EMPTY'
        if valid and field in TEXT_FIELDS and episode[field]:
            resolved=exact_quote(episode[field],' '.join(r['evidence_text'] for r in evidence[field]))
            if resolved is not None:episode[field]=resolved
    # The pass-1 exact symptom anchor remains the episode identity, even when the
    # second pass paraphrases it or supplies no description evidence.
    exact=detection['short_exact_description'];sid=next(s for s in detection['source_span_ids'] if exact in index[s]['evidence_text'])
    source=index[sid];lo=source['evidence_start']+source['evidence_text'].index(exact)
    episode['symptom_description']=exact
    evidence['symptom_description']=[dict(source,evidence_text=exact,evidence_start=lo,evidence_end=lo+len(exact),source_span_id=sid,
        evidence_id=source['section_id']+f':{lo}:{lo+len(exact)}',field='symptom_description',assertion='ESTABLISHED')]
    status['symptom_description']='SUPPORTED_PASS_1'
    if not evidence.get('assertion'):
        episode['assertion']='UNCERTAIN';evidence['assertion']=[dict(e,field='assertion',assertion='UNCERTAIN') for e in evidence['symptom_description']]
    def text(field):return ' '.join(e['evidence_text'] for e in evidence.get(field,[]))
    # Uncertain/negated/conditional evidence cannot establish present values.
    for field in list(evidence):
        if field in ('symptom_description','assertion','historical_diagnosis') or not populated(episode[field]):continue
        if any(e['assertion'] in ('UNCERTAIN','CONDITIONAL','NOT_ASSESSABLE') or e['assertion']=='NEGATED' and episode[field]!='FALSE' for e in evidence[field]):
            remove(field,'UNSUPPORTED_FIELD_REMOVED','Nonpositive assertion cannot establish this field')
    # Validate component descriptions independently before deciding mixed course.
    components=[]
    for c in episode['symptom_components']:
        ids=c.get('source_span_ids',[])
        if not ids or any(s not in detection['source_span_ids'] or s not in index for s in ids):continue
        ct=' '.join(index[s]['evidence_text'] for s in ids)
        if c.get('domain') not in DOMAIN or not has(DOMAIN[c['domain']],ct):continue
        c=copy.deepcopy(c)
        if c['fully_resolved']=='TRUE' and not resolution(ct):c['fully_resolved']='UNKNOWN'
        if c['fully_resolved']=='FALSE' and not has(PERSIST,ct):c['fully_resolved']='UNKNOWN'
        if c['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and c['fully_resolved']!='TRUE':c['temporal_course']='UNKNOWN'
        if c['temporal_course']=='PERSISTENT' and not has(PERSIST,ct):c['temporal_course']='UNKNOWN'
        if c['temporal_course']=='PROGRESSIVE' and not has(PROGRESS,ct):c['temporal_course']='UNKNOWN'
        components.append(c)
    if components!=episode['symptom_components']:flag('symptom_components','UNSUPPORTED_FIELD_REMOVED',episode['symptom_components'],'Unsupported component values removed')
    episode['symptom_components']=components
    # Arrays are checked item by item, so one hallucinated item does not erase a
    # separately grounded symptom in the same field.
    for field,patterns in [('symptom_domains',DOMAIN),('positive_symptoms',POLARITY),('negative_symptoms',POLARITY),('body_regions',BODY),('persistent_deficit_domains',DOMAIN)]:
        original=episode[field];kept=[]
        for item in original:
            problem=support(field,[item],text(field),episode)
            if problem:flag(field,problem,item,'This item lacks explicit field-specific support');status[field]='PARTIALLY_REMOVED'
            else:kept.append(item)
        episode[field]=kept
        if not kept and original:evidence.pop(field,None);status[field]='REMOVED_UNSUPPORTED'
    if has(DOMAIN['LANGUAGE'],text('symptom_description')) and 'LANGUAGE' not in episode['symptom_domains']:
        flag('symptom_domains','DOMAIN_POLARITY_CONFLICT',episode['symptom_domains'],'Explicit language concept mapped to LANGUAGE; no other domain inferred')
        episode['symptom_domains'].append('LANGUAGE')
        evidence.setdefault('symptom_domains',[]).extend(dict(e,field='symptom_domains') for e in evidence['symptom_description'])
        status['symptom_domains']='SUPPORTED_PHYSICIAN_RULE'
    # Unit conversion is mechanical and carries the exact duration provenance.
    literal=text('duration_text') if episode['duration_text'] and episode['duration_text'] in text('duration_text') else ''
    numeric=duration(episode['duration_text']) if literal else None
    if numeric:
        for field,value in zip(('duration_min_minutes','duration_max_minutes','duration_precision'),numeric):
            if episode[field]!=value:flag(field,'DURATION_UNSUPPORTED',episode[field],'Normalized only from explicit duration quantity/unit; ongoing duration retains an open bound')
            episode[field]=value
            if populated(value):evidence[field]=[dict(e,field=field) for e in evidence['duration_text']];status[field]='SUPPORTED_UNIT_CONVERSION'
            else:evidence.pop(field,None);status[field]='UNKNOWN_OR_EMPTY'
    else:
        for field in ('duration_min_minutes','duration_max_minutes'):
            if episode[field] is not None:remove(field,'DURATION_UNSUPPORTED','No exact episode duration wording with quantity and unit')
        if episode['duration_precision'] in ('EXACT','RANGE'):remove('duration_precision','DURATION_UNSUPPORTED','No grounded numeric duration')
    # Follow dependencies: validate resolution and spread before their derivatives.
    ordered=['fully_resolved','spreading_progression','spread_sequence','spread_type','temporal_course']
    ordered += [f for f in base if f not in ordered and f not in ('evidence','episode_index','symptom_components','symptom_domains','positive_symptoms','negative_symptoms','body_regions','persistent_deficit_domains','duration_min_minutes','duration_max_minutes','duration_precision')]
    for field in ordered:
        if not populated(episode[field]):continue
        problem=support(field,episode[field],text(field),episode)
        if problem:remove(field,problem,'Exact evidence does not support this clinical value')
    if episode['temporal_course']=='UNKNOWN' and has(r'fluktuier\w*',text('symptom_description')) and not resolution(text('symptom_description')):
        episode['temporal_course']='FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'
        evidence['temporal_course']=[dict(e,field='temporal_course') for e in evidence['symptom_description']];status['temporal_course']='SUPPORTED_PHYSICIAN_RULE'
    if episode['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and episode['fully_resolved']!='TRUE':
        remove('temporal_course','TRANSIENT_WITHOUT_RESOLUTION_EVIDENCE','Hard resolution invariant')
    if episode['fully_resolved']=='TRUE' and episode['temporal_course'] in ('PERSISTENT','PROGRESSIVE','MIXED','FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'):
        remove('temporal_course','TEMPORAL_COURSE_CONFLICT','Episode-level complete resolution conflicts with an ongoing or mixed course')
    if not evidence.get('assertion'):
        # Assertion has no UNKNOWN enum. Keep a conservative review state tied
        # to the independently grounded pass-1 symptom anchor, never an uncited
        # default ESTABLISHED value.
        episode['assertion']='UNCERTAIN'
        evidence['assertion']=[dict(e,field='assertion',assertion='UNCERTAIN') for e in evidence['symptom_description']]
        status['assertion']='REVIEW_REQUIRED_PASS_1_ANCHOR'
    if flags:flag('episode','REVIEW_REQUIRED',None,'Inspect removed/normalized fields and episode completeness')
    episode['episode_index']=detection['episode_index'];episode['field_evidence']=evidence
    episode['evidence']=[e for field,rows in evidence.items() if populated(episode.get(field)) for e in rows]
    episode['field_validation_status']=status;episode['detection']=copy.deepcopy(detection)
    episode['validation_flags']=flags;episode['physician_review_status']='UNREVIEWED'
    episode.update(derive(episode))
    return episode,flags
