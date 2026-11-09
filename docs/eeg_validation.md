# Local EEG validation 1.0

The separate `data/patient_artifacts/eeg_validation_cohort.json` registry bounds EEG assembly
to 20 selected patients. The diagnosis/medication registry and clinical algorithms are unchanged.
Native PDF screening is read-only. No clinical database is used.

`extraction/eeg_sources.py` snapshots native page text and bounds reports. `eeg_schema.py` defines
independent fields: booleans use true/false/null and enums use UNKNOWN. Unmentioned fields and
unchecked text-form controls stay unknown. Unsupported/vector-only controls require manual review.
Historical terminology stays explicit; PLED is not automatically renamed to modern LPD.

`eeg_semantic.py` restricts selective local Granite proposals to current finding clauses and checks
source IDs, terminology, negation, uncertainty and historical scope. Indications remain separate.
Isolated spikes cannot establish seizure/status. EEG findings never generate a TFNE diagnosis.
Original interpretation and report copies are retained. Additional source-reviewed reconciliations
are saved separately. Conflicting dated interpretations remain visible.

The EEG-only worker's OS audit hook blocks network/DB access, child processes and writes outside
a fresh output directory. Semantic preparation uses the numeric loopback Granite client with
proxies and redirects disabled. Publication creates immutable candidates; EEG promotion is disabled.

The EEG tab displays schema groups, structured phenotypes, source grading, original text and page
links. Exam/finding actions use candidate-scoped `eeg_review.json` projections and the review audit.
Current actions apply in timestamp order; rejected/uncertain fields become unknown, never false.
Edits are schema validated and summaries recalculated. Stale corrections do not apply.

Recall concerns identifiable study/report units, including retained conflicts. Undated references
cannot prove an additional distinct study. Date discrepancies remain unresolved. Validation does
not establish physician approval or exhaustively validate every historical form layout.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_eeg_validation.py tests/test_eeg_validation_ui.py -q
```

[Validation evidence](../data/debug/eeg_validation_20261028T103249_606975Z/EEG_VALIDATION_REPORT.md).
