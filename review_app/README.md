# Local physician extraction workbench ? Stage 1

Launch from `C:\app\pdf_extractor`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-review.txt
.venv\Scripts\Activate.ps1
streamlit run review_app/app.py
```

Or double-click `run_review_app.bat`, then open http://127.0.0.1:8501.
The server binds to loopback and disables usage statistics. Patient data stays local.

## Scope and source inputs

Stage 1 supports diagnosis/event V4 and medication V3 reruns for exactly these pilot patients:
`2435608415`, `2459345815`, `2552817617`, `2684469520`, `2826947422`.
There is no batch/cohort extraction interface, and the shared runner rejects every non-pilot ID,
patient list and unsupported module. Patient selection, page navigation and ordinary rerendering
never execute extraction. CAA, neurosonology, EEG, scales, scores and OAC tabs remain read-only.

A rerun executes the existing `diagnosis_events_v4.assemble` or `medication_identity_v3.assemble`;
there is no duplicate extraction implementation in the UI. It reassembles saved source sections,
mentions and medication rows, as the existing V4/V3 runners do. It does not rerun OCR/PDF parsing
or upstream extraction. Saved inputs and source PDF SHA-256 hashes must match, or execution stops.
Changed PDFs require a separately refreshed upstream source snapshot.

## Stable storage and migration

The authoritative root is `data/patient_artifacts/<patient_id>/`:

```text
module_manifest.json
sources/<input-digest>/                    # immutable copies of the five required source inputs
diagnoses/v4.0/                           # migrated existing V4, when available
diagnoses/v4.0+<timestamp>-<run-id>/       # newly generated candidate revision
medications/v3.0/
medications/v3.0+<timestamp>-<run-id>/
.runs/<run-id>/request.json
.runs/<run-id>/result.json
```

Each module directory contains clinical JSON/CSV artifacts and `module_metadata.json`.
Migration retains original artifact filenames and exact bytes so existing physician review hashes
remain valid. Candidate artifacts use unversioned layer filenames inside their version directory.
The algorithm version remains **4.0** or **3.0**; `+<run-id>` identifies a rerun rather than claiming
an algorithm change. Version selection is driven by the manifest, not hard-coded version numbers.

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

PDF pages render locally with PyMuPDF. Evidence navigation selects exact document/page; invalid references
show an error. No text highlighting or keyboard shortcuts. Structured edits use JSON inputs. Current and
candidate table rows use separate item selectors. A large evidence set may create a long panel.

Artifact discovery is cached for 30 seconds; **Refresh artifact index** clears the UI cache. File-signature
caches avoid repeatedly parsing stable clinical JSON; PDF rendering stays lazy. Workbench reads are
read-only. No browser-pixel screenshot was taken; functional UI coverage uses Streamlit AppTest.

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
