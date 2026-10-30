"""Versioned consolidation preflight. Extraction is blocked until all adapters pass.

python -m caa_pdf_extractor.full_pipeline --verify-only
No database, PDF reader or model is invoked by preflight itself.
"""
import argparse
import ast
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION='FULL_PIPELINE_0.1.0_CONSOLIDATION'
ROOT=Path(__file__).resolve().parents[2]
# Ordered registry is an inventory, not a claim of an operational full path.
MODULES=[
 ('PDF parsing','utils','parse_pdf','IMPLEMENTED_IN_PRODUCTION'),
 ('Section detection','preparation.patient_bundle','prepare_document','IMPLEMENTED_IN_PRODUCTION'),
 ('Demographics','extraction.case_rules','extract','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Diagnosis extraction / history V2','extraction.history_v2','extract_bundle','IMPLEMENTED_IN_PRODUCTION'),
 ('Diagnosis eligibility','extraction.diagnosis_canonical_v21','validate_eligibility','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Diagnosis canonicalization / AF / valves','extraction.diagnosis_taxonomy_v23','normalize','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Encounters / clinical presentation','extraction.encounter_granite_v2','validate','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Sensory phenotype','extraction.sensory_presentation','validate','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Medications','extraction.medication_taxonomy_v23','normalize','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Laboratories','extraction.laboratory_layout','extract_page','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Radiology V2 / MRI CT phenotype','extraction.radiology_v2','normalize','IMPLEMENTED_IN_PRODUCTION'),
 ('Neurosonology','extraction.neurosonology_v23','parse_exam','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('EEG','extraction.eeg','normalize','IMPLEMENTED_IN_PRODUCTION'),
 ('PET','extraction.examination_inputs','select_pet_inputs','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('MMSE / cognitive testing','extraction.cognitive','extract_bundle','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Cognitive canonical identities','extraction.cognitive_canonical','canonicalize','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Clinical scales / mRS / NIHSS','extraction.clinical_scales_v23','extract_section','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Retrospective PRE_EVENT CFS','extraction.functional_status','check_estimate','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Retrospective PRE_EVENT mRS','extraction.functional_status','validate_candidates','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('CAA source assertions','extraction.caa_diagnostic','validate_semantic','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Boston v2','extraction.caa_criteria_v2','boston_v2','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Edinburgh CT / MRI routing','extraction.caa_criteria_v2','caa_imaging_assessment','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('CAA longitudinal patient status','extraction.caa_evidence_v21','assemble_patient','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('CSF / amyloid / tau','extraction.csf_analytes','extract_row','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Anticoagulation decisions','extraction.anticoagulation_decisions_v23','extract','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('CHA2DS2-VA','extraction.research_scores_v23','cha2ds2_va','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('ORBIT','extraction.research_scores_v23','orbit','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('STESS','extraction.research_scores_v23','stess','IMPLEMENTED_BUT_NOT_CONNECTED'),
 ('Research review outputs','v23_review','generate','LEGACY'),
 ('Legacy cohort extraction','full_cohort_extraction','extract','LEGACY'),
 ('Legacy enrichment','full_cohort_enrichment','enrich','LEGACY'),
]
BLOCKERS={
 'source_preparation':'full_cohort_sources.prepare depends on a dated frozen snapshot for document IDs; no baseline-independent cached discovery adapter is connected.',
 'semantic_patient_extraction':'full_cohort_extraction.extract and enrichment require dated debug baselines, source reuse and separate manual phases; no baseline-independent local Granite patient adapter is connected.',
 'diagnosis_eligibility_and_events':'Source-bound eligibility/grouping, CAA versus inflammatory CAA, ischemic stroke/primary ICH/hemorrhagic transformation and recurrent-event ownership need a fresh-patient adapter before taxonomy can be promoted.',
 'CAA_evidence_assembly':'Separate assertion, scan phenotype, episode aggregation and patient milestones are implemented but not wired to fresh validated semantic outputs.',
 'retrospective_function':'Shared validators are refactored, but the local phenotype/anchor prompt runner remains in isolated/re-audit wrappers. Known adequate-baseline CFS regression still abstains; no physician number is copied.',
 'scale_event_linkage':'Explicit extraction is available, but the cohort review still has unresolved stroke/event linkage; generic production ownership resolution and assessment storage are unverified.',
 'clinical_scale_assessment':'A common source-bound artifact exists in review code; master patient-output adapter and persistence-free schema integration remain unverified.',
 'neurosonology_completeness':'Structured module exists; NORMAL/NEAR_OCCLUSION and some collateral/velocity/vertebral-negative fields are not fully implemented; master report-to-exam adapter is missing.',
 'risk_and_decision_indices':'CHA bounds are integrated into shared scoring; validated fresh-patient AF/OAC index and negative-ascertainment adapters, ORBIT dated labs and STESS status ownership remain disconnected.',
 'decision_contract':'Existing OAC extractor uses START/CONTINUE/WITHHOLD/STOP/PAUSE/SWITCH labels and review reasons; required normalized decision/reason-strength contract is not yet wired.',
 'research_artifact_export':'Review generation consumes frozen cohort files; no unified fresh-patient report adapter or final clinical validation gate is connected.'}

def module_path(module):return ROOT/'src/caa_pdf_extractor'/Path(*module.split('.')).with_suffix('.py')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory():
    rows=[]
    for feature,module,symbol,status in MODULES:
        path=module_path(module);exists=path.exists()
        definitions={n.name for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))} if exists else set()
        rows.append(dict(feature=feature,module='caa_pdf_extractor.'+module,callable=symbol,path=str(path),implementation_status=status if exists and symbol in definitions else 'MISSING',
            consolidation_status='INTEGRATED_FROM_ISOLATED_PATCH' if module=='extraction.functional_status' else 'PRESERVED_AND_ACTIVE' if status=='IMPLEMENTED_IN_PRODUCTION' else 'PRESENT_BUT_NOT_ACTIVE',
            connected_to_complete_master=False,definition_verified=exists and symbol in definitions,sha256=sha(path) if exists else None))
    return rows

def manifest(rows):
    paths=sorted((ROOT/'src/caa_pdf_extractor').rglob('*.py'))
    dictionary=ROOT/'data/reference/medication_dictionary.csv'
    try:commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    except (subprocess.SubprocessError,OSError):commit=None
    hashes={str(p.relative_to(ROOT)):sha(p) for p in paths}
    versions={}
    for r in rows:
        constants={}
        for n in ast.parse(Path(r['path']).read_text(encoding='utf-8')).body:
            if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str):
                for t in n.targets:
                    if isinstance(t,ast.Name) and ('VERSION' in t.id):constants[t.id]=n.value.value
        versions[r['module']]=constants or {'version_basis':'SOURCE_HASH','sha256':r['sha256']}
    return dict(pipeline_version=VERSION,git_commit=commit,working_tree_code_hashes_are_authoritative=True,creation_timestamp=datetime.now(timezone.utc).isoformat(),
        production_ready=False,critical_final_answer='NO',blockers=BLOCKERS,module_versions=versions,
        taxonomy_version='DIAGNOSIS_TAXONOMY_V2.3',medication_dictionary_hash=sha(dictionary),
        caa_rules_version='BOSTON_V2_2022_TABLE2 / EDINBURGH_2018_FIG3 / SIMPLIFIED_2022; adequate-MRI-first',
        clinical_scale_rules_version='CLINICAL_SCALES_V2.3 / FUNCTIONAL_STATUS_V1',cha2ds2_va_rules_version='CHA2DS2_VA_BOUNDS_V1',orbit_rules_version='ORBIT_V2.3_DATED_COMPONENTS',
        prompt_hash_basis='SHA256 of prompt-template source modules; no patient-filled prompts or patient data included',prompt_hashes={k:v for k,v in hashes.items() if '/llm/' in k or k.endswith(('encounter_granite_v2.py','functional_status.py','caa_evidence_v21.py','diagnosis_canonical_v21.py'))},python_module_hashes=hashes,
        patients_processed=0,clinical_database_writes=0,cloud_patient_data_calls=0)

def verify(out):
    out.mkdir(parents=True,exist_ok=False)
    rows=inventory()
    with (out/'pipeline_module_inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    m=manifest(rows)
    # Entire regression suite precedes any permitted extraction, never after it.
    result=subprocess.run([sys.executable,'-m','pytest','-q','--junitxml='+str((out/'regression_results.xml').resolve())],cwd=ROOT,capture_output=True,text=True)
    (out/'regression_test_output.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    import xml.etree.ElementTree as ET
    tests=[]
    if (out/'regression_results.xml').exists():
        for t in ET.parse(out/'regression_results.xml').iter('testcase'):
            tests.append(dict(module=t.get('classname'),test=t.get('name'),seconds=t.get('time'),status='FAILED' if t.find('failure') is not None or t.find('error') is not None else 'SKIPPED' if t.find('skipped') is not None else 'PASSED'))
    with (out/'regression_test_results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['module','test','seconds','status']);w.writeheader();w.writerows(tests)
    m.update(regression_exit_code=result.returncode,regression_counts={s:sum(t['status']==s for t in tests) for s in ('PASSED','FAILED','SKIPPED')},status='PIPELINE_CONSOLIDATION_INCOMPLETE')
    (out/'pipeline_manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    graph='''# Production call-path inspection

Current complete artifact workflow is disconnected:

```mermaid
flowchart TD
 Sources[full_cohort_sources.prepare] --> Extract[full_cohort_extraction.extract]
 Baseline[Dated debug snapshots] --> Sources
 Baseline --> Extract
 Extract --> Enrich[full_cohort_enrichment.enrich]
 Enrich --> D[diagnosis CAA review scripts]
 D --> V[v23_review.generate]
 V --> R[cohort_score_reaudit]
```

Parser: utils.parse_pdf. Sectioning: preparation.patient_bundle.prepare_document -> sectionizer.sectionize -> diagnostic_splitter.split_diagnostic_procedures.
The documented ingestion executable is python -m caa_pdf_extractor.pdf_ingestion. It populates source provenance by default and supports --dry-run. It is not complete clinical extraction. README lists separate clinical extraction module commands; no complete production executable was found.
Active domain paths include clinical_history_v2 -> history_v2.extract_bundle, radiology_v2.extract_patient -> normalize, and eeg.extract_patient -> normalize. IMPLEMENTED_IN_PRODUCTION in the inventory refers to these domain paths, not to a complete master. Existing domain extraction was preserved.
Granite: llm.granite.GraniteClient (numeric loopback, installed local model only). Legacy cohort RetainedModel is defined in diagnosis_caa_v21_review.
Validation: domain Pydantic schemas, source-bound validators, caa_criteria_v2, research_scores_v23 and functional_status.
Review outputs: full_cohort_report, diagnosis CAA audit, v23_review, cohort_score_reaudit; these are separate commands, not one fresh-patient pipeline.

New master: full_pipeline.main -> verify -> inventory + full regression suite + versioned manifest -> readiness refusal.
The registration of a callable is NOT a production connection. No PDF discovery, patient model call or clinical database operation occurs in verification.
Shared function validators: isolated wrappers now import extraction.functional_status. CHA2DS2-VA: research_scores_v23.cha2ds2_va -> score_bounds.minimum_score.
The master --run path refuses before discovery until fresh-patient adapters and clinical regressions are verified.
'''
    (out/'production_pipeline_call_graph.md').write_text(graph,encoding='utf-8')
    report=['# Pipeline consolidation verification','',m['status'],'','Critical final answer: **NO**. Adding patient folders and running the master will currently refuse extraction; it cannot yet execute every approved module automatically.','',
        'No cohort extraction, clinical database writes or cloud patient-data calls were performed. No patient-specific number was promoted. Existing clinical extractors remain available.','',
        'General CFS phenotype/anchor schemas, numerical consistency checks, temporal/source validation and retrospective mRS validators were moved to extraction/functional_status.py. Isolated CLIs re-export those helpers for compatibility. The known CFS model-abstention regression remains a blocker; synthetic validator acceptance is not evidence of successful patient inference.','',
        'Shared CHA2DS2-VA now retains exact, minimum, maximum, component states and minimum-determined OAC recommendation. Unknown remains unknown. This recommendation is thromboembolic indication only; CAA/ICH/bleeding risk and preference remain separate. ORBIT and STESS mathematical logic was preserved.','',
        'Medication normalization and V2.3 diagnosis mappings exist and pass regression tests, but the legacy full extraction command does not call their newer layers. Anemia identities currently use subtype names (e.g. IRON_DEFICIENCY_ANEMIA), not a single canonical ANEMIA identity; this requested contract needs reconciliation without changing clinical values.','',
        'CAA routing itself is implemented (adequate MRI blocks Edinburgh), but the fresh-patient assembly/channel/milestone pipeline is not consolidated. CSF normalization preserves unspecified beta amyloid and reported isoforms/ratios; assay-specific p-tau codes may need a parent CSF_PTAU representation. No ratio or isoform was invented.','',
        'Structured neurosonology conservatively keeps unassessed vessels unknown. NORMAL and NEAR_OCCLUSION, vertebral/intracranial details and report-to-master wiring are not all complete. These are disclosed gaps, not verified support.','',
        '## Modules still not connected','']
    report += [f'- **{k}**: {v}' for k,v in BLOCKERS.items()]
    report += ['','## Verification','',json.dumps(m['regression_counts']),f'Regression exit code: {result.returncode}. Definition lookup gaps: '+', '.join(r['module']+':'+r['callable'] for r in rows if not r['definition_verified']), '',
        'Supported verification command: `python -m caa_pdf_extractor.full_pipeline --verify-only`. `--run` executes preflight then refuses with PIPELINE_CONSOLIDATION_INCOMPLETE. There is no database commit mode. A future extraction must be enabled only after the listed adapters are implemented and exercised with fresh synthetic patient bundles and the known functional regression.']
    (out/'pipeline_consolidation_report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    return m

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--verify-only',action='store_true');p.add_argument('--run',action='store_true')
    p.add_argument('--output',type=Path,default=ROOT/'data/debug/pipeline_consolidation')
    args=p.parse_args(argv)
    m=verify(args.output.resolve())
    print(json.dumps(dict(status=m['status'],critical_final_answer='NO',report=str(args.output/'pipeline_consolidation_report.md'),regressions=m['regression_counts'],patients_processed=0)))
    return 2 if args.run or m['regression_exit_code'] else 0

if __name__=='__main__':raise SystemExit(main())
