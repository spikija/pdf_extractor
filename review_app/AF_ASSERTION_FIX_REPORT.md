# AF assertion safety repair complete

Historical repair/audit report. The subsequent diagnosis-only rerun is complete:
[Five-patient V4.1 candidate report](FIVE_PATIENT_DIAGNOSIS_V4_1_REPORT.md).

```text
ROOT_CAUSE = V4 classify() NEGATIVE regex omitted “nicht detektiert”; intact source text fell through to ESTABLISHED_DISEASE and AF canonicalization.
GRANITE_INVOLVED = NO
DETERMINISTIC_NEGATION_FAILURE = YES
PATIENT_2435608415_CONFIRMED_AF_AFTER_FIX = NO
NEGATED_AF_CAN_CREATE_DISEASE_IDENTITY = NO
AF_HARD_GATES_PASS = YES
```

The AF-only source audit found zero established positive AF sources for patient 2435608415.
The reported evidence is NEGATED, with source date 2014-05-27, scope CURRENT_ASSESSMENT and
Holter/24-hour ECG modality. It does not imply that the patient never had AF.

No patient diagnosis extraction or score recalculation was run. Original files and physician reviews
are unchanged; unsupported historical AF is suppressed through a visible safety overlay, with stale
reviews and blocked promotion. Existing dependent pointers remain stale. Patient 2552817617 also
has an unsafe V4 candidate: sources support SUSPECTED_AF, not confirmed AF.

245 tests passed. All seven hard-gate violation counts are zero. All 300 protected files are unchanged.
The local UI smoke test opens the exact negative evidence on page 3. No cohort processing, cloud
patient calls or clinical database writes occurred.

Evidence directory: `data/debug/af_assertion_fix_20261026T070748_219717Z/`

- [Exact patient trace](../data/debug/af_assertion_fix_20261026T070748_219717Z/patient_2435608415_af_trace.md)
- [Patient AF occurrences](../data/debug/af_assertion_fix_20261026T070748_219717Z/patient_2435608415_af_evidence.csv)
- [Five-patient audit](../data/debug/af_assertion_fix_20261026T070748_219717Z/five_patient_af_assertion_audit.csv)
- [Downstream invalidation](../data/debug/af_assertion_fix_20261026T070748_219717Z/downstream_invalidation.json)
- [Implementation and validation](../docs/af_assertion_safety.md)

Refresh the app's artifact index to load the annotations. A corrected diagnosis rerun remains an
explicit future action; this repair has not generated or approved a replacement revision.
