"""Finalize retained raw proposals with full source-scope checks; no model calls."""
import copy
from datetime import datetime,timezone
from pathlib import Path

from .tfne_v02_pilot import read,write,ARTIFACT
from .tfne_v02_validation import ground
from .tfne_sources import span_index
from .workbench_store import ROOT,sha


def revalidate(directory):
    directory=Path(directory).resolve()
    from .workbench_worker import guard
    guard(directory)
    if (directory/'published_artifacts.json').exists():raise ValueError('Cannot rewrite published candidates')
    freeze=read(directory/'cohort_freeze.json');hashes={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'src/caa_pdf_extractor').glob('tfne_v02*.py')}
    records=[]
    for patient in freeze['patient_ids']:
        folder=directory/'workers'/patient;path=folder/ARTIFACT;backup=folder/'artifact_before_final_source_scope_validation.json'
        if backup.exists():raise ValueError('Final source-scope validation already performed')
        artifact=read(path);write(backup,artifact)
        source=read(directory/'inputs'/f'{patient}.json');index=span_index(source['sections'])
        proposals=read(folder/'raw_phenotype_proposals.json');flags=read(folder/'validated_detections.json')['validation_flags']
        episodes=[]
        for prior,raw in zip(artifact['episodes'],proposals):
            e,f=ground(raw['proposal'],raw['detection'],index)
            e['episode_id']=prior['episode_id'];episodes.append(e);flags+=f
        if len(episodes)!=len(artifact['episodes']):raise ValueError('Episode cardinality changed during deterministic finalization')
        artifact.update(episodes=episodes,validation_flags=flags,final_validation_version='TFNE_V02_EXACT_SOURCE_SCOPE_1',
            final_validation_timestamp=datetime.now(timezone.utc).isoformat(),final_validation_code_hashes=hashes)
        write(path,artifact)
        records.append(dict(patient_id=patient,before_sha256=sha(backup),after_sha256=sha(path),episode_count=len(episodes)))
    write(directory/'source_scope_revalidation.json',dict(records=records,additional_model_calls=0,
        reason='Field excerpts cannot hide source negation or uncertainty immediately outside the quoted substring. Original proposals and before-validation candidates retained.',validation_code_hashes=hashes))
    print('FINAL_SOURCE_SCOPE_VALIDATION_COMPLETE',len(records),'patients; 0 additional model calls')


if __name__=='__main__':
    import sys
    revalidate(sys.argv[1])
