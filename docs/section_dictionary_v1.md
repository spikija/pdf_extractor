# Section dictionary v1

`config/section_dictionary.yaml` is the runtime authority for EEG v1.2 section
boundaries and TFNE v0.1 narrative routing. The physician DOCX is retained as
documentation and is never opened by normal extraction. Historical EEG parsers,
published candidates, saved reviews, and other extraction modules are unchanged.

The initial migration read 51 DOCX section entries, including one repeated
`Therapieempfehlung`: 50 unique source entries. Six individual multi-vessel
angiography prefixes preserve the full concatenated heading. Confirmed resting
ECG and psychiatry headings and existing regression aliases supplement the manual.
The source document checksum and original source entries are retained in YAML.

## Physician workflow

1. Edit `config/section_dictionary.yaml`. Add a variant to the appropriate
   canonical section, or copy a complete entry for a new section. `active: false`
   disables an entry. Every entry explicitly specifies its module actions.
2. Use `PREFIX` for headings with variable continuation; omit the documentation
   asterisk from the variant. Set `continuation_allowed` and `date_suffix_allowed`
   to `true`. Exact, regex and contextual rules are also supported.
3. Run `.venv\Scripts\python.exe -B -m caa_pdf_extractor.section_dictionary_tools check`.
4. Run `.venv\Scripts\python.exe -B -m pytest tests/test_section_dictionary.py -q`.
   The manual inventory tests are generated from YAML automatically. For a new
   physician example, append it to `manual_entries` as well as its runtime rule.
5. Run `.venv\Scripts\python.exe -B -m caa_pdf_extractor.section_dictionary_tools export`.
   This refreshes `docs/section_dictionary_review.csv` and
   `docs/section_dictionary_ambiguous_entries.csv`. CSV is a review export; edits
   to it must be transferred into YAML to affect extraction.

The parser rereads the dictionary on the next document. Python edits and app
restarts are unnecessary. A document uses one dictionary snapshot throughout its
matching. Do not rerun `scripts/initialize_section_dictionary_v1.py` over the live
config; it is a one-time migration helper and refuses nonempty output folders.

## Recognition and boundaries

Heading normalization affects recognition only. It handles Unicode, spacing,
hyphens, punctuation, trailing colons, date suffixes and wrapped heading cores,
including page joins. Exact source strings and character offsets remain intact.
Prefix recognition is anchored to a heading line and rejects explicit narrative
verbs, preserving incidental mentions such as “MRT wurde empfohlen”.

EEG consumes only its bounded report. Known hard non-EEG headings terminate before
the heading itself. `Beurteilung`, `Status`, `Detailbericht`, hospital summaries and
`Epikrise / Verlauf` have contextual boundary strength. EEG assessments and
structured ECG channel labels stay inside their report; evidence of a general
clinical assessment makes it a peer boundary. The ordered shallow section tree
retains the contextual classification rather than inventing a hierarchy.

TFNE includes only canonical `ANAMNESE` (family `HISTORY`) and `HOSPITAL_SUMMARY`.
All other recognized sections stop narrative ingestion. Probable unknown headings
also stop TFNE conservatively and remain available as `UNKNOWN_SECTION` in the
section tree for physician review. The existing embedded EEG/imaging/CAA input
firewall remains active. No temporal screening or clinical interpretation rules
were changed.

The reusable `build_section_tree` API preserves section ID, canonical section and
family, original heading, start/end page and offsets, confidence, matching rule,
module actions and source layer. New EEG geometry snapshots store this tree in
each document's `section_tree`. Existing source bundles can be routed on demand;
historical artifacts are not rewritten.

## Separate page boilerplate

`config/page_boilerplate.yaml` contains institutional and generic patient/page
identity patterns, never clinical section headings. Page cleaning requires a
margin block and known boilerplate on every nonempty line, supported by repetition
or strong signatures. Clinical text protects the entire block. Unknown repeated
headings and body-position institutional text remain. Identity exclusions record
`patient_identity_footer` and `page_number`; patient names are not hard-coded.

Raw text, raw geometry and provenance remain intact. Cleaning produces a distinct
`CLEANED_PAGE_TEXT`; trees and module bounded text are derived layers. This task
does not reclean historical bundles or regenerate clinical candidates.

## Validation scope

The focused test suite covers every manual heading with date suffixes, all hard
peer families, the previous physician EEG boundary fixture, mobile video type,
contextual assessments, multiline/page joins, unknown headings, exact evidence
offsets, all supplied footer examples, clinical text preservation, and existing
EEG/TFNE Review App behavior. Validation also routes the six existing EEG boundary
cases and ten frozen TFNE patients using saved source text only. It never calls a
clinical assembler or model for those patients.

The audit uses the existing offline worker guard to prohibit network, database
imports, subprocesses and writes outside its fresh debug directory. Candidate,
review and physician-document checksums are verified against the pre-change
snapshot. Scope is deterministic regression coverage, not a cohort-wide recall or
clinical validation claim.
