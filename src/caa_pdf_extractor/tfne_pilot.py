"""Frozen ten-patient TFNE pilot: local history-only extraction and separate artifacts."""
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

from .workbench_store import ROOT,STABLE_ROOT,read,sha,atomic
from .module_artifacts import digest
from .tfne_sources import source_folder,select_sections,model_sections,span_index,FORBIDDEN
from .tfne_granite import PROMPT_PATH,PROMPT_NAME,extract

ARTIFACT='tfne_neurological_deficit_v0_1.json'


def write(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def csv_file(path,rows,fields=None):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        keys=fields or (list(rows[0]) if rows else ['status']);writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def prepare():
    directory=ROOT/'data/debug'/('tfne_pilot_v0_1_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'));directory.mkdir()
    protected={str(p):sha(p) for base in (STABLE_ROOT,ROOT/'data/review') for p in base.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
    write(directory/'protected_files.json',protected)
    write(directory/'existing_extraction_code.json',{str(p):sha(p) for p in (ROOT/'src/caa_pdf_extractor').rglob('*.py') if not p.name.startswith('tfne_')})
    candidates=[];sources={}
    patterns=dict(enriched=r'Amyloidangiopathie|\bCAA\b|Siderose|cSS|cSAH|Subarachnoidalblutung',transient=r'vorübergehend|transient|Minuten|rückläufig|regredient',
        epilepsy=r'Epilepsie|epileptisch|Anfall|Anfälle',tia=r'\bTIA\b|transitorisch',persistent=r'persistierend|residuell|anhaltend|verblieben',
        cognitive=r'kognitiv|Demenz|Gedächtnis|progredient',spread=r'ausbreit|wandern|aufsteigend',recurrent=r'wiederholt|rezidivierend|mehrfach|stereotyp')
    for manifest in sorted(STABLE_ROOT.glob('*/module_manifest.json')):
        m=read(manifest);patient=m['patient_id'];folder=source_folder(m)
        if not folder:continue
        bundle=read(folder/'source_bundle.json');sections=[];excluded=[]
        for doc in bundle['documents']:
            rows,omitted=select_sections(patient,doc);sections+=rows;excluded+=omitted
        history=' '.join(p['text'] for s in sections for p in s['spans']);full=' '.join(s['text'] for d in bundle['documents'] for s in d['sections'])
        signals={k:bool(re.search(pattern,full if k=='enriched' else history,re.I)) for k,pattern in patterns.items()}
        row=dict(patient_id=patient,document_count=len(bundle['documents']),section_count=len(sections),source_characters=len(history),**signals)
        candidates.append(row);sources[patient]=dict(sections=sections,exclusions=excluded,source_path=str(folder/'source_bundle.json'),source_sha256=sha(folder/'source_bundle.json'),
            pdf_hashes={str(ROOT/'pdf_db'/patient/d['filename']):d['sha256'] for d in bundle['documents']})
    eligible=[r for r in candidates if r['enriched'] and r['section_count']>=2 and r['source_characters']>=1500]
    slots=[('Source spread terminology',lambda r:r['spread']),('TIA terminology and persistent-deficit wording',lambda r:r['tia'] and r['persistent']),
        ('Persistent deficit without transient wording',lambda r:r['persistent'] and not r['transient']),('Cognitive presentation without epilepsy wording',lambda r:r['cognitive'] and not r['epilepsy']),
        ('Transient episodes with epilepsy wording',lambda r:r['epilepsy'] and r['transient']),('Epilepsy wording without transient wording',lambda r:r['epilepsy'] and not r['transient']),
        ('TIA wording without epilepsy wording',lambda r:r['tia'] and not r['epilepsy']),('No explicit transient/persistent/cognitive/spreading indicators control',lambda r:not any(r[k] for k in ('transient','cognitive','persistent','spread'))),
        ('Mixed transient/persistent/cognitive source indicators',lambda r:r['transient'] and r['persistent'] and r['cognitive']),('TIA and cognitive source indicators',lambda r:r['tia'] and r['cognitive'])]
    selected=[];used=set()
    for reason,predicate in slots:
        options=[r for r in eligible if r['patient_id'] not in used and predicate(r)]
        if not options:raise ValueError('Source selection slot unavailable; no model extraction has run: '+reason)
        chosen=min(options,key=lambda r:(r['source_characters'],digest(r['patient_id'])));used.add(chosen['patient_id'])
        sections=sources[chosen['patient_id']]['sections']
        selected.append(dict(patient_id=chosen['patient_id'],document_count=chosen['document_count'],Anamnese_available=any(s['section_type']=='ANAMNESE' for s in sections),
            Zusammenfassung_available=any(s['section_type']=='ZUSAMMENFASSUNG_DES_AUFENTHALTES' for s in sections),selection_reason=reason+'; CAA/cSS/cSAH source enrichment, not a clinical classification'))
    assert len(used)==10
    csv_file(directory/'tfne_pilot_10_selection.csv',selected);csv_file(directory/'source_screening_patients.csv',candidates)
    inventory=[];exclusions=[]
    for row in selected:
        patient=row['patient_id'];source=sources[patient]
        model_sections(source['sections']);write(directory/'inputs'/f'{patient}.json',source);exclusions+=source['exclusions']
        for section in source['sections']:inventory.append({k:section[k] for k in ('patient_id','document_id','filename','page','section_id','parent_section_id','section_type','source_start','source_end')}|dict(model_span_count=len(section['spans'])))
    csv_file(directory/'tfne_source_section_inventory.csv',inventory);write(directory/'input_exclusions.json',exclusions)
    write(directory/'cohort_freeze.json',dict(patient_ids=[r['patient_id'] for r in selected],selection_before_extraction=True,frozen_at=datetime.now(timezone.utc).isoformat(),
        selection_sha256=sha(directory/'tfne_pilot_10_selection.csv'),prompt_name=PROMPT_NAME,prompt_version='0.1',prompt_hash=sha(PROMPT_PATH),
        input_hashes={str(p):sha(p) for p in (directory/'inputs').glob('*.json')},strategy='Ten prespecified heterogeneous source-indicator slots; minimum narrative size within slot; all available allowed narrative sections of selected patients; no model-output selection'))
    print(directory,flush=True);print('FROZEN_PATIENT_IDS',[r['patient_id'] for r in selected],flush=True)
    return directory


def offline_guard(directory):
    directory=Path(directory).resolve()
    def guard(event,args):
        if event in ('subprocess.Popen','os.system','sqlite3.connect'):raise RuntimeError('TFNE_DATABASE_CHILD_PROCESS_FORBIDDEN')
        if event=='import' and any(s in args[0].lower() for s in ('sqlalchemy','psycopg','sqlite3')):raise RuntimeError('TFNE_DATABASE_IMPORT_FORBIDDEN')
        if event=='socket.connect':
            address=args[1]
            if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1') or address[1]!=11434:raise RuntimeError('TFNE_NONLOCAL_NETWORK_FORBIDDEN')
        if event=='socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1'):raise RuntimeError('TFNE_EXTERNAL_DNS_FORBIDDEN')
        if event=='open':
            import os
            name,mode,flags=args
            writing=isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
            if writing and isinstance(name,(str,bytes,os.PathLike)) and not Path(name).resolve().is_relative_to(directory):raise RuntimeError('TFNE_WRITE_OUTSIDE_DEBUG_FORBIDDEN')
    sys.addaudithook(guard)


def run(directory):
    from .llm.granite import GraniteClient
    directory=Path(directory).resolve();freeze=read(directory/'cohort_freeze.json')
    if len(freeze['patient_ids'])!=10 or len(set(freeze['patient_ids']))!=10 or not freeze['selection_before_extraction']:raise ValueError('Exactly ten frozen patients required')
    if sha(directory/'tfne_pilot_10_selection.csv')!=freeze['selection_sha256'] or sha(PROMPT_PATH)!=freeze['prompt_hash']:raise ValueError('Frozen selection/prompt changed')
    if any(sha(p)!=h for p,h in freeze['input_hashes'].items()):raise ValueError('Frozen narrative inputs changed')
    offline_guard(directory)
    client=GraniteClient(base_url='http://127.0.0.1:11434',model='granite4.2:latest',timeout=1800);client.check_model()
    write(directory/'local_model.json',dict(endpoint=client.base_url,name=client.model_name,tag=client.model_name,digest=client.model_version))
    code_hashes={str(p):sha(p) for p in (ROOT/'src/caa_pdf_extractor').glob('tfne_*.py')}
    write(directory/'extraction_code_hashes.json',code_hashes)
    for patient in freeze['patient_ids']:
        output=directory/'workers'/patient
        if output.exists():raise ValueError('Existing TFNE output; no silent retries or overwrites')
        output.mkdir(parents=True)
        source=read(directory/'inputs'/f'{patient}.json')
        if sha(source['source_path'])!=source['source_sha256'] or any(sha(p)!=h for p,h in source['pdf_hashes'].items()):raise ValueError('Original sources changed')
        print('TFNE_LOCAL_GRANITE_START',patient,len(source['sections']),'sections',flush=True)
        episodes,flags=extract(source['sections'],client,lambda name,value:write(output/(name+'.json'),value))
        for n,episode in enumerate(episodes,1):
            episode['episode_id']=f'{patient}:TFNE01:'+digest([n,episode['symptom_description'],episode['field_evidence']])[:18]
            episode['physician_review_status']='UNREVIEWED'
        artifact=dict(patient_id=patient,module_name='TFNE_NEUROLOGICAL_DEFICIT',module_version='0.1',status='CANDIDATE',physician_review_status='UNREVIEWED',
            granite_prompt_name=PROMPT_NAME,granite_prompt_version='0.1',granite_prompt_hash=freeze['prompt_hash'],granite_model_name=client.model_name,
            granite_model_tag=client.model_name,granite_model_version=client.model_version,extraction_timestamp=datetime.now(timezone.utc).isoformat(),
            source_section_ids=[s['section_id'] for s in source['sections']],source_sections_processed=len(source['sections']),episodes=episodes,validation_flags=flags,
            raw_model_output=str(output/'raw_episodes.json'),source_inputs=str(directory/'inputs'/f'{patient}.json'),source_bundle_sha256=source['source_sha256'],source_pdf_hashes=source['pdf_hashes'],
            code_hashes=code_hashes,pilot_directory=str(directory),database_writes=0,cloud_patient_calls=0,auto_promoted=False)
        write(output/ARTIFACT,artifact)
        print('TFNE_LOCAL_GRANITE_DONE',patient,len(episodes),'episodes',len(flags),'abstention flags',flush=True)


def publish(directory):
    directory=Path(directory).resolve();freeze=read(directory/'cohort_freeze.json')
    if len(freeze['patient_ids'])!=10 or len(set(freeze['patient_ids']))!=10:raise ValueError('Exactly ten patients required')
    if read(directory/'validation_metrics.json')['hard_gate_failures']:raise ValueError('Hard-gate failures require review before publication')
    for patient in freeze['patient_ids']:
        source=directory/'workers'/patient/ARTIFACT;target=STABLE_ROOT/patient/ARTIFACT
        if target.exists():raise ValueError('TFNE artifact exists; immutable candidates are never overwritten')
        with target.open('xb') as stream:stream.write(source.read_bytes())
    write(directory/'published_artifacts.json',{p:dict(path=str(STABLE_ROOT/p/ARTIFACT),sha256=sha(STABLE_ROOT/p/ARTIFACT)) for p in freeze['patient_ids']})


if __name__=='__main__':
    sys.dont_write_bytecode=True
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='run':run(sys.argv[2])
    elif sys.argv[1]=='publish':publish(sys.argv[2])
