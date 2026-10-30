# Ordered sensory admission review

`extraction.sensory_presentation` is the refined sensory-only review path. It
uses the installed local `granite4.2:latest` model. It leaves the preceding
sensory/CAA reports and extraction code intact for comparison. It has no writer,
does not call CAA extraction and does not recompute mRS, MMSE, radiology or history.

Seven sequential schema-constrained model requests enforce the workflow:

1. `Episode` identifies the index presentation, explicit sensory wording and
   current ownership. It records `current_episode_evidence_ids`, current sensory
   evidence and explicitly labelled `HISTORICAL_OR_CHRONIC` exclusions. One
   snippet can contain current and historical clauses; an exclusion never masks
   the entire snippet in Python.
2. Separate `Polarity`, `Localization`, `Spread`, `Transience` and `Recurrence`
   requests receive the unchanged episode result and original snippets. Granite
   evaluates these five dimensions in that order, with separate evidence IDs.
   A later normal examination does not negate a presenting symptom. A
   conflicting duration remains NULL while transience can remain true.
3. `Phenotype` receives the preceding model results and original snippets.
   Granite decides primary reason and independent coexisting flags. Pure
   sensory presentation can be primary `SENSORY_SYMPTOMS`; sensory symptoms with
   aphasia or paresis can coexist with a focal primary reason. This request
   does not contain index/purpose or functional-score output fields.

Python prepares source metadata, enforces strict schemas and enum/numeric shape,
checks all supplied evidence IDs against the patient/document registry and
assembles the unchanged model fields. It does not map symptoms to polarity,
body parts or categories, infer transience, derive scores, correct IDs, or change
model classifications to satisfy an expectation. The evaluated JSON retains
raw responses and separate validated stages. An episode failure blocks dependent
requests; a feature failure remains local and other feature outputs are retained.
Clinical consistency checks only flag
review holds; they do not replace values.

Evidence arrays are bounded to eight IDs per dimension and twelve role labels.
The model is asked to select distinct, direct evidence. Request schemas restrict
ID values to the exact source registry. These bounds prevent
unbounded generation loops; Python does not deduplicate or repair model output.
The full original source registry remains available for review.

Run the protected same-cohort evaluation using:

```powershell
.venv\Scripts\python.exe data/debug/sensory_refinement_validation.py
```

The runner freezes the exact preceding 55 encounter snippet registries, checks
their live source offsets and all 20 patient bundle hashes, uses a read-only SQL
guard and blocks non-loopback networking. All public-table/schema fingerprints,
pre-existing code and artifacts must remain unchanged. Local model requests,
settings, digest, results, hashes, regressions, changed-case source passages and
the before/after full test counts are retained in a protected report directory.
Resume requires the same request hashes, code, model and settings. A retained
failed inference is not automatically retried; unavailable local Granite stops
the run and there is no cloud or other-model fallback.

Frozen regression expectations are evaluation criteria, never model inputs or
postprocessing rules. Readiness requires the five requested document checks,
the synthetic clinical regressions, all technical stages and consistency gates.
Any model clinical concern found during source review must be recorded as a
hold even when these bounded regression checks pass. A readiness result does
not perform or authorize a clinical backfill.
