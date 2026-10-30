# Sensory presentation and CAA diagnostic-event review

This additive review pipeline does not backfill clinical data. Existing MMSE,
radiology, clinical-history, mRS and scalar case extraction code stays unchanged.
Migration `0011_sensory_caa_diagnostic` adds nullable sensory fields to
`hospital_encounter`, the independent `SENSORY_SYMPTOMS` primary category/flag,
and an initially empty `caa_diagnostic_event` table. Existing encounter fields and
mRS constraints remain intact. Downgrade refuses populated new evidence.

## Current sensory presentation

`extraction.admission_sensory` prepares bounded admission/history/examination
snippets and stops at embedded peer diagnostic reports. The installed local
Granite model interprets the presenting episode, primary reason, independent
coexisting flags, polarity, type, laterality, distribution, spreading, duration,
transience, stereotyped recurrence and resolution. Positive-to-negative evolution
is retained explicitly. Exact duration and duration ranges remain separate; an
endpoint is never promoted to an exact duration. Positive presentation requires
both presenting-event and sensory-phenomenology evidence IDs. Missing evidence
remains NULL/UNKNOWN. A normal examination
after an episode resolves does not negate its earlier presenting symptoms.

The prompt distinguishes pain alone, chronic polyneuropathy, historical TFNE,
old sensory stroke deficits, copied diagnoses and unrelated examination findings
from current sensory presentation. It extracts phenomenology, never a TFNE diagnosis.
Pure sensory episodes may have primary SENSORY_SYMPTOMS; aphasia and paresthesia
may have focal primary presentation with both flags. Mutual exclusivity is not
imposed. The older mRS assessments are retained, not recomputed by this prompt.

Admission reason and sensory components validate independently. Python checks
strict JSON/enums, finite nonnegative numeric shape, evidence IDs and patient/
document provenance. Positive sensory presence requires an explicit current-event
ownership field and IDs. Model inconsistencies are reported without changing
categories or phenomenology. All model outputs remain physician-review proposals.

## CAA diagnostic acts and dates

`extraction.caa_diagnostic` first inventories all requested CAA, Amyloidangiopathie,
Boston, stage, suspicion, diagnosis and pathology terms across every source document.
The raw-page census is checked against prepared sections. Every occurrence is
retained even when it belongs to an unrelated condition. CAA-related atomic
assertions retain exact source offsets, bounded context, date offers and disposition.

Deterministic extraction handles explicit unambiguous suspicion, clinical diagnosis,
formal criteria and directly attributed pathological proof. Mere imaging compatibility
is not a diagnostic act. Conditional/multiple stages, generic pathology and unclear
current/historical attribution enter a bounded local-Granite queue. For already
unambiguous explicit diagnostic acts, the deterministic type/status is retained
and Granite supplies temporal ownership only, so a clear suspicion is not lost
when its evidence happens to be an MRI. Its output uses
only supplied snippet IDs and date-evidence IDs, not reproduced quotations or free
dates. Python validates structure/provenance and retains the model's decisions.

Only diagnosis-owned date tokens are offered. A separate clause's diagnosis,
imaging/hemorrhage/TFNE/discharge/specimen dates and ambiguous multiple dates are
not fallback choices. An explicitly dated diagnostic examination may supply a date
only with explicit current diagnostic linkage. No generic document/admission date
is substituted. A source-dated encounter can be offered conditionally for an
explicitly described diagnostic act; it is selected only when the model attests
both current diagnostic ownership and a link to this exact encounter. Merely
known/copied diagnoses do not receive that option. DAY stores a source-backed
date with its explicit basis; MONTH/YEAR store only year/month fields;
UNKNOWN stores no date components. No first-of-month/year padding is permitted.

Suspicion, possible/probable criteria, clinical diagnosis without a stated class,
supporting pathology and definite confirmation remain distinct. Boston versions
come from source wording, never the calendar year. Schema constraints prevent
suspected events carrying confirmed classes and invalid pathology confirmation.

Canonical projections merge matching explicitly owned DAY identities. Coarse dates
require matching diagnostic statements; current undated acts group only within the
same diagnostic document and matching type/status/system. Undated historical acts
without a unique identity remain held source assertions. No group authorizes writes.

Derived first-date fields report the earliest supported DAY only, with separate
flags showing whether coarse or undated events prevent proof of chronological first.
The report preserves suspicion, first diagnosis, possible, probable and pathological
confirmation separately. Existing case dates receive a review classification and
are never updated. MORE_PRECISE_SOURCE_AVAILABLE requires independently known
coarser existing provenance; no placeholder precision is assumed from its date value.

## Validation and locality

Run `data/debug/sensory_caa_validation.py`; its protected local directory stores all
inputs, schemas, source census, request/response hashes, raw outputs, component
statuses and patient timelines. Resume requires identical code, sources, model,
settings and prompts. No cloud fallback, prompt retry or Python clinical correction
is performed. Synthetic local-model regressions check chronic/old sensory exclusions,
pain alone, pure transient sensory presentation and coexistence with aphasia.

The final freeze verifies the same 55 encounters, all 77 documents, all database
table fingerprints, old artifacts and old-domain code, then reruns the full suite.
Readiness is for validation of review proposals and is not backfill authorization.
