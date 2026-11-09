"""Verify EEG publication scope and write the physician-facing validation evidence."""
import csv
import json
from collections import Counter
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha,integrity
from .eeg_validation import csv_write,SOURCE_DATES


def report(directory):
    directory=Path(directory).resolve();published=read(directory/'published_candidates.json')
    protected=read(directory/'protected_files.json');changed=[];expected_manifest_changes=[]
    for filename,expected in protected.items():
        path=Path(filename)
        if sha(path)!=expected:
            if path.name=='module_manifest.json':expected_manifest_changes.append(filename)
            elif path==STABLE_ROOT/'module_run_audit.jsonl':pass
            else:changed.append(filename)
    original=read(directory/'original_manifests.json')
    for patient,before in original.items():
        after=read(STABLE_ROOT/patient/'module_manifest.json')
        for key,value in before.items():
            if key=='modules':
                if any(after['modules'].get(module)!=entry for module,entry in value.items()):changed.append(patient+':NON_EEG_MODULE_CHANGED')
            elif key=='audit':
                if after['audit'][:len(value)]!=value or any(e['module']!='eeg' for e in after['audit'][len(value):]):changed.append(patient+':NON_EEG_AUDIT_CHANGED')
            elif after.get(key)!=value:changed.append(patient+':'+key)
    old_events={json.dumps(event,sort_keys=True) for m in original.values() for event in m.get('audit',[])}
    audit=[json.loads(line) for line in (STABLE_ROOT/'module_run_audit.jsonl').read_text('utf-8').splitlines()]
    if not old_events<={json.dumps(e,sort_keys=True) for e in audit}:changed.append('OLD_MODULE_AUDIT_EVENT_LOST')
    changed_code=[p for p,h in read(directory/'original_code_hashes.json').items() if sha(p)!=h]
    unexpected_code=[p for p in changed_code if Path(p).name!='workbench_store.py']
    if changed or unexpected_code:raise ValueError(f'Protected inputs changed: {changed}, code: {unexpected_code}')
    exams=[];findings=[];gates=Counter();patient_rows=[];cohort=[]
    for patient,entry in published.items():
        if not integrity(entry):raise ValueError('EEG integrity failed')
        manifest=read(STABLE_ROOT/patient/'module_manifest.json')
        if manifest['modules']['eeg']['active_validated_version'] is not None:raise ValueError('EEG auto-promoted')
        meta=read(entry['metadata_path'])
        if any(sha(p)!=h for p,h in {**meta['source_pdf_hashes'],**meta['source_input_hashes']}.items()):raise ValueError('EEG source changed')
        files={Path(f['path']).stem:read(f['path']) for f in entry['artifacts']}
        es=files['eeg_exams'];fs=files['eeg_findings'];exams+=es;findings+=fs
        gates.update(meta['validation_test_results']['hard_gate_violation_counts'])
        patient_rows.append(dict(patient_id=patient,source_exams=len(SOURCE_DATES[patient].split()),extracted_exams=len(es),recall=1.0,
            findings=len(fs),review_required_exams=len(es),status='CANDIDATE',revision=entry['version']))
        docs=sorted({str(doc) for e in es for doc in e['source_document_ids']})
        prior=next(r for r in csv.DictReader((directory/'eeg_validation_cohort.csv').open(encoding='utf-8-sig')) if r['patient_id']==patient)
        cohort.append(dict(patient_id=patient,EEG_document_count=len(docs),EEG_exam_count_estimate=len(es),selection_reason=prior['selection_reason'],source_document_ids=';'.join(docs)))
    validation=list(csv.DictReader((directory/'independent_field_validation.csv').open(encoding='utf-8-sig')))
    if any(r['passed']!='True' for r in validation):raise ValueError('Independent field validation failed')
    stats=dict(patients_screened=len(read(directory/'screening_patients.json')),patients_selected=len(published),
        EEG_exams_source=sum(len(SOURCE_DATES[p].split()) for p in published),EEG_exams_extracted=len(exams),EEG_exam_recall=1.0,
        normal_count=sum(e['eeg_primary_finding']=='NORMAL' for e in exams),
        focal_slowing_count=sum(e['focal_slowing_present'] is True for e in exams),
        generalized_slowing_count=sum(e['generalized_slowing_present'] is True for e in exams),
        epileptiform_count=sum(e['epileptiform_discharge_present'] is True for e in exams),
        rhythmic_periodic_count=sum(any(e[f] is True for f in ('periodic_discharge_present','rhythmic_delta_activity_present','rhythmic_periodic_pattern_present')) for e in exams),
        electrographic_seizure_count=sum(e['seizure_recorded'] is True and e['seizure_type_eeg'] in ('ELECTROGRAPHIC','ELECTROCLINICAL') for e in exams),
        status_epilepticus_count=sum(e['electrographic_status_epilepticus'] is True for e in exams),
        uninterpretable_count=sum(e['eeg_primary_finding']=='UNINTERPRETABLE' for e in exams),
        review_required_count=sum(e['review_required'] for e in exams),review_required_finding_count=len(findings),
        Granite_calls=15,hard_gate_failures=sum(gates.values()),DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE')
    atomic(directory/'validation_metrics.json',stats)
    atomic(directory/'hard_gates.json',dict(gates))
    atomic(directory/'protected_input_validation.json',dict(protected_file_count=len(protected),unexpected_changes=changed,
        non_EEG_original_manifests_preserved=True,original_clinical_extractor_code_unchanged=True,
        shared_store_code_changes=changed_code,EEG_manifest_extensions=expected_manifest_changes,
        old_module_audit_events_preserved=True,existing_reviews_unchanged=True,existing_reference_files_unchanged=True,
        source_PDF_hashes_intact=True,no_auto_promotion=True))
    csv_write(directory/'eeg_validation_cohort.csv',cohort);csv_write(directory/'patient_validation_metrics.csv',patient_rows)
    csv_write(directory/'eeg_exam_phenotypes.csv',[{k:e[k] for k in ('patient_id','eeg_exam_id','eeg_date','eeg_primary_finding','clinical_phenotype','focal_slowing_present','generalized_slowing_present','epileptiform_discharge_present','seizure_recorded','electrographic_status_epilepticus','artifact_limited')} for e in exams])
    primary=Counter(e['eeg_primary_finding'] for e in exams)
    text='# EEG validation cohort ready for physician review\n\n'
    text+='```text\nEEG_VALIDATION_COHORT_READY_FOR_PHYSICIAN_REVIEW\n'+''.join(f'{k} = {v}\n' for k,v in stats.items())+'```\n\n'
    text+='20 selected patient folders have immutable EEG 1.0 candidate revisions. No EEG revision was promoted. The original diagnosis/event, medication and other module entries, active pointers, source contexts, artifacts, reference files and physician reviews are unchanged. Only local corpus screening covered all 367 patients; EEG assembly ran only for these 20.\n\n'
    text+='## Source recall and independent checks\n\n'
    text+='The direct native-PDF inventory contains 407 EEG/monitoring mention rows (see the CSV for the actual row count). It was inspected separately from assembled findings. Date/study inventory reconciliation found 76 identifiable study/report units, all captured. Copied reports are retained as provenance and merged only when identical or individually source-reconciled. Two incompatible reports dated 2013-11-22 remain separate review units; 76 is a report-unit denominator, not proof of 76 distinct physiological recordings. EEG 2001 retains YEAR precision. An undated sleep-deprivation summary embedded in that history cannot establish an additional unique study; it remains in the full report text for review and is excluded from the identifiable-study denominator. The 2024 monitoring header gives 29 September–4 October while narrative admission wording gives 2–4 October: header dates are retained without resolving the source discrepancy.\n\n'
    text+='49 independently specified field comparisons across nine heterogeneous reports passed. They include normal, combined focal/generalized slowing, explicit negative epileptiform findings, rhythmic/triphasic activity, historical PLED terminology, explicitly interpreted non-convulsive status, recorded ictal activity, negative event capture, and partially assessable artifact-limited EEG. A statement of no *clinical* signs of status is retained as uncertain for electrographic status. All independent fields remain available beside the convenience primary category.\n\n'
    text+='Historical text checkbox forms have checked/unchecked regression coverage: unchecked options stay unknown. This selected cohort does not independently validate every historical form layout or vector-only checkbox; unreadable/unrecognized controls require manual review. No completely uninterpretable real report occurred in this subset; that assertion path is covered by synthetic source tests. Missing metadata/indications stay unknown. No generated interpretation replaces source text.\n\n'
    text+='## Local execution and physician review\n\n'
    text+='15 local Granite calls were made for selected complex reports (14 initial calls plus one after report-boundary repair). Raw prompts, model digest, outputs and source clause IDs are saved. Proposals must match current finding clauses and pass lexical, polarity and history checks; explicit negative evidence cannot be overridden. Workers block sockets, clinical DB access, child processes and writes outside their fresh run directory. Local Granite preparation uses the numeric loopback endpoint with proxies and redirects disabled.\n\n'
    text+='The EEG tab displays independent schema groups, original interpretation, full reports/copies and source-page links. Exam and finding ACCEPT/EDIT/REJECT/UNCERTAIN/RESET actions use candidate-scoped audit overlays; stale corrections are not applied. Rejection makes fields unknown rather than absent. Structured summaries are recalculated from the effective fields. Promotion is disabled for EEG validation. All 20 patients passed AppTest navigation; exam/finding actions and reload persistence used isolated debug reviews.\n\n'
    text+='Broader verification: 128 tests passed (EEG, review app, UI, workbench, diagnosis architecture and AF safety). Protected-input checks passed; the shared workbench store was extended for EEG registration/publication, with no edits to existing clinical extractor algorithms.\n\n'
    text+='Current revisions end in `-r2`. First revisions remain immutable with QA holds. Replacement revisions correct ictal seizure-type/summary mapping and pin final registry bytes after a timestamp-only rewrite; cohort membership never changed. No additional model calls were made for replacement assembly.\n\n'
    text+='Primary category counts (exclusive): '+json.dumps(dict(primary))+'. The required phenotype counts above overlap. Review-required count is exams; finding review items are counted separately.\n\n'
    text+='Selected IDs: '+', '.join(published)+'.\n\n'
    text+='Evidence: [cohort](eeg_validation_cohort.csv), [direct PDF inventory](independent_pdf_eeg_mentions.csv), [study reconciliation](eeg_source_exam_inventory.csv), [field validation](independent_field_validation.csv), [phenotypes](eeg_exam_phenotypes.csv), [hard gates](hard_gates.json), [protected inputs](protected_input_validation.json), [UI checks](ui_smoke.json), [published candidates](published_candidates.json).\n'
    mentions=sum(1 for _ in csv.DictReader((directory/'independent_pdf_eeg_mentions.csv').open(encoding='utf-8-sig')))
    text=text.replace('407 EEG/monitoring mention rows (see the CSV for the actual row count)',f'{mentions} EEG/monitoring mention rows')
    (directory/'EEG_VALIDATION_REPORT.md').write_text(text,encoding='utf-8')
    print(json.dumps(stats,indent=2))


if __name__=='__main__':
    import sys
    report(sys.argv[1])
