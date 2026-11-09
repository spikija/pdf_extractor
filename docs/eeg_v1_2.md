# EEG v1.2 candidates

EEG 1.2 is a separate deterministic source-cleaning, exam-boundary and explicit-NCSE
revision. It reuses the 1.1 phenotype/localization implementation without changing
the older extractor files. Historical 1.0/1.1 candidates and physician reviews stay
immutable; new candidates require explicit review and cannot auto-promote.

`eeg_geometry_v12.py` stores native PyMuPDF blocks, coordinates and page dimensions.
Document-local normalized margin signatures ignore numeric/spacing differences and
patient/page header variations. Suppression requires margin geometry and identifiable
nonclinical boilerplate. Repeated clinical/table headings, mixed clinical/boilerplate
blocks and unsupported candidates are retained conservatively. Exclusion records
retain the full removed block, reason, page, bbox, signature and repetition pages.
Raw PDF text is preserved separately from cleaned page text.

`eeg_sources_v12.py` bounds dated EEG exams, handles two-digit years and local EEG
assessment headings, and stops at peer clinical sections and separate modalities.
Adjacent cEEG interval reports can join an explicitly continuous/video parent with
a supported interval gap of at most five minutes. Routine exams stay separate.
Copied reports corroborate one identity; conflicting same-date reports stay distinct.

`eeg_extraction_v12.py` gives explicitly established current EEG NCSE interpretations
priority over pattern heuristics. Negation, indication, uncertainty and historical
scope remain separate. Periodic patterns, raw frequencies, painful-stimulus reactivity,
probable seizure interpretation and cEEG template facts retain source evidence.
Timed artifact limitation/electrode removal becomes a temporal quality period, not
an uninterpretable label for the entire preceding EEG.

`CEEG_SEGMENTS` retains intervals, independent findings, interpretations, status and
evidence. Episode phenotype flags describe any-time occurrence; final-segment status
is separate. Accepting a later negative segment cannot erase an earlier positive
status episode. Mobile/video/continuous/ICU attributes retain unknown values when
source support is absent. No exam-level recording-end field is reintroduced.

The review panel shows prominent NCSE details, recording type, source-bounded text
and individual segments. Raw and cleaned page context are optional debugging views.
Standard review actions save version/hash-scoped overlays. The original EEG 1.1
holdout queue stays pinned to its archived 1.1 candidates as 1.2 becomes available.

The 1.2 validation runner derives its bounded patient set from the six physician
cases and the existing 1.1 regression evidence. Its independent denominator comes
from prior source inventories, frozen before 1.2 extraction. Offline workers use
the OS audit guard to block network/database access, child processes and writes
outside their fresh output directory. No other extraction module is invoked.

Run focused synthetic regressions with:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_eeg_v12.py tests/test_eeg_v12_ui.py tests/test_eeg_v11.py tests/test_eeg_validation.py tests/test_eeg_recording_end.py -q
```

These checks and the source audits establish regression behavior on the bounded
set. They do not establish production readiness or generalization to unseen data.

## Focused 1.2 boundary correction

Physician review of patient 2989288225 / document 27700090930173 found resting ECG
and psychiatric consultation text in the bounded EEG source. A pre-patch trace
confirmed that both headings survived cleaning in the correct order, but the stop
pattern omitted them. The initial safety audit also omitted these two categories.

`eeg_section_headings_v12.py` now classifies complete peer headings after Unicode,
spacing/hyphen and trailing-date normalization. It recognizes ECG variants,
psychiatric consultations, cardiac echo, clinical status, imaging, neurosonology
and laboratory headings. Ordinary prose and embedded ECG-channel sentences remain
eligible; an undated ECG field in a structured EEG template is also retained.

This remains version 1.2. Clinical extraction/schema/assembly and page cleanup are
unchanged. The focused review invokes source discovery only for the six specified
patients; it never loads the clinical extractor or rewrites historical candidates.
Corrected source-only snapshots and the pre-patch trace are stored in
`data/debug/eeg_v1_2_boundary_fix_20261101T124730_912442Z/`.

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_eeg_v12_boundaries.py -q
```
