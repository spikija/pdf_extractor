# Diagnosis and CAA v2: validation-only architecture

This implementation produces proposed records for the frozen 20-patient cohort.
It does not import a persistence writer, create migrations, backfill a table,
change an existing extraction path, or update a clinical value. The proposed
objects below are additive schema concepts, not deployed database tables.

## Proposed objects

| Object | Identity and fields | Ownership |
|---|---|---|
| canonical_diagnosis | Stable patient/group ID; canonical_name; diagnosis_domain; diagnosis_subcategory; etiology; status; confidence; first_known_date/year/month; date_precision; date_basis; first-date evidence ID; deleted=false | One condition identity. Semantic grouping and representative metadata come from retained Granite output. |
| diagnosis_evidence | Stable original-mention ID; case_id; nullable proposed diagnosis links; source_document_id; page; section_id; offsets; original_text; normalized_text; original temporal object; temporal_role; deleted=false | Every original mention survives. A compound mention can require multiple identities; unresolved identity stays evidence-only. Unknown temporal roles are not invented. |
| clinical_event | Stable independent event ID; event_type; date/year/month; date_precision/basis; etiology; evidence IDs; document IDs; identity-review status | Recurrent events do not become a single chronic condition. Undated candidates are not asserted to be unique events. |
| source_caa_assertion | Source status; named criteria framework/version; source-owned date; temporal ownership; exact source IDs | Clinician wording and formal source criteria are independent of computed criteria. Examination-specific negation does not erase later positive assertions. |
| boston_v2_computed | Validated structured features with per-feature sources; category; assessability; reasons; rule version; computed_classification_date/basis | Research calculation, never a historical diagnosis or source date. |
| edinburgh_ct_computed | MRI adequacy gate; CT/lobar/spontaneous/context applicability; FLP; SA extension; APOE; explicit version; category; source IDs; computed date/basis | Fallback only when adequate MRI is unavailable. Unknown applicability is held separately from definite inapplicability. |
| imaging_metadata | MRI sequence flags and evidence; contrast class and whether agent named; ICH location and dimensions; conservative CMB location; CT morphology | One examination at a time; raw stored findings remain unchanged. No derived volume. |
| lab_research_proposal | First-column value only; source units/reference interval; abnormality; admission-anchored measurement_date; ENCOUNTER_ADMISSION_DATE | Study convention, not phlebotomy dating. Invalid/ambiguous source rows remain held. |
| medication_research_proposal | Discharge context/date; category; antithrombotic class and role; source locator | Discharge date never becomes first prescription. Dose alone does not establish anticoagulant role. |
| echo_metadata | TTE/TEE/unknown; explicit EF exact/range; supported positive or negative structured findings | Omitted findings remain unknown. |
| gender_promotion_proposal | Existing value; proposed value; physician acceptance; eligible flag; patient-bound evidence | Only an already physician-accepted value and live NULL can produce a promotion candidate. No live write. |

Domains and neurologic subcategories are strict enums in
`diagnosis_canonical_v2.py`. IDs are patient-bound, stable SHA-256 derivatives.
Model responses, including rejected and refined responses, are retained; Python
does not reconcile conflicting clinical labels. Member/root disagreement is
reported. A lexical contradiction between an explicit named neurologic condition
and the model category is a review hold, not a Python recategorization.

The initial model format can omit IDs, and compound source items can conflate
unrelated conditions. The validation report exposes these limitations. It must
return MORE_DIAGNOSIS_CAA_V2_WORK_REQUIRED if required semantic checks or blocking
holds remain. Technical JSON validity does not establish clinical correctness.

## Criteria contracts

[Boston v2.0 original Table 2](https://pubmed.ncbi.nlm.nih.gov/35841910/)
defines the calculator's age, presentation, strictly lobar hemorrhagic/white
matter marker, deep-lesion exclusion, alternative-cause and pathology inputs.
Autopsy-level definite and biopsy/hematoma supporting pathology are separate.
Cerebellar lesions do not supply a lobar or deep count. Generic white matter
disease/Fazekas and Marklager-only CMB wording do not establish qualifying WM
patterns or strict lobar CMB. Serial finding rows are not a lesion count. Counts
require distinctness and cSS/cSAH overlap resolution upstream. Incomplete counts
can establish a lower-bound probable category only if every other prerequisite
is supported; they cannot establish absence or possible-only disease.

The original Edinburgh calculator is pinned to
[Rodrigues 2018 Figure 3](https://www.pure.ed.ac.uk/ws/files/51217772/1_s2.0_S1474442218300061_main.pdf).
It holds FLP-without-SAH combinations absent from that figure rather than
substituting a later variant. The
[simplified rule](https://pubmed.ncbi.nlm.nih.gov/35314501/) uses both CT features
for high, one for intermediate, neither for low. Unknown APOE remains unknown.
Unknown CT morphology never becomes a negative feature. CT eligibility requires
supported spontaneous lobar ICH and an appropriate context; unresolved clinical
eligibility is explicitly held. These are report-derived feature proposals,
not an independent review of the actual MRI/CT images.

Edinburgh is a fallback framework. At the patient/timepoint level, adequate
cerebral MRI routes to Boston v2.0 and suppresses Edinburgh for every CT,
including serial follow-ups. Missing Boston presentation or exclusion inputs
does not authorize Edinburgh when MRI is adequate. Confirmed absence or
inadequacy of MRI permits Edinburgh only for spontaneous lobar ICH with
diagnostic CT and appropriate context. Otherwise imaging classification is
NOT_ASSESSABLE. Unresolved MRI adequacy remains a review hold.

Adequacy requires interpretable information sufficient for CAA hemorrhagic
marker assessment, including susceptibility-sensitive information as needed.
Missing SWI/GRE names alone does not establish inadequacy: supported indirect
susceptibility-sensitive evaluation may establish adequacy, flagged for review.
The evidence assembler owns this decision and its source references;
`adequate_mri_available` defaults to unknown, never to presumed absence.
`caa_imaging_assessment` routes assembled evidence to exactly one framework.

Every non-unknown calculator feature requires provenance. The cohort runner
uses only frozen source/finding/encounter IDs; `require_provenance` can also
validate against an explicit source-ID allowlist. Missing blood-sensitive
sequences never mean no CMB. An MRI-owned explicit contrast administration/header
supplies contrast class; the named-agent flag separately records drug naming.

## Review workflow and safety

The debug freeze captures all-table/schema fingerprints and pre-existing code
and JSON hashes under read-only SQL enforcement. Validation reads the frozen
cohort and retained local-model responses. Loopback-only Ollama uses installed
granite4.2:latest with pinned digest, temperature 0, seed 0, context 16384.
No cloud or model download is used. All generated patient artifacts inherit
restricted local ACLs. The report contains structured clinical proposals and
source locators; full source objects/responses are in the protected audit.

The 13 required CSVs are accompanied by echo, admission-phenotype and separate
source/computed temporal-summary CSVs. A replay reuses responses and must produce
identical proposal CSVs. Full tests and final read-only fingerprints check that
existing clinical and source values were not changed. Unknown observations and
unresolved semantic disagreements remain review holds.
