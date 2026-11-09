# EEG v1.2 regression candidates

`EEG_V1_2_READY_FOR_PHYSICIAN_REVIEW`

18 patients; 83/83 independent source exams matched. Both explicit NCSE cases
are correctly detected. All 28 regression groups / 185 checks pass; hard-gate
failures=0. Candidates remain UNREVIEWED and promotion is disabled.

Use the ALL review queue and select revision `1.2+20261101T113547_290839Z` in the EEG tab.
The EEG V1.1 HOLDOUT queue continues to show the archived 1.1 candidates.

[Full results and audit files](../data/debug/eeg_v1_2_20261101T113547_290839Z/eeg_v1_2_regression_summary.md).

No clinical database writes or cloud patient-data calls. No full-cohort extraction.

Subsequent physician review identified resting-ECG and psychiatric-consultation
contamination that the initial audit did not check. The focused v1.2 source-only
correction and expanded heading tests are documented in the
[boundary correction report](../data/debug/eeg_v1_2_boundary_fix_20261101T124730_912442Z/eeg_v1_2_boundary_fix_report.md).
Historical candidate artifacts and their clinical findings remain unchanged.
