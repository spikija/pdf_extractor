# TFNE / neurological deficits v0.2

This version revises extraction and validation alongside v0.1. The v0.1 prompt,
candidates, raw logs, and physician reviews remain immutable. EEG, radiology/CAA,
diagnosis, medication and other modules are unchanged.

## Controlled comparison

The original ten-patient freeze at
`data/debug/tfne_pilot_v0_1_20261101T130202_586729Z/cohort_freeze.json` is the only
cohort authority. v0.2 copies the original input files byte-for-byte and verifies
their checksums. It does not select patients, run the newer section router, parse
PDFs again, or add context from other modules. This keeps the clinical source
material identical even though the shared section dictionary has since improved.

The installed local Granite tag and digest must match v0.1. The transport accepts
numeric loopback only, disables proxies/redirects and does not download models.
The worker audit guard blocks nonlocal network/DNS, database imports/writes,
subprocesses and writes outside the new debug directory.

## Four passes

1. **Local Granite episode detection.** Only frozen ANAMNESE and hospital-summary
   spans are supplied. The output has indices, source IDs, exact symptom excerpts
   and a literal episode date or null. A schema enum of unchanged source
   spans/sentences prevents invented or inflected paraphrases. The options are
   not presumed to be actual episodes; denied symptoms, plans, investigations,
   diagnoses without symptoms and background migraine are excluded.
2. **Local Granite phenotype extraction.** Each accepted detection receives one
   separate request containing only its cited spans and minimal detection data.
   Global source IDs remain stable. The schema requires exactly one episode
   object. Every clinical field has its own IDs, assertion and exact quotation;
   unsupported fields stay UNKNOWN/null/empty.
3. **Deterministic validation.** Exact-source checks, explicit concept support,
   polarity/domain consistency, independent persistence, duration units/open
   bounds, literal dates, recurrence versus stereotypy, and complete resolution
   are checked before candidates may be published. A source quote cannot hide a
   preceding negation or uncertainty. Whitespace-only PDF wrapping differences
   resolve to the original exact substring; changed wording is rejected. Language
   concept mapping and unit conversion are transparent source-supported rules.
4. **Derived research screen.** The unchanged `tfne_screen.py` rules operate only
   on validated fields. Clinical labels, imaging, EEG and CAA status do not supply
   symptoms or etiology. The screen is not a diagnosis.

The assertion schema has no UNKNOWN value. If its proposed assertion cannot be
grounded, it becomes conservative UNCERTAIN with a review flag and the independent
pass-1 symptom anchor as provenance. It cannot silently default to ESTABLISHED.

## Examples and safeguards

The permanent physician fixtures cover fluctuating confusion/headache/malaise,
ongoing two-day symptoms, ascending arm numbness with visual flickering, language
disturbance, and background migraine. Additional tests cover denied events,
conditional plans, missing field evidence, exact source scope, partial versus
complete resolution, recurrence without stereotypy, and uncertainty in negatives.

`seit 2 Tagen` yields minimum 2880 minutes and maximum null. A symptom present at
admission or for two days does not establish a persistent residual deficit.
Language alone supplies no cognitive, motor or sensory polarity. Ascending arm
numbness does not supply an invented hand-arm-face sequence.

Removed values remain in raw model logs and visible flags. Before any final
source-scope revalidation, the unpublished candidate is backed up. Request-time
code hashes and final-validation hashes/timestamps are separate. Revalidation
does not make model calls. Saved compatible responses may be replayed only when
source messages and prompts match and the response satisfies the final episode
cardinality contract; replay provenance identifies the original response hash.

## Physician review

Use the existing TFNE pilot queue and patient list. In **TFNE / Neurological
Deficits**, switch **TFNE extraction version** between v0.1 and v0.2. v0.2
highlights changed fields and shows Value, Evidence span and Validation status.
Complete frozen narratives remain available for checking missed episodes.

Candidates use `data/patient_artifacts/<patient_id>/tfne_neurological_deficit_v0_2.json`.
Reviews use the separate `data/review/tfne_neurological_deficit_v0_2/` namespace,
scoped by patient and artifact checksum. Physician edits are labeled as overlays;
candidate bytes and v0.1 reviews remain unchanged. Accept, edit, reject, uncertain,
split and merge actions remain available.

## Reading the comparison

The comparison CSV contains all episode/field pairs, unmatched episodes, available
physician judgments and the requested change types. Matching uses source parents
and quote overlap. A source passage can describe multiple events, so split/merge
alignment needs physician review. CORRECTED is a difference label, not proof of
clinical correctness. A rejection is retained as an episode-level judgment and
never turned into invented corrected field values.

Both retained-candidate and pre-validation raw-proposal metrics are reported using
the same v0.2 checks. Historical v0.1 abstention flags are not counted as surviving
candidate errors. Exact-excerpt/lexical support checks are conservative and cannot
replace clinical adjudication. Episode recall has no independently adjudicated
denominator; fewer unsupported fields do not establish better recall. Empty
extraction is not evidence that symptoms were absent.
