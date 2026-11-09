# EEG 1.1 physician regression repair

EEG 1.1 adds a separate extraction/schema/source-boundary path. The EEG 1.0 source modules,
published artifact bytes, metadata and physician reviews are preserved. Only the current
20-patient EEG validation registry is eligible; this phase neither screens nor extracts the
remaining patient population. There are no clinical database or cloud patient-data calls.

## Source-scoped representation

`focal_abnormalities[]` retains side, atomic regions, primary region where supported, anterior/
mid/posterior extent, source extent text, continuity, occurrence frequency, severity,
epileptiform component, asymmetry, relationship, spread targets and evidence IDs.
Relationships distinguish PRIMARY, SECONDARY_INDEPENDENT, SPREAD and UNKNOWN.
Source repetitions with matching geometry corroborate a focus rather than creating a second
identical one. Conflicting regional source descriptions remain separate source statements.

Three-region descriptions such as fronto-zentro-temporal retain FRONTAL, CENTRAL and TEMPORAL.
The older compact enum uses OTHER where its enum cannot express the complete list; it does
not silently report TEMPORAL alone. Primary region is not invented when the source gives no
regional maximum. MID_TO_ANTERIOR preserves both temporal qualifiers. Regional qualifiers
remain visible in the original extent description.

An explicit spread target cannot turn a unilateral primary focus into a bilateral primary
abnormality. Independently described left and right foci may support a bilateral overall
summary while the dominant/source-primary focus remains unilateral. Bilateral descriptions
retain LEFT_PREDOMINANT or RIGHT_PREDOMINANT separately. Explicit elliptical references such
as “unabhängig davon/hiervon” inherit the named preceding activity, with their own localization.
No seizure/status inference follows from that linkage.

`slow_patterns[]` preserves separate focal, diffuse and rhythmic pattern statements. FIRDA
remains the source type; the optional GRDA family is separately derived. An asymmetric
generalized frontal rhythmic pattern does not independently establish focal slowing. Genuine
focal slowing can coexist with FIRDA. The multi-label phenotype retains both. The primary
finding remains a convenience marker, not an exclusive scientific phenotype.

## Assertion and modifier scope

| Source distinction | EEG 1.1 behavior |
| --- | --- |
| No statement about epileptiform activity | Presence/confirmed remain null; evidence status UNKNOWN |
| Explicitly no definite epileptiform activity | Presence remains null, confirmed=false, NO_DEFINITE_EVIDENCE |
| Explicit absence of epileptiform potentials | Presence=false, confirmed=false, EXPLICITLY_ABSENT |
| Suspected/uncertain epileptiform activity | No confirmation; UNCERTAIN is retained |
| No definite ictal activity | NO_DEFINITE_EVIDENCE; no recorded seizure is inferred |
| No clinical signs of NCSE | Clinical signs ABSENT; electrographic status remains unknown |
| Explicitly no status pattern | May support electrographic status=false |

Broad sharp-waves retain SHARP_WAVE morphology, BROAD modifier and an explicitly occasional
frequency where stated. Conventional regional epileptiform activity is FOCAL, rather than
being labelled LATERALIZED solely because it has a side. Secondary bilateral synchrony stays
linked to the focal abnormality; it does not establish generalized epileptiform activity.
Spikes, sharp waves or cortical excitability alone cannot establish seizure/status or TFNE.

Focal slowing continuity, background-slowing continuity and global background continuity are
different fields. A continuous focal theta/delta pattern cannot populate global background
continuity. Frequency adverbs attached to spread stay with spread. Clear generalized theta/
delta with a bifrontal maximum is generalized slowing, not focal slowing or an OTHER fallback.
Source abnormality grading is parsed from explicit wording, never derived from phenotype.

`reactivity_findings[]` retains VERBAL, TACTILE or EXTERNAL stimuli and their separate responses.
Stimulus-specific responses do not automatically populate global background reactivity.
Mixed responses remain distinct, with an UNKNOWN aggregate response rather than a forced one.

Assessability uses FULL, LIMITED, NOT_ASSESSABLE or UNKNOWN. FULL requires explicit source
support. “Soweit beurteilbar” and interpretable artifact-limited EEGs are LIMITED. Explicitly
uninterpretable reports can be NOT_ASSESSABLE. Artifact presence, types and severity remain
separate. Quality limitations of the embedded ECG channel do not become EEG-wide limitations.

## Source boundaries and provenance

`eeg_sources_v11.py` terminates EEG at separate 24/48/72-hour, Holter, resting and explicitly
dated ECG report headings. Inline historical 24-hour ECG references are excluded too. The
small ECG channel explicitly embedded in an EEG report remains eligible. Inline EEG findings
after a dated header are retained rather than silently discarded as header text.

Source occurrence IDs and all 76 baseline study/report identities are retained. Character
positions for new clinical clauses map to original native page text across whitespace and
page furniture. Findings retain patient/exam/document/page/section/evidence links. Original
EEG interpretation, bounded report copies and source text remain available. Two conflicting
historical reports sharing a date remain separate review units; undated references do not
prove an additional recording. Existing source-date discrepancies remain unresolved.

## Recording-end metadata cleanup

New EEG 1.0/1.1 artifacts omit `eeg_date_end`; exam metadata retains `eeg_date`
and `eeg_date_precision`. The review header shows the date, its precision and
recording type when known. Legacy JSON and saved reviews can still be read;
the obsolete field is ignored in the review projection. Clinical interpretation
rules are unchanged. The checked-in EEG persistence model and migration have no
recording-end column, so no database migration is needed.

Historical artifacts and the October 29 holdout freeze remain unchanged. This
subsequent schema cleanup changes assembly/review source hashes; the old freeze
must continue to reject extraction with changed code. Do not regenerate its
hashes or rerun the holdout merely for this cleanup.

## Review and publication

The EEG tab displays dominant/independent foci, spread, atomic regions, extent, asymmetry,
rhythmic types, artifact/assessability, reactivity and evidence status as text and tables.
Detailed JSON remains expandable. Source links work for focus evidence and individual
finding targets. The revision selector keeps EEG 1.0 accessible with its own review history.

Reviews remain candidate-version/hash-scoped overlays. Old edits are not applied to 1.1.
Editing/rejecting one structured focus preserves other foci, and recalculates compact
summaries. Derived focus geometry is edited through the detailed array, not through a
contradictory compact-field override. Structured identities and source evidence IDs remain
immutable. Reject/uncertain yields unknown or removes the adjudicated object, not an inferred
negative. Raw artifacts are never changed. Promotion stays disabled during EEG validation.

`workbench_eeg_v11_worker.py` processes one registered patient inside the existing OS audit
guard: no sockets, clinical DB access, child processes or writes outside the fresh worker
directory. No Granite calls were made for this deterministic repair. The parent publisher
requires the unchanged registry, intact source PDF/input hashes, all source regressions and
all hard gates before publishing immutable 1.1 candidates. No other module pointer or
clinical extraction algorithm is changed.

## Validation

The source regression set includes all nine physician-specified A–I cases, prior independently
validated EEG cases, and the saved physician-reviewed exam: 18 unique source cases, 141 field/
relationship checks. Synthetic tests additionally cover uncertainty, negation, unchecked forms,
mixed stimuli, elliptical references, modifier scope, per-focus overlays and page provenance.
The existing diagnosis/AF/workbench/review regressions remain in the validation run.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_eeg_v11.py tests/test_eeg_v11_ui.py -q
```

[Current results and fix counts](../review_app/EEG_V1_1_REPORT.md).
The validation evidence directory contains the source inventory, explicit regression checks,
per-exam changes, hard gates, worker execution evidence and protected-input validation.
Fix counts concern affected exams and include newly explicit structural representations;
they overlap and are not counts of unique patients or false diagnoses.
