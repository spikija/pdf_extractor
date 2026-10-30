# Versioned pipeline consolidation

Current status: **PIPELINE_CONSOLIDATION_INCOMPLETE**. The master entry point is
`python -m caa_pdf_extractor.full_pipeline`. It is a versioned preflight and
readiness gate; it cannot yet perform complete fresh-patient extraction.

Run verification into a fresh artifact directory:

```powershell
python -m caa_pdf_extractor.full_pipeline --verify-only --output data/debug/pipeline_verification_NEW
```

The command inventories source definitions, records versions, Git commit, Python
and prompt-template source hashes, medication dictionary hash, then executes the
repository-wide regression suite. Existing artifact directories are protected
against overwrite. `--run` also performs preflight and returns exit code 2 while
required adapters are disconnected. No database commit mode exists.

The documented legacy ingestion executable is `python -m
caa_pdf_extractor.pdf_ingestion`; ingestion is not complete clinical extraction.
Domain CLIs and dated cohort/review scripts currently form separate paths.

General retrospective CFS/mRS validators are now in
`extraction.functional_status`; isolated scripts import these shared helpers.
They retain raw versus validated candidates, baseline provenance, acute-state
exclusion, CFS consistency rules and range-preserving mRS validation. Patient
adjudications are not embedded in these helpers. The known CFS abstention still
requires work in the semantic inference adapter.

`extraction.research_scores_v23.cha2ds2_va` calls the shared
`extraction.score_bounds.minimum_score`. Missing components remain UNKNOWN;
confirmed positive components still produce a minimum score and, when possible,
a determined thromboembolic recommendation. This does not resolve anticoagulation
appropriateness in CAA or change the separate bleeding/decision layer.

See `data/debug/pipeline_consolidation/pipeline_consolidation_report.md`, its
module inventory and call graph for all remaining adapters. Registration and
passing unit tests alone do not demonstrate a connected production path. The
current answer to automatic execution of every approved module for new patient
folders is **NO**. No full-cohort extraction was performed during consolidation.
