# Physician failure repairs: diagnosis/events 4.2, medication 3.1

This repair applies to the seven patients in the source-to-output audit. New artifacts are candidate revisions; no active extraction, physician decision, reference file or previous revision is replaced. Source-only CAA assertions remain separate from computed Boston criteria.

`extraction/structured_diagnoses.py` inventories numbered, bulleted and unnumbered diagnosis-list source occurrences before mapping. Each receives a mapped, non-disease/event or unmapped disposition. Unknowns carry their raw text, document/page/section, list position, source assertion, date precision and stable candidate ID. The Review App displays these candidates and includes them in the promotion adjudication gate. The conservative inventory also retains diagnostic details and possible line fragments for review; its item count is not a count of distinct diseases.

V4.2 adds the audited renal, tumor, epilepsy, vestibular, dementia and breast cancer mappings. Attributes remain source-specific: CKD stage is never inferred, dialysis dependence and nephrocalcinosis require explicit wording, tumor dates retain partial precision, and laterality is source-bound. Dementia uses one disease family with longitudinal subtype assertions. Numeric causal references resolve only to a unique numbered item in the same document/list.

Assertion engine `ASSERTION_SCOPE_1.2` preserves legacy NEGATED/UNCERTAIN handling and adds DIFFERENTIAL and NOT_ASSESSABLE for workup/nondiagnostic statements. HISTORICAL is an orthogonal temporal status so a historical diagnosis can still be established or uncertain. Phenotype non-support carries CAA_SUPPORT=NEGATIVE. Adjacent expanded names/acronyms inherit assertion scope. Only independently positive established mentions contribute to source disease identities. Existing immutable versions are not migrated or rewritten; the UI reads optional fields conservatively and retains the older state vocabulary.

Generic Insult yields a reviewable STROKE_UNSPECIFIED event. Ischemic corroboration requires a unique compatible same-document/year explicit ischemic source. A reliable document date plus an event-bound “vor N Jahren” can yield APPROXIMATE_YEAR and RELATIVE_HISTORICAL_INFERENCE. Canonical `date_evidence` retains the individual derivations even if an explicit source year makes the combined event more precise. No inferred day/month is stored.

Medication selector `MEDICATION_SOURCE_1.1` recognizes embedded lists, recommendation headings, qualifier headings and supported table continuation. It stops at unrelated clinical sections and allergy/history boundaries. Every selected row is materialized in `medication_source_rows` before cleaning or dictionary lookup. The workbench worker reselects from immutable prepared sections instead of replaying the old low-recall CSV. Generic module metrics leave source recall unknown without an independent inventory; normalization resolution always uses extracted mentions as its denominator.

No unrestricted fuzzy matching or dictionary alias was added. The local ATC/alias sources do not verify Furon, Levebon, Jardiance, Lixiana or Endoxaban brand associations. The versioned curation assessment records these as withheld, with reference hashes. Generic ingredient records alone are insufficient pharmaceutical provenance. These products reach normalization and remain visible as unresolved exposures where appropriate.

Evidence and all before/after tables are in `data/debug/physician_failure_repair_20261026T190001_368626Z/repair_summary.md`. The bounded repair/report driver is `python -m caa_pdf_extractor.physician_failure_repair --output <evidence-directory>`; it requires the seven original manifest snapshots and protected-input baseline, preflights the independent 320-row inventory, and publishes through guarded single-patient workers. It has no promotion call.

Run the targeted tests with:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_physician_failure_repairs.py tests/test_physician_failure_repair_ui.py tests/test_diagnosis_events_v4.py tests/test_af_assertion_safety.py tests/test_diagnosis_medication_v3.py tests/test_validation_cohort.py tests/test_workbench.py tests/test_review_app.py -q
```
