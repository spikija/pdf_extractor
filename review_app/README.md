# Local physician extraction workbench

Diagnosis architecture vNext: [4.3 candidate report](DIAGNOSIS_ARCHITECTURE_VNEXT_REPORT.md).
The nine affected patients now have unified Patient diagnoses, Clinical events and Admissions sections.
Explicit diagnosis runs use local Granite on complete discharge summaries for probable admission reasons.
Browsing is read-only; these inferences remain separate and require physician adjudication.

The seven audited patients now have repaired diagnosis/event 4.2 and medication 3.1 candidates.
See the [repair report](../data/debug/physician_failure_repair_20261026T190001_368626Z/repair_summary.md)
and [implementation notes](../docs/physician_failure_repairs.md). Refresh the artifact index to review
the newest candidates. Active versions and physician decisions are preserved.

Current validation phase: [20-patient validation report](TWENTY_PATIENT_VALIDATION_REPORT.md).
The original five physician-reviewed references are preserved. Fifteen new patients have candidate-only
diagnosis/event and medication outputs; two diagnosis candidates are held after independent QA failures.
Historical five-patient results: [V4.1 report](FIVE_PATIENT_DIAGNOSIS_V4_1_REPORT.md).

An AF assertion safety repair supersedes the clinical readiness of affected historical V4 revisions.
See [AF_ASSERTION_FIX_REPORT.md](AF_ASSERTION_FIX_REPORT.md). Unsafe AF identities are visibly
suppressed, affected reviews remain stale, and bad candidates cannot be promoted. No patient
diagnosis extraction was rerun during that AF-only repair. Current explicit runs use diagnosis code 4.3.

Launch from `C:\app\pdf_extractor`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-review.txt
.venv\Scripts\Activate.ps1
streamlit run review_app/app.py
```

Or double-click `run_review_app.bat`, then open http://127.0.0.1:8501.
The server binds to loopback and disables usage statistics. Patient data stays local.

## Scope and source inputs

Diagnosis/event V4.3 and medication V3.1 reruns are enabled only for patients registered in
`data/patient_artifacts/validation_cohort.json`, with a hard limit of 20. The original five form the
immutable reference regression set; fifteen heterogeneous new patients form the second validation set.
There is no batch extraction button. The single-patient runner rejects unregistered IDs,
patient lists and unsupported modules. Patient selection, page navigation and ordinary rerendering
never execute extraction. CAA, neurosonology, scales, scores and OAC tabs remain read-only.

The EEG tab exposes EEG 1.1 candidates for the same separate 20-patient validation subset, with
structured focus/spread, pattern, reactivity, assessability and evidence-status displays.
The revision selector preserves EEG 1.0 and its separate reviews. EEG promotion stays disabled.
See [EEG 1.1 implementation](../docs/eeg_v1_1.md), [current results](EEG_V1_1_REPORT.md),
and the [historical EEG 1.0 results](EEG_VALIDATION_REPORT.md).

A rerun executes the existing `diagnosis_events_v4.assemble` or `medication_identity_v3.assemble`;
there is no duplicate extraction implementation in the UI. It reassembles saved source sections,
mentions and medication rows, as the existing V4/V3 runners do. It does not rerun OCR/PDF parsing
or upstream extraction. Saved inputs and source PDF SHA-256 hashes must match, or execution stops.
Changed PDFs require a separately refreshed upstream source snapshot.
Diagnosis runs additionally infer probable admission reasons from complete saved summary narratives
using local Granite. These remain a separate reviewed layer. Medication runs select bounded source
rows from saved prepared sections before normalization.

The cohort dashboard reports patient and module statuses. Filters include unreviewed, in review,
review required, approved, stale, error and diagnosis-layer resolved AF states. Candidate-only patients
open their candidate dataset by default. Frozen reference approval reflects the physician review reported
by the user; current/validated pointers and individual adjudications remain unchanged. Duplicate event
IDs expose every raw source row with separate inspection keys; ambiguous rows cannot be adjudicated,
and duplicate canonical IDs block promotion even without a pre-existing QA annotation.

## Stable storage and migration

The authoritative root is `data/patient_artifacts/<patient_id>/`:

```text
module_manifest.json
sources/<input-digest>/                    # immutable copies of the five required source inputs
diagnoses/v4.0/                           # migrated existing V4, when available
diagnoses/v4.3+<timestamp>-<run-id>/       # newly generated candidate revision
medications/v3.0/
medications/v3.1+<timestamp>-<run-id>/
.runs/<run-id>/request.json
.runs/<run-id>/admission_model_results.json # diagnosis runs only
.runs/<run-id>/result.json
```

Each module directory contains clinical JSON/CSV artifacts and `module_metadata.json`.
Migration retains original artifact filenames and exact bytes so existing physician review hashes
remain valid. Candidate artifacts use unversioned layer filenames inside their version directory.
Historical algorithm versions are retained. Current algorithms are **4.3** and **3.1**;
`+<run-id>` identifies a new revision. Version selection is driven by the manifest, not hard-coded numbers.

The stable manifest has `storage: STABLE`, a `modules` dictionary, and per-module
`active_validated_version`, `available_versions` and `versions`. Only the active pointer selects
current data. A newer candidate never supersedes current data. Historical validated versions remain
VALIDATED on disk, but inactive. The events pointer follows the diagnosis revision atomically.

Stable manifests take priority over debug discovery. Debug manifests are fallback only for patients
without a stable manifest. A corrupt active stable artifact does not silently fall back to older debug
results. Legacy modules retain their source status. Existing debug snapshots and PDFs are unchanged.

The initial migration has already been performed for all five pilots. The explicit, idempotent helper:

```powershell
.venv\Scripts\python.exe -B review_app/migrate_pilot.py
```

copies only the latest intact validated diagnosis/medication artifacts and the required saved inputs,
retaining original snapshot paths/checksums in metadata. It never reruns extraction or approves candidates.
Source PDFs remain in `pdf_db/<patient_id>/*.pdf`. Saved source bundle IDs map evidence to exact PDFs;
DOB/sex and encounters come from the stable saved `patient_summary.json` and `encounters.json` copies.

## Run, compare and adjudicate

1. Select the patient, then Diagnoses & Events or Medications.
2. Click **Run diagnoses for this patient** or **Run medications for this patient**.
3. Check the patient, module, current version and PDF count, then click **CONFIRM RUN**.
4. The local worker publishes a new **CANDIDATE**. Current validated data remains active.
5. Compare **ADDED / REMOVED / CHANGED / UNCHANGED** entities, then choose the candidate dataset.
6. Use the existing evidence, **Open source**, ACCEPT/EDIT/REJECT and explicit **Save review** controls.
7. When required candidate entities are adjudicated, check the physician approval confirmation and
   click **Approve candidate as validated version**, or click **Reject candidate version**.

Candidate reviews are visually labeled and stored separately from active reviews. Rejected candidates,
clinical bytes and all review history remain available. Rejected data cannot be promoted.

Comparison uses disease identities, event type/date/anatomy and overlapping evidence for unambiguous
changed event matching; it does not compare JSON lines or count repeated mentions as diagnoses.
Ambiguous event matches remain added/removed rather than silently merging episodes. Medication
comparison includes canonical ingredients/classes/ATC and individual exposure dose/schedule semantics.
Technical provenance/serialization changes alone do not change semantic entity status.

Promotion requires passing hard gates, intact artifact hashes, unchanged PDFs/source inputs/references,
no fatal extraction errors, and current ACCEPT/EDIT/REJECT adjudication of every review-required entity.
UNCERTAIN/UNRESOLVED/RESET do not satisfy this gate. Promotion rechecks the expected current version
under a file lock, preventing a concurrent update from being overwritten. Newly generated outputs
are never automatically validated, even when every technical test passes.

Diagnosis activation marks applicable AF, CHA2DS2-VA, ORBIT, CAA chronology/context and OAC modules
STALE_DEPENDENCY. Medication activation marks ORBIT, OAC and antithrombotic exposure analysis stale
where present. No dependent module is deleted or automatically run.

## Reviews, metadata and audit

Existing active adjudications remain under `data/review/<patient_id>/` and are never silently migrated.
Candidate adjudications use `data/review/candidates/<revision>/<patient_id>/`. Each scope has its own
review audit. When an active extraction hash changes, older active reviews become STALE_REVIEW;
old corrections are not applied. Missing reviewed identities also prevent patient approval.
Candidate adjudications remain in their original scope after promotion; they are not copied into
active review files. The promoted manifest records physician approval separately from active-entity
review status, which may still require review.

Module metadata records algorithm/revision, creation time, status, PDF/input hashes, saved-source
parser identifier, upstream snapshot identity, implementation/code hashes, optional git commit,
empty prompt hashes for these deterministic assemblers, reference hashes, artifact hashes, validation
results and fatal errors. Migration also records the original debug snapshot and unchanged content.

`data/patient_artifacts/module_run_audit.jsonl` records MIGRATE/RUN/PROMOTE/REJECT, patient/module,
previous/new revision, code hash and source hashes. Each per-patient manifest stores the authoritative
audit together with the active pointer in one atomic commit. The JSONL log is a recoverable projection
rebuilt under a global audit lock after each action. If a crash interrupts projection updates, manifest
audit remains intact; the next action or explicit `WorkbenchStore().sync_audit()` rebuilds JSONL.
Module metadata status is likewise a projection; the stable manifest is authoritative.

Atomic writes use same-directory temporary files, flush/fsync and replacement. Local OS locks serialize
module publication/promotion and review saves. A failed/incomplete publication cannot change the active
pointer. Newly generated clinical artifact files are never overwritten. Failure candidates retain
fatal errors and cannot be promoted.

## Safety and limitations

The app writes only new stable module/source files and mutable stable manifests/metadata/audits beneath
`data/patient_artifacts/`, adjudications beneath `data/review/`, and isolated smoke results beneath
`data/debug/review_app/`. It has no clinical database write interface. The offline extraction subprocess
blocks socket/database access, child processes, deletion and writes outside its fresh execution directory.
No cloud models/services run. Local medication dictionary files are read-only; suggestions remain separate.
Explicit diagnosis runs contact the installed local Granite service through numeric loopback only.
The model results are snapshotted before the offline worker starts; ordinary browsing makes no model calls.

PDF pages render locally with PyMuPDF. Evidence navigation selects exact document/page; invalid references
show an error. No text highlighting or keyboard shortcuts. Structured edits use JSON inputs. Current and
candidate table rows use separate item selectors. A large evidence set may create a long panel.

Artifact discovery is cached for 30 seconds; **Refresh artifact index** clears the UI cache. File-signature
caches avoid repeatedly parsing stable clinical JSON; PDF rendering stays lazy. Workbench reads are
read-only. No browser-pixel screenshot was taken; functional UI coverage uses Streamlit AppTest.

## EEG v1.1 fresh holdout

Select **EEG V1.1 HOLDOUT** in **Review queue** after refreshing the artifact index.
The 12 new patients have EEG v1.1 candidates only; all start UNREVIEWED and promotion
is disabled. The EEG tab shows the independent source inventory, exact page links and
source-comparison review flags, alongside standard explicit review actions.
The holdout has 43 source recordings and 42 candidate records, with 38 distinct source
recordings matched (88.4% recall). Five missed recordings, duplicate/false records and
other safety discrepancies are retained for physician review. Frozen extraction logic
was not tuned or rerun. See [the holdout report](EEG_V1_1_HOLDOUT_REPORT.md).

## Validation

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_review_app.py tests/test_review_app_ui.py tests/test_workbench.py tests/test_diagnosis_events_v4.py tests/test_diagnosis_medication_v3.py -q
```

Tests cover byte-preserving/idempotent migration, active pointer selection, candidate isolation,
semantic comparison, confirmation/rerender safety, real single-patient worker runs, gated UI promotion,
rejection, dependency invalidation, stale adjudications, audit, and blocked network/DB access.
Promotion/rejection tests use synthetic isolated storage and never approve a real patient candidate.
A separate recorded pilot smoke run exercised confirmed diagnosis and medication runs against real stable artifacts,
leaving candidates pending physician review. See `WORKBENCH_STAGE1_REPORT.md` for detected versions.
