"""Reclassify every PDF in original-cohort folders using existing CAA logic only."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import create_engine, text
from caa_pdf_extractor.config import database_url
import audit_parser_caa_mri as audit

ROOT = Path(__file__).resolve().parents[1]


def original_cohort():
    sources = [ROOT / folder / 'orbis_01012015_01062023_amyloidangiopat.xlsx'
               for folder in ('old_db_definition', 'data/old_retool_db')]
    sets = []
    for source in sources:
        if not source.exists():
            continue
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            iterator = workbook['CAA'].iter_rows(values_only=True)
            column = next(iterator).index('Fallnummer')
            sets.append({str(row[column]).strip() for row in iterator if row[column] is not None})
        finally:
            workbook.close()
    if not sets or any(cohort != sets[0] for cohort in sets):
        raise ValueError('Original cohort source missing or conflicting')
    return sets[0]


def discover(cohort):
    root = ROOT / 'pdf_db'
    folders = [path for path in root.rglob('*') if path.is_dir() and path.name.strip() in cohort]
    with_folder = {path.name.strip() for path in folders}
    # A repeated/nested same-name directory must not cause duplicate PDF scans.
    files = {path.resolve() for folder in folders for path in folder.rglob('*')
             if path.is_file() and path.suffix.lower() == '.pdf'}
    rows = []
    for path in sorted(files):
        owners = {parent.name.strip() for parent in path.parents
                  if parent.is_relative_to(root) and parent.name.strip() in cohort}
        if len(owners) != 1:
            raise ValueError('Conflicting original-cohort folder ownership')
        rows.append(dict(patient_id=owners.pop(), filename=path.name, pdf_path=path,
                         relative_path=path.relative_to(root).as_posix(),
                         source_hash=audit.utils.get_sha256(path)))
    return with_folder, rows


def parsed_index(cohort, rows):
    index = audit.ParsedIndex()
    # Read only source hashes belonging to this cohort, never scan unrelated text.
    cache_root = audit.CACHE.parent
    for digest in {row['source_hash'] for row in rows}:
        for path in cache_root.glob('*/' + digest + '.json'):
            try:
                index.add(audit.read(path), path)
            except (ValueError, OSError):
                index.corrupt_hashes.add(digest)
    for pid in {row['patient_id'] for row in rows}:
        for path in (ROOT / 'data/parsed' / pid).glob('*.json'):
            try:
                index.add(audit.read(path), path)
            except (ValueError, OSError):
                index.corrupt_names.add((pid, path.name.split('__')[0]))
    engine = create_engine(database_url(), isolation_level='REPEATABLE READ',
                           connect_args={'options': '-c default_transaction_read_only=on', 'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SHOW transaction_read_only')).scalar() == 'on'
            params = dict(patients=sorted(cohort))
            index.db_sources = [dict(row) for row in connection.execute(text(
                "SELECT id,filename,sha256,document_json FROM source_documents WHERE "
                "document_json->'document'->>'patient_id'=ANY(:patients)"), params).mappings()]
            for row in index.db_sources:
                if row['document_json']:
                    index.add(row['document_json'], keep=True)
                else:
                    index.indicated.add((row['sha256'], row['filename']))
            index.db_caa = [dict(row) for row in connection.execute(text(
                'SELECT e.*,c.fallnr::text AS patient_id FROM caa_diagnostic_event e '
                'JOIN "case" c ON c.id=e.case_id WHERE NOT coalesce(e.deleted,false) '
                'AND c.fallnr::text=ANY(:patients)'), params).mappings()]
            index.db_cases = [dict(row) for row in connection.execute(text(
                'SELECT fallnr::text AS patient_id,caa_criteria,caa_criteria_stage FROM "case" '
                'WHERE NOT coalesce(deleted,false) AND fallnr::text=ANY(:patients)'), params).mappings()]
    finally:
        engine.dispose()
    return index


def run(workers=4):
    cohort = original_cohort()
    with_folder, rows = discover(cohort)
    index = parsed_index(cohort, rows)
    pending = {}
    initial = Counter()
    for row in rows:
        status, _, _ = index.classify(row)
        initial[status] += 1
        if status == 'AMBIGUOUS':
            raise ValueError('Ambiguous source identity; classification withheld')
        if status != 'CURRENT_PARSED':
            pending.setdefault(row['source_hash'], row)
    print(f'ORIGINAL_INVENTORY folders={len(with_folder)} pdfs={len(rows)} requiring_parse={sum(n for s,n in initial.items() if s != "CURRENT_PARSED")}', flush=True)
    if pending:
        failures = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(audit.parse_one, row) for row in pending.values()]
            for number, future in enumerate(as_completed(futures), 1):
                cache, error = future.result()
                if error:
                    failures += 1
                else:
                    index.add(audit.read(cache), Path(cache))
                if number % 100 == 0 or number == len(futures):
                    print(f'PARSE_PROGRESS {number}/{len(futures)} failures={failures}', flush=True)
        if failures:
            raise RuntimeError(f'{failures} PDF parses failed; incomplete reclassification withheld')
    assertions = defaultdict(list)
    scanned = Counter()
    for number, row in enumerate(rows, 1):
        status, artifact, _ = index.classify(row)
        if status != 'CURRENT_PARSED':
            raise ValueError('Current parsed coverage incomplete')
        payload = artifact.load()
        if not audit.valid_payload(payload) or payload['document']['sha256'] != row['source_hash']:
            raise ValueError('Parsed artifact failed final source validation')
        assertions[row['patient_id']].extend(audit.caa_text(payload))
        scanned[row['patient_id']] += 1
        if number % 500 == 0:
            print(f'ORIGINAL_CAA_SCAN {number}/{len(rows)}', flush=True)
    # Preserve the previously used source priority; no new clinical criteria.
    higher = audit.priority_sources(index, rows, assertions)
    output = [dict(fallnr=pid, pdf_count=scanned[pid],
                   CAA_status=higher.get(pid) or audit.strongest(assertions[pid]) or 'CAA_NONE')
              for pid in sorted(cohort)]
    expected = Counter(row['patient_id'] for row in rows)
    assert all(scanned[pid] == expected[pid] for pid in cohort)
    assert len(output) == len({row['fallnr'] for row in output}) == len(cohort)
    assert sum(row['pdf_count'] for row in output) == len(rows)
    path = ROOT / 'output/original_cohort_caa_reclassification.csv'
    audit.csvout(path, ['fallnr', 'pdf_count', 'CAA_status'], output)
    review = [dict(fallnr=row['fallnr'], CAA_status=row['CAA_status'],
                   short_reason='Differential/workup only' if row['CAA_status'] == 'CAA_DIFFERENTIAL_OR_WORKUP'
                   else 'Uncertain assertion or markers only') for row in output
              if row['CAA_status'] in {'CAA_DIFFERENTIAL_OR_WORKUP', 'CAA_REVIEW_CANDIDATE'}]
    reviewpath = ROOT / 'output/original_cohort_caa_review_candidates.csv'
    if review:
        audit.csvout(reviewpath, ['fallnr', 'CAA_status', 'short_reason'], review)
    elif reviewpath.exists():
        reviewpath.unlink()
    counts = Counter(row['CAA_status'] for row in output)
    for key, value in [
        ('ORIGINAL_COHORT_TOTAL', len(cohort)),
        ('ORIGINAL_COHORT_WITH_LOCAL_FOLDER', len(with_folder)),
        ('ORIGINAL_COHORT_WITHOUT_LOCAL_FOLDER', len(cohort - with_folder)),
        ('TOTAL_PDFS_SCANNED', len(rows)),
        ('PDFs_PARSED_OR_REPARSED', sum(n for status,n in initial.items() if status != 'CURRENT_PARSED')),
        *((status, counts[status]) for status in ['CAA_ESTABLISHED', 'CAA_SUSPECTED', 'CAA_DIFFERENTIAL_OR_WORKUP',
                                                'CAA_REVIEW_CANDIDATE', 'CAA_NEGATED', 'CAA_NONE']),
        ('CAA_ESTABLISHED_OR_SUSPECTED', counts['CAA_ESTABLISHED'] + counts['CAA_SUSPECTED']),
        ('OUTPUT_FILE', 'output/original_cohort_caa_reclassification.csv'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]:
        print(f'{key} = {value}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    run(parser.parse_args().workers)
