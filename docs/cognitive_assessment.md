# Longitudinal cognitive assessments

Migration `0009_cognitive_assessment` adds an empty `cognitive_assessment` table.
Migration `0010_cognitive_evidence` adds `cognitive_assessment_evidence` and
establishes the distinction between a canonical test event and its textual
mentions. Both tables remain empty pending a separately authorized backfill.
It does not replace or populate the legacy scalar `case.mmse`, and does not alter
encounter/mRS, history, imaging or other extraction logic.

The event table preserves test type, integer score/max, nullable date,
year/month precision and basis, source context, document/page/section and exact
offsets for representative evidence. The evidence table preserves every source
mention, including those whose canonical identity is unresolved. Date constraints prohibit a populated date for MONTH/YEAR/UNKNOWN;
DAY requires a genuine full date and matching year/month. MMSE and MoCA require
0–30 scores and a maximum of 30. OTHER is reserved for future explicitly defined
tests; clock/composite scores are not automatically converted into MMSE.

The deterministic parser recognizes MMSE/MMST and Mini-Mental labels, explicit
scores/fractions, directly attached full/partial dates and explicitly current
secondary values. Age, percentages, date tokens, non-scalar ranges and unsupported
denominators are rejected. Every alias occurrence is retained in the census,
including aliases without a usable score. Multiple values retain separate score
offsets and assessment mentions; a current secondary score cannot inherit the
first historical year.

The only inherited assessment date is the date of an explicitly dated
neuropsychological examination containing the test. Peer report headings stop
that ownership. Historical/external references do not inherit a current section
date. Generic document/admission/discharge/diagnosis/MRI dates are never used as
assessment dates or as a substitute for temporal context. Unstated context stays
UNKNOWN; the word "hier" alone does not establish a current test. Partial dates
are never padded.

Source extraction remains unchanged. Its original `canonical_candidates` output
is a provisional census grouping, not the canonical event projection. The separate
`cognitive_canonical.canonicalize` function consumes all accepted source rows and
an explicit, complete identity review bound to each entire row's SHA-256 and exact
source spans. It has no database writer and makes no inference from equal scores.

A reviewed seed needs directly owned date/partial date, a clear current performed
test context, or sufficiently identified prior/external testing. A bare score is
evidence only. Same-document summaries/diagnoses link only after reviewing the
apparent test identity. Cross-document links require a named prior report or
explicitly attributed originating test; nearest equal score alone is insufficient.
Repeated equal scores in separate current reports stay separate events.

Evidence roles are CURRENT_PERFORMED, HISTORICAL_REFERENCE, EXTERNAL_REPORT,
COPIED_FORWARD and UNKNOWN_MENTION. These are separate from immutable extracted
source contexts. Unresolved mentions have UNKNOWN_MENTION and a NULL assessment
link, while their original contexts/dates remain in the audit artifacts. Copied
evidence cannot seed an event. Canonical date fields are selected exclusively from
unchanged accepted dates in linked evidence, with the date-owner mention identified;
report-header dates used for identity review are not newly copied into date fields.

The evidence table stores case/document/page/section, exact source offsets/text,
score-token offsets, role, test type, score/max, original owned date text and soft
deletion. Unique source-token identity preserves two scores in one sentence while
making replay idempotent. A composite foreign key prevents links to another case,
test type or result. Source and role constraints permit unresolved evidence and
prevent unsupported role/link shapes. The existing event source columns identify
representative evidence, not additional tests. Migration 0010 refuses a populated
predecessor, preventing silent reinterpretation of earlier mention rows; downgrade
refuses any populated event/evidence tables. No rows are converted automatically.

Run `data/debug/cognitive_canonical_validation.py` for the frozen same-20 identity
review. Its protected JSON/CSV include every canonical event with all supporting
mentions, every unresolved source passage/reason, and the complete source-bound
adjudication. Readiness is a recommendation for a separately authorized reviewed
backfill; it is not physician approval or permission to link unresolved mentions.

Granite is not called for score extraction or when temporal information is simply
absent. Ambiguous additional values are retained with exact source offsets and
`fallback_eligible`; any future temporal fallback must use only the containing
bounded passage, stable snippet/score-token IDs and already present date tokens.
It may assign CURRENT/HISTORICAL/EXTERNAL_REPORT/UNKNOWN or abstain, never invent
scores/dates or use another document's metadata. Only installed local Ollama
Granite is permitted, with no cloud or alternate model. Unresolved ambiguity
stays blocked for review. The same-20 validation reports whether that fallback
queue is empty; it does not silently manufacture temporal labels.

Run `data/debug/cognitive_validation.py` for the read-only same-20 census. Protected
JSON/CSV retain every raw occurrence, accepted/rejected disposition, dates and
source references. Separate files show assessments and canonical candidates.
Ordinary progress logs contain counts and coded diagnostics only. The frozen
run checks all database tables/schema, existing artifacts, source bundles and
other-domain code, and runs the full tests before/after. No clinical writer or
backfill is part of this implementation. Clinical and longitudinal identity
review remain necessary before a separately authorized backfill.
