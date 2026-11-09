"""Exactly one registered EEG 1.1 patient; offline assembly in a restricted worker."""
import json
import sys
from pathlib import Path
from .workbench_store import read,sha
from .workbench_worker import guard


def execute(request):
    from .workbench_eeg import EEGWorkbenchStore
    EEGWorkbenchStore(request['stable_root']).require_enabled(request['patient_id'])
    if any(sha(path)!=expected for path,expected in request['input_hashes'].items()):raise ValueError('EEG 1.1 source/input changed')
    from .extraction.eeg_pipeline_v11 import assemble
    from .eeg_v11_gates import gates
    source=Path(request['source_dir']);bundle=read(source/'source_bundle.json')
    if bundle['patient_id']!=request['patient_id']:raise ValueError('Source patient mismatch')
    result=assemble(bundle,read(source/'encounters.json'),read(request['source_reconciliation']),read(request['baseline_exams']))
    counts=gates(result,bundle)
    if any(counts.values()):raise ValueError('EEG 1.1 gate violations: '+str(counts))
    output=Path(request['output'])
    for name,rows in result.items():(output/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'execution.json').write_text(json.dumps(dict(module_version='1.1',patient_id=request['patient_id'],hard_gate_violation_counts=counts,
        guard='OS audit hook; sockets, clinical DB access and child processes blocked; writes limited to fresh worker directory',Granite_calls=0),indent=2),encoding='utf-8')


if __name__=='__main__':
    sys.dont_write_bytecode=True
    request=read(sys.argv[1]);guard(Path(request['output']));execute(request)
