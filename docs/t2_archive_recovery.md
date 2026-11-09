# Archive-wide conventional brain T2 recovery

Completed archive run: **29 new T2 groups recovered; zero eligible conversions
remain missing**. The complete 9,148-row inventory was updated atomically. After
source/QC and database-diff checks, 27 T2 image records and nine studies were added
to the private PostgreSQL MRI index; the coordinator manifest was refreshed with
zero provenance QC issues. Ambiguous sequences and existing-output review cases
remain unchanged. Two recovered groups have no verified patient linkage and remain
local. See the [consolidated completion report](../data/debug/t2_archive_recovery_20261029T142348_485280Z/COMPLETION_REPORT.md)
and [all 29 recovered series](../data/debug/t2_archive_recovery_20261029T142348_485280Z/all_recovered_T2_groups.csv).

`caa_recover_t2_archive.py` reads every acquisition group in
`E:\caa_nifti_batch\inventory.sqlite`. It does not use PostgreSQL linkage to select
studies, import PostgreSQL drivers, or modify any database. The original
`caa_recover_t2_for_fallnr.py` is retained unchanged.

Install the optional dependencies from `C:\app\pdf_extractor`:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-mri-recovery.txt
```

## Preview and conversion

Dry-run is the default. Every run uses a new report directory beneath `data/debug/`.

```powershell
.venv\Scripts\python.exe -B caa_recover_t2_archive.py
```

Reports include `REPORT.md`, `summary.json`, `t2_recovery_results.csv`,
`t2_ambiguous_review.csv`, and per-group source/output QC JSON. Source QC includes
private DICOM identity/provenance; these are local clinical working files.

To perform at most five new conversion **attempts**, including failures:

```powershell
$t2Run = Join-Path 'C:\app\pdf_extractor\data\debug' ('t2_archive_conversion_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
.venv\Scripts\python.exe -B caa_recover_t2_archive.py --convert --limit 5 --report-dir $t2Run
```

The default limit is one. Raising `--limit` is explicit; there is no implicit
unbounded conversion. Already valid, ambiguous, incomplete, or existing partial
groups do not consume conversion attempts. Existing group directories are never
overwritten or reconverted. To scan another existing T2 tree before converting,
add `--existing-t2-root 'E:\another_T2_directory'` (repeatable).

The exact destination is `E:\caa_nifti_t2_display\T2`; the CLI `--destination`
already includes the `T2` suffix. Other supported arguments are `--inventory`,
`--dicom-root`, `--dcm2niix`, `--timeout` and `--reserve-gb`.

Classification recognizes compressed-sensing prefixes, T2W TSE/FSE/FRFSE variants,
brain dual/TSE and other conventional naming variants. It verifies brain context
and sequence timing from DICOM headers. FLAIR/dark-fluid, T2*-GRE, SWI, DWI/DTI,
spine/non-brain imaging, localizers and derived/secondary screenshots are excluded.
Dual-echo short-TE PD components are excluded. Missing/unclear anatomy, unknown
subtypes, untyped spin echo, small series, specialized DRIVE/fat-suppressed variants
and enhanced multiframe inputs are explicitly routed to review. They are not
automatically converted. Existing T2 outputs inconsistent with source classification
also appear in the review CSV, even when the source sequence is excluded.

Conversion uses the successful pilot's dcm2niix flags:

```text
-d 0 -z y -b y -ba y -p n -m n -w 0 -f image_%s_%e
```

`-p n` retains Philips display scaling. Source files are copied into a fresh staging
directory; original DICOM files are not changed. A per-group OS lock serializes
conversion. A successful converter exit is insufficient: output must pass technical
QC before a no-clobber directory rename publishes it. Failed output is retained in
staging for review, while disposable DICOM copies are removed within the checked
staging boundary. Interrupted jobs cannot publish partial identity inventories.

QC loads the complete NIfTI payload, checks finite/nonzero data, dimensions, positive
spacing, qform/sform, millimeter units, unique image/frame counts and physical
voxel-center corners against DICOM (0.5 mm tolerance). Source checks cover every
file, source identity/UID agreement, consistent sequence classification, geometry,
duplicate positions, internal slice gaps and declared counts. Exact duplicate SOP
copies count as one image only after byte comparison; conflicting copies are
blocked. All original archive files remain intact. Without a declared acquisition
count, an entirely missing terminal slice cannot be established from this inventory.

`QC_PASS_NOT_CLINICALLY_VALIDATED` means technical QC only. Undocumented/incorrect
Philips scaling and ambiguous sequences remain review items, not automatic repairs.

## Safe incremental identity inventory update

Do **not** rebuild the identity inventory with a truncated/max-groups run. Use the
complete archive recovery CSV to prepare an incremental plan:

```powershell
.venv\Scripts\python.exe -B caa_update_t2_identity_inventory.py --results "$t2Run\t2_recovery_results.csv" --report-dir "$t2Run\identity_update"
```

This writes a complete proposed CSV, an incremental changes CSV and
`update_plan_PRIVATE.json`. It does not replace the live inventory. Inspect the
new T2 images, QC and proposed changes first. The current complete baseline has
9,148 rows covering all 9,132 SQLite groups; the historical 200-row partial file
will be refused. The 1,206-row safety minimum is additional to full group coverage.

Only `ALREADY_VALID`/`CONVERTED_QC_PASS` results with matching source identity and
QC proofs can add paths. Existing paths and other categories are retained. An empty
`NOT_CONVERTED` row can become T2 without losing its identity/provenance. Source
groups without complete baseline identity provenance require review rather than
invented linkage.

After checking the proposal, publish the **local CSV only** explicitly:

```powershell
.venv\Scripts\python.exe -B caa_update_t2_identity_inventory.py --apply-plan "$t2Run\identity_update\update_plan_PRIVATE.json"
```

Application takes an OS lock and rechecks input hashes, schema, row counts, all
group coverage, preservation of existing paths, source signatures and new NIfTI
hashes. It fsyncs a full backup, writes and validates a same-directory temporary
complete CSV, then atomically replaces the live file. A failed/interrupted replace
leaves the previous complete inventory intact. Changed input since plan creation
causes refusal; prepare a fresh plan instead of overwriting concurrent changes.
Zero-change plans do not replace the live inventory. No PostgreSQL operation occurs.

## Refresh PostgreSQL and the coordinator manifest after review

Run these from `C:\app\pdf_extractor` with the existing `DATABASE_URL` or `PG*`
environment configured. These existing import/export commands do not load `.env`
automatically. Keep the verified v3 linkage report; archive-wide recovery does not
create new patient identity matches or rater assignments.

First preview the PostgreSQL index additions (transaction rolls back):

```powershell
.venv\Scripts\python.exe -B caa_import_mri_links_postgres_v1.py --report caa_private_linkage\mri_orbis_fallnr_exact_candidates_v3_PRIVATE.csv --inventory caa_private_linkage\mri_identity_series_PRIVATE.csv
```

**Only after the new T2 records and dry-run additions have been checked**, explicitly
commit the index refresh:

```powershell
.venv\Scripts\python.exe -B caa_import_mri_links_postgres_v1.py --report caa_private_linkage\mri_orbis_fallnr_exact_candidates_v3_PRIVATE.csv --inventory caa_private_linkage\mri_identity_series_PRIVATE.csv --commit
```

Then regenerate the coordinator manifest with DICOM verification and NIfTI hashes:

```powershell
.venv\Scripts\python.exe -B caa_export_coordinator_manifest.py --private-dir caa_private_linkage --data-root 'E:\' --verify-dicom --hash-files
```

The importer only indexes existing verified patient linkages; unlinked archive
studies remain available locally without being assigned to a PostgreSQL patient.
Neither recovery utility automatically invokes these commands. Existing exam IDs
are preserved by the exporter. The export is coordinator-only and does not grant
rater assignments.

## Tests

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_mri_t2_recovery.py -q
```

Synthetic tests exercise naming/exclusion, ambiguity, real DICOM/NIfTI geometry and
payload checks, duplicate SOP handling, incomplete outputs, no overwrite, alternate
existing roots, conversion-attempt limits, source preservation, complete-file merge,
truncation rejection, stale/tampered inputs and interrupted atomic replacement.
