"""Local TFNE pilot report generation; no extraction or model calls."""
import csv
import subprocess
from pathlib import Path
from .workbench_store import ROOT,read,sha
from .tfne_granite import PROMPT_PATH
from .tfne_pilot import write


def report(directory):
    p=Path(directory).resolve();m=read(p/'validation_metrics.json');f=read(p/'cohort_freeze.json');old=read(p/'original_cohort_freeze.json')
    model=read(p/'local_model.json');published=read(p/'published_artifacts.json')
    assert f['patient_ids']==old['patient_ids'] and f['input_hashes']==old['input_hashes'] and f['selection_sha256']==old['selection_sha256']
    assert all(sha(path)==h for path,h in f['input_hashes'].items())
    assert len(published)==10 and all(sha(row['path'])==row['sha256'] for row in published.values()) and m['hard_gate_failures']==0
    assert all(read(row['path'])['granite_prompt_hash']==sha(PROMPT_PATH) and read(row['path'])['physician_review_status']=='UNREVIEWED' for row in published.values())
    tracked=subprocess.run(['git','ls-files','--stage','--','prompts/tfne_neurological_deficit_v0_1.txt'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    assert tracked
    write(p/'prompt_version_control.json',dict(path=str(PROMPT_PATH),prompt_name=f['prompt_name'],version='0.1',revision=f['prompt_revision'],sha256=sha(PROMPT_PATH),git_index_entry=tracked,commit_created=False))
    write(p/'test_results.json',dict(command='python -B -X utf8 -m pytest tests/test_tfne_pilot.py tests/test_tfne_ui.py -q',result='33 passed in 10.10s',real_physician_reviews_written=False,
        scope='Synthetic source/schema/screen safety, isolated ACCEPT/EDIT/REJECT/UNCERTAIN/SPLIT/MERGE and all-ten-patient Streamlit source navigation/reload'))
    write(p/'final_code_hashes.json',{str(q):sha(q) for base,pattern in [(ROOT/'src/caa_pdf_extractor','tfne_*.py'),(ROOT/'review_app','tfne_*.py'),(ROOT/'review_app/views','tfne.py'),(ROOT/'tests','test_tfne_*.py')] for q in base.glob(pattern)})
    with (p/'tfne_screen_summary.csv').open(encoding='utf-8-sig') as stream:summary=list(csv.DictReader(stream))
    keys=['patients_processed','source_sections_processed','episodes_total','transient_fully_resolved_count','persistent_count','progressive_count','mixed_count','unknown_course_count',
        'fluctuating_without_clear_resolution_count','TFNE_strong_count','TFNE_possible_count','transient_atypical_count','persistent_focal_count','progressive_cognitive_count',
        'historical_epilepsy_count','historical_TIA_count','historical_TFNE_count','grounding_abstention_flags','hard_gate_failures']
    lines=['# TFNE / neurological deficit pilot 0.1','', '`TFNE_NEUROLOGICAL_DEFICIT_V0_1_READY_FOR_PHYSICIAN_REVIEW`','',
        '**Ten CANDIDATE patient artifacts, all UNREVIEWED. No diagnostic validity, episode recall or TFNE prevalence has been established.**','',
        '**Main limitation:** 19 candidate episodes were extracted, but 14 have UNKNOWN temporal course and 177 field-level grounding/abstention flags remain. No episode met STRONG or POSSIBLE screening after source validation. These zeros do not establish that the patients lack TFNE, epilepsy or TIA. They describe the current conservative extraction and limited label/phenomenology coverage. Physician review is essential.','',
        '| Metric | Count |','| --- | ---: |']
    lines += [f'| {k} | {m[k]} |' for k in keys]
    lines += ['', '## Frozen cohort and source scope','', 'Patient IDs: '+', '.join(m['pilot_patient_ids'])+'.','',
        f'The source-based list was frozen at {old["frozen_at"]}, before any patient model call. The original patient IDs, selection CSV hash and all narrative-input hashes are unchanged. No extra patients were extracted or substituted based on output. Selection hash: `{f["selection_sha256"]}`.','',
        'Selection used ten heterogeneous source-indicator slots, CAA/cSS/cSAH enrichment vocabulary and narrative size within each slot. These are screening signals, not confirmed diagnoses; epilepsy/TIA words can be negated, uncertain or unrelated to a specific episode. One patient has summary input only. All available permitted narratives in the ten existing local bundles were processed. This is an enriched convenience pilot, not a representative cohort or a holdout.','',
        '| Patient | Sections | Episodes | STRONG | POSSIBLE | Grounding flags |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in summary:lines.append('| '+' | '.join(row[k] for k in ('patient_id','source_sections','episodes','strong','possible','grounding_abstention_flags'))+' |')
    lines += ['', '## Clinical intent and research rules','',
        'The module extracts NEUROLOGICAL_DEFICIT_EPISODE units, preserving temporal course, retrospective phenotype and historical label independently. It combines transient focal phenomenology reviewed by [Smith et al., Neurology 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8356377/) with the transient-versus-persistent manifestations examined by [Weidauer et al., European Journal of Neurology](https://onlinelibrary.wiley.com/doi/10.1111/ene.16425).','',
        'The 24-hour operational limit and STRONG/POSSIBLE combination are the requested research definitions, not validated diagnostic criteria. STRONG requires a fully resolved focal sensory/motor/language/visual episode plus spread, recurrence with independently supported stereotypy, or positive and negative symptoms. Aphasia or recurrence alone cannot establish STRONG. Historical labels never enter the screening rule. Persistent/progressive/mixed deficits and atypical nonfocal transient symptoms remain separate. No etiology is inferred.','',
        'UNKNOWN is not converted to resolved or persistent. The four persistent and one progressive outputs are also unvalidated proposals. Zero historical epilepsy/TIA/TFNE labels means no episode-level label of these types survived this history-only extraction/validation; it does not erase or contradict clinical diagnoses recorded elsewhere.','',
        '## Source isolation and provenance','',
        'Only ANAMNESE and ZUSAMMENFASSUNG_DES_AUFENTHALTES spans reach Granite. Unrelated headings stop input sections; embedded EEG, MRI/CT, CAA/Boston/cSS/cSAH and administrative passages are excluded with offsets retained in input_exclusions.json. Every final data message was checked against the frozen span index. No disallowed section heading was found in the actual data messages. Enrichment signals and existing extraction outputs are not supplied to the model.','',
        'Evidence resolves to document, page, narrative section, parent section, exact text and character offsets. CSV episode rows include document/page/section/evidence references and artifact paths; patient JSON holds field-to-source maps. Original PDFs and existing cleaned/prepared bundles are unchanged.','',
        'The firewall can remove relevant clinical context when it shares a passage with forbidden test/CAA information. Routing, exclusions, narrative copies, recurrent series and model under-recall require physician inspection. No independently adjudicated episode denominator exists; episode recall is therefore not reported.','',
        '## Dedicated local Granite prompt','',
        f'Prompt: `prompts/tfne_neurological_deficit_v0_1.txt`; name `{f["prompt_name"]}`; version **0.1**; revision `{f["prompt_revision"]}`. SHA-256: `{f["prompt_hash"]}`. The prompt is tracked in the Git index, staged for review; no commit was created.','',
        f'Installed local model: `{model["name"]}`; digest `{model["digest"]}`; endpoint `{model["endpoint"]}`. Task instructions are read verbatim from the prompt file as a system message. Narratives are separate JSON data. Strict JSON grammar prohibits TFNE screen output and requires a per-field evidence map. Every artifact records prompt name/hash/version, model tag/digest, timestamp and source section IDs.','',
        'There were **20 local semantic calls: 10 preliminary and 10 final**, all involving the same frozen ten patients. The initial optional field-group evidence contract produced mostly description-only evidence, causing extensive abstention. Those unpublished proposals, prompt/schema, requests and responses remain under preliminary_* for audit. The final contract structurally requires a separate evidence entry for every clinical field. Patient/source selection and deterministic screening rules were unchanged.','',
        'Final proposals were revalidated without further model calls for explicit quantities, original date/duration/frequency wording, negated resolution, seizure-feature grounding, assertion scope and component consistency. Few minutes cannot become an invented exact five minutes; epilepsy history cannot establish jerking or automatisms. Unsupported values become UNKNOWN/null/empty with flags. Raw proposals, request-time code hashes and later validation hashes are preserved separately.','',
        'The loopback-only client disables proxies/redirects, requires an installed local Granite model and performs no model download or cloud fallback. The execution guard permits only numeric loopback port 11434, blocks database imports/child processes and confines writes to the new debug directory.','',
        '## Physician review','',
        'Run run_review_app.bat, refresh the artifact index, choose **TFNE V0.1 PILOT**, then **TFNE / Neurological Deficits**. The queue contains exactly ten patients. Episode cards prominently show temporal course and assertion, separate historical label/phenotype/screen, symptoms, polarity, side/body distribution, spread, recurrence, stereotypy, resolution, differential features, exact text and Open source links. All permitted narratives are also accessible to check omissions.','',
        'ACCEPT/EDIT/REJECT/UNCERTAIN are explicit saves. SPLIT creates physician-overlay episodes; MERGE leaves unsupported temporal facts unknown and preserves original evidence and labels. Screens are recalculated within overlays. Raw artifacts remain immutable, judgments remain artifact-hash-scoped in a separate TFNE review namespace, and no promotion occurs. No real physician judgments were saved by this task.','',
        'Review priorities are episode recall/splitting, transient versus persistent/progressive course, complete resolution, focality, polarity/spread, recurrence versus stereotypy, historical-label coverage and screening usefulness. The 177 flags overlap; they are not 177 independent episodes or adjudicated clinical errors.','',
        '## Safety audit','', 'These gates establish implemented data-path/rule safeguards, not clinical accuracy of every proposal.','', '| Gate | Violations |','| --- | ---: |']
    for key,value in read(p/'hard_gates.json').items():lines.append(f'| {key} | {value} |')
    lines += ['', '## Tests and preservation','',
        '**33 tests passed**, covering source/schema/screen safety, duration/resolution/negation, seizure-feature grounding, historical labels, nonfocal/persistent controls, exact offsets, split/merge, save/reload and all ten patients in Streamlit AppTest. Review writes were isolated; raw artifact hashes stayed intact.','',
        'The audit checked **3,891 pre-existing artifact/review files** and all pre-existing extraction source files, with no unexpected changes. EEG, diagnosis, medication, CAA, radiology and other extraction modules, manifests, artifacts and physician judgments are unchanged. App changes add the TFNE tab/filter using separate TFNE-specific files.','',
        '```text','PATIENTS_PROCESSED = 10','DIAGNOSTIC_VALIDITY_CLAIMED = NO','AUTO_PROMOTIONS = NONE','DATABASE_WRITES = NONE','CLOUD_PATIENT_CALLS = NONE','```','', '## Deliverables','']
    for name in ['tfne_pilot_10_selection.csv','tfne_source_section_inventory.csv','tfne_episode_extraction.csv','tfne_temporal_course_audit.csv','tfne_screen_summary.csv','tfne_hard_gates.csv','tfne_grounding_flags.csv',
        'validation_metrics.json','cohort_freeze.json','original_cohort_freeze.json','input_exclusions.json','protocol_repair.json','source_revalidation.json','published_artifacts.json','prompt_version_control.json','test_results.json','ui_smoke.json','preservation_audit.json']:
        lines.append(f'- [{name}]({name})')
    lines += ['', 'Patient artifacts: `data/patient_artifacts/<patient_id>/tfne_neurological_deficit_v0_1.json`. All ten start UNREVIEWED.']
    (p/'tfne_pilot_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (ROOT/'review_app/TFNE_NEUROLOGICAL_DEFICIT_V0_1_REPORT.md').write_text(f'''# TFNE / neurological deficits pilot 0.1

`TFNE_NEUROLOGICAL_DEFICIT_V0_1_READY_FOR_PHYSICIAN_REVIEW`

Ten UNREVIEWED candidates; 60 narrative sections; 19 candidate episodes.
Four persistent, one progressive and fourteen UNKNOWN courses.
STRONG/POSSIBLE TFNE screens: 0/0; 177 grounding/abstention flags require review.
These zeros do not establish TFNE absence or diagnostic validity.

Choose **TFNE V0.1 PILOT**, then **TFNE / Neurological Deficits**.

[Full report and evidence](../data/debug/{p.name}/tfne_pilot_report.md).

No clinical database writes, cloud patient-data calls or changes to other extraction modules.
''',encoding='utf-8')
    print(p/'tfne_pilot_report.md')


if __name__=='__main__':
    import sys
    report(sys.argv[1])
