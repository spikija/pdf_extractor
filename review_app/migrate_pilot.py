"""Explicit one-time pilot migration. Does not run extraction or promote candidates."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True

from review_app.config import ROOT
from review_app.artifact_repository import discover, select_modules, module_hash
from caa_pdf_extractor.workbench_store import WorkbenchStore, PILOTS, integrity, read
from caa_pdf_extractor.workbench_runner import configure_sources


def migrate(store=None, roots=None, pdf_root=None):
    store = store or WorkbenchStore()
    index = discover(roots or (ROOT / 'data' / 'debug', ROOT / 'patient_artifacts'))
    report = {}
    for patient in PILOTS:
        selected, latest, errors = select_modules(index.get(patient, []))
        if not all(m in selected for m in ('diagnoses', 'medications')):
            raise ValueError(f'{patient}: required validated modules are missing')
        source_dir = next((Path(e['artifact']).parent for e in latest.values()
                           if (Path(e.get('artifact', '')).parent / 'source_bundle.json').is_file()), None)
        if source_dir is None:
            raise ValueError(f'{patient}: source context missing')
        kwargs = {'pdf_root': pdf_root} if pdf_root else {}
        context = configure_sources(store, patient, source_dir, **kwargs)
        report[patient] = {}
        for module in ('diagnoses', 'medications'):
            group = store.manifest(patient)['modules'].get(module, {})
            if group.get('active_validated_version'):
                report[patient][module] = group['active_validated_version']
                continue
            entry = selected[module]
            if not integrity(entry):
                raise ValueError('Source integrity failed before migration')
            artifacts = {Path(f['path']).name: Path(f['path']).read_bytes() for f in entry['artifacts']}
            report_path = Path(entry['artifact']).parents[2] / 'validation_report.json'
            old_report = read(report_path) if report_path.is_file() else {}
            implementation = old_report.get('implementation_hashes', {})
            metadata = dict(module_version=entry['version'], source_pdf_hashes=context['source_pdf_hashes'],
                source_input_hashes=context['input_hashes'], parser_version=context['parser_version'],
                upstream_module_versions={'saved_source': Path(context['directory']).name},
                code_hash=hashlib.sha256(json.dumps(implementation, sort_keys=True).encode()).hexdigest() if implementation else None,
                git_commit=None, implementation_hashes=implementation, prompt_hashes={},
                validation_test_results=entry.get('validation', {}), fatal_errors=[],
                migration_provenance=dict(original_artifact=entry['artifact'], original_artifacts=entry['artifacts'],
                                          original_created_at=entry['created_at'], content_changed=False))
            migrated = store.publish(patient, module, entry['version'], artifacts, metadata, validated=True, legacy=latest)
            if module_hash(migrated) != module_hash(entry):
                raise RuntimeError('Migration changed review fingerprint')
            report[patient][module] = migrated['version']
    return report


if __name__ == '__main__':
    print(json.dumps(migrate(), indent=2))
