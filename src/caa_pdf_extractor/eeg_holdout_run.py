"""Bounded, one-pass frozen EEG 1.1 holdout orchestration; no other modules."""
import json
import subprocess
import sys
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha
from .workbench_eeg import EEGWorkbenchStore
from .eeg_holdout_design import verify_freeze


class EEGHoldoutStore(EEGWorkbenchStore):
    def enabled_patients(self):
        cohort=read(self.root/'eeg_v1_1_holdout_cohort.json')
        ids=cohort['patient_ids']
        if not 10<=len(ids)<=15 or len(set(ids))!=len(ids) or set(ids)&set(cohort['excluded_patient_ids']):raise ValueError('Invalid fresh bounded holdout')
        if set(ids)&set(read(self.root/'eeg_validation_cohort.json')['patient_ids']):raise ValueError('Development overlap')
        if cohort['module_version']!='1.1' or not cohort['selection_before_extraction']:raise ValueError('Invalid holdout version or selection')
        return set(ids)


def check_design(directory):
    directory=Path(directory).resolve();freeze=verify_freeze(directory)
    cohort=read(STABLE_ROOT/'eeg_v1_1_holdout_cohort.json')
    if cohort!=read(directory/'frozen_cohort.json') or Path(cohort['output_directory'])!=directory:raise ValueError('Cohort changed')
    for name,key in [('eeg_v1_1_holdout_selection.csv','selection_sha256'),('eeg_v1_1_holdout_exclusions.csv','exclusions_sha256')]:
        if sha(directory/name)!=cohort[key]:raise ValueError('Frozen cohort design changed')
    inventory=read(directory/'source_inventory_freeze.json')
    if not inventory['before_extraction'] or any(sha(p)!=h for p,h in inventory['files'].items()):raise ValueError('Independent source inventory changed')
    if read(directory/'selected_patients.json')!=cohort['patient_ids']:raise ValueError('Selection mismatch')
    EEGHoldoutStore().enabled_patients()
    return freeze,cohort


def worker(request):
    # Loaded in a separate audited process; never imports a clinical DB or model.
    if any(sha(p)!=h for p,h in request['input_hashes'].items()):raise ValueError('Worker input changed')
    check_design(Path(request['directory']));EEGHoldoutStore().require_enabled(request['patient_id'])
    from .extraction.eeg_pipeline_v11 import assemble
    from .eeg_v11_gates import gates
    source=Path(request['source_dir']);bundle=read(source/'source_bundle.json')
    if bundle['patient_id']!=request['patient_id']:raise ValueError('Patient mismatch')
    # No preflight extraction, baseline/development input, reconciliation tuning or retries.
    result=assemble(bundle,read(source/'encounters.json'),source_reconciliation=None,baseline_exams=None)
    counts=gates(result,bundle);output=Path(request['output'])
    for name,rows in result.items():(output/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    execution=dict(module_version='1.1',patient_id=request['patient_id'],hard_gate_violation_counts=counts,
        invocation_count=1,Granite_calls=0,database_writes=0,cloud_patient_calls=0,
        guard='OS audit hook blocks sockets, clinical DB access, child processes and writes outside fresh worker output',
        source_inventory_was_frozen=True,logic_changed=False)
    (output/'execution.json').write_text(json.dumps(execution,indent=2),encoding='utf-8')


def run(directory,resume=False):
    directory=Path(directory).resolve();freeze,cohort=check_design(directory)
    if not resume and ((directory/'workers').exists() or (directory/'published_candidates.json').exists()):raise ValueError('One-pass holdout already started; no automatic reruns')
    audit=STABLE_ROOT/'module_run_audit.jsonl'
    if not resume:(directory/'original_module_run_audit.jsonl').write_bytes(audit.read_bytes() if audit.exists() else b'')
    store=EEGHoldoutStore();published=read(directory/'published_candidates.json') if (directory/'published_candidates.json').exists() else {};version='1.1+holdout.'+directory.name.removeprefix('eeg_v1_1_holdout_')
    for patient in cohort['patient_ids']:
        if patient in published:continue
        check_design(directory);source=directory/'source_inputs'/patient
        bundle=read(source/'source_bundle.json')
        pdfs={str(ROOT/'pdf_db'/patient/d['filename']):d['sha256'] for d in bundle['documents']}
        if any(sha(p)!=h for p,h in pdfs.items()):raise ValueError('Source PDF changed')
        output=directory/'workers'/patient
        inputs={str(p):sha(p) for p in source.glob('*.json')}
        inputs.update({str(directory/name):sha(directory/name) for name in ('frozen_cohort.json','source_inventory_freeze.json','eeg_holdout_source_inventory.csv')})
        inputs.update(freeze['files']);inputs.update(pdfs)
        request=dict(directory=str(directory),patient_id=patient,source_dir=str(source),output=str(output),input_hashes=inputs)
        path=directory/('request_'+patient+'.json')
        if output.exists():
            if not resume or not (output/'execution.json').exists():raise ValueError('Existing worker cannot be retried; recover completed output explicitly')
            original=read(path)
            if any(sha(p)!=h for p,h in original['input_hashes'].items()):raise ValueError('Recovered input changed')
        else:
            output.mkdir(parents=True,exist_ok=False);atomic(path,request)
            completed=subprocess.run([sys.executable,'-B','-X','utf8','-m','caa_pdf_extractor.eeg_holdout_run','--worker',str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
            (directory/('worker_'+patient+'.log')).write_text(completed.stdout+completed.stderr,encoding='utf-8')
            if completed.returncode:raise RuntimeError('Holdout worker failed; no retry: '+patient+' '+completed.stderr[-1600:])
        execution=read(output/'execution.json');counts=execution['hard_gate_violation_counts']
        metadata=dict(module_version='1.1',parser_version=freeze['parser_version'],code_hash=freeze['code_hash'],code_file_hashes=freeze['files'],
            source_pdf_hashes=pdfs,source_input_hashes=inputs,eeg_source_directory=str(source),validation_phase=True,holdout=True,
            auto_promotion=False,initial_review_status='UNREVIEWED',fatal_errors=[],execution_evidence=execution,
            source_inventory=dict(path=str(directory/'eeg_holdout_source_inventory.csv'),sha256=sha(directory/'eeg_holdout_source_inventory.csv')),
            validation_test_results=dict(hard_gates={k:v==0 for k,v in counts.items()},hard_gate_violation_counts=counts))
        published[patient]=store.publish(patient,'eeg',version,{p.name:p.read_bytes() for p in output.glob('eeg_*.json')},metadata)
        atomic(directory/'published_candidates.json',published)
        print('EEG_HOLDOUT_CANDIDATE',patient,len(read(output/'eeg_exams.json')),'gate flags',sum(counts.values()),flush=True)
    check_design(directory)
    return published


if __name__=='__main__':
    sys.dont_write_bytecode=True
    if sys.argv[1]=='--worker':
        from .workbench_worker import guard
        request=read(sys.argv[2]);guard(Path(request['output']));worker(request)
    else:run(Path(sys.argv[1]),resume='--resume-completed' in sys.argv)
