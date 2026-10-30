# Encounter phenotypes and retrospective mRS

Migration `0008_encounter_mrs` adds `hospital_encounter` with one unique
case/source-document identity. It adds no clinical rows. It keeps encounter
dates separate from document, imaging and disease dates, and stores primary
reason plus four nullable coexisting category flags. It retains baseline and
admission mRS separately, including source type, confidence and evidence.
`assessment_json` retains exact source offsets, functional facts, temporal
ownership and audit-only rejected assertions. No existing extraction semantics
change.

Admission reason selection uses bounded current-presentation text from
Anamnese, admission summaries and explicit presentation sections. Embedded
dated Anamnese and neurological-exam subreports retain their original prepared
section/page offsets. Prior admissions, diagnosis-only histories, medication
lists, peer consultations and later intrahospital transfers cannot independently
establish admission reasons. Unresolved primary ties remain UNKNOWN while
supported coexisting flags remain true. A NULL flag means not established;
absence of a finding is not a negative admission assertion.

This implementation uses conservative deterministic source rules. It does not
call Granite. Bounded semantic model classification was permitted by the task,
but no model interpretation is needed for the accepted source projections.
Unknown reasons and unclassified source references remain visible for review.

Functional facts are extracted before scoring and carry PRESENT, ABSENT or
UNKNOWN plus BASELINE, CURRENT_ADMISSION, DISCHARGE or UNKNOWN ownership.
Admission scores use CURRENT_ADMISSION only. Baseline scores use BASELINE only.
Literal temporally labelled mRS fields may be read from a diagnosis/outcome
table; that exception never supplies admission-reason categories.

An estimated exact score requires direct functional evidence. Unspecified help
with independent walking supports 2–3; inability to walk independently or
explicit bodily-needs assistance supports 4–5 until severe-dependency facts
separate the levels. Walking aids, hemiparesis, aphasia, admission and subjective
absence of complaints alone do not determine mRS. Unknown stays NULL/LOW.
Living admission mRS 6, invalid noninteger scores, conflicting literal scores
and conflicting functional evidence are blocked. Admission/discharge/baseline
labels cannot overwrite each other.

Generate a review artifact without clinical writes:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.extraction.encounter_workflow --patient 2834596323 --case-id 19 --dry-run
```

The workflow re-reads patient/case identity. Immutable artifacts fingerprint
source content and policy/schema implementation. `verify_artifact` revalidates
exact deterministic output from the current source; `plan_commit` is a pure,
zero-model persistence plan. Matching rows are UNCHANGED_IDEMPOTENT and curated
differences/duplicate identities are CONFLICT_EXISTING. This task exposes no
clinical write endpoint and does not authorize a backfill.

The same-20 validation includes a physician review table containing selected
Anamnese/examination text, extracted facts, baseline/admission scores or ranges,
confidence and exact evidence. Its proposed estimates are pending physician
review, not a completed clinical gold-standard evaluation. Source coverage is
bounded and does not establish exhaustive recall.
