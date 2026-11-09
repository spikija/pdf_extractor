"""Explicitly bounded EEG 1.2 regression execution, audit and candidate publication."""
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha,integrity
from .workbench_eeg import EEGWorkbenchStore
from .module_artifacts import digest

BASE=ROOT/'data/debug/eeg_validation_20261028T103249_606975Z'
V11=ROOT/'data/debug/eeg_v1_1_20261029T075226_750876Z'
HOLDOUT=ROOT/'data/debug/eeg_v1_1_holdout_20261029T101453_435311Z'
REQUIRED=('2687726420','2702072620','2809457122','2887921324','2957537425','2989288225')


def patients():return sorted(set(REQUIRED)|{r['patient_id'] for r in read(V11/'regression_results.json')['checks']})


def csv_write(path,rows,fields=None):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields or list(rows[0]) if rows else fields or ['status'])
        writer.writeheader();writer.writerows(rows)


def prepare():
    from .extraction.eeg_geometry_v12 import snapshot
    directory=ROOT/'data/debug'/('eeg_v1_2_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    directory.mkdir();ids=patients();atomic(directory/'selected_patients.json',ids)
    protected={str(p):sha(p) for root in (STABLE_ROOT,ROOT/'data/review') for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
    atomic(directory/'protected_files.json',protected)
    atomic(directory/'original_manifests.json',{p:read(STABLE_ROOT/p/'module_manifest.json') for p in ids})
    expected=[];old=read(V11/'published_candidates.json');dates=read(BASE/'independent_expected_dates.json')
    for patient in ids:
        bundle=snapshot(patient,directory/'source_inputs'/patient)
        if patient in REQUIRED:
            for study in read(HOLDOUT/'source_inventory_studies.json'):
                if study['patient_id']!=patient:continue
                expected.append(dict(patient_id=patient,eeg_date=study['EEG_date'],recording_type='VIDEO_EEG' if study['study_key'][2]=='VIDEO' else 'CONTINUOUS_EEG' if study['study_key'][2]=='CONTINUOUS' else 'ROUTINE_EEG',
                    document_id=study['document_id'],page=study['page'],expected_heading=study['source_heading'],inventory_origin='INDEPENDENT_HOLDOUT_SOURCE_INVENTORY'))
        else:
            prior=next(read(f['path']) for f in old[patient]['artifacts'] if Path(f['path']).name=='eeg_exams.json')
            used=set()
            for dt in dates[patient]:
                e=next(e for e in prior if e['eeg_date']==dt and e['eeg_exam_id'] not in used);used.add(e['eeg_exam_id'])
                expected.append(dict(patient_id=patient,eeg_date=dt,recording_type='VIDEO_EEG' if e['recording_type']=='VIDEO_EEG' else 'CONTINUOUS_EEG' if e['recording_type'] in ('LONG_TERM_EEG','ICU_EEG') else 'ROUTINE_EEG',
                    document_id=e['document_id'],page=e['source_page_start'],expected_heading=e['source_header'],inventory_origin='INDEPENDENT_V1_SOURCE_DATES_WITH_ARCHIVED_SOURCE_LINK'))
    for n,e in enumerate(expected,1):e['inventory_id']=f'EEG12_EXPECTED_{n:03}'
    atomic(directory/'expected_source_inventory.json',expected)
    atomic(directory/'independent_inventory_freeze.json',dict(before_v12_extraction=True,frozen_at=datetime.now(timezone.utc).isoformat(),
        sha256=sha(directory/'expected_source_inventory.json'),method='Prior independently inventoried source identities and physician-specified boundary cases; not derived from v1.2 outputs'))
    print(directory,flush=True)
    return directory


class EEG12Store(EEGWorkbenchStore):
    def __init__(self,directory,root=STABLE_ROOT):super().__init__(root);self.directory=Path(directory)
    def enabled_patients(self):
        selected=read(self.directory/'selected_patients.json')
        if selected!=patients():raise ValueError('EEG 1.2 scope differs from the explicit regression set')
        return set(selected)


def worker(request):
    from .extraction.eeg_pipeline_v12 import assemble
    if request['patient_id'] not in patients():raise ValueError('Unregistered EEG patient')
    if any(sha(p)!=h for p,h in request['input_hashes'].items()):raise ValueError('Worker input changed')
    result=assemble(read(Path(request['source_dir'])/'source_bundle.json'))
    for name,value in result.items():
        (Path(request['output'])/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (Path(request['output'])/'execution.json').write_text(json.dumps(dict(patient_id=request['patient_id'],module_version='1.2',modules_executed=['eeg'],
        database_writes=0,cloud_patient_calls=0,guard='OS audit hook: sockets, database imports, child processes and writes outside fresh output blocked')),encoding='utf-8')


def run(directory,label='workers'):
    directory=Path(directory).resolve()
    if read(directory/'selected_patients.json')!=patients():raise ValueError('Scope changed')
    inventory=read(directory/'independent_inventory_freeze.json')
    if not inventory['before_v12_extraction'] or sha(directory/'expected_source_inventory.json')!=inventory['sha256']:raise ValueError('Source denominator changed')
    output=directory/label;output.mkdir(exist_ok=False)
    hashes={str(p):sha(p) for p in (ROOT/'src/caa_pdf_extractor/extraction').glob('eeg_*.py')}
    for relative in ('src/caa_pdf_extractor/eeg_v12_validation.py','src/caa_pdf_extractor/eeg_v12_audit.py',
        'src/caa_pdf_extractor/workbench_eeg.py','src/caa_pdf_extractor/workbench_eeg_v12.py','review_app/app.py','review_app/views/eeg.py','review_app/eeg_holdout.py'):
        path=ROOT/relative;hashes[str(path)]=sha(path)
    atomic(output/'code_hashes.json',hashes)
    for patient in patients():
        source=directory/'source_inputs'/patient;target=output/patient;target.mkdir()
        inputs={str(p):sha(p) for p in source.glob('*.json')};inputs.update(hashes)
        request=dict(patient_id=patient,source_dir=str(source),output=str(target),input_hashes=inputs)
        path=output/('request_'+patient+'.json');atomic(path,request)
        done=subprocess.run([sys.executable,'-B','-X','utf8','-m','caa_pdf_extractor.eeg_v12_validation','worker',str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        (output/('worker_'+patient+'.log')).write_text(done.stdout+done.stderr,encoding='utf-8')
        if done.returncode:raise RuntimeError(done.stderr[-3000:])
        print('EEG12',patient,len(read(target/'eeg_exams.json')),flush=True)
    return output


def results(directory,label='workers'):
    return {p:{f.stem:read(f) for f in (Path(directory)/label/p).glob('eeg_*.json')} for p in patients()}


def publish(directory):
    directory=Path(directory).resolve();store=EEG12Store(directory)
    if (directory/'published_candidates.json').exists():raise ValueError('Candidates already published')
    metrics=read(directory/'validation_metrics.json')
    if metrics['hard_gate_failures'] or metrics['regression_cases_failed'] or metrics['matched_source_EEG_exams']!=metrics['source_EEG_exams']:
        raise ValueError('Final source/regression gates must pass before candidate publication')
    hashes=read(directory/'workers/code_hashes.json')
    if any(sha(p)!=h for p,h in hashes.items()):raise ValueError('Source changed since final worker run')
    published={};version='1.2+'+directory.name.removeprefix('eeg_v1_2_')
    for patient in patients():
        source=directory/'source_inputs'/patient;output=directory/'workers'/patient
        inputs=read(directory/'workers'/('request_'+patient+'.json'))['input_hashes']
        if any(sha(p)!=h for p,h in inputs.items()):raise ValueError('Source inputs changed')
        bundle=read(source/'source_bundle.json');pdfs={str(ROOT/'pdf_db'/patient/d['filename']):d['sha256'] for d in bundle['documents']}
        if any(sha(p)!=h for p,h in pdfs.items()):raise ValueError('Source PDFs changed')
        metadata=dict(module_version='1.2',parser_version='DETERMINISTIC_EEG_1.2',code_hash=digest(hashes),code_file_hashes=hashes,
            source_input_hashes=inputs,source_pdf_hashes=pdfs,eeg_source_directory=str(source),validation_phase=True,auto_promotion=False,initial_review_status='UNREVIEWED',
            model_configuration=dict(mode='DETERMINISTIC_ONLY',model=None,prompt=None,cloud_patient_calls=0),
            execution_evidence=read(output/'execution.json'),validation_test_results=dict(hard_gates={k:v==0 for k,v in read(directory/'hard_gates.json').items()},
                hard_gate_violation_counts=read(directory/'hard_gates.json'),regression_summary=str(directory/'eeg_v1_2_regression_summary.md')))
        published[patient]=store.publish(patient,'eeg',version,{f.name:f.read_bytes() for f in output.glob('eeg_*.json')},metadata)
    atomic(directory/'published_candidates.json',published)
    return published


if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    elif sys.argv[1]=='worker':
        from .workbench_worker import guard
        sys.dont_write_bytecode=True;request=read(sys.argv[2]);guard(Path(request['output']));worker(request)
    elif sys.argv[1]=='run':run(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else 'workers')
    elif sys.argv[1]=='publish':publish(sys.argv[2])
