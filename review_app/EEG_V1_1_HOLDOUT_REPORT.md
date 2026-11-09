# EEG v1.1 fresh holdout ready for physician review

`EEG_V1_1_HOLDOUT_READY_FOR_PHYSICIAN_REVIEW`

20 development patients excluded; 12 new holdout patients selected before extraction.
The independent inventory contains 43 performed recordings. Frozen EEG v1.1 produced
42 candidate records, matching 38 distinct source recordings: **exam recall 88.4%**.
Five recordings were missed as separate exams; two duplicate records and two false
performed-exam records remain visible. All discrepancies are retained for review.

There are 23 source-comparison review flags and 23 violation occurrences across
12 gate categories, despite zero violations from the existing narrow v1.1 gates.
No extraction logic was changed or rerun. This is not a production-readiness approval.
All candidates remain UNREVIEWED, with promotion disabled. No clinical database writes
or cloud patient-data calls occurred.

Start `run_review_app.bat`, refresh the artifact index, and select **EEG V1.1 HOLDOUT**
in **Review queue**. The EEG panel includes the independent source inventory, exact
source-page links, review flags and standard explicit review actions.

[Full report, patient IDs, distributions and validation evidence](../data/debug/eeg_v1_1_holdout_20261029T101453_435311Z/eeg_holdout_validation_report.md).

83 tests passed, including all 12 holdout patients in functional Streamlit AppTest.
Real physician reviews and all 3,690 protected pre-existing files were preserved,
apart from the authorized append-only EEG run-audit extension.
