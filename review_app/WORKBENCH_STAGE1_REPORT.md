# EXTRACTION_WORKBENCH_STAGE1_READY

- stable_artifact_root: `C:\app\pdf_extractor\data\patient_artifacts\`
- launch_command: `streamlit run review_app/app.py` or `run_review_app.bat`
- local_url: http://127.0.0.1:8501
- candidate/promotion workflow: **IMPLEMENTED AND TESTED**
- per-patient diagnosis rerun: **CONFIRMED UI RUN PASSED**, existing V4 assembler
- per-patient medication rerun: **CONFIRMED UI RUN PASSED**, existing V3 assembler
- database writes = **NONE**
- cloud patient calls = **NONE**
- batch extraction capability = **DISABLED**

## Detected pilot versions

| Patient | Active diagnoses | Diagnosis candidate | Active medications | Medication candidate |
| --- | --- | --- | --- | --- |
| 2435608415 | 3.0 VALIDATED | 4.0 CANDIDATE | 3.0 VALIDATED | — |
| 2459345815 | 4.0 VALIDATED | 4.0 CANDIDATE, UI rerun | 3.0 VALIDATED | 3.0 CANDIDATE, UI rerun |
| 2552817617 | 3.0 VALIDATED | 4.0 CANDIDATE | 3.0 VALIDATED | — |
| 2684469520 | 4.0 VALIDATED | — | 3.0 VALIDATED | — |
| 2826947422 | 3.0 VALIDATED | 4.0 CANDIDATE | 3.0 VALIDATED | — |

Each candidate has a distinct `+<timestamp>-<run-id>` revision. Algorithm versions remain accurate;
rerunning unchanged V4 code does not claim a new V4.1 implementation.
All five patients now have V4 diagnosis/event artifacts as candidates or validated active versions.
The three newly upgraded patients retain V3 as active until explicit physician adjudication/promotion.
No real-patient candidate was automatically promoted or rejected.

## Workflow and verification

Stable storage contains byte-identical copies of the newest intact validated pilot artifacts,
with provenance back to their original debug snapshots. Stable active pointers take precedence;
candidate creation cannot replace current data. Previous versions remain on disk.

The three required V4 candidates passed all applicable hard gates. The complete existing V4
two-patient gate set also passed. Patient 2459345815 retains the 2015 right MCA ischemic stroke
and distinct 2006 stroke. Patient 2684469520 retains the 2021 left mesiotemporal event and excludes
narrative junk from canonical diagnoses. The real confirmed medication rerun is semantically
unchanged from validated V3; the confirmed diagnosis rerun is likewise unchanged.

**85 focused tests passed**, covering the original diagnosis/medication regressions and new storage,
candidate, comparison, audit, confirmation, promotion, rejection, stale-review and safety checks.
Synthetic isolated UI tests exercised successful physician-confirmed promotion and rejection.
The real-pilot Streamlit AppTest exercised confirmation, one diagnosis rerun and one medication rerun,
candidate comparison, blocked premature promotion, and rerender safety. The server health endpoint
returned `ok`. No browser-pixel screenshot/manual browser verification was performed.

**647 pre-existing source/debug/review files** matched their saved before/after SHA-256 hashes.
All original snapshots and source PDFs remain unchanged. No non-pilot patient was processed.
No CAA, neurosonology, EEG, scale, score or OAC editing/extraction was added.

Machine-readable evidence:

- [Stage 1 validation report](../data/debug/review_app/workbench_stage1_c701d55318e94d2d94c821609b3d0e48/stage1_validation_report.json)
- [Confirmed UI runs](../data/debug/review_app/workbench_stage1_c701d55318e94d2d94c821609b3d0e48/confirmed_ui_runs.json)
- [Module lifecycle audit](../data/patient_artifacts/module_run_audit.jsonl)

## Operational limits

Runs reassemble existing saved parsed source inputs; changed PDFs require a separately refreshed
upstream source snapshot. Promotion requires hard gates, intact/current source hashes and explicit
adjudication of review-required candidate entities. Candidate adjudications remain separately scoped
after promotion; active adjudications are not silently migrated. Future module tabs remain read-only.

See [README.md](README.md) for launch, storage, review workflow and recovery details.
