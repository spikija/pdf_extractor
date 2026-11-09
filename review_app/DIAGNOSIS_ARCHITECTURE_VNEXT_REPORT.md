# Diagnosis architecture vNext ready for physician review

Nine affected patients have new diagnosis/event 4.3 candidates. The UI provides a unified diagnosis registry, chronological clinical events and a separate probable-admission-diagnosis review section. Local Granite processed the complete summaries for 34 encounters. Two abstentions remain null/LOW rather than receiving invented diagnoses.

All active pointers, previous revisions, control artifacts, medication artifacts and physician reviews are preserved. No medication, computed CAA, scales, neurosonology, risk-score or OAC module ran. The Phenobarbital issue is retained in the deferred medication queue.

- [Full report and per-patient summary](../data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z/REPORT.md)
- [Every admission](../data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z/admissions.csv)
- [AF source regression](../data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z/AF_assertion_regression.csv)
- [Regression controls and baseline discrepancy](../data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z/regression_controls.csv)
- [Architecture notes](../docs/diagnosis_architecture_vnext.md)

Refresh the artifact index and choose the latest 4.3 candidate. Earlier immutable candidates remain in version history. Admission inferences remain probable and require explicit physician adjudication. Control 2953158925's stored confirmed AF is flagged as a baseline discrepancy because its positive source is conditional; its active artifact has not been changed.
