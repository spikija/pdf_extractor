"""Symmetric candidate audit, source-based alignment, and v0.1/v0.2 comparison."""
import csv
import json
from collections import Counter
from pathlib import Path

from .workbench_store import ROOT,sha
from .tfne_v02_pilot import BASE,ARTIFACT,read,write
from .tfne_v02_schema import FIELDS
from .tfne_v02_validation import support,populated,has,DOMAIN,PERSIST,PROGRESS,resolution
from .tfne_sources import span_index,FORBIDDEN

GATES=['NONEMPTY_FIELD_WITHOUT_EVIDENCE','TRANSIENT_FULLY_RESOLVED_WITHOUT_RESOLUTION','INVENTED_DURATION_RANGE',
    'LANGUAGE_ONLY_CREATES_MOTOR_FEATURE','LANGUAGE_ONLY_CREATES_SENSORY_FEATURE','FLUCTUATING_CONFUSION_CREATES_SENSORY_FEATURE',
    'PERSISTENT_DEFICIT_WITHOUT_PERSISTENCE_EVIDENCE','DOCUMENT_DATE_IMPORTED_AS_EPISODE_DATE',
    'MIGRAINE_HISTORY_RECLASSIFIES_CURRENT_EPISODE','EEG_USED_IN_EXTRACTION','IMAGING_USED_IN_EXTRACTION','DATABASE_WRITES','CLOUD_PATIENT_CALLS']


def csv_file(path,rows,fields=None):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields or list(dict.fromkeys(k for r in rows for k in r)) or ['status'])
        writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def field_text(episode,field):return ' '.join(r['evidence_text'] for r in episode.get('field_evidence',{}).get(field,[]))


def assess(episode,patient,version):
    rows=[];counts=Counter(dict.fromkeys(['unsupported_fields','contradictory_fields','invented_duration_fields',
        'language_to_motor_errors','language_to_cognitive_errors','transient_without_resolution',
        'persistent_without_persistence_evidence','language_to_sensory_errors'],0))
    for field in FIELDS:
        value=episode[field];text=field_text(episode,field)
        issue=None
        if populated(value):
            if not episode.get('field_evidence',{}).get(field):issue='NONEMPTY_FIELD_WITHOUT_EVIDENCE'
            else:issue=support(field,value,text,episode)
        if issue:
            counts['unsupported_fields']+=1
            if field in ('duration_min_minutes','duration_max_minutes'):counts['invented_duration_fields']+=1
            if field in ('persistent_deficit_domains','persistent_course'):counts['persistent_without_persistence_evidence']+=1
        rows.append(dict(patient_id=patient,episode_id=episode.get('episode_id'),episode_index=episode['episode_index'],version=version,
            field=field,value=value,grounding_status=issue or 'SUPPORTED' if populated(value) else 'UNKNOWN_OR_EMPTY',
            issue=issue,source_span=[dict(document_id=e['document_id'],page=e['page'],section_id=e['section_id'],
                start=e['evidence_start'],end=e['evidence_end'],text=e['evidence_text']) for e in episode.get('field_evidence',{}).get(field,[])]))
    course=episode['temporal_course'];resolved=episode['fully_resolved']
    if course=='TRANSIENT_FULLY_RESOLVED' and (resolved!='TRUE' or not resolution(field_text(episode,'fully_resolved'))):counts['transient_without_resolution']+=1
    if course=='TRANSIENT_FULLY_RESOLVED' and resolved!='TRUE' or resolved=='TRUE' and course in ('PERSISTENT','PROGRESSIVE','MIXED','FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'):counts['contradictory_fields']+=1
    entire=' '.join(field_text(episode,f) for f in FIELDS)
    language=has(DOMAIN['LANGUAGE'],entire)
    if language and not has(DOMAIN['MOTOR'],entire):
        counts['language_to_motor_errors']+=('MOTOR' in episode['symptom_domains'] or bool(set(episode['positive_symptoms']+episode['negative_symptoms'])&{'POSITIVE_MOTOR','NEGATIVE_MOTOR'}) or any(episode.get(f)=='TRUE' for f in ('clonic_jerking','tonic_posturing','todd_paresis')))
    if language and not has(DOMAIN['COGNITIVE'],entire):counts['language_to_cognitive_errors']+='COGNITIVE' in episode['symptom_domains']
    if language and not has(DOMAIN['SENSORY'],entire):counts['language_to_sensory_errors']+=('SENSORY' in episode['symptom_domains'] or bool(set(episode['positive_symptoms']+episode['negative_symptoms'])&{'POSITIVE_SENSORY','NEGATIVE_SENSORY'}))
    return rows,counts


def anchor(e):
    return {(str(x['document_id']),x['section_id'],x['page']) for x in e.get('field_evidence',{}).get('symptom_description',[])}


def matching(old,new):
    """Candidate alignment by unchanged source location, never by screen result.

    A shared parent source can contain multiple events; all many-to-many matches
    are explicit review candidates, not claimed physician-adjudicated identities.
    """
    pairs=[]
    for i,a in enumerate(old):
        scores=[]
        for j,b in enumerate(new):
            if not anchor(a)&anchor(b):continue
            atext=a['symptom_description'].casefold();btext=b['symptom_description'].casefold()
            words=lambda t:set(__import__('re').findall(r'\w{4,}',t))
            wa,wb=words(atext),words(btext)
            score=len(wa&wb)/max(1,len(wa|wb))
            # Exact source ranges further distinguish episodes on the same page.
            overlap=any(x['evidence_start']<y['evidence_end'] and y['evidence_start']<x['evidence_end'] for x in a['field_evidence'].get('symptom_description',[]) for y in b['field_evidence'].get('symptom_description',[]) if x['section_id']==y['section_id'])
            if score>=.08 or overlap:scores.append((j,score,overlap))
        pairs.extend((i,j) for j,score,overlap in scores)
    return pairs


def comparison(patient,old,new,events):
    from review_app.tfne_repository import project
    projected=project(dict(episodes=old),events) if events else []
    pairs=matching(old,new);old_links=Counter(i for i,j in pairs);new_links=Counter(j for i,j in pairs)
    pairs += [(i,None) for i in range(len(old)) if not old_links[i]]
    pairs += [(None,j) for j in range(len(new)) if not new_links[j]]
    rows=[]
    for i,j in pairs:
        a=old[i] if i is not None else None;b=new[j] if j is not None else None
        physician=[p for p in projected if a and (p['episode_id']==a['episode_id'] or a['episode_id'] in p.get('review_parent_ids',[]))]
        for field in FIELDS:
            av=a[field] if a else None;bv=b[field] if b else None
            review=None;review_status=None
            if len(physician)==1:
                review_status=physician[0].get('review_status')
                if review_status in ('ACCEPT','EDIT'):review=physician[0].get(field)
            if a and b and old_links[i]>1:change='EPISODE_SPLIT'
            elif a and b and new_links[j]>1:change='EPISODE_MERGED'
            elif av==bv and bool(a)==bool(b):change='UNCHANGED'
            elif not populated(bv):change='REMOVED_UNSUPPORTED' if a and (not a.get('field_evidence',{}).get(field) or support(field,av,field_text(a,field),a)) else 'NEWLY_UNKNOWN'
            elif not populated(av):change='NEWLY_SUPPORTED'
            else:change='CORRECTED'
            rows.append(dict(patient_id=patient,episode_id=b['episode_id'] if b else a['episode_id'],
                v0_1_episode_id=a['episode_id'] if a else None,v0_2_episode_id=b['episode_id'] if b else None,
                field=field,v0_1_value=av,v0_2_value=bv,physician_review_value=review,
                physician_review_status=review_status,change_type=change,alignment_method='source_parent_and_quote_overlap' if a and b else 'unmatched_source_episode',
                alignment_requires_physician_review=True,correction_is_adjudicated=review is not None and review==bv))
    # Preserve explicit rejection as an episode-level judgment, never fabricate
    # corrected clinical field values from a REJECT click.
    for e in projected:
        if e.get('review_status')=='REJECT':rows.append(dict(patient_id=patient,episode_id=e['episode_id'],field='__episode_review_status__',
            v0_1_value='CANDIDATE',v0_2_value=None,physician_review_value='REJECT',physician_review_status='REJECT',change_type='UNCHANGED',alignment_requires_physician_review=True))
    return rows


def audit(directory):
    directory=Path(directory).resolve()
    from .workbench_worker import guard
    guard(directory)
    freeze=read(directory/'cohort_freeze.json')
    if freeze['patient_ids']!=read(BASE/'cohort_freeze.json')['patient_ids']:raise ValueError('Cohort mismatch')
    fields=[];flags=[];compare=[];totals={'v0_1':Counter(),'v0_2':Counter()};gates=Counter(dict.fromkeys(GATES,0));hashes={};calls=0
    episode_counts={};review_count=0;actual_calls=0;replayed_calls=0
    for patient in freeze['patient_ids']:
        source_path=directory/'inputs'/f'{patient}.json'
        if sha(source_path)!=sha(BASE/'inputs'/source_path.name):raise ValueError('Identical-source comparison failed')
        source=read(source_path);index=span_index(source['sections'])
        old_path=ROOT/'data/patient_artifacts'/patient/'tfne_neurological_deficit_v0_1.json'
        old=read(old_path);new=read(directory/'workers'/patient/ARTIFACT);hashes[patient]=sha(directory/'workers'/patient/ARTIFACT)
        for version,artifact in [('v0_1',old),('v0_2',new)]:
            totals[version]['episodes']+=len(artifact['episodes'])
            for e in artifact['episodes']:
                rows,counts=assess(e,patient,version);fields+=rows;totals[version].update(counts)
                if version=='v0_2':
                    gates['NONEMPTY_FIELD_WITHOUT_EVIDENCE']+=sum(r['issue']=='NONEMPTY_FIELD_WITHOUT_EVIDENCE' for r in rows)
                    gates['TRANSIENT_FULLY_RESOLVED_WITHOUT_RESOLUTION']+=counts['transient_without_resolution']
                    gates['INVENTED_DURATION_RANGE']+=counts['invented_duration_fields']
                    gates['LANGUAGE_ONLY_CREATES_MOTOR_FEATURE']+=counts['language_to_motor_errors']
                    gates['LANGUAGE_ONLY_CREATES_SENSORY_FEATURE']+=counts['language_to_sensory_errors']
                    gates['PERSISTENT_DEFICIT_WITHOUT_PERSISTENCE_EVIDENCE']+=counts['persistent_without_persistence_evidence']
                    gates['DOCUMENT_DATE_IMPORTED_AS_EPISODE_DATE']+=sum(r['field']=='episode_date_text' and bool(r['issue']) for r in rows)
                    gates['MIGRAINE_HISTORY_RECLASSIFIES_CURRENT_EPISODE']+=sum(r['field']=='historical_diagnosis' and r['value']=='MIGRAINE' and bool(r['issue']) for r in rows)
                    # Additional gate covers every supported clinical field,
                    # beyond the user's named headline failure categories.
                    gates['UNSUPPORTED_VALUE_RETAINED_AFTER_VALIDATION']+=counts['unsupported_fields']
                    if has(r'fluktuier\w*',field_text(e,'symptom_description')) and not has(DOMAIN['SENSORY'],field_text(e,'symptom_description')):
                        gates['FLUCTUATING_CONFUSION_CREATES_SENSORY_FEATURE']+=bool(set(e['positive_symptoms']+e['negative_symptoms'])&{'POSITIVE_SENSORY','NEGATIVE_SENSORY'})
        for flag in new['validation_flags']:flags.append(dict(patient_id=patient,**flag))
        review_path=ROOT/'data/review/tfne_neurological_deficit_v0_1'/patient/sha(old_path)/'events.json'
        events=read(review_path) if review_path.exists() else [];review_count+=len(events)
        compare+=comparison(patient,old['episodes'],new['episodes'],events)
        calls+=new['semantic_calls']['pass1_calls']+new['semantic_calls']['pass2_calls']
        actual_calls+=new['semantic_calls'].get('actual_local_calls',new['semantic_calls']['pass1_calls']+new['semantic_calls']['pass2_calls'])
        replayed_calls+=new['semantic_calls'].get('replayed_calls',0)
        episode_counts[patient]=dict(v0_1=len(old['episodes']),v0_2=len(new['episodes']))
        worker=directory/'workers'/patient
        for path in worker.glob('*_request.json'):
            request=read(path);data=json.loads(request['messages'][1]['content'])
            if path.name.startswith('pass1'):
                expected=__import__('caa_pdf_extractor.tfne_sources',fromlist=['model_sections']).model_sections(source['sections'])
                from .tfne_v02_granite import anchor_options
                if data!={'source_sections':expected,'exact_episode_anchor_options':anchor_options(index)}:raise ValueError('Pass 1 received other material')
                texts=[s['text'] for sec in expected for s in sec['spans']]
            else:
                texts=[s['text'] for s in data['episode_source_spans']]
                if set(s['span_id'] for s in data['episode_source_spans'])!=set(data['detected_episode']['source_span_ids']):raise ValueError('Pass 2 context expansion')
                if any(s['text']!=index[s['span_id']]['evidence_text'] for s in data['episode_source_spans']):raise ValueError('Pass 2 source mutated')
            for text in texts:
                if FORBIDDEN.search(text):raise ValueError('EEG/imaging/CAA entered semantic extraction')
    protected=read(directory/'preservation_before.json')
    changed=[p for p,h in protected.items() if not (ROOT/p).exists() or sha(ROOT/p)!=h]
    if changed:raise ValueError('Preserved v0.1/source/review files changed: '+str(changed[:3]))
    code=read(directory/'code_before.json');allowed={'review_app/tfne_repository.py','review_app/views/tfne.py'}
    modified=[p for p,h in code.items() if sha(ROOT/p)!=h and Path(p).as_posix() not in allowed]
    if modified:raise ValueError('Other module or v0.1 code changed: '+str(modified))
    write(directory/'preservation_audit.json',dict(protected_files=len(protected),changed_files=changed,other_module_code_changes=modified))
    csv_file(directory/'tfne_v0_1_vs_v0_2_comparison.csv',compare)
    csv_file(directory/'tfne_v0_2_validation_flags.csv',flags)
    csv_file(directory/'tfne_v0_2_field_grounding_audit.csv',fields)
    write(directory/'hard_gates.json',dict(gates))
    metrics=dict(patients_rerun=len(freeze['patient_ids']),candidate_metrics={k:dict(v) for k,v in totals.items()},
        episodes_by_patient=episode_counts,hard_gate_failures=sum(gates.values()),candidate_hashes=hashes,semantic_calls=calls,
        actual_new_semantic_calls=actual_calls,replayed_semantic_responses=replayed_calls,
        v0_1_physician_review_events_preserved=review_count,validation_flags=len(flags),protected_files=len(protected),
        prompt_version='tfne_neurological_deficit_v0_2',database_writes=0,cloud_patient_calls=0,
        comparison_is_physician_adjudicated=False,metric_basis='Published v0.1 and new validated v0.2 candidates assessed symmetrically with v0.2 support checks; not historical abstention-flag counts')
    write(directory/'validation_metrics.json',metrics)
    print(json.dumps(metrics,indent=2))
    if metrics['hard_gate_failures']:raise SystemExit(1)


if __name__=='__main__':
    import sys
    audit(sys.argv[1])
