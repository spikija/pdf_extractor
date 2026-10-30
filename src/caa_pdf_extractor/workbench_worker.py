"""Single-request offline subprocess for existing diagnosis/medication assemblers."""
import csv
import io
import json
import os
import sys
from pathlib import Path

from .workbench_store import PILOTS, read, sha


def guard(output):
    output = output.resolve()
    def audit(event, args):
        if event.startswith(('socket.', 'sqlite3.')) or event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.spawn'):
            raise RuntimeError('WORKBENCH_NETWORK_DATABASE_AND_CHILD_PROCESS_FORBIDDEN')
        if event == 'import' and any(s in args[0].lower() for s in ('sqlalchemy', 'psycopg', 'requests', 'httpx')):
            raise RuntimeError('WORKBENCH_DATABASE_AND_HTTP_IMPORT_FORBIDDEN')
        if event == 'open':
            name, mode, flags = args
            writing = isinstance(mode, str) and any(c in mode for c in 'wax+')
            writing |= isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and isinstance(name, (str, bytes, os.PathLike)) and not Path(name).resolve().is_relative_to(output):
                raise RuntimeError('WORKBENCH_WRITE_OUTSIDE_NEW_RUN_FORBIDDEN')
        if event in ('os.rename', 'os.remove', 'os.rmdir'):
            raise RuntimeError('WORKBENCH_WORKER_REPLACEMENT_FORBIDDEN')
    sys.addaudithook(audit)


def csv_text(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys(k for row in rows for k in row)) or ['status'])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return out.getvalue()


def execute(request):
    patient, module = request['patient_id'], request['module']
    if patient not in PILOTS or module not in ('diagnoses', 'medications'):
        raise ValueError('Exactly one pilot patient and one supported module are required')
    source = Path(request['source_dir'])
    # Required inputs have been snapshotted; reject any mutation before execution.
    if any(sha(path) != value for path, value in request['input_hashes'].items()):
        raise ValueError('Saved source inputs changed')
    bundle = read(source / 'source_bundle.json')
    if str(bundle.get('patient_id')) != patient:
        raise ValueError('Source bundle patient mismatch')
    if module == 'diagnoses':
        from .extraction import diagnosis_events_v4 as dx
        from .diagnosis_v4_regression import regression
        result = dx.assemble(patient, bundle, read(source / 'source_mentions.json'))
        gates = regression({patient: result}, patient_id=patient)
        artifacts = {}
        for name, rows in result.items():
            artifacts[name + '.json'] = json.dumps(rows, ensure_ascii=False, indent=2)
            if isinstance(rows, list):
                artifacts[name + '.csv'] = csv_text(rows)
        version = dx.VERSION
    else:
        from .extraction.medication_identity_v3 import Resolver, COMMON_BRANDS, VERSION, assemble
        with (source / 'medications.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        # Same saved CSV decoding convention as the existing V3 runner.
        for row in rows:
            for key, value in row.items():
                if value and value[0] in '[{':
                    try:
                        row[key] = json.loads(value)
                    except ValueError:
                        pass
        for row in rows:
            if str(row.get('patient_id')) != patient:
                raise ValueError('Medication source row patient mismatch')
        resolver = Resolver()
        encounters = {str(e['source_document_id']): e for e in read(source / 'encounters.json')}
        mentions, canonical, exposures = assemble(patient, rows, resolver, encounters)
        gates = dict(source_mentions_and_exposures_retained=len(mentions) == len(exposures) == len(rows),
                     common_brand_regressions_pass=all(resolver.resolve(b + ' 10 mg 1 0 1')['canonical_drug'] is not None for b in COMMON_BRANDS),
                     unique_drug_identities=len({r['drug_identity_id'] for r in canonical}) == len(canonical))
        artifacts = {}
        for name, records in [('medication_mentions', mentions), ('canonical_medications', canonical), ('medication_exposures', exposures)]:
            artifacts[name + '.json'] = json.dumps(records, ensure_ascii=False, indent=2)
            artifacts[name + '.csv'] = csv_text(records)
        version = VERSION
    return dict(artifacts=artifacts, module_version=version, hard_gates=gates, fatal_errors=[],
                patients_processed=[patient], modules_executed=[module], network_calls=0, database_writes=0)


def main():
    sys.dont_write_bytecode = True
    request_path = Path(sys.argv[1]).resolve()
    request = read(request_path)
    guard(request_path.parent)
    try:
        result = execute(request)
    except Exception as exc:
        result = dict(artifacts={}, hard_gates={}, fatal_errors=[f'{type(exc).__name__}: {exc}'],
                      patients_processed=[request.get('patient_id')], modules_executed=[request.get('module')])
    with (request_path.parent / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
