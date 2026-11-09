# SECTION_DICTIONARY_V1_READY

The physician DOCX remains documentation. The two YAML files are runtime sources of truth; normal extraction never reads the DOCX.

| Requested measure | Result |
|---|---|
| manual_entries_ingested | 51 |
| manual_unique_entries | 50 |
| unique_variants | 132 |
| canonical_sections | 66 |
| canonical_section_families | 48 |
| prefix_rules_created | 43 |
| prefix_variants | 85 |
| contextual_rules_created | 5 |
| page_boilerplate_patterns_created | 14 |
| known_heading_tests_passed | 150 |
| known_heading_tests_failed | 0 |
| ambiguous_entries | 9 |
| unmapped_entries | 0 |
| EEG_BOUNDARY_RULES_UPDATED | YES |
| TFNE_SECTION_ROUTING_UPDATED | YES |
| RAW_TEXT_MUTATED | NO |
| DATABASE_WRITES | NONE |
| CLOUD_PATIENT_CALLS | NONE |

The 51 source entries include one duplicate; 50 unique manual entries are represented. The 132 unique runtime variants include confirmed earlier EEG boundary regressions. Prefix counts are rule entries (43) and their variants (85).

Ambiguity is boundary context, not an unmapped family: nine exported variants across five contextual rules. No supplied entry lacks a canonical mapping. Status, assessment, detail report, hospital summary and epikrisis/course are resolved by the consuming module. The CSV records why context is required.

Validation: 458 focused deterministic and Review App tests passed, zero failures. The manual inventory contributes 150 heading/date cases, all passing.

Source-only audit: six saved EEG patients, 42 recording/segment occurrences; ten frozen TFNE patients. Zero model calls. Recording identities were preserved. Exactly one EEG source shortened from 2061 to 579 characters, stopping before the known neurological examination heading. Existing clinical candidates were not regenerated.

All 4000 protected candidate/review/documentation files match their pre-change checksums. No new clinical artifact or review files were created. Raw inputs remain immutable; cleaning, section trees and module bounded text are derived layers.

Runtime changes: shared exact/prefix/regex/contextual matching, heading-only normalization, wrapped/page-joined headings, unknown probable headings retained for review, reusable ordered section trees, separate geometry-gated boilerplate, EEG peer boundaries and history/summary-only TFNE routing. Mobile video recording type remains distinct. Existing clinical interpretation and TFNE screening rules are unchanged.

The new tests also verify that editing YAML changes EEG and TFNE routing without Python edits. See `docs/section_dictionary_v1.md` for editing, checking and exporting commands. PyYAML >=6,<7 is an explicit dependency (tested 6.0.3).

## Hard gates

| Gate | Violations |
|---|---|
| KNOWN_SECTION_HEADING_MISSED | 0 |
| DATE_SUFFIX_BREAKS_HEADING_MATCH | 0 |
| PREFIX_SECTION_WITH_CONTINUATION_MISSED | 0 |
| PATIENT_PAGE_HEADER_RETAINED_AS_CLINICAL_TEXT | 0 |
| KNOWN_INSTITUTIONAL_FOOTER_RETAINED_AS_CLINICAL_TEXT | 0 |
| CLINICAL_BODY_TEXT_FALSELY_REMOVED_AS_FOOTER | 0 |
| EEG_CROSSES_HARD_NON_EEG_BOUNDARY | 0 |
| TFNE_MODULE_INGESTS_NON_ANAMNESE_NON_SUMMARY_SECTION | 0 |
| RAW_TEXT_MUTATED | 0 |
| DATABASE_WRITES | 0 |
| CLOUD_PATIENT_CALLS | 0 |
| EEG_RECORDING_IDENTITIES_CHANGED | 0 |
| EEG_SOURCE_GREW_BEYOND_PREVIOUS_BOUNDARY | 0 |

The gates describe deterministic fixtures and this bounded source audit; they do not establish cohort-wide heading recall or clinical validity.

## Outputs

- Audit directory: `data/debug/section_dictionary_v1_20261101T161120_075786Z`; final source audit is `validation_final/`.
- `config/section_dictionary.yaml` and `config/page_boilerplate.yaml`
- `docs/section_dictionary_review.csv` and `docs/section_dictionary_ambiguous_entries.csv`
- `docs/section_dictionary_v1.md`
- This run: `validation_final/validation_metrics.json`, `hard_gates.json`, `section_trees.json`, `eeg_boundary_changes.json`, `tfne_source_routing_counts.json`, `preservation_audit.json`
- `test_results.xml`, `final_summary.json`, `final_code_hashes.json`

Earlier source-only probes are retained in the run root and `final_validation/`. They exposed blank-line identity shifts, bullet EEG references, and an existing internal-medicine consult boundary. These were corrected before the final run in `validation_final/`. One intermediate probe was stopped; no probe published or modified clinical artifacts.

No clinical database operation, cloud patient call, full clinical cohort rerun, or other module extraction was performed.
