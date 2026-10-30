The five-patient pilot runs with:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.integration_pilot --five-patient-review
```

It creates a new `data/debug/five_patient_integration_<timestamp>/` directory.
The four user-specified patients are fixed in the entry point. A fifth patient
is chosen deterministically using hash-verified cached source-text availability:
number of target feature classes, document count, then ascending patient ID.
This selection census does not extract clinical outcomes for other patients.
The ranking and selection rationale are retained. There is no unrestricted
patient-ID argument.

Every selected PDF is discovered directly and parsed fresh. Fresh source IDs
are stable hashes of relative PDF paths; frozen database IDs and cohort clinical
summaries are not inputs. Exact local Granite requests, raw responses, source
evidence, validation failures and accepted proposals are retained. A previous
pilot's model response may be reused only for an identical request and installed
model digest; the current validators always run again.

The process audit guard denies filesystem writes outside its own artifact tree,
SQLite access and external socket connections. A SQLAlchemy execution guard also
denies SQL. The only model transport is the existing numeric-loopback Granite
client, which rejects proxies, redirects and remote models. The runner never
calls database ingestion or persistence functions.

The functional pathway extracts narrow source quotations, validates temporal
ownership, interprets a phenotype, and then requests CFS and pre-event mRS.
Explicit baseline observations must survive phenotype interpretation. The
combined communication/cognition field can describe a communication limitation
without establishing cognitive disease. No patient-specific score is supplied
to these prompts. An inconsistent CFS anchor permits at most one consistency
retry. Rejected raw candidates are retained and no numeric result is repaired
in Python. Explicit scale observations outrank retrospective proposals.

The entry point is an integration experiment. Missing adapters and source
limitations are disclosed in `pipeline_gap_audit.csv`, the five-row module
matrix and individual physician reviews. `FIVE_PATIENT_INTEGRATION_PARTIAL`
does not authorize production use. The separate `full_pipeline --run` remains
blocked by its existing consolidation checks.

The command finishes with a separate artifact review phase. It applies the
current neurological source-label taxonomy, connects literal reports and
source-derived lesion identity to CAA serial-CT continuity checks, and evaluates
patient-specific regression assertions only after inference. It never repairs
a CFS, mRS or risk score. Raw model features remain separate from source-based
CAA reconciliation. The MRI/Edinburgh exclusion gate is checked again after
reconciliation. The artifact-only phase can also be replayed with:

```powershell
.venv\Scripts\python.exe -m caa_pdf_extractor.integration_pilot_review --pilot-output data/debug/five_patient_integration_<timestamp>
```

The combined review, per-patient artifacts, source manifest and hard-gate audit
are written locally. These are research review proposals and do not update any
clinical record. Source absence is distinguished from failed or incomplete
module integration. In particular, unassessed vessels, undated laboratories,
unknown risk components and unresolved event/scale ownership remain visible.
