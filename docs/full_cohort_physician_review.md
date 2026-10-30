# Full cohort physician review

Run `python -m caa_pdf_extractor.full_cohort_review --output data/debug/full_cohort_<timestamp>`.
The source cohort is discovered from the current immediate patient directories
under `pdf_db`, with recursive case-insensitive PDF discovery inside each folder.
No former patient list controls selection. The frozen source manifest records
relative filenames, SHA-256 hashes and page counts; cohort membership and bytes
are reconciled again before readiness.

The pipeline writes local artifacts only. It does not call clinical commit
endpoints, run migrations, ingest source rows into PostgreSQL, or connect to the
clinical database. Model requests use the installed digest-pinned local Granite
through numeric loopback Ollama, with proxies and redirects disabled. Frozen
baseline proposals are reused only for patients whose complete source path/hash
sets match; new source documents are parsed with the current parser and prepared
with the current section/procedure discovery modules.

The full-cohort adapters invoke the current demographic/gender, encounter/mRS,
sensory episode and phenotype, history/diagnosis eligibility and grouping,
hierarchical taxonomy/V2.2 cardiovascular, radiology/CT, EEG, cognitive source and
canonical identity, first laboratory value, discharge medication and sourced CAA
evidence modules. Sensory episode ownership precedes dependent dimensions;
dimension responses are packaged in one request but validated independently by
each current dimension schema. Phenotype follows the validated dimensions.
Clinical uncertainty, disagreements and bounded-input omissions remain explicit
review holds. They are not silently replaced by deterministic clinical guesses.

All raw extracted cognitive score/date mentions remain immutable. The local
identity review is checked by the current cognitive canonicalizer against exact
source spans, owned dates and permitted seed/link bases. Equal scores alone
never establish test identity. Unresolved mentions appear as evidence-only,
not additional performed tests.

Laboratory geometry selects first-value columns only, preserving original units
and reference-column tokens. Research dates use admission dates; medication dates
use discharge dates and only explicit discharge/home-treatment sections are
included. CSF matching scans every raw page and structured table. An assay whose
result says `folgt` is retained as a pending-result exclusion, not a missed
numeric measurement. Copied outpatient CSF results without a supported inpatient
admission retain null dates and explicit holds; outpatient/document dates are
not relabeled as admissions. Known available admission dates must still match.
Unspecified amyloid assays remain unspecified; no isoform or ratio is inferred.

The diagnosis engine accepts an explicitly frozen dynamic cohort in addition to
its original same-20 mode. Source assertion, formal source classification,
computed Boston, applicable Edinburgh fallback and pathology stay separate.
Patient-level statistics count each patient once. Suspicion/differential and
compatible imaging alone do not satisfy inclusive CAA positivity. Chronic
epilepsy requires a canonical chronic disease; an isolated seizure, status event,
or abnormal EEG does not establish epilepsy. Overlapping CAA evidence-stream
counts are explicitly labeled as overlapping.

An undated CT finding without a source-owned ICH event identity stays an
evidence-only routing row (`id=null`) with an explicit review hold. It does not
create an ICH event or an Edinburgh feature/score. Adequate MRI still routes
that evidence to Boston; otherwise the CT row is not assessable. The unresolved
event-identity hard gate checks actual assigned event IDs, not evidence-only
rows. No missing onset date is replaced with the CT date. Named infarct and
hemorrhage occurrences remain events even when historical; chronic multiinfarct
burden and basilar thrombosis retain a broader cerebrovascular category rather
than acquiring an unsupported acute stroke/TIA identity.

The combined PDF is portrait A4, with fixed readable fonts, 16 mm side margins,
wrapped narrow tables, explicit row pagination and repeated table headers.
Every patient starts a new page. The cohort summary, visible patient navigation,
bookmarks, running patient header and page X/Y footer support source comparison.
Document IDs resolve to original filenames and hashes through
`source_document_lookup.csv`. The PDF excludes repeated raw diagnosis dumps and
long source passages. Detailed source evidence stays in protected artifacts.

Readiness requires matching folder/manifest/summary/PDF patient sets, unchanged
source files, supported-document and amyloid coverage, preserved diagnosis
mentions, valid source/computed separation, correct MRI/Edinburgh routing,
patient-level statistic reconciliation, and zero clinical writes/cloud calls.
Every PDF page is rendered to PNG and checked for portrait size, margins, blank
body pages, overlapping text, minimum body font size, Unicode round trips and
patient navigation. Clinical review holds remain visible at readiness; readiness
does not mean physician approval of proposals.

Artifacts are resumable. Retained model requests and proposals are keyed by
prompt/schema hashes; rejected attempts remain available. A completed extraction
can resume with `--finalize-only` using the same output directory. Source changes
require a new frozen run instead of silently extending the existing denominator.
