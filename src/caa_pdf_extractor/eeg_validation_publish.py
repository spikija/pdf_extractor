"""Publish only the explicit EEG-enriched validation registry as candidate revisions."""
import json
import subprocess
import sys
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha,now
from .workbench_eeg import EEGWorkbenchStore
from .module_artifacts import digest
from .eeg_validation import preflight


def main(directory,repair_patient=None):
    directory=Path(directory).resolve();patients=read(directory/'selected_patients.json')
    if not 15<=len(patients)<=20 or len(set(patients))!=len(patients):raise ValueError('Invalid EEG validation subset')
    preflight(directory)
    registry=STABLE_ROOT/'eeg_validation_cohort.json'
    if registry.exists() and read(registry)['patient_ids']!=patients:raise ValueError('EEG registry already exists with a different subset')
    if not registry.exists():atomic(registry,dict(patient_ids=patients,status='VALIDATION_ONLY',created_at=now(),source=str(directory)))
    store=EEGWorkbenchStore();published=read(directory/'published_candidates.json') if repair_patient else {}
    if repair_patient=='remaining':patients=[p for p in patients if not published[p]['version'].endswith('-r2')]
    elif repair_patient:
        if repair_patient not in patients:raise ValueError('Repair patient outside EEG registry')
        patients=[repair_patient]
    code_files=list((ROOT/'src/caa_pdf_extractor/extraction').glob('eeg_*.py'))+list((ROOT/'src/caa_pdf_extractor').glob('*eeg*.py'))
    code_hashes={str(p):sha(p) for p in code_files}
    for patient in patients:
        source=directory/'source_inputs'/patient;output=directory/('workers_r2' if repair_patient else 'workers')/patient
        output.mkdir(parents=True,exist_ok=False)
        inputs={str(p):sha(p) for p in source.glob('*.json')}
        inputs.update({str(p):sha(p) for p in [directory/'semantic_results.json',directory/'source_reconciliation.json',registry]})
        pdf_hashes={str(p):sha(p) for p in (ROOT/'pdf_db'/patient).glob('*.pdf')}
        request=dict(patient_id=patient,stable_root=str(STABLE_ROOT),source_dir=str(source),output=str(output),input_hashes=inputs,
            semantic_results=str(directory/'semantic_results.json'),source_reconciliation=str(directory/'source_reconciliation.json'))
        request_path=directory/('request_'+patient+'.json');atomic(request_path,request)
        completed=subprocess.run([sys.executable,'-B','-m','caa_pdf_extractor.workbench_eeg_worker',str(request_path)],cwd=ROOT,capture_output=True,text=True)
        (directory/('worker_'+patient+'.log')).write_text(completed.stdout+completed.stderr,encoding='utf-8')
        if completed.returncode:raise RuntimeError('EEG worker failed for '+patient+': '+completed.stderr[-2000:])
        execution=read(output/'execution.json')
        artifacts={p.name:p.read_bytes() for p in output.glob('eeg_*.json')}
        version='1.0+'+directory.name.removeprefix('eeg_validation_')+('-r2' if repair_patient else '')
        metadata=dict(module_version='1.0',code_hash=digest(code_hashes),code_file_hashes=code_hashes,
            source_pdf_hashes=pdf_hashes,source_input_hashes=inputs,eeg_source_directory=str(source),
            validation_test_results=dict(hard_gates={k:v==0 for k,v in execution['hard_gate_violation_counts'].items()},hard_gate_violation_counts=execution['hard_gate_violation_counts']),
            fatal_errors=[],execution_evidence=execution,validation_phase=True,auto_promotion=False)
        published[patient]=store.publish(patient,'eeg',version,artifacts,metadata)
        print(patient,version,len(read(output/'eeg_exams.json')),flush=True)
        atomic(directory/'published_candidates.json',published)


if __name__=='__main__':main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
