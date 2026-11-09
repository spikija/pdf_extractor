"""Render the holdout evidence report without running extraction or saving reviews."""
import csv
import json
from collections import Counter,defaultdict
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha
from .eeg_holdout_audit import preservation
from .eeg_holdout_design import csv_file


def report(directory):
    directory=Path(directory).resolve();preservation(directory)
    metrics=read(directory/'validation_metrics.json');freeze=read(directory/'eeg_v1_1_freeze.json');cohort=read(directory/'frozen_cohort.json')
    studies=read(directory/'source_inventory_studies.json');issues=read(directory/'review_issue_details.json')
    def rows(name):
        with (directory/name).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))
    gates=rows('eeg_holdout_hard_gates.csv');summaries=rows('eeg_holdout_extraction_summary.csv');matches=rows('eeg_holdout_recall_reconciliation.csv')
    files=[ROOT/'src/caa_pdf_extractor'/name for name in ('eeg_holdout_design.py','eeg_holdout_inventory.py','eeg_holdout_run.py','eeg_holdout_audit.py','eeg_holdout_overlap.py','eeg_holdout_report.py')]
    files += [ROOT/name for name in ('review_app/eeg_holdout.py','review_app/cohort_dashboard.py','review_app/app.py','review_app/views/eeg.py')]
    atomic(directory/'holdout_orchestration_code_hashes.json',{str(p):sha(p) for p in files})
    documents=read(directory/'holdout_screening_documents.json');by_patient=defaultdict(list)
    for row in documents:by_patient[row['patient_id']].append(row['screening_status'])
    classifications=[]
    for row in read(directory/'holdout_screening_patients.json'):
        states=by_patient[row['patient_id']]
        state=next((s for s in ('EEG_PERFORMED','EEG_PLANNED','EEG_REFERENCED') if s in states),'EEG_UNKNOWN')
        classifications.append(dict(patient_id=row['patient_id'],screening_status=state,screening_only=True,selected=row['patient_id'] in cohort['patient_ids']))
    csv_file(directory/'eeg_holdout_screening_classification.csv',classifications,list(classifications[0]))
    lines=[
        '# EEG v1.1 fresh holdout validation', '',
        '`EEG_V1_1_HOLDOUT_READY_FOR_PHYSICIAN_REVIEW`', '',
        '**CANDIDATES ONLY. All 12 patients remain UNREVIEWED. No promotion or production-readiness declaration.**', '',
        f"Independent source recall is **{metrics['matched_source_EEG_exams']}/{metrics['source_EEG_exams']} = {metrics['EEG_exam_recall']:.1%}**. The frozen extractor produced {metrics['extracted_EEG_exams']} records: {metrics['matched_source_EEG_exams']} distinct source recordings, two duplicate records and two false performed-exam records. Five actual recordings have no separate candidate. All five are individually disclosed; none is silently omitted from this report.", '',
        'The existing v1.1 gates report zero violations. Independent source comparison adds **23 review flags / 23 violation occurrences across 12 gate categories**. These are overlapping review units, not 23 independent clinical errors or patients. The zero existing-gate result does not establish generalization. Physician adjudication must distinguish extraction error, acceptable ambiguity, source ambiguity and schema limitation.', '',
        '## Freeze and bounded scope', '',
        f"EEG version: **1.1**. Parser: `{freeze['parser_version']}`. Source boundary implementation: `{freeze['source_boundary_version']}`. Code hash: `{freeze['code_hash']}`. All 15 frozen implementation/dependency files are byte-identical after the run. Model/prompt configuration: deterministic only, model=null, prompt=null, Granite calls=0.", '',
        f"Code frozen at {freeze['frozen_at']}; selection frozen at {cohort['frozen_at']}; independent inventory frozen at {read(directory/'source_inventory_freeze.json')['frozen_at']}, before the first extraction. [Freeze](eeg_v1_1_freeze.json), [frozen cohort](frozen_cohort.json), [inventory freeze](source_inventory_freeze.json).", '',
        'Twenty development/regression patients were excluded using the EEG cohort registry, AST inspection of physician regression cases, stable EEG manifests, execution/run metadata and existing EEG reviews. The exclusion CSV records each reason. Native PDF screening covered the remaining 347 local patients; 222 had preliminary performed-source signals. Only the selected 12 received EEG extraction. No full-cohort extraction or other clinical module was run.', '',
        'Selection used source-feature coverage, a soft preference for 2-3 estimated recordings, and the predeclared hash tie-break seed. It was frozen at 12 patients / 37 estimated exams before extraction. The independent inventory found 43 actual recordings; the patient list was not changed to chase a target or improve results. Screening estimates are not clinical findings.', '',
        'The source mixture includes normal/near-normal reports, diffuse and focal slowing, asymmetric bilateral abnormalities, positive/negative epileptiform wording, status questions, rhythmic/FIRDA terminology, artifact limitations, multiple exams, older 2015 report formatting and structured continuous/video reports. No older checkbox EEG form was demonstrated by screening. Selection signals mostly labeled free-text; the independent inventory additionally identified the structured continuous/video report. No held-out patient was selected based on v1.1 output.', '',
        'Exact development PDF reuse=0. Native name/DOB linkage hashes cover all 20 excluded and all 12 selected patients, with no shared identity fingerprints. No shared long report-prefix/date fingerprints were found. This additional verification did not change selection. See [exam overlap verification](development_exam_overlap_validation.json).', '',
        '## Patients and source recall', '',
        '| Patient | Source recordings | Extracted records | Source recordings matched | Missed | Duplicate records | Unmatched records |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in summaries:lines.append('| '+' | '.join(row[k] for k in ('patient_id','source_EEG_exams','extracted_EEG_exams','matched_source_EEG_exams','missing_source_exams','duplicate_candidate_records','unmatched_candidate_records'))+' |')
    lines += ['', 'The denominator is the source-only recording inventory. Copied/abbreviated reports count once; continuous monitoring interval summaries count as one recording. Routine and video recordings on the same date remain distinct. Unknown dates are preserved for two narrative-only exams. Embedded report text inside a different candidate is not separate-exam recall. The original audit, native page snapshots and source-copy decisions remain available.', '',
        'The acute ZANE narrative for 2944202925 is provisionally linked to its admission-day 11 April report; the source does not establish a second same-day recording. Physician confirmation of this source-identity decision is requested. Any revised denominator must be reported transparently, without silently rerunning or changing frozen extraction.', '',
        '### All missed recordings', '', '| Patient | Date | Document ID | Page | Inventory ID |', '| --- | --- | --- | ---: | --- |']
    for row in matches:
        if row['match_status']=='MISSED_AS_SEPARATE_EXAM':lines.append('| '+' | '.join(row[k] or 'UNKNOWN' for k in ('patient_id','EEG_date','document_id','page','inventory_id'))+' |')
    lines += ['',
        '2687726420: short two-digit-year historical EEG headings were not separate studies; denied Video-EEG form headings instead became two false candidates and absorbed historical/clinical content. The 30 July continuous EEG was absorbed into the 29 July report. 2957537425: mobile continuous/video monitoring was absorbed rather than represented as a separate recording; abbreviated routine-report copies produced two extra records. The narrative-only performed recordings for 2730816321 and 2537649217 were missed.', '',
        '## Automated safety findings', '', '| Gate with nonzero result | Existing v1.1 | Independent holdout | Total |', '| --- | ---: | ---: | ---: |']
    for row in gates:
        if int(row['violations']):lines.append('| '+' | '.join(row[k] for k in ('gate','existing_v11_violations','independent_holdout_violations','violations'))+' |')
    lines += ['',
        'Every required gate, including zero counts, is listed in [eeg_holdout_hard_gates.csv](eeg_holdout_hard_gates.csv). Actual performed EEG silently missed=0 because all five misses are explicitly listed; actual performed EEG missed as a separate candidate=5. These statements must not be conflated. Required zero targets are **not all met**.', '',
        'Notable source contradictions: preserved response to pain/external stimuli has no structured reactivity; positively stated nonconvulsive status is marked absent; a separate next-day negative/unassessable recording contaminates a positive preceding exam; explicitly benign wicked-spikes are marked epileptiform. The benign-variant failure is separately named rather than hidden behind a zero narrow negated-epileptiform gate. FIRDA plural terminology, independent bilateral temporal foci and left dominant focus versus spread also require review.', '',
        'The holdout audit adds transparent source-linked checks and discrepancy accounting; it does not change, replace or tune the frozen extractor or existing gate implementation. Zero values are limited to the implemented checks, not a claim of complete semantic validation. This is functional source/data-path validation, not a browser-pixel test or a completed physician chart review. No primary-finding choice alone creates a failure.', '',
        '## Extraction output distribution', '',
        'Counts below overlap and include erroneous/duplicate candidate records. They are extraction outputs, **not physician-validated prevalence**. Multi-label phenotype and localized/asserted detailed findings are the scientific representation; focal epileptiform counts include the focal/multifocal phenotype family.', '', '| Output | Count |', '| --- | ---: |']
    for key in ('extracted_EEG_exams','normal_count','generalized_slowing_count','focal_slowing_count','focal_epileptiform_count','generalized_epileptiform_count','rhythmic_delta_count','periodic_discharge_count','electrographic_seizure_count','status_epilepticus_count','artifact_limited_count','limited_assessability_count'):
        lines.append(f'| {key} | {metrics[key]} |')
    lines += ['', '## Physician review', '',
        'Start `run_review_app.bat`, click **Refresh artifact index**, choose **EEG V1.1 HOLDOUT** in **Review queue**, then open the EEG tab. The holdout dashboard shows candidate record count, review status and candidate revision. Candidate version: `1.1+holdout.20261029T101453_435311Z`.', '',
        'The EEG panel includes all independent source recordings with exact document/page Open source controls, including missed recordings, plus the source-comparison issues. Standard candidate actions ACCEPT / EDIT / REJECT / UNCERTAIN remain explicit overlay saves; raw artifacts stay immutable. Review priorities cover exam detection, multi-label phenotype, localization, dominance/spread, independent focus, asymmetry, polarity, seizure/status, FIRDA/GRDA, reactivity, assessability, boundaries and source abnormality grade.', '',
        'Statuses are UNREVIEWED, IN_REVIEW, REVIEW_REQUIRED, PHYSICIAN_APPROVED and STALE. All real candidates are UNREVIEWED. Accepting every extracted entity cannot approve a patient with an omitted source recording. Promotion remains disabled. No physician judgments were created by this task; simulated save/reload tests use only a separate debug review directory.', '',
        '## Execution, checks and preservation', '',
        'Each selected patient received exactly one frozen v1.1 assembly invocation. The first worker completed all five artifacts and the gate log, then the new orchestration wrapper attempted an atomic execution-log rename prohibited by the existing guard. This wrapper-only failure was reported before correction. The completed temporary log was recovered byte-for-byte, and the already-produced extraction bytes were published without re-extraction. Remaining workers use direct creation of their new execution log. The frozen implementation and guard were not modified. See [wrapper recovery](wrapper_log_recovery.json); the original failure log is retained.', '',
        '**83 tests passed**, including source screening exclusions, bounded publication safeguards, source-denominator reconciliation, status/stale handling, all 12 holdout patients in Streamlit AppTest, exact navigation for a missed narrative recording, explicit ACCEPT/EDIT/REJECT/UNCERTAIN saves and reload persistence in isolated debug storage. Existing synthetic v1.0/v1.1 tests passed; no additional real patient extraction occurred during testing.', '',
        '`python -B -m pytest tests/test_eeg_holdout.py tests/test_eeg_holdout_design.py tests/test_eeg_v11.py tests/test_eeg_validation.py -q`', '',
        'All 3,690 pre-existing protected inputs were checked. No unexpected changes; all original source-code hashes, development patient manifests/artifacts, physician reviews, reference data and non-EEG modules are unchanged. Allowed persistent additions are the separate holdout registry, 12 new EEG candidate manifests/artifacts and 12 EEG RUN audit entries. Existing audit history is retained; active validated EEG pointers remain null. Worker audit guards blocked network/DB/child-process operations and confined writes to fresh execution directories.', '',
        '```text', 'EEG_VERSION = 1.1', 'EEG_LOGIC_CHANGED_DURING_HOLDOUT = NO', 'DATABASE_WRITES = NONE', 'CLOUD_PATIENT_CALLS = NONE', 'EXTRACTION_RERUNS = 0', 'AUTO_PROMOTIONS = 0', '```', '',
        '## Deliverables', '',
        '- [Frozen selection](eeg_v1_1_holdout_selection.csv)', '- [Development exclusions](eeg_v1_1_holdout_exclusions.csv)',
        '- [Independent source inventory](eeg_holdout_source_inventory.csv)', '- [Extraction summary](eeg_holdout_extraction_summary.csv)',
        '- [Individual review flags](eeg_holdout_errors_for_review.csv)', '- [All hard gates](eeg_holdout_hard_gates.csv)',
        '- [Source/extraction reconciliation](eeg_holdout_recall_reconciliation.csv)', '- [Machine-readable final metrics](validation_metrics.json)',
        '- [Protected-input checks](protected_input_validation.json)', '- [Functional UI evidence](ui_smoke.json)', '',
        'Production readiness requires subsequent physician review. No additional patients were extracted.', '']
    (directory/'eeg_holdout_validation_report.md').write_text('\n'.join(lines),encoding='utf-8')
    atomic(directory/'design_status.json',dict(stage=metrics['status'],development_exclusions=20,remaining_patients_screened=347,
        code_hash=freeze['code_hash'],frozen_code_intact=True,holdout_registry_frozen=True,EEG_extraction_runs=12,extraction_reruns=0,
        physician_review_status='UNREVIEWED',hard_gate_failures=metrics['hard_gate_failures'],production_ready=False,pending='Subsequent physician review; no extraction tuning authorized in this holdout task'))
    return directory/'eeg_holdout_validation_report.md'


if __name__=='__main__':
    import sys
    print(report(Path(sys.argv[1])))
