"""Same ten v0.1 patients and byte-identical inputs; immutable v0.2 candidates."""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path

from .workbench_store import ROOT,sha
from .module_artifacts import digest
from .tfne_pilot import offline_guard
from .tfne_v02_granite import PROMPT,DETECTION_PROMPT,PROMPT_NAME,extract

BASE=ROOT/'data/debug/tfne_pilot_v0_1_20261101T130202_586729Z'
ARTIFACT='tfne_neurological_deficit_v0_2.json'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def prepare(directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to((ROOT/'data/debug').resolve()) or not (directory/'preservation_before.json').exists():raise ValueError('Fresh debug directory with preservation snapshot required')
    freeze=read(BASE/'cohort_freeze.json');ids=freeze['patient_ids']
    if len(ids)!=10 or len(set(ids))!=10:raise ValueError('Original collective must contain exactly ten IDs')
    inputs=directory/'inputs';inputs.mkdir()
    for patient in ids:
        original=BASE/'inputs'/f'{patient}.json'
        if sha(original)!=freeze['input_hashes'][str(original)]:raise ValueError('Original frozen input checksum mismatch')
        (inputs/original.name).write_bytes(original.read_bytes())
    (directory/'v0_1_cohort_freeze.json').write_bytes((BASE/'cohort_freeze.json').read_bytes())
    (directory/'tfne_pilot_10_selection.csv').write_bytes((BASE/'tfne_pilot_10_selection.csv').read_bytes())
    code={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src/caa_pdf_extractor').glob('tfne_v02*.py')}
    write(directory/'cohort_freeze.json',dict(patient_ids=ids,original_pilot=str(BASE),
        original_freeze_sha256=sha(BASE/'cohort_freeze.json'),selection_before_extraction=True,no_cohort_reselection=True,
        same_source_bytes=True,input_hashes={str(p):sha(p) for p in sorted(inputs.glob('*.json'))},
        original_input_hashes=freeze['input_hashes'],selection_sha256=sha(directory/'tfne_pilot_10_selection.csv'),
        prompts={str(p):sha(p) for p in (PROMPT,DETECTION_PROMPT)},code_hashes=code,
        frozen_at=datetime.now(timezone.utc).isoformat()))
    print('FROZEN_SAME_TEN_PATIENTS',ids,flush=True)


def run(directory,replay_directory=None):
    from .llm.granite import GraniteClient
    directory=Path(directory).resolve();freeze=read(directory/'cohort_freeze.json')
    original=read(BASE/'cohort_freeze.json')
    if freeze['patient_ids']!=original['patient_ids'] or sha(BASE/'cohort_freeze.json')!=freeze['original_freeze_sha256']:raise ValueError('Original cohort changed')
    if any(sha(p)!=h for p,h in {**freeze['input_hashes'],**freeze['prompts']}.items()):raise ValueError('Frozen inputs or prompts changed')
    if any(sha(ROOT/p)!=h for p,h in freeze['code_hashes'].items()):raise ValueError('Frozen extraction code changed')
    offline_guard(directory)
    client=GraniteClient(base_url='http://127.0.0.1:11434',model='granite4.2:latest',timeout=1800);client.check_model()
    prior=read(BASE/'local_model.json')
    if client.model_version!=prior['digest']:raise ValueError('Installed Granite digest differs from v0.1; controlled comparison requires the same model')
    write(directory/'local_model.json',dict(endpoint=client.base_url,name=client.model_name,tag=client.model_name,digest=client.model_version))
    for patient in freeze['patient_ids']:
        output=directory/'workers'/patient
        output.mkdir(parents=True)  # exclusive; no silent retries or overwrites
        source=read(directory/'inputs'/f'{patient}.json')
        if sha(source['source_path'])!=source['source_sha256'] or any(sha(p)!=h for p,h in source['pdf_hashes'].items()):raise ValueError('Original source provenance changed')
        print('TFNE_V02_LOCAL_START',patient,flush=True)
        replay=Path(replay_directory)/'workers'/patient if replay_directory else None
        episodes,flags,calls=extract(source['sections'],client,lambda name,value:write(output/(name+'.json'),value),replay=replay)
        calls['replayed_calls']=len(list(output.glob('*_replayed.json')))
        calls['actual_local_calls']=calls['pass1_calls']+calls['pass2_calls']-calls['replayed_calls']
        for n,e in enumerate(episodes,1):
            e['episode_id']=f'{patient}:TFNE02:'+digest([n,e['symptom_description'],e['detection']])[:18]
        artifact=dict(patient_id=patient,module_name='TFNE_NEUROLOGICAL_DEFICIT',module_version='0.2',status='CANDIDATE',physician_review_status='UNREVIEWED',
            granite_prompt_name=PROMPT_NAME,granite_prompt_version='0.2',granite_prompt_hash=freeze['prompts'][str(PROMPT)],
            semantic_pass_prompt_hashes=freeze['prompts'],granite_model_name=client.model_name,granite_model_tag=client.model_name,granite_model_version=client.model_version,
            extraction_timestamp=datetime.now(timezone.utc).isoformat(),source_section_ids=[s['section_id'] for s in source['sections']],
            source_sections_processed=len(source['sections']),episodes=episodes,validation_flags=flags,semantic_calls=calls,
            source_inputs=str(directory/'inputs'/f'{patient}.json'),source_bundle_sha256=source['source_sha256'],source_pdf_hashes=source['pdf_hashes'],
            code_hashes=freeze['code_hashes'],pilot_directory=str(directory),original_pilot_directory=str(BASE),original_candidate_sha256=sha(ROOT/'data/patient_artifacts'/patient/'tfne_neurological_deficit_v0_1.json'),
            screen_rule_version='TFNE_SCREEN_0.1',database_writes=0,cloud_patient_calls=0,auto_promoted=False)
        write(output/ARTIFACT,artifact)
        print('TFNE_V02_LOCAL_DONE',patient,len(episodes),'episodes',len(flags),'validation flags',flush=True)


def publish(directory):
    directory=Path(directory).resolve();freeze=read(directory/'cohort_freeze.json');metrics=read(directory/'validation_metrics.json')
    if metrics['hard_gate_failures'] or freeze['patient_ids']!=read(BASE/'cohort_freeze.json')['patient_ids']:raise ValueError('Publication gates failed')
    targets={p:ROOT/'data/patient_artifacts'/p/ARTIFACT for p in freeze['patient_ids']}
    if any(p.exists() for p in targets.values()):raise ValueError('v0.2 candidates already exist; immutable artifacts cannot be overwritten')
    published={}
    for patient,target in targets.items():
        source=directory/'workers'/patient/ARTIFACT
        if sha(source)!=metrics['candidate_hashes'][patient]:raise ValueError('Validated candidate checksum changed')
        with target.open('xb') as stream:stream.write(source.read_bytes())
        published[patient]=dict(path=str(target),sha256=sha(target))
    write(directory/'published_artifacts.json',published)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','publish']);p.add_argument('directory',type=Path)
    p.add_argument('--replay-directory',type=Path)
    a=p.parse_args()
    if a.action=='run':run(a.directory,a.replay_directory)
    else:globals()[a.action](a.directory)
