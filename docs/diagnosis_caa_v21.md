# Diagnosis and CAA V2.1 frozen review

This is a review-only workflow for the existing frozen 20-patient cohort. It
does not connect to a database, import a persistence writer, create migrations,
backfill records, or change validated history, encounter, radiology, MMSE or mRS.
The CLI accepts only a V2.1 output directory directly under `data/debug`.

```powershell
.\.venv\Scripts\python.exe -m caa_pdf_extractor.diagnosis_caa_v21_review --output data/debug/diagnosis_caa_v21_<timestamp> --infer
```

Omit `--infer` for an offline replay of retained responses. Inference uses only
the installed, digest-pinned Granite 4.2 model at numeric loopback, without
proxies, redirects, downloads or cloud access. Prompts, responses, rejected
attempts, settings and input hashes are retained in the protected output folder.
Replay rejects stale prompts and invalid responses. A disease response from an
earlier generation schema may be reused with the identical input prompt only
after passing all current semantic, identity and date validators; its original
schema and raw response remain retained. Other stale schemas are rejected. Inference can
archive and replace invalid/stale responses while preserving the prior file.

## Diagnosis eligibility and grouping

Granite first assigns every original candidate record one of the required 14
eligibility categories. Only `DIAGNOSIS_DISEASE` candidates enter a second,
patient-level Granite grouping request. Acute events have independent identities
and dates; historical occurrences remain events. Other candidates remain source
evidence. All original mention objects survive in `source_mentions_audit.json`
and `diagnosis_mentions.csv`, including mentions with no canonical link.

The model nominates a root record for each disease. Root metadata supplies the
canonical proposal. Differences in member metadata are exposed for review and
retained in the response. Python rejects incompatible domain/subcategory
proposals; it does not replace them with a guessed disease. A first supported
date must equal the earliest accepted source date in that group. Unresolved
eligibility, technical failures and rejected groups remain explicit holds.
For unambiguous labels covered by the requested domain/subcategory sanity
rules, the generation schema also rejects incompatible enum values. This
constrains categories without supplying a replacement disease name or identity.
When generating a new response, meaningful names are bounded to retained source
labels (with the explicitly requested `SYMPTOMATIC_EPILEPSY` normalization where
supported). Granite chooses the disease grouping and representative root.
Previously retained normalized names may still be reused after current semantic
validation; no source label or diagnosis is rewritten in Python.
Category sanity follows the primary disease wording before etiologic phrases:
epilepsy secondary to CAA remains epilepsy. Repeated chronic CAA or epilepsy
identities are rejected for model regrouping. Imaging-marker wording merely
suggesting CAA remains investigation evidence rather than an established disease.
Previously validated, explicitly current admission events are retained with
their original date basis and evidence IDs. This preserves dated status/seizure
episodes that have no exact date on a chronic-history candidate; history dates
are not changed or filled from the admission date.

## Patient/timepoint evidence

Granite proposes sourced episode and imaging features, never a final criteria
category. Admission narrative, validated encounter phenotype, diagnosis records,
and validated positive/negative imaging findings are supplied together by
document. Original clinical values remain immutable. Literal susceptibility
sequence evidence and previously validated positive lobar markers are retained
if a new feature proposal drops them; disagreements are flagged for review.

Each dated MRI assembles prior available episode presentations and hemorrhagic
evidence. Future imaging cannot contribute to an earlier MRI. Event dates supply
conservative ICH identity when serial localization changes. Undated observations
cannot establish additional distinct ICH. CMB and cSS lower bounds use the maximum
supported scan count rather than summing serial reports; this can underestimate
additional lesions when their identities cannot be established. Feature rows
retain the counting basis and source references. Redundant unchanged clinical
states do not produce extra Boston result rows. A category's computed date is
its first sufficiently supported timepoint, separate from source diagnosis dates.
An earlier adequate MRI remains available when a later MRI is unreported or
inadequate; the feature row identifies both the current and assessment MRI.

Deep exclusion distinguishes explicit absence, an adequate complete distribution
report with no deep lesion mentioned, and unknown. The middle state requires
physician review and remains visible when supplying an exclusion proposal.
Alternative cause distinguishes present, explicitly excluded, no supported
alternative identified in the episode, and unknown. The third state likewise
requires review; omission from one MRI is insufficient to establish it.
Susceptibility-sensitive evaluation can establish MRI adequacy without sequence
names and receives the indirect-evidence flag. Generic symptoms do not create
TFNE, generic WM disease does not create a qualifying WM marker, and nonspecific
SAH does not create cSS. Computed results are deterministic review proposals.

## Edinburgh fallback

An adequate MRI available in the event's diagnostic episode (or earlier) blocks
Edinburgh for all its CTs. Later unrelated episodes do not change an earlier
timepoint. Unresolved MRI adequacy stays held. Confirmed absent or inadequate MRI
permits CT assessment only with supported spontaneous lobar ICH and appropriate
clinical context. Missing other Boston inputs never permits switching from an
adequate MRI to Edinburgh.

CTs share an event by supported ICH onset date. Unknown event identity is held,
never silently treated as distinct independent disease. The earliest available
CT with both morphology features assessable supplies the primary morphology
assessment; the earliest available CT date is retained separately. CT roles and
all supporting IDs remain in the event row. Original APOE-dependent and
simplified morphology results occupy separate columns. Unknown morphology is
never a negative feature. Source CAA assertions are copied separately from the
previous frozen source review and never changed by either calculator.
Conflicting onset dates can share an event only when the source explicitly
compares a prior dated scan and describes continuity of the same localized
hemorrhage. Similar locations or nearby dates alone do not merge recurrences.

## Outputs and gates

The runner writes a physician Markdown review plus the ten requested CSVs,
original mention audit, source/feature assembly audit, input hashes and CSV hashes.
Each non-unknown calculator input has provenance. Independent gates inspect
eligibility, accepted category compatibility, mention preservation, recurrent
dated events, source/computed separation, temporal presentation joins, lesion
counts, feature links, event CT assignments, and fallback eligibility. Technical
failures, unresolved event identities and eligible diseases without accepted
groups prevent readiness. Physician review flags remain visible even when all
mechanical gates pass. These checks do not substitute for physician review of
the model's interpretation or the original images.

The result is `READY_FOR_DIAGNOSIS_CAA_V21_PHYSICIAN_VALIDATION` only when every
hard gate passes; otherwise it is `MORE_DIAGNOSIS_CAA_V21_WORK_REQUIRED`.

## Critical addendum: CSF biomarkers and neurological hierarchy

The review runner also reads every raw page and structured table in the frozen
source-document snapshot. It does not rely on previously extracted laboratory
rows. `csf_analytes.py` matches Unicode beta, sharp-s and documented OCR/name
variants only in analyte contexts; original text remains unchanged. Source
rows, analyte spans, document/page/section/line evidence, reported units,
reference intervals and abnormality flags accompany each result. Research dates
use the source encounter admission with `ENCOUNTER_ADMISSION_DATE`; missing
admission provenance blocks readiness. Explicit serum/plasma rows are excluded.
Ambiguous numeric cells are held and block the supported-amyloid coverage gate.
The lexical census includes narrative exclusions as well as laboratory results.

Supported assays include Aβ42, Aβ40, reported 42/40 ratios, total tau, nonspecific
p-tau and explicit p-tau181/217/231. A nonspecific amyloid assay is retained as
`CSF_ABETA_UNSPECIFIED`, never assumed to be Aβ42. No derived ratio is generated.
In the actual two regression sources, document 40/page 7 reports ß-Amyloid 425
pg/ml and document 69/page 14 reports 269 pg/ml; both specify reference >600 and
neither specifies an isoform or reported ratio. Admission dates are 2020-11-16
and 2022-12-22 respectively. The corresponding tau and p-tau values remain
342/54 and 749/61 pg/ml.

`neurologic_taxonomy.py` defines and validates category/subcategory parentage.
Explicit source-label rules apply the requested hierarchy to retained disease
and event identities, preserving original model domain/subtype/etiology fields.
Canonical disease names are unchanged. The addendum's explicit subjective
cognitive decline inclusion retains the former symptom-only eligibility and
links all original mentions to a source-named review proposal. SAH and seizure
refinements remain events, with original event type retained. Previously broad
event groups split only when their source records have different fine subtypes;
their source dates and evidence remain intact. Ischemic hemorrhagic
transformation is a separate flag; delirium is psychiatric, separate from dementia.
CAA-ri and ABRA are distinct inflammatory subtypes; a mixed ordinary/inflammatory
CAA group blocks readiness. Source-diagnosed subdural collections retain their
identity without inventing acute onset or chronicity.

The added exports are `beta_amyloid_census.csv`, `csf_biomarkers.csv`,
`csf_regression_review.csv`, and `neurologic_taxonomy_review.csv`, plus
`addendum_audit.json`. The physician review includes the hierarchy, CSF source
results, lexical exclusions and explicit isoform/ratio absence review. Additional
gates check frozen-document census coverage, supported amyloid omissions,
beta notation variants, neighboring tau/amyloid omissions, taxonomy parentage,
recognizable labels left under OTHER, event/disease separation and evidence
links. All operations produce local review artifacts only; no backfill,
clinical writes, database connection or cloud inference is performed.

## Diagnosis taxonomy V2.2: deterministic cardiovascular identities

`cardiovascular_taxonomy_v22.py` applies the requested source-name rules after
the retained model grouping. Vorhofflimmern/VHF/AF and atrial fibrillation map
to `ATRIAL_FIBRILLATION`, display name `Atrial fibrillation`, under
`CARDIOVASCULAR / ARRHYTHMIA`. TRINS and both German tricuspid insufficiency
names map to `TRICUSPID_REGURGITATION`; MINS and both mitral insufficiency names
map to `MITRAL_REGURGITATION`, under
`CARDIOVASCULAR / STRUCTURAL_HEART_DISEASE`.

One stable patient/disease identity combines synonyms independently of grade
or AF type. Original canonical names/IDs/domains/subtypes/statuses/etiologies
and all source memberships are retained as provenance. Original mention text,
accepted dates and source evidence remain unchanged; source-only partial dates
are not filled in. The earliest supported exact diagnosis date is retained.
Explicit AF diagnoses previously excluded as headings can enter through the
audited V2.2 rule; their original eligibility is preserved. Negated findings,
suspected diagnoses and investigation prose are not promoted.

`af_type` retains explicit PAROXYSMAL, PERSISTENT, LONG_STANDING_PERSISTENT or
PERMANENT wording; otherwise it is UNSPECIFIED. Intermittent wording alone
does not establish a formal AF type. Source evidence is checked for qualifiers
even when the retained candidate label is nonspecific. Conflicting explicit
types remain in `af_types` and `qualifier_observations`, with aggregate
UNSPECIFIED. Valve severity is separate: TRACE, MILD, MODERATE, SEVERE or
UNKNOWN. Ranges and changing grades retain every value and source observation;
aggregate UNKNOWN avoids selecting an unsupported single grade.

Flutter never supplies AF membership. Secondary AF receives its own canonical
identity while its source record also keeps its primary heart-failure identity;
both links appear in `canonical_diagnosis_ids`. A mixed legacy group is split
where needed rather than copying flutter sources into AF. Original source
mentions remain unique even when supporting more than one diagnosis.

The report displays AF type and valve severity separately from disease identity.
`cardiovascular_taxonomy_review.csv` records matched candidates, exclusions and
canonical links. The five requested cardiovascular gates, flutter separation,
duplicate patient/disease identities and qualifier preservation all block
readiness on failure. The frozen same-20, no-backfill/no-clinical-write/no-cloud
constraints continue to apply.
