"""Explicit bounded local Granite preparation for EEG validation snapshots."""
import sys
from pathlib import Path
from .workbench_store import read,atomic
from .extraction.eeg_sources import discover
from .extraction.eeg_semantic import eligible,infer
from .llm.granite import GraniteClient


def main(directory):
    directory=Path(directory);client=GraniteClient();client.check_model()
    results=read(directory/'semantic_results.json') if (directory/'semantic_results.json').exists() else {}
    selected=read(directory/'selected_patients.json')
    for patient in selected:
        reports,_=discover(read(directory/'source_inputs'/patient/'source_bundle.json'))
        candidates=[r for r in reports if eligible(r) and len(r['eeg_report_text'])<6500]
        if not candidates:continue
        report=max(candidates,key=lambda r:len(r['eeg_report_text']))
        identity=report['report_occurrence_id']
        if identity not in results:
            results[identity]=infer(report,client)
            atomic(directory/'semantic_results.json',results)
        print(patient,identity,len(results[identity]['proposals']),flush=True)
    print('LOCAL_GRANITE_CALLS',len(results),flush=True)


if __name__=='__main__':main(sys.argv[1])
