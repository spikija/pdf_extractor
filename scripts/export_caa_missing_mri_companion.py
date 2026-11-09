"""Export CAA cohort minus registered MRI identities; no model or DB writes.

--refresh-cohort inventories current folders and reuses the existing deterministic
CAA classifier/parser cache, parsing only missing/stale sources. PDF MRI evidence
is never evaluated. Without that flag, reuse the latest companion summary/cohort.

Identifier contract: case_bootstrap._get_or_create maps patient_id directly to
public.case.fallnr. caa_import_mri_links_postgres_v1 stores that same Fallnummer
in caa_mri_private.identity_links.fallnr, with a foreign key to public.case.id.
Absence from public.case alone does not invalidate this documented namespace.
MRI presence is identity_links membership, regardless of report/image counts.
"""
import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import create_engine, text
from caa_pdf_extractor.config import database_url

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'output/caa_all_patients_mri_status.csv'
TARGET = ROOT / 'output/caa_patients_missing_from_mri_companion.csv'
SUMMARY = ROOT / 'output/caa_mri_companion_match_summary.csv'
ISSUES = ROOT / 'output/caa_mri_identity_mapping_issues.csv'
INCLUDE = {'CAA_ESTABLISHED', 'CAA_SUSPECTED'}


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def refresh_local_cohort(workers=4):
    """Same existing CAA logic and source priority, with no PDF MRI extraction."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    import audit_parser_caa_mri as audit
    patients, rows, orphans = audit.inventory(ROOT / 'pdf_db')
    if orphans:
        raise ValueError('PDFs outside patient folders require identity review')
    index = audit.ParsedIndex()
    index.scan()
    pending = {}
    for row in rows:
        status, _, _ = index.classify(row)
        if status == 'AMBIGUOUS':
            raise ValueError('Ambiguous source identity prevents complete cohort refresh')
        if status != 'CURRENT_PARSED':
            pending.setdefault(row['source_hash'], {key: row[key] for key in
                               ('pdf_path', 'relative_path', 'patient_id', 'source_hash')})
    print(f'REFRESH patients={len(patients)} pdfs={len(rows)} unique_sources_requiring_parse={len(pending)}', flush=True)
    failed = 0
    if pending:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(audit.parse_one, row) for row in pending.values()]
            for number, future in enumerate(as_completed(futures), 1):
                cache, error = future.result()
                if error:
                    failed += 1
                else:
                    index.add(audit.read(cache), Path(cache))
                if number % 200 == 0 or number == len(futures):
                    print(f'PARSE {number}/{len(futures)} failures={failed}', flush=True)
        if failed:
            raise RuntimeError(f'{failed} PDF parses failed; complete cohort export withheld')
    text_status = defaultdict(list)
    for row in rows:
        status, artifact, _ = index.classify(row)
        if status != 'CURRENT_PARSED':
            raise ValueError('Current parsed coverage incomplete')
        text_status[row['patient_id']].extend(audit.caa_text(artifact.load()))
    higher = audit.priority_sources(index, rows, text_status)
    cohort = []
    for patient in sorted(patients):
        status = higher.get(patient) or audit.strongest(text_status[patient])
        if status in INCLUDE:
            cohort.append(dict(patient_id=patient.strip(), CAA_status=status))
    return cohort, len(patients)


def run(*, refresh_cohort=False, workers=4):
    source = SUMMARY if SUMMARY.exists() else SOURCE
    original_hash = None
    if refresh_cohort:
        cohort, local_total = refresh_local_cohort(workers)
    else:
        local_total = sum(p.is_dir() for p in (ROOT / 'pdf_db').iterdir())
        original_hash = hashlib.sha256(source.read_bytes()).digest()
        with source.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if not {'patient_id', 'CAA_status'}.issubset(reader.fieldnames or []):
                raise ValueError('Cohort is missing required identifier/status columns')
            cohort = [dict(patient_id=str(row['patient_id']).strip(), CAA_status=row['CAA_status'])
                      for row in reader if row['CAA_status'] in INCLUDE]
    cohort.sort(key=lambda row: row['patient_id'])
    ids = [row['patient_id'] for row in cohort]
    if len(ids) != len(set(ids)):
        raise ValueError('Cohort contains duplicate patient identifiers')
    problems = defaultdict(set)
    for pid in ids:
        if not pid or pid != pid.strip():
            problems[pid].add('Empty identifier or surrounding whitespace; exact matching unsafe')

    engine = create_engine(database_url(), isolation_level='REPEATABLE READ',
                           connect_args={'options': '-c default_transaction_read_only=on', 'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
                raise RuntimeError('Read-only transaction required')
            columns = set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='caa_mri_private' AND table_name='identity_links'"
            )).scalars())
            if not {'identity_key', 'case_id', 'fallnr'}.issubset(columns):
                raise ValueError('MRI identity schema differs from inspected mapping contract')
            links = [dict(row) for row in connection.execute(text(
                'SELECT l.identity_key,l.case_id,l.fallnr,c.fallnr::text AS case_fallnr '
                'FROM caa_mri_private.identity_links l '
                'LEFT JOIN public."case" c ON c.id=l.case_id'
            )).mappings()]
            case_rows = [dict(row) for row in connection.execute(text(
                'SELECT id,fallnr::text AS fallnr FROM public."case" WHERE fallnr IS NOT NULL'
            )).mappings()]
    finally:
        engine.dispose()

    for link in links:
        link['fallnr'] = str(link['fallnr']).strip() if link['fallnr'] is not None else ''
        link['case_fallnr'] = str(link['case_fallnr']).strip() if link['case_fallnr'] is not None else ''
    for row in case_rows:
        row['fallnr'] = str(row['fallnr']).strip()
    # Do not coerce case_id, DICOM patient IDs, or numeric-looking identifiers
    # into Fallnummer. Only the explicitly stored exact text identity is used.
    duplicates = {pid for pid, n in Counter(row['fallnr'] for row in case_rows).items() if n > 1}
    for pid in set(ids) & duplicates:
        problems[pid].add('Fallnummer maps to multiple public.case rows')
    linked_patients = set()
    linked_fall_counts = Counter(row['fallnr'] for row in links)
    for link in links:
        fallnr = link['fallnr']
        invalid = (not fallnr or fallnr != fallnr.strip() or
                   fallnr != link['case_fallnr'] or linked_fall_counts[fallnr] != 1)
        if invalid:
            affected = {fallnr, link['case_fallnr']} & set(ids)
            if not affected:
                affected = {fallnr or ''}
            for pid in affected:
                problems[pid].add('MRI identity Fallnummer is empty, duplicated, or conflicts with linked case')
        else:
            linked_patients.add(fallnr)

    unmapped = set(ids) & set(problems)
    matched = (set(ids) - unmapped) & linked_patients
    missing = sorted((row for row in cohort
                      if row['patient_id'] not in linked_patients and row['patient_id'] not in unmapped),
                     key=lambda row: row['patient_id'])
    missing_ids = {row['patient_id'] for row in missing}
    assert len(missing) == len(missing_ids)
    assert not missing_ids & linked_patients
    assert not missing_ids & matched
    assert len(cohort) == len(matched) + len(missing) + len(unmapped)
    assert all(row['CAA_status'] in INCLUDE for row in missing)
    if original_hash is not None:
        assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    if problems:
        raise ValueError('Identity mapping issues prevent a definitive companion export')
    summary = [dict(row, MRI_companion_present='TRUE' if row['patient_id'] in linked_patients else 'FALSE')
               for row in cohort]
    assert all((row['MRI_companion_present'] == 'TRUE') == (row['patient_id'] in linked_patients) for row in summary)
    write_csv(SUMMARY, ['patient_id', 'CAA_status', 'MRI_companion_present'], summary)
    write_csv(TARGET, ['patient_id', 'CAA_status'], missing)
    if problems:
        write_csv(ISSUES, ['patient_id', 'issue'],
                  [dict(patient_id=pid, issue='; '.join(sorted(issues)))
                   for pid, issues in sorted(problems.items())])
    elif ISSUES.exists():
        ISSUES.unlink()
    # Validate the serialized colleague export against the exact SQL-derived set.
    with TARGET.open(encoding='utf-8-sig', newline='') as stream:
        exported = csv.DictReader(stream)
        assert exported.fieldnames == ['patient_id', 'CAA_status']
        assert list(exported) == missing
    with SUMMARY.open(encoding='utf-8-sig', newline='') as stream:
        exported = csv.DictReader(stream)
        assert exported.fieldnames == ['patient_id', 'CAA_status', 'MRI_companion_present']
        assert list(exported) == summary
    for key, value in (
        ('LOCAL_PATIENTS_TOTAL', local_total),
        ('CAA_TOTAL', len(cohort)),
        ('MRI_COMPANION_UNIQUE_PATIENTS', len(linked_patients)),
        ('CAA_MATCHED_TO_MRI_COMPANION', len(matched)),
        ('CAA_MISSING_FROM_MRI_COMPANION', len(missing)),
        ('IDENTIFIER_MAPPING_ISSUES', len(problems)),
        ('MISSING_MRI_FILE', 'output/caa_patients_missing_from_mri_companion.csv'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE'),
    ):
        print(f'{key} = {value}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-cohort', action='store_true')
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    run(refresh_cohort=args.refresh_cohort, workers=args.workers)
