"""Revalidate retained local proposals without any additional model/patient calls."""
from datetime import datetime,timezone
from pathlib import Path
from .workbench_store import ROOT,read,sha
from .module_artifacts import digest
from .tfne_pilot import ARTIFACT,write
from .tfne_granite import ground,PROMPT_PATH


def revalidate(directory):
    directory=Path(directory);freeze=read(directory/'cohort_freeze.json')
    if (directory/'published_artifacts.json').exists():raise ValueError('Published TFNE artifacts cannot be rewritten')
    if sha(PROMPT_PATH)!=freeze['prompt_hash']:raise ValueError('Prompt changed since final model requests')
    code={str(p):sha(p) for p in (ROOT/'src/caa_pdf_extractor').glob('tfne_*.py')}
    for patient in freeze['patient_ids']:
        output=directory/'workers'/patient;artifact=read(output/ARTIFACT)
        if not (output/'before_additional_source_validation.json').exists():(output/'before_additional_source_validation.json').write_bytes((output/ARTIFACT).read_bytes())
        source=read(directory/'inputs'/f'{patient}.json');episodes,flags=ground(read(output/'raw_episodes.json'),source['sections'])
        for n,e in enumerate(episodes,1):e.update(episode_id=f'{patient}:TFNE01:'+digest([n,e['symptom_description'],e['field_evidence']])[:18],physician_review_status='UNREVIEWED')
        artifact.update(episodes=episodes,validation_flags=flags,request_execution_code_hashes=artifact['code_hashes'],code_hashes=code,
            source_validation_version='TFNE_GROUNDING_0.1_source_checks_1',source_validation_timestamp=datetime.now(timezone.utc).isoformat(),granite_prompt_revision=freeze['prompt_revision'])
        write(output/ARTIFACT,artifact)
    write(directory/'source_revalidation.json',dict(reason='Validate explicit quantities, exact source wording, negated resolution and seizure-feature grounding against the same retained Granite proposals. No screening-rule, patient or source-input change.',
        patients=freeze['patient_ids'],additional_model_calls=0,code_hashes=code))


if __name__=='__main__':
    import sys
    revalidate(sys.argv[1])
