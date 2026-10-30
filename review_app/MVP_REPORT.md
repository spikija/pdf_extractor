# REVIEW_APP_MVP_READY

Historical MVP report. The current implementation is described in
[WORKBENCH_STAGE1_REPORT.md](WORKBENCH_STAGE1_REPORT.md).

- launch_command: `streamlit run review_app/app.py` (activate `.venv` first), or `run_review_app.bat`
- local_url: `http://127.0.0.1:8501`
- review_data_path: `C:\app\pdf_extractor\data\review\`
- patients_detected: **5**
- pilot_ids: `2435608415`, `2459345815`, `2552817617`, `2684469520`, `2826947422`
- diagnosis_version_detected: **4.0** for `2459345815`, `2684469520`; **3.0** for other pilot patients
- medication_version_detected: **3.0** for all five
- database_write_capability = **NONE**
- cloud_patient_data_calls = **NONE**

## Artifact paths used

Manifest selection currently resolves validated files beneath:

```text
data/debug/diagnosis_v4_regression_20261025T220504_188209+0100/patient_artifacts/<patient_id>/
data/debug/five_patient_diagnosis_medication_v3_20261025T214202_295019+0100/patient_artifacts/<patient_id>/
data/debug/five_patient_diagnosis_medication_v3_20261025T214202_647858+0100/patient_artifacts/<patient_id>/
```

Preserved context/legacy metadata comes from:

```text
data/debug/five_patient_integration_20261025T203648+0100/<patient_id>/
pdf_db/<patient_id>/*.pdf
```

These are detected results, not hard-coded configuration.

## Validation

**75 tests passed** across the new review tests and existing V4 diagnosis/V3 medication regressions.
Streamlit AppTest checked all five patients, local PDF rendering, canonical diagnoses/events,
medication grouping, exact source document/page selection, save/reload, medication edits,
separate dictionary suggestions and stale review display. **105 source files** (artifacts and PDFs)
had identical before/after SHA-256 checksums. Test reviews are isolated beneath
`data/debug/review_app/smoke_<id>/review/`; no real physician adjudications were created.
The loopback Streamlit server launched successfully; `/_stcore/health` returned `ok`.
No full-cohort extraction was run.

## Known UI limitations

- Three pilot patients have no validated event layer; their events cannot yet be adjudicated.
- Three patients still use V3 diagnosis artifacts, with their existing extraction limitations.
- Future module tabs expose status and expandable metadata; no editing.
- Separate row selector, no keyboard shortcuts, no exact PDF text highlighting.
- Structured list/dictionary edits use JSON text inputs; no automatic dictionary updates.
- Functional AppTest validation only; no screenshot or browser-pixel verification.

See [README.md](README.md) for installation, persistence, version selection and safety details.
