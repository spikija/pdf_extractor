# Granite-led encounter review

`extraction/encounter_granite_v2.py` is the current review path for the frozen
55-encounter comparison. The earlier deterministic and Granite comparison modules
remain available to reproduce their original results. Their clinical semantics
and artifacts are unchanged.

Python prepares ordered source chunks from approved narrative/admission-exam
sections. Up to three adjacent sentences stay together; original section, page,
date and offsets remain in the local registry. IDs include document identity and
a content/provenance hash. Different-day, transfer, historical and discharge
clauses inside approved narrative sections remain available to the model. Input
limits leave explicit omission records. No complete PDF is sent to the model.

Local Ollama Granite classifies primary/coexisting presentation, separates index
presentation from encounter purpose, interprets temporal context, describes
function and proposes exact/range/NULL mRS with confidence. The prompt contains
the clinical guidance. Previous deterministic/model predictions are not included
in the prompt.

Python validates the JSON schema, enum values, numeric types/range shape and
evidence IDs against the patient/document/approved-section registry. It does not
match quotes, classify clinical meaning, interpret temporal ownership, override
Granite using a previous result, or calculate mRS. Numeric 0–6 is a structural
constraint; clinically inappropriate use of a valid number remains visible for
physician review. Technical validity does not prove clinical correctness.

Run from the project root:

```powershell
.venv\Scripts\python.exe data/debug/encounter_granite_v2_validation.py
```

The runner pins local `127.0.0.1:11434`, installed `granite4.2:latest`, digest,
temperature 0, seed 0 and a 16384-token context. It disables proxies and redirects
through the existing transport, blocks non-loopback socket connections and stops
on model unavailability. It does not retry or fall back to another model.

Protected local artifacts contain snippets, prompts, raw responses and technical
validation results. Ordinary progress logs contain IDs, categories and counts.
The runner hashes all public tables/schema, source bundles, both original result
sets and production/test code before and after the frozen run. All application
SQL is read-only. It writes no clinical or database audit rows.

The JSON and human-readable Markdown show every encounter. Focused CSVs cover
all original 14 UNKNOWN reasons and 45 LOW/NULL admission assessments. A separate
CSV lists every changed proposal and newly introduced index/purpose dimension.
Earlier reports lacked these transfer dimensions; a historical purpose-change
count is therefore unavailable rather than retrospectively inferred by Python.

All resulting model decisions are review candidates. This path has no clinical
commit entry point or backfill authorization.

## Component validation and exact-score normalization

The technical layer now normalizes `(score=N,min=N,max=N)` to an exact score
with NULL bounds. It rejects conflicting or half-populated combinations and
strictly checks integer types and 0–6 bounds. It does not derive scores from prose.
The prompt, response JSON schema and snippet preparation remain unchanged.

Admission reason and functional/mRS have independent statuses. The two score
periods and each functional-status item also have separate error scopes. A bad
functional-status evidence reference drops only that item; a period-specific
reference blocks that period; an invalid global functional reference blocks the
functional component. Source IDs are never guessed or repaired.

The normalized model proposal remains separate from `final_mrs`. The latter
preserves already eligible, temporally owned explicit frozen mRS values and
records conflicting model estimates for review. Held or unowned explicit fields
are not promoted. Missing model data is separate from a technical failure.

Use `data/debug/encounter_granite_v2_technical_validation.py` for the controlled
same-response comparison. It revalidates all 55 retained responses, checks current
source/snippet/prompt fingerprints, runs the full tests before/after, and makes
no inference calls. The report includes every equal-range normalization,
inconsistent triple, isolated evidence error and explicit-value conflict.
