# Radiology v2 and PET examinations

The additive Alembic revision `0003_radiology_exam_findings_pet` creates
`radiology_exam` and `radiology_finding` alongside the unchanged legacy
`radiology` table. Three nullable PET enum columns extend `examinations`.
There is no clinical backfill. A downgrade refuses to discard populated v2/PET
data. Parent identity is `(case_id, source_document_id, procedure_id)`; the
database enforces its uniqueness. Findings never define exam identity.
Revision `0004_radiology_temporal` adds nullable `temporal_status` and
`interval_new`, plus optional grade/distribution columns. It performs no backfill.

## Workflows

```powershell
python -m alembic upgrade head
python -m caa_pdf_extractor.extraction.radiology_v2 2684469520
python -m caa_pdf_extractor.extraction.examinations --patient 2684469520 --dry-run
python -m caa_pdf_extractor.extraction.examinations --patient 2684469520 --pet-only --dry-run
python -m caa_pdf_extractor.extraction.radiology_legacy_review 2684469520 --output data/debug/legacy_review.json
```

`radiology_v2` is the new exam/finding workflow. The established `radiology
--patient ...` CLI also defaults to v2. `--legacy-v1 --dry-run` permits reference
extraction; legacy CLI commits are refused. The old Python API is retained for
compatibility/tests, and its artifacts are never v2 commit inputs. Existing
legacy batch harnesses require explicit migration before a future broader run.

V2 commit requires an independently reviewed current artifact and separate
authorization. Its `--commit` path verifies checksum, source/rule/config
fingerprints and original dry-run audit, revalidates deterministic evidence,
checks all existing rows, then inserts parent exams before children in one
transaction. It never calls a model. Exact existing exams/children are skipped;
curated conflicts abort the transaction. Even an exam with zero findings or a
failed classifier survives when its source identity is valid. No clinical commit
is performed by discovery or the legacy review tool.

## Source and date policy

`imaging_reports` discovers bounded report headers independently of finding
classification. Wrapped composite MRI headings retain their final explicit date.
Comparison dates are excluded. Date priority is current procedure header,
attached examination date, prepared document date, explicit first-page
admission/discharge end date, then NULL. Fallback is configurable using
`--no-document-fallback`. Offsets refer to `document_text_v1`, the exact source
sections joined by a newline. Explicit dated performed-study summaries are
included and labeled separately from full reports. Plans and comparison-only or
partial-date mentions do not create invented dated studies.

Repeated identical dated composite PET/CT headers are source fragments of one
parent, with every exact span retained. Different dates remain different exams.
PET is routed only to examinations, never to the radiology tables. A normal or
indeterminate PET still produces a source-backed examination. Low-dose CT text
does not determine PET classification. Generic abnormal FDG uptake never
establishes an Alzheimer pattern. An explicitly abnormal classic bilateral
temporoparietal/posterior-cingulate distribution or an explicit named pattern is
required; isolated temporomesial hypometabolism is nonspecific.
FDG classifications are brain-scoped. Exact components are attributed to BRAIN,
HEAD_NECK, BODY, OTHER (including CT) or UNASSIGNED. Only a reported brain
interpretation can determine `fdg_pet_result` and `fdg_pet_pattern`. An empty
brain heading, deferred brain report, gastric uptake, lymph nodes or bone lesions
cannot manufacture an abnormal brain result. Missing brain interpretation is
INDETERMINATE with NULL pattern; extracranial findings remain free text/audit.
The PET-only workflow uses separate immutable `pet_examinations_*.json`
artifacts and never re-extracts ultrasound or unrelated examination domains.

## Finding normalization

Atomic clauses preserve exact evidence and offsets. New and pre-existing CMB
components can coexist within the same MRI. Interval-new CMB has
`temporal_status=INTERVAL_NEW`, `interval_new=true`, `acute_lesion=NULL`;
interval detection alone does not establish acute age. Explicit pre-existing CMB
is CHRONIC with false compatibility acuity and false interval-new. Unestablished
age is INDETERMINATE with NULL acuity. Explicit acute ischemia/ICH is ACUTE;
subacute age is SUBACUTE with NULL compatibility acuity. Resorbed ICH is CHRONIC.
CSS remains chronic even when first detected; a separate acute hemorrhagic event
is a separate finding. Stable/unchanged ICH alone is INDETERMINATE, and future
resorption recommendations do not establish current lesion age.
Susceptibility artifacts require explicit microhemorrhagic morphology in
the same report, or a unique same-document MRI-month link with exact supporting
evidence. Dental/technical susceptibility alone is not CMB.

Prefer WMH for Fazekas/white-matter structural abnormalities, avoiding a second
microangiopathy row for the same structural statement. Explicit microangiopathy
without WMH morphology can remain `MICROANGIOPATHY`. Fazekas grade is retained
as `finding.attributes.fazekas_grade` in the immutable artifact and is available
to the nullable structured column during a separately approved commit. Optional
`lobar`, `deep`, `infratentorial`, `cortical`, `subcortical` and `distribution`
columns are reserved without automatic population. Values are not inferred.
Report-level pattern is derived from atomic findings and never replaces them.

Discovery and rule coverage remain bounded, not exhaustive clinical recall.
Uncertain or incomplete historical mentions are listed separately in the focused
audit rather than silently assigned to a current study.

## Explicit negatives and assertion ownership

Migration `0005_radiology_negatives` creates `radiology_negative_finding`, linked
to its exam by a required foreign key. It stores an explicit negative enum,
nullable location, exact evidence and deleted flag. Negative statements never
become positive finding rows. Absence of an accepted phenotype creates no
negative row. Uncertain/conditional text remains in `uncertain_findings` audit;
`keine sichere Ischämie` and `nicht auszuschließen` are not explicit negatives.
Each MRI artifact separates positive, negative and uncertainty evidence.

Negation and uncertainty are scoped to the candidate clause. A negative acute
ischemia clause can coexist with a positive chronic CMB clause. Etiologic
differentials do not negate otherwise definite morphology. Template headings,
indications, unknown bleeding compartments and conditional parenchymal components
are blocked from positive output. Subdural hematoma is not ICH; sulcal siderosis
is CSS, not a second ICH.

Logical headers aggregate only procedure vocabulary and attached dates, retaining
exact physical-fragment offsets. Peer procedure headers end the parent report;
angiography evidence cannot be used by the preceding MRI. A versioned source
fingerprint invalidates older artifacts. Future authorized commits revalidate
both positive and negative children, plan conflicts before writes, and replay
idempotently without inference.

Nullable `radiology_exam.anatomic_scope` and `performed` are additive, without
backfilling existing rows. Only performed BRAIN studies generate CAA phenotype or
negative children. Spinal studies and aborted attempts keep their exam provenance
with zero children; aborted attempts have performed=false. These attempts are
reported separately from completed imaging.

## Concept ownership

`radiology_concept_ownership_v2` identifies morphology and modifier concepts
before assigning PRESENT, ABSENT, UNCERTAIN or CONDITIONAL. Negation belongs to
its explicit target; a coordinated list can share a negator, while relational
phrases such as enhancement around a hemorrhage cannot. Qualified negatives
(`no large/space-occupying hemorrhage`) remain in audit rather than broadening
to NO_ACUTE_HEMORRHAGE. Etiology, enhancement, mass effect, tumor assessments,
vascular assessments and temporal predictions are retained in `other_assertions`.
Their uncertainty cannot suppress a separately asserted morphology.

Atomic findings retain exact contiguous source evidence and offsets. Separate
age components remain distinct; explicit new-and-old CMB coordination supports
interval-new and chronic children with shared source evidence. Current artifacts
must revalidate this policy and its implementation; older assertion artifacts
are stale. PET normalization and study discovery are unchanged by this policy.
