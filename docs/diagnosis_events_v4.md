# Diagnosis and event V4 regression review

Run only the two specified regression patients from saved pilot source text:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.diagnosis_v4_regression
```

The defaults are the existing `five_patient_integration_20261025T203648+0100`
source snapshot and validated V3 snapshot
`five_patient_diagnosis_medication_v3_20261025T214201_791289+0100`.
Override them using `--pilot-output` and `--previous`; `--output` must name a
new directory. There is no full-cohort or arbitrary-patient mode.

The runner creates a fresh `data/debug/diagnosis_v4_regression_<timestamp>/`.
It does not invoke medication resolution, models, PDF parsing, ingestion,
CAA assessment, imaging classification, EEG, neurosonology or scores.
A process audit guard rejects network/database access, deletion/replacement,
and file writes outside the new output root. Existing snapshot bytes and
medication code are hash-checked before and after assembly.

Outputs include HTML and Markdown physician reviews plus, for each patient:

- `raw_source_spans_v4.json`: all legacy mentions/audit-only items and newly
  discovered source spans, including original source records.
- `diagnosis_mentions_v4.json`: qualified, positive disease/event concept spans.
- `canonical_disease_identities_v4.json`: one identity per disease.
- `canonical_clinical_events_v4.json`: separate event instances.
- `diagnostic_assertion_timeline_v4.json`: established, suspected, negated and
  finding-only assertions, with source evidence and date bases.
- `patient_clinical_diagnoses_v4.csv`: chronic diseases and clinical events in
  one physician-facing table.
- `diagnosis_rejected_spans_v4.csv`: detailed evidence-only dispositions.
- `unresolved_candidates_v4.json`: unsupported ontology mappings and ambiguous
  event references. These require review rather than silent promotion.
- JSON and CSV versions of all tabular layers, plus candidate-count QA.

Structured diagnosis/history sections have priority. Explicit concepts in
narrative text receive local assertion qualification; headings, therapy-only
lines, MRI technique, normal findings and negatives are not promoted. Suspected
conditions stay in the assertion layer. Unknown structured disease descriptions
remain available to review; the current approved mapping set is intentionally
limited and does not infer an unfamiliar disease from a treatment.

Event grouping requires compatible type, date precision and anatomy. Distinct
explicit dates cannot collapse. An undated reference joins an existing event
only with sufficient anatomical/temporal support and unambiguous ownership;
otherwise it stays unresolved. Partial year/month dates remain partial. A recent
ischemic lesion may use its investigation date as an approximate event date;
comparison-scan dates are excluded. Treatment-associated hemorrhage remains
separate from ischemic stroke, and an unqualified hemorrhage is not automatically
called spontaneous primary ICH. Thrombolysis is bound to the same numbered
stroke diagnosis block. A DVT associated with a dated pulmonary embolism may
share that explicitly linked period; its date basis discloses the association.

The chronology reports the earliest established *documentation* date separately
from suspicion. Explicit historical suspicion periods (e.g. a suspected diagnosis
with `Erstdiagnose 2004`) retain their original precision. Historical diagnosis
claims remain in the source timeline. A diagnosis date is not an inferred onset.
Potentially conflicting same-document assertions remain visible for review.

QA distinguishes raw audited spans, promoted source spans and promoted concept
mentions: one source span may support multiple concepts. Raw spans include the
previously overgenerated headings/findings so that nothing disappears from audit.
The report displays only a small rejected-example selection, with full detail in
CSV. No target candidate count is used for filtering.

Each new manifest publishes diagnoses/events at V4, points to the unchanged V3
medication artifacts and preserves all unrelated paths/checksums. A preserved
validated medication keeps `status: VALIDATED` and gains
`preservation_status: PRESERVED`, so latest-validated selection still works.
Legacy modules retain their existing review/validation status. Dependent modules
remain `STALE_DEPENDENCY`; no dependent artifact is deleted or recalculated.
Failed hard gates publish `FAILED_VALIDATION`, never `VALIDATED`.

Validation:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_diagnosis_events_v4.py tests/test_diagnosis_medication_v3.py test_history_v2.py tests/test_integration_pilot.py tests/test_diagnosis_caa_v21.py tests/test_v23_review.py -q
```
