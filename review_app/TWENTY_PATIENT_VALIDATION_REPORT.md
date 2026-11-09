# TWENTY_PATIENT_VALIDATION_READY_FOR_PHYSICIAN_REVIEW

**The cohort is ready for inspection and review, with two diagnosis candidates held for repair.**
The full cohort remains disabled. No new candidate was promoted.

- Cohort: **20 patients**, comprising the five original physician-reviewed references and 15 new patients.
- Source scope: **111 PDFs / 776 pages**.
- New patients with both mature modules executed: **15**.
- New patients passing independent validation: **13**; validation failures: **2**.
- Runtime pipeline failures: **0**. Failed diagnosis candidates: **2695942320**, **2953158925**.
- Versions: diagnoses/events **4.1**, medications **3.0**, AF assertion engine **ASSERTION_SCOPE_1.1**.
- Raw new-candidate entities requiring review or repair: **367**.
- Clinical database writes = **NONE**; cloud patient calls = **NONE**.

## Selected new patients

```text
2953158925, 2110307009, 2788388022, 2695942320, 2608083318,
2834596323, 2709535720, 2620249118, 2183012310, 2990288425,
2536647917, 2984303525, 2888149624, 2892132324, 2586499918
```

Selection was deterministic weighted feature coverage with patient-ID tie breaks, frozen before
extraction. Read-only screening used existing hash-matched saved text where available and bounded
native-text skims otherwise. All requested heterogeneity categories were covered: positive/negative/
uncertain AF wording, CAA wording/differentials, stroke/ICH/TIA/seizure signals, multiple admissions,
large and simple charts, neurosonology/scales wording, complex medication lists, formatting proxies
and copied diagnoses. These screening flags are **source indicators, not clinical diagnoses**.
No modules ran for patients outside this selected set. Only the 15 new patients were extracted.

## Results and queues

All 20 reference/candidate snapshots contain **103 disease identities**, **61 raw event rows**,
**170 medication identities** and **464 medication exposures**. The event count includes five duplicate
rows in the held candidates; there are **56 unique canonical event IDs**, not 61 approved distinct events.
Diagnosis-layer AF states: **12 confirmed, 2 suspected, 6 no confirmed AF, 0 conflicting**.

The dictionary-learning queue contains **121 unresolved normalized-name groups / 180 mentions**
across reference and new outputs; **141 mentions** belong to the new patients. Names are sorted by
patient count and then mention count. Existing suggestions are retained where available; no ingredients
are guessed and the global medication dictionary was not changed. Reference raw mapping statuses remain
available for dictionary learning without modifying physician adjudications.

The diagnosis queue contains **286 rows**, including assertion uncertainty/evolution, partial/unknown
event dates, ambiguous event ownership, unsupported ontology mappings and duplicate event IDs.
Raw review flags and queue rows are different counts; neither implies an automatic clinical diagnosis.

## Stress-test finding and safety gates

Repeated partial-year stroke references produced colliding canonical event IDs:

| Patient | Repeated event reference | Raw rows sharing the ID | Duplicate rows |
| --- | --- | ---: | ---: |
| 2953158925 | 2021 right MCA ischemic stroke reference | 4 | 3 |
| 2695942320 | 2014 left MCA ischemic stroke reference | 3 | 2 |

There are **two failing diagnosis-module gates / five duplicate rows**. Original clinical extraction
semantics were preserved for this stress test. Both candidates remain CANDIDATE with independent QA
holds. Duplicate rows are all visible with separate inspection keys; ambiguous-row adjudication is
disabled. Promotion rejects duplicate canonical IDs even without a pre-existing QA annotation.
Future worker runs also check uniqueness explicitly. Their medication candidates remain reviewable.

Other requested safety counters are zero: negated/uncertain AF creating an identity, negative monitoring
erasing confirmed AF, acute source rows disappearing from the view, duplicate chronic identities,
distinct-year events merging, promoted narrative junk, unresolved curated common trade names, database
writes and cloud calls. The newly exposed duplicate-event gate did not pass and is not reported as passing.

## Preservation and app

All **678 protected pre-existing reference, review, source and dictionary files** retain their hashes.
The original five active pointers, clinical artifacts and adjudications were not altered. Their frozen
reference versions and physician approval reported by the user are recorded separately in the cohort
registry; no individual review records were fabricated and no reference version was auto-promoted.

The app discovers all 20 patients. Its dashboard currently shows **5 approved reference snapshots,
13 unreviewed new patients and 2 validation errors**. Approval here concerns the frozen reference snapshot;
patient tabs retain the actual active-versus-candidate distinction. Filters include status and resolved
AF states, never raw keyword matches. Candidate-only patients open candidate review by default.

**256 focused tests passed.** Streamlit AppTest exercised all 15 new patients, both held cases, the
dashboard and every requested filter. The original-five regression tests remain active. No browser-pixel
screenshot was taken. Parsed PDFs are cached by source hash, PyMuPDF version and parser code hash.

No CAA classification, neurosonology structured extraction, EEG, scale reconstruction, CHA2DS2-VA,
ORBIT or OAC decision module ran. Preparation used existing section/history/medication selectors and
explicit encounter-date lookup only. Processing stopped after these 15 new patients.

## Artifacts

Stable candidates: `data/patient_artifacts/<patient_id>/{diagnoses,medications}/v<revision>/`.
Execution allowlist: `data/patient_artifacts/validation_cohort.json`, capped at 20 patients.

Run evidence: `data/debug/validation_cohort20_20261026T134900_917311Z/`.

- [Selection report](../data/debug/validation_cohort20_20261026T134900_917311Z/validation_cohort_selection.csv)
- [Full cohort summary](../data/debug/validation_cohort20_20261026T134900_917311Z/validation_cohort_summary.md)
- [Unresolved medications](../data/debug/validation_cohort20_20261026T134900_917311Z/validation_unresolved_medications.csv)
- [Diagnosis review queue](../data/debug/validation_cohort20_20261026T134900_917311Z/validation_diagnosis_review_queue.csv)
- [Validation results](../data/debug/validation_cohort20_20261026T134900_917311Z/validation_results.json)
- [UI/filter smoke checks](../data/debug/validation_cohort20_20261026T134900_917311Z/cohort_ui_smoke.json)

Launch `run_review_app.bat`, refresh the artifact index, and use **ERROR** to inspect held candidates.
