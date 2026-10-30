# V2.3 research review layer

Run from the repository root using the existing virtual environment:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.v23_review --source data/debug/full_cohort_20261022T201512+0200
.venv\Scripts\python.exe -m pytest tests/test_v23_review.py -q
```

The command creates a fresh timestamped directory under `data/debug`. It reads cached JSON/CSV only. It installs an execution audit hook that denies network and SQLite access, limits file writes to the newly created output directory, and prohibits deletion, replacement and renaming. It checks SHA-256 for every frozen source artifact before and after execution. It neither imports a clinical persistence workflow nor opens a PDF. The production schema and clinical tables are outside this workflow.

`V23_physician_review.md` links all outputs. `manifest.json` records source coverage, versions, dictionary hash, output counts and gate results. `hard_gates.json` contains the actual cohort checks and required synthetic regression checks. `READY_FOR_V23_PHYSICIAN_REVIEW` means the deterministic regression gates passed; it does not mean the proposals have received clinical approval or every score is complete.

## Diagnosis and medication normalization

Source candidate eligibility is preserved. A deterministic taxonomy annotation does not turn an anatomical finding, symptom or rejected source candidate into an accepted disease. Compound labels can yield several separate identities, with the same original record and evidence links. AF subtype and valve severity remain separate source qualifiers. Existing event identities and hemorrhagic transformation flags are preserved, while cerebrovascular event taxonomy is annotated separately.

`data/reference/medication_dictionary.csv` is a local reference dictionary. Generic entries with a unique ATC ingredient label are obtained from the cached official [ATC German WIdO code system](https://termgit.elga.gv.at/CodeSystem-atc-deutsch-wido.html), version `4.0.0+20260501`; route-ambiguous ingredient names are omitted unless separately curated. Verified brand aliases carry individual BASG/EMA references and a verification date. Atorvalan and Rosuvalan follow the user's approved mappings. Lookup is an exact normalized name prefix with word boundaries. Uncurated combinations and ambiguous brands remain unknown. ATC classes describe drugs; they do not establish the patient's treatment indication. Heparin use does not establish therapeutic anticoagulation. Unknown names are grouped and sorted by distinct patient count, with no invented suggestions.

## Source observations and event linkage

Every cached section is searched for explicit NIHSS/mRS and other scale totals. Observations preserve document, page, section, source offset, evidence ID and a short quote. The separate lexical census checks explicit totals and qualified timepoints. The regression fixture independently verifies the five required stroke values and thrombectomy/TICI metadata.

Stroke-block context resets at a new event. A pre-mRS qualifier belongs to that score and does not change neighboring admission scores. Explicit later observations cannot become admission scores. Linking requires a unique dated event or an explicit stroke block; ambiguous observations remain unlinked. A historical event's admission/discharge score is not dated to a later encounter. Source block candidates remain review proposals and must not be counted as adjudicated events. `stroke_score_timepoint_review.csv` records all seven sought slots for existing stroke events and dated source block candidates; missing slots remain null. It is a review availability table, not a stroke incidence denominator.

Explicit observations override retrospective values. Conflicting explicit totals are retained as observations and the resolved value stays null. This implementation does not reconstruct NIHSS items or infer mRS/CFS from acute neurological deficits. Nonstroke admissions receive availability/assessability rows, including postictal or sedation flags where reported, rather than fabricated totals.

Existing canonical MMSE/test assessments are reused. Raw repeated mentions are not promoted into additional performed tests. Supplemental explicit CERAD totals and named subtests retain their raw scores; historical references are marked separately and an unknown maximum stays missing. Unparsed CERAD/table references remain in a separate cognitive lexical review. MoCA is never converted into MMSE. Tests with incomplete dates retain their original date precision. STESS attempts require a true source status episode, including aphasic status; isolated seizures and erroneous legacy status labels are excluded. Age, previous seizure history, event-owned seizure type and admission consciousness are scored separately. Sedated consciousness and unresolved components remain unknown.

## Temporal research scores

CHA2DS2-VA requires confirmed AF. The first confirmed source-availability date is the conservative AF index; a diagnosis retrospectively described in a later document does not automatically establish that all its accompanying facts were known at an earlier date. Each OAC decision gets a separate index. Positive and explicitly negative component evidence is retained; absence from a document does not imply a negative. Conflicting facts remain unresolved. Carotid plaque alone does not satisfy vascular disease. Age bands are mutually exclusive. A score is produced only with all components known.

ORBIT uses the decision index, sex-specific Hb/Hct thresholds, documented anemia/bleeding history, eGFR and active antiplatelet treatment. Normal Hb alone does not exclude an unknown anemia history. The prior lab window is 90 days. Selection order is same date, prior same admission, then closest prior measurement. Different same-day values without sample times are ambiguous. The helper `get_lab_value_before_index` requires a validated, explicitly dated sample; admission convention dates and undated first/last/min/max table cells cannot enter historical ORBIT. Such recovered observations remain visible in `orbit_laboratory_evidence.csv`. Creatinine is retained separately and never converted to a guessed eGFR. The mandatory synthetic example scores 7/HIGH and is invariant to later lab results.

Definitions: [ESC 2024 AF guideline](https://academic.oup.com/eurheartj/article/45/36/3314/7738779), [original ORBIT publication](https://pmc.ncbi.nlm.nih.gov/articles/4670965/), [STESS guideline](https://link.springer.com/article/10.1007/s12028-025-02425-8). ESC OAC recommendation is a thromboembolic indication field; it never overrides hemorrhagic phenotype or clinical decisions. STESS is a research prognostic measure, not a treatment rule.

## Decision semantics and limitations

Anticoagulation decisions are recognized in a separate bounded local semantic-rule module. It distinguishes initiation, continuation, withholding, stopping, pausing, switching, LAAO consideration, antiplatelet substitution and absence of a documented decision. Every result requires physician review. A reason's historical date is not automatically an action date. Without an owned explicit date, a discharge date means only that the statement was available by discharge. It must not be interpreted as an exact treatment timestamp. The indication is populated only when source language links it to the action.

The event-level dataset retains candidate event IDs when linkage is uncertain. Frozen whole-cohort CAA membership is named separately from dated CAA evidence. Imaging findings are dated and source linked, but serial lesions have not been newly adjudicated, and the dataset explicitly retains a phenotype review flag. It must not be used to sum distinct lesions without review. Short evidence quotes are bounded; the full frozen source remains the reference for adjudication.

All incomplete scores, unresolved medication mappings, conflicting observations, uncertain dates and event links remain in the review outputs. They are substantive missingness and review holds, not zero-valued risk factors. No new extraction or clinical backfill is authorized by a passing release status.
