# Diagnosis and medication V3

This offline runner reassembles the existing five-patient pilot's saved source
mentions. It never reruns upstream extraction, imaging, CAA, neurosonology, EEG,
PET, clinical scales or risk scores. It uses no network, model or database.
The original semantic diagnosis eligibility remains a review limitation.

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.diagnosis_medication_v3 --pilot-output data/debug/five_patient_integration_20261025T203648+0100
```

Each invocation creates an exclusive timestamped directory under `data/debug`.
Previous output bytes are not overwritten. Per-patient JSON/CSV files and
`module_manifest.json` live under `patient_artifacts/<patient_id>/`.
The two physician reviews, unresolved medication queue, reference regression
results and validation report are at the snapshot root. Validation means
technical checks passed; it does not mean physician approval.

The existing medication database is `data/reference/medication_dictionary.csv`:
3,992 rows, fields `alias`, `brand`, `active_ingredient`, `canonical_drug`,
`atc_code`, `therapeutic_class`, `research_class`, `source`, `reference_version`,
`brand_name`, `normalized_brand_name`, `ATC_level_2`, `ATC_level_3`,
`ATC_level_4`, `last_verified`.
The older integration runner calls `medication_taxonomy_v23.load_dictionary`
and `normalize`. V3 actively calls `medication_identity_v3.Resolver`, which
loads that database plus `data/reference/medication_aliases_v3.json`.
The supplemental mappings are the user's explicit regressions; ATC is retained
only where the local reference supplies an unambiguous matching code.
Lookup uses the cleaned product, generic ingredients, exact trade names,
normalized aliases and safe spacing aliases. There is no edit-distance guessing.
Unresolved products remain visible and require review.

Diagnosis mentions retain evidence, original source data, assertion and dates.
Only asserted disease candidates establish patient disease identities. Negated,
uncertain and event mentions remain in the mention layer. Existing event
artifacts remain intact. Dates use explicit disease periods before source letter
dates; a letter header is accepted only with a clear date label and a valid date.
Secondary disease references cannot inherit another disease's onset date.
Unknown status and onset remain unknown. AF subtypes and steroid-induced diabetes
modifiers retain their source evidence.

For selective reruns, supply the prior snapshot explicitly:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.diagnosis_medication_v3 --pilot-output data/debug/five_patient_integration_20261025T203648+0100 --previous <prior-v3-directory> --modules diagnoses
.venv\Scripts\python.exe -m caa_pdf_extractor.diagnosis_medication_v3 --pilot-output data/debug/five_patient_integration_20261025T203648+0100 --previous <prior-v3-directory> --modules medications
```

Unchanged modules retain their original artifact paths and checksums. Legacy
artifacts have `PRESERVED` status and their review status is never upgraded.
Diagnoses invalidate AF, CHA2DS2-VA, ORBIT (through AF) and OAC decision analysis.
Medications invalidate ORBIT and OAC decision analysis. These become
`STALE_DEPENDENCY`; nothing recalculates them automatically.

`module_artifacts.ModuleStore` is module-independent: later module runners can
publish neurosonology, CAA or scores into new snapshots and carry forward other
entries. Their extraction entry points are outside this task's scope.
`select_latest_validated` accepts chronological manifest snapshots and selects
only intact `VALIDATED` artifacts. Later invalidation of the same artifact
supersedes its earlier validated occurrence. Missing, tampered, preserved or
stale artifacts cannot be selected as validated.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_diagnosis_medication_v3.py tests/test_integration_pilot.py tests/test_diagnosis_caa_v21.py tests/test_v23_review.py -q
```
