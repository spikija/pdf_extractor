"""Classify all direct pdf_db patient folders; no Excel, MRI or model execution."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import audit_parser_caa_mri as audit
from reclassify_original_cohort_caa import parsed_index

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ['CAA_ESTABLISHED', 'CAA_SUSPECTED', 'CAA_DIFFERENTIAL_OR_WORKUP',
            'CAA_REVIEW_CANDIDATE', 'CAA_NEGATED', 'CAA_NONE']


def discover():
    root = ROOT / 'pdf_db'
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    patients = [folder.name for folder in folders]
    rows = []
    for folder in folders:
        for pdf in sorted(folder.rglob('*')):
            if pdf.is_file() and pdf.suffix.lower() == '.pdf':
                rows.append(dict(patient_id=folder.name, filename=pdf.name, pdf_path=pdf,
                                 relative_path=pdf.relative_to(root).as_posix(),
                                 source_hash=audit.utils.get_sha256(pdf)))
    return patients, rows


def run(workers=4):
    patients, rows = discover()
    index = parsed_index(patients, rows)
    initial = Counter()
    pending = {}
    for row in rows:
        status, _, _ = index.classify(row)
        initial[status] += 1
        if status == 'AMBIGUOUS':
            raise ValueError('Ambiguous source identity; complete export withheld')
        if status != 'CURRENT_PARSED':
            pending.setdefault(row['source_hash'], row)
    requiring_parse = sum(n for status, n in initial.items() if status != 'CURRENT_PARSED')
    print(f'PDF_DB_INVENTORY patients={len(patients)} pdfs={len(rows)} requiring_parse={requiring_parse}', flush=True)
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
            raise RuntimeError(f'{failures} PDF parses failed; incomplete classification withheld')
    assertions = defaultdict(list)
    scanned = Counter()
    for number, row in enumerate(rows, 1):
        status, artifact, _ = index.classify(row)
        if status != 'CURRENT_PARSED':
            raise ValueError('Current parser coverage incomplete')
        payload = artifact.load()
        if not audit.valid_payload(payload) or payload['document']['sha256'] != row['source_hash']:
            raise ValueError('Current artifact/source validation failed')
        assertions[row['patient_id']].extend(audit.caa_text(payload))
        scanned[row['patient_id']] += 1
        if number % 500 == 0:
            print(f'CAA_SCAN {number}/{len(rows)}', flush=True)
    # Reuse the same existing clinical source priority and document aggregation.
    higher = audit.priority_sources(index, rows, assertions)
    output = [dict(fallnr=patient, pdf_count=scanned[patient],
                   CAA_status=higher.get(patient) or audit.strongest(assertions[patient]) or 'CAA_NONE')
              for patient in patients]
    expected = Counter(row['patient_id'] for row in rows)
    assert len(output) == len({row['fallnr'] for row in output}) == len(patients)
    assert all(scanned[patient] == expected[patient] for patient in patients)
    assert sum(row['pdf_count'] for row in output) == len(rows)
    assert all(row['CAA_status'] in STATUSES for row in output)
    target = ROOT / 'output/pdf_db_caa_classification.csv'
    audit.csvout(target, ['fallnr', 'pdf_count', 'CAA_status'], output)
    review = [dict(fallnr=row['fallnr'], CAA_status=row['CAA_status'],
                   short_reason='Differential/workup only' if row['CAA_status'] == 'CAA_DIFFERENTIAL_OR_WORKUP'
                   else 'Uncertain assertion or markers only') for row in output
              if row['CAA_status'] in {'CAA_DIFFERENTIAL_OR_WORKUP', 'CAA_REVIEW_CANDIDATE'}]
    reviewpath = ROOT / 'output/pdf_db_caa_review_candidates.csv'
    if review:
        audit.csvout(reviewpath, ['fallnr', 'CAA_status', 'short_reason'], review)
    elif reviewpath.exists():
        reviewpath.unlink()
    counts = Counter(row['CAA_status'] for row in output)
    for key, value in [
        ('PDF_DB_PATIENTS_TOTAL', len(patients)), ('TOTAL_PDFS', len(rows)),
        ('PDFs_PARSED_OR_REPARSED', requiring_parse),
        *((status, counts[status]) for status in STATUSES),
        ('CAA_ESTABLISHED_OR_SUSPECTED', counts['CAA_ESTABLISHED'] + counts['CAA_SUSPECTED']),
        ('OUTPUT_FILE', 'output/pdf_db_caa_classification.csv'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]:
        print(f'{key} = {value}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4, choices=range(1, 9))
    run(parser.parse_args().workers)
