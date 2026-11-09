# TFNE / neurological deficit pilot 0.1

This independent pilot extracts neurological symptom episodes from existing cleaned
Anamnese and Zusammenfassung des Aufenthalts sections. It covers transient,
persistent, progressive, mixed, fluctuating and uncertain presentations. It does
not diagnose TFNE, epilepsy, TIA or an underlying mechanism.

The clinical motivation combines the transient focal phenomenology reviewed by
[Smith et al., Neurology 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8356377/)
with the distinction between transient and persistent manifestations in
[Weidauer et al., European Journal of Neurology](https://onlinelibrary.wiley.com/doi/10.1111/ene.16425).
The implemented 24-hour operational boundary and STRONG/POSSIBLE screen are the
requested research rules, not validated diagnostic criteria from either paper.

The ten-patient list is frozen before model extraction using source indicators.
All available permitted narrative sections in those patients' existing bundles
are routed. Other headings terminate the input; embedded EEG, imaging and CAA
passages are excluded with exact offsets and retained separately for audit.
The model receives only selected source spans and their IDs. Selection enrichment
signals, imaging, EEG and other module outputs are not sent to Granite.

The dedicated prompt is `prompts/tfne_neurological_deficit_v0_1.txt`. Its name,
version and SHA-256, the installed local Granite tag/digest, timestamp, source IDs
and code hashes accompany each patient artifact. Task instructions are read from
this file; patient narratives are a separate JSON data message. Strict JSON grammar
requires a per-field source-evidence map. Granite cannot output research screens.

Source evidence is resolved to document, page, parent section, exact text and
character offsets. Unsupported or contradictory values become UNKNOWN/null with
explicit review flags; raw requests, responses and proposals remain available.
In particular, recurrence cannot establish stereotypy, missing follow-up cannot
establish resolution, vague durations cannot establish exact numeric bounds, and
a historical diagnosis cannot establish seizure-like phenomenology.

`tfne_screen.py` derives the clinical screen and three research axes. STRONG needs
a fully resolved focal event plus documented spread, recurrence with stereotypy,
or positive and negative symptoms in that episode. Aphasia or recurrence alone
cannot establish STRONG. Historical labels are stored independently and never
used as a screening input. Nonfocal transient symptoms receive a separate atypical
category; persistent/progressive/mixed episodes retain separate categories.

Artifacts are written only to a new `tfne_neurological_deficit_v0_1.json` within
each selected patient folder. Existing module manifests/artifacts are untouched.
The review app adds the TFNE V0.1 PILOT queue and TFNE / Neurological Deficits tab.
Its ACCEPT/EDIT/REJECT/UNCERTAIN/SPLIT/MERGE actions are separate hash-scoped
physician overlays. Split and merge preserve original source evidence and labels.
No review action rewrites the extraction artifact or writes to the clinical DB.

Use `tests/test_tfne_pilot.py` and `tests/test_tfne_ui.py` for focused validation.
Episode recall, splitting, phenomenology, assertion and screening usefulness still
require physician assessment. Document copies and recurrent series need particular
attention. No diagnostic validity or cohort prevalence is claimed.
