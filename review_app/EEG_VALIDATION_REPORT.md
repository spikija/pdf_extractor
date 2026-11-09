# EEG validation complete

```text
EEG_VALIDATION_COHORT_READY_FOR_PHYSICIAN_REVIEW
patients_screened = 367
patients_selected = 20
EEG_exams_source = 76
EEG_exams_extracted = 76
EEG_exam_recall = 1.0
normal_count = 10
focal_slowing_count = 48
generalized_slowing_count = 48
epileptiform_count = 5
rhythmic_periodic_count = 9
electrographic_seizure_count = 1
status_epilepticus_count = 1
uninterpretable_count = 0
review_required_count = 76
review_required_finding_count = 1387
Granite_calls = 15
hard_gate_failures = 0
DATABASE_WRITES = NONE
CLOUD_PATIENT_CALLS = NONE
```

EEG 1.0 candidate previews are available for 20 selected patients. Promotion remains disabled. Existing clinical modules, artifacts and physician reviews are unchanged. Screening covered 367 patients; extraction stayed within the selected subset.

128 tests and 49 independent field comparisons across nine heterogeneous reports passed. All 20 patients passed EEG UI/source navigation checks. Review-save smoke tests used isolated debug directories. All protected-input checks passed.

Recall is 76/76 identifiable study/report units, including two conflicting reports sharing a historical date. Copied reports are reconciled; an undated historical reference cannot establish an additional distinct study. Source date discrepancies and unsupported form layouts remain physician-review limitations.

Selected IDs: 2921697224, 2661437519, 2984303525, 2964796925, 2878624323, 2788388022, 2976764725, 2897716624, 2739464121, 2693909420, 2648066819, 2529565017, 2520723716, 2345733313, 2956023325, 2945890325, 2920768024, 2918603524, 2831142723, 2674159019.

[Full validation evidence](../data/debug/eeg_validation_20261028T103249_606975Z/EEG_VALIDATION_REPORT.md) ? [Implementation](../docs/eeg_validation.md).

Refresh the artifact index in the local app, select a listed patient, and open **EEG**. Current candidate revisions end in `-r2`; superseded initial revisions remain preserved with QA holds.
