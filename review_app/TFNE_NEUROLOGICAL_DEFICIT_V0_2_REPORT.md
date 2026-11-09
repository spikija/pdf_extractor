# TFNE v0.2 physician review

The same ten v0.1 patients were rerun on byte-identical source inputs. Ten separate
v0.2 CANDIDATE artifacts are available; v0.1 candidates, prompt, raw logs and two
existing rejection reviews are preserved.

Validated episodes: 19 (v0.1) versus 17 (v0.2). Under the same mechanical support
checks, retained unsupported fields are 79 versus 0. All hard gates are zero;
74 focused tests pass, including 30 core regressions and all ten version switches.
These are lexical/source-support checks, not adjudicated clinical accuracy.

Important: episode detection still misses the known confusion/headache/malaise
case in patient 2674159019. Four v0.2 patients have no detected episodes; absence
of extraction is not evidence of absence. Repeated accounts may need merging.
Granite still proposes unsupported values, which the validator removes.

In Review App use the TFNE pilot queue, open TFNE / Neurological Deficits and
switch TFNE extraction version to v0.2. Changed fields show values, exact evidence
and validation status; v0.2 reviews are stored separately from v0.1.

Full report:
`data/debug/tfne_v0_2_20261101T174337_177561Z/release/tfne_v0_2_report.md`.
Comparison CSV and grounding/validation audits are beside the full report.

PROMPT_VERSION = tfne_neurological_deficit_v0_2
DATABASE_WRITES = NONE
CLOUD_PATIENT_CALLS = NONE
