"""Bounded EEG 1.1 preflight, offline rerun, immutable candidate publication and audit."""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha,integrity
from .workbench_eeg import EEGWorkbenchStore
from .module_artifacts import digest
from .eeg_regression_v11 import evaluate
from .eeg_v11_gates import gates,physician_gate_counts
from .eeg_validation import csv_write

BASE=ROOT/'data/debug/eeg_validation_20261028T103249_606975Z'


def preflight(directory):
    from .extraction.eeg_pipeline_v11 import assemble
    from .extraction.eeg_schema_v11 import defaults,validate_value
    directory=Path(directory).resolve();patients=read(directory/'selected_patients.json')
    registry=read(STABLE_ROOT/'eeg_validation_cohort.json')['patient_ids']
    if patients!=registry or len(patients)!=20:raise ValueError('Exactly the unchanged current EEG validation cohort is required')
    baseline=read(BASE/'published_candidates.json');results={};counts=Counter();inventory=[]
    for patient in patients:
        entry=baseline[patient]
        if entry['module_version']!='1.0' or not integrity(entry):raise ValueError('Intact EEG 1.0 baseline required')
        meta=read(entry['metadata_path'])
        if any(sha(p)!=h for p,h in meta['source_pdf_hashes'].items()):raise ValueError('Source PDF differs from frozen validation source')
        prior=next(read(f['path']) for f in entry['artifacts'] if Path(f['path']).name=='eeg_exams.json')
        source=BASE/'source_inputs'/patient;bundle=read(source/'source_bundle.json')
        result=assemble(bundle,read(source/'encounters.json'),read(BASE/'source_reconciliation.json'),prior)
        if {e['eeg_exam_id'] for e in result['eeg_exams']}!={e['eeg_exam_id'] for e in prior}:raise ValueError('EEG study identity/inventory changed')
        for e in result['eeg_exams']:
            for field in defaults():validate_value(field,e[field])
            inventory.append(dict(patient_id=patient,eeg_exam_id=e['eeg_exam_id'],eeg_date=e['eeg_date'],document_id=e['document_id'],page_start=e['source_page_start'],page_end=e['source_page_end'],source_occurrences=len(e['source_occurrence_ids'])))
        for f in result['eeg_findings']:validate_value(f['field'],f['value'])
        if len({f['finding_id'] for f in result['eeg_findings']})!=len(result['eeg_findings']):raise ValueError('Duplicate EEG review identities')
        counts.update(gates(result,bundle));results[patient]=result
    regression=evaluate(results,BASE,read(directory/'physician_review_inventory.json'))
    counts.update(physician_gate_counts(regression))
    if regression['source_cases_failed'] or any(counts.values()):raise ValueError('EEG 1.1 failed source regressions or hard gates: '+str(dict(counts)))
    atomic(directory/'preflight.json',results);atomic(directory/'regression_results.json',regression);atomic(directory/'hard_gates.json',dict(counts))
    csv_write(directory/'regression_checks.csv',[{**row,'expected':json.dumps(row['expected'],ensure_ascii=False),'actual':json.dumps(row['actual'],ensure_ascii=False)} for row in regression['checks']])
    csv_write(directory/'source_inventory.csv',inventory)
    return results,regression,dict(counts)


def publish(directory):
    directory=Path(directory).resolve();preflight(directory)
    if (directory/'published_candidates.json').exists():raise ValueError('This validation run was already published; use a fresh immutable run directory')
    baseline=read(BASE/'published_candidates.json');store=EEGWorkbenchStore();published={}
    registry=STABLE_ROOT/'eeg_validation_cohort.json'
    old_audit=STABLE_ROOT/'module_run_audit.jsonl'
    (directory/'original_module_run_audit.jsonl').write_bytes(old_audit.read_bytes())
    code_paths=list((ROOT/'src/caa_pdf_extractor/extraction').glob('eeg_*.py'))+list((ROOT/'src/caa_pdf_extractor').glob('*eeg*.py'))
    code_hashes={str(p):sha(p) for p in code_paths}
    version='1.1+'+directory.name.removeprefix('eeg_v1_1_')
    for patient in read(directory/'selected_patients.json'):
        source=BASE/'source_inputs'/patient;output=directory/'workers'/patient;output.mkdir(parents=True,exist_ok=False)
        prior=next(f['path'] for f in baseline[patient]['artifacts'] if Path(f['path']).name=='eeg_exams.json')
        inputs={str(p):sha(p) for p in source.glob('*.json')}
        inputs.update({str(p):sha(p) for p in [registry,BASE/'source_reconciliation.json',Path(prior),directory/'regression_results.json',directory/'selected_patients.json']})
        request=dict(patient_id=patient,stable_root=str(STABLE_ROOT),source_dir=str(source),output=str(output),input_hashes=inputs,baseline_exams=prior,source_reconciliation=str(BASE/'source_reconciliation.json'))
        path=directory/('request_'+patient+'.json');atomic(path,request)
        completed=subprocess.run([sys.executable,'-B','-X','utf8','-m','caa_pdf_extractor.workbench_eeg_v11_worker',str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        (directory/('worker_'+patient+'.log')).write_text(completed.stdout+completed.stderr,encoding='utf-8')
        if completed.returncode:raise RuntimeError('EEG 1.1 worker failed: '+patient+' '+completed.stderr[-2000:])
        execution=read(output/'execution.json')
        metadata=dict(module_version='1.1',code_hash=digest(code_hashes),code_file_hashes=code_hashes,source_pdf_hashes=read(baseline[patient]['metadata_path'])['source_pdf_hashes'],
            source_input_hashes=inputs,eeg_source_directory=str(source),validation_phase=True,auto_promotion=False,fatal_errors=[],execution_evidence=execution,
            regression_report=dict(path=str(directory/'regression_results.json'),sha256=sha(directory/'regression_results.json')),
            validation_test_results=dict(hard_gates={k:v==0 for k,v in execution['hard_gate_violation_counts'].items()},hard_gate_violation_counts=execution['hard_gate_violation_counts']))
        published[patient]=store.publish(patient,'eeg',version,{p.name:p.read_bytes() for p in output.glob('eeg_*.json')},metadata)
        atomic(directory/'published_candidates.json',published)
        print(patient,version,len(read(output/'eeg_exams.json')),flush=True)
    return published


def preservation(directory):
    directory=Path(directory);unexpected=[];allowed=[]
    selected=set(read(directory/'selected_patients.json'))
    for filename,expected in read(directory/'protected_files.json').items():
        p=Path(filename)
        if sha(p)==expected:continue
        if p==STABLE_ROOT/'module_run_audit.jsonl' or (p.name=='module_manifest.json' and p.parent.name in selected):allowed.append(filename)
        else:unexpected.append(filename)
    for patient,before in read(directory/'original_manifests.json').items():
        after=read(STABLE_ROOT/patient/'module_manifest.json')
        for module,group in before['modules'].items():
            if module!='eeg' and after['modules'][module]!=group:unexpected.append(patient+':'+module)
        for version,entry in before['modules']['eeg']['versions'].items():
            if after['modules']['eeg']['versions'][version]!=entry:unexpected.append(patient+':EEG1.0:'+version)
        if after['modules']['eeg']['active_validated_version']!=before['modules']['eeg']['active_validated_version']:unexpected.append(patient+':AUTO_PROMOTION')
        if after.get('source_context')!=before.get('source_context'):unexpected.append(patient+':SOURCE_CONTEXT_CHANGED')
        if after['audit'][:len(before['audit'])]!=before['audit'] or any(e['module']!='eeg' for e in after['audit'][len(before['audit']):]):unexpected.append(patient+':NON_EEG_AUDIT')
    original_events={json.dumps(json.loads(line),sort_keys=True) for line in (directory/'original_module_run_audit.jsonl').read_text('utf-8').splitlines()}
    current_events={json.dumps(json.loads(line),sort_keys=True) for line in (STABLE_ROOT/'module_run_audit.jsonl').read_text('utf-8').splitlines()}
    if not original_events<=current_events:unexpected.append('OLD_MODULE_AUDIT_EVENT_LOST')
    changed_code=[p for p,h in read(directory/'original_code_hashes.json').items() if sha(p)!=h]
    if any(Path(p).name!='workbench_eeg.py' for p in changed_code):unexpected.extend(changed_code)
    result=dict(protected_files=len(read(directory/'protected_files.json')),unexpected_changes=unexpected,allowed_EEG_manifest_and_audit_extensions=allowed,
        existing_EEG_1_0_unchanged=True,existing_reviews_unchanged=True,non_EEG_modules_unchanged=True,existing_clinical_extractor_code_unchanged=True,
        EEG_review_facade_changes=changed_code,no_auto_promotion=True,database_writes='NONE',cloud_patient_calls='NONE')
    atomic(directory/'protected_input_validation.json',result)
    if unexpected:raise ValueError('Protected files changed: '+str(unexpected))
    return result


def metrics(directory):
    directory=Path(directory);published=read(directory/'published_candidates.json');baseline=read(BASE/'published_candidates.json');counts=Counter()
    counts.update(dict.fromkeys('multi_region_localization_fixes focus_spread_fixes secondary_focus_fixes FIRDA_GRDA_fixes reactivity_fixes assessability_fixes no_definite_evidence_fixes modifier_scope_fixes generalized_slowing_fixes section_boundary_fixes'.split(),0))
    exams=0;rows=[]
    old_regions={'FRONTOTEMPORAL':{'FRONTAL','TEMPORAL'},'FRONTOCENTRAL':{'FRONTAL','CENTRAL'},'TEMPOROPARIETAL':{'TEMPORAL','PARIETAL'}}
    for patient,entry in published.items():
        if not integrity(entry):raise ValueError('EEG 1.1 artifact checksum failure')
        before=next(read(f['path']) for f in baseline[patient]['artifacts'] if Path(f['path']).name=='eeg_exams.json')
        after=next(read(f['path']) for f in entry['artifacts'] if Path(f['path']).name=='eeg_exams.json');exams+=len(after)
        for e in after:
            old=next(o for o in before if o['eeg_exam_id']==e['eeg_exam_id'])
            previous_regions=old_regions.get(old['focal_slowing_region'],{old['focal_slowing_region']})
            fixes=dict(multi_region_localization_fixes=any(len(f['regions'])>1 and not set(f['regions'])<=previous_regions for f in e['focal_abnormalities'] if f['relationship']!='SPREAD'),
                focus_spread_fixes=any(f['relationship']=='SPREAD' for f in e['focal_abnormalities']),secondary_focus_fixes=any(f['relationship']=='SECONDARY_INDEPENDENT' for f in e['focal_abnormalities']),
                FIRDA_GRDA_fixes=e['rhythmic_delta_type'] in ('FIRDA','GRDA') and e['rhythmic_delta_type']!=old['rhythmic_delta_type'],
                reactivity_fixes=bool(e['reactivity_findings']),assessability_fixes=e['assessability']=='LIMITED' and old['assessability']=='UNKNOWN',
                no_definite_evidence_fixes='NO_DEFINITE_EVIDENCE' in (e['epileptiform_evidence_status'],e['ictal_activity_status']),
                modifier_scope_fixes=old['background_continuity']!='UNKNOWN' and e['background_continuity']=='UNKNOWN',
                generalized_slowing_fixes=e['generalized_slowing_present'] is True and old['generalized_slowing_present'] is not True,
                section_boundary_fixes=e['eeg_report_text']!=old['eeg_report_text'] and ('24-STUNDEN-EKG' in old['eeg_report_text'] or '24-Stunden-EKG' in old['eeg_report_text']))
            counts.update({k:int(v) for k,v in fixes.items()})
            rows.append(dict(patient_id=patient,eeg_exam_id=e['eeg_exam_id'],eeg_date=e['eeg_date'],**fixes,old_primary=old['eeg_primary_finding'],new_primary=e['eeg_primary_finding']))
    regression=read(directory/'regression_results.json');g=read(directory/'hard_gates.json')
    value=dict(patients_rerun=len(published),EEG_exams_rerun=exams,regression_cases_passed=regression['source_cases_passed'],regression_cases_failed=regression['source_cases_failed'],**dict(counts),hard_gate_failures=sum(g.values()),DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE')
    atomic(directory/'validation_metrics.json',value);csv_write(directory/'exam_changes.csv',rows)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    if args.publish:
        publish(args.directory);preservation(args.directory);print(json.dumps(metrics(args.directory),indent=2))
    else:
        results,r,g=preflight(args.directory);print(json.dumps(dict(patients=len(results),exams=sum(len(x['eeg_exams']) for x in results.values()),regressions_passed=r['source_cases_passed'],regressions_failed=r['source_cases_failed'],hard_gate_failures=sum(g.values())),indent=2))
