# Descriptive cohort analysis

Install the optional local reporting dependencies from `requirements-analysis.txt`.
Run:

```
python -m caa_pdf_extractor.cohort_descriptive_analysis --source data/debug/full_cohort_20261022T201512+0200
```

Each execution creates a new timestamped directory under `data/analysis/`.
`caa_cohort_descriptive_latest.txt` identifies the latest ready package.
The analysis freezes SHA-256 hashes for every source-run artifact and checks
them after writing the reports. An audit hook rejects writes to the source
directory and all network/database/model connections. It never reruns clinical
extraction or changes a patient's validated CAA, epilepsy or AF indicator.
The 50/20/9/11 baseline is checked before tables or tests are generated.

The package includes Table 1, Table 2, CSV/XLSX exports, one-row-per-patient data,
missingness, raw statistical tests, continuous mean/SD/range supplements,
individual selected CSF measurements, examination/admission/ICH/medication/PET
identity audits, six static PNG/PDF figures and a short portrait master PDF.
All master/Table 1 pages are rendered to PNG for inspection. The analysis
verification records gates, dependency versions and implementation hashes.

Available-case denominators are explicit. First presentation uses validated
admission reason and clinically linked events, never imaging alone. Unsupported
structured presentation components and absent discharge mRS remain missing.
Different same-day scan reports without examination identity are ambiguous:
exact counts are missing and supported examination-day lower bounds remain
available separately. CTA never becomes a second plain CT. Source clinical ICH
history and radiology markers are separate streams, with disagreements exported.

Anticoagulant classes and treatment roles are separate. Missing lists are not
negative exposures. An unspecified heparin/DOAC/VKA role is not inferred to be
therapeutic, and prophylaxis is never classified as therapeutic anticoagulation.
Ever exposure means any available discharge list, not complete lifetime exposure.

Death declarations come from retained explicit patient death labels and existing
literal patient-attribution death-date rules on frozen source sections. Exact
quotes, offsets and rule versions are exported; conditional/relative deaths are
excluded. Generic accepted record dates are not death dates. Inpatient status is
used only when directly supported. These documentation counts are not survival
estimates. PET result annotations use the existing canonical literal rules on
frozen report text, with exact quotations and versions; indeterminate results
are not negative and never change the validated CAA grouping.

One value per patient/analyte is selected for CSF: earliest supported admission
measurement or a consistent undated result. Copies are not independent patients.
Original assay/unit/reference and abnormality classification are preserved.
No beta-amyloid isoform, ratio or universal laboratory cutoff is inferred.
Figure 7 is omitted with an explicit insufficiency statement for this small,
non-comparable sample.

Two-sided Fisher exact and Mann–Whitney U tests are exploratory. Comparisons
require at least five known patients in each group; invariant data and sparse
binary margins are not tested. P values are unadjusted and retained at full
precision alongside their three-decimal display. Missingness and source review
holds limit interpretation; the package does not establish clinical adjudication.
