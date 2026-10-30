"""Shared single-patient module runner; Streamlit calls this API, never a batch CLI."""
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from .workbench_store import WorkbenchStore, ROOT, PILOTS, EDITABLE, atomic, read, sha, now, file_lock


def implementation_hashes(module):
    names = ['workbench_runner.py', 'workbench_worker.py', 'workbench_store.py', 'module_artifacts.py']
    names += ['extraction/diagnosis_events_v4.py', 'extraction/diagnosis_identity_v3.py', 'diagnosis_v4_regression.py'] if module == 'diagnoses' else [
        'extraction/medication_identity_v3.py']
    folder = Path(__file__).parent
    return {str(folder / name): sha(folder / name) for name in names}


def configure_sources(store, patient, folder, pdf_root=ROOT / 'pdf_db'):
    """Copy only the five required saved inputs into a content-addressed source snapshot."""
    folder = Path(folder)
    bundle = read(folder / 'source_bundle.json')
    if str(bundle.get('patient_id')) != str(patient):
        raise ValueError('Source patient mismatch')
    names = ['source_bundle.json', 'source_mentions.json', 'medications.csv', 'encounters.json', 'patient_summary.json']
    source_hashes = {name: sha(folder / name) for name in names}
    digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()[:20]
    target = store.patient_dir(patient) / 'sources' / digest
    pdf_hashes = {}
    for doc in bundle['documents']:
        filename = doc['filename']
        if Path(filename).name != filename:
            raise ValueError('Unsafe source PDF name')
        path = (pdf_root / patient / filename).resolve()
        if not path.is_relative_to(pdf_root.resolve()):
            raise ValueError('Source PDF escapes local PDF root')
        actual = sha(path)
        if doc.get('sha256') != actual:
            raise ValueError('Saved source bundle does not match current PDF bytes; upstream parsing required')
        pdf_hashes[str(path)] = actual
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = target / name
        if path.exists():
            if sha(path) != source_hashes[name]:
                raise ValueError('Stable saved-source snapshot was modified')
        else:
            with path.open('xb') as out:
                out.write((folder / name).read_bytes())
                out.flush()
                os.fsync(out.fileno())
    with file_lock(store.patient_dir(patient) / '.manifest.lock'):
        manifest = store.manifest(patient)
        manifest['source_context'] = dict(directory=str(target), original_directory=str(folder),
            input_hashes={str(target / name): value for name, value in source_hashes.items()},
            source_pdf_hashes=pdf_hashes, parser_version='SAVED_SOURCE_BUNDLE_V1', python_version=platform.python_version())
        atomic(store.patient_dir(patient) / 'module_manifest.json', manifest)
    return manifest['source_context']


def run_module(patient_id, module_name, *, store=None, expected_active=None):
    """Run exactly one local assembler, publishing CANDIDATE regardless of passing gates."""
    store = store or WorkbenchStore()
    patient = str(patient_id)
    if patient not in PILOTS or module_name not in EDITABLE:
        raise ValueError('Only one of the five pilot patients and one supported module may run')
    # A patient/module lock serializes concurrent rerun submissions without blocking other modules.
    with file_lock(store.patient_dir(patient) / f'.{module_name}.run.lock'):
        manifest = store.manifest(patient)
        group = manifest['modules'].get(module_name, {})
        active = group.get('active_validated_version')
        if active != expected_active:
            raise ValueError('Current active version changed; confirm the run again')
        source = manifest.get('source_context')
        if not source:
            raise ValueError('Migrate pilot sources before running modules')
        for path, expected in {**source['input_hashes'], **source['source_pdf_hashes']}.items():
            if sha(path) != expected:
                raise ValueError('Saved inputs or source PDFs changed; rerun aborted')
        codes = implementation_hashes(module_name)
        references = {}
        if module_name == 'medications':
            from .extraction.medication_identity_v3 import DICTIONARY, ALIASES
            references = {str(p): sha(p) for p in (DICTIONARY, ALIASES)}
        run_id = now().replace(':', '').replace('+', '_') + '-' + uuid.uuid4().hex[:8]
        stage = store.patient_dir(patient) / '.runs' / run_id
        stage.mkdir(parents=True, exist_ok=False)
        request = dict(patient_id=patient, module=module_name, source_dir=source['directory'],
                       input_hashes={**source['input_hashes'], **references})
        atomic(stage / 'request.json', request)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT / 'src'))
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            process = subprocess.run([sys.executable, '-B', '-m', 'caa_pdf_extractor.workbench_worker', str(stage / 'request.json')],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, creationflags=flags)
            if process.returncode or not (stage / 'result.json').exists():
                raise RuntimeError('Offline module worker failed to produce a result')
            result = read(stage / 'result.json')
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            result = dict(artifacts={}, hard_gates={}, fatal_errors=[str(exc)], patients_processed=[patient], modules_executed=[module_name])
        protected = {**source['input_hashes'], **source['source_pdf_hashes'], **references, **codes}
        if any(sha(path) != expected for path, expected in protected.items()):
            result['fatal_errors'].append('Protected inputs or code changed during extraction')
        if result.get('patients_processed') != [patient] or result.get('modules_executed') != [module_name]:
            raise RuntimeError('Single-patient execution boundary violation')
        if not result['artifacts']:
            result['artifacts'] = {'extraction_failure.json': json.dumps({'fatal_errors': result['fatal_errors']})}
        if module_name == 'diagnoses':
            from .extraction.diagnosis_events_v4 import VERSION
        else:
            from .extraction.medication_identity_v3 import VERSION
        algorithm = result.get('module_version', VERSION)
        revision = algorithm + '+' + run_id
        metadata = dict(module_version=algorithm, source_pdf_hashes=source['source_pdf_hashes'],
            source_input_hashes=source['input_hashes'], parser_version=source['parser_version'],
            upstream_module_versions={'saved_source': Path(source['directory']).name},
            implementation_hashes=codes, code_hash=hashlib.sha256(json.dumps(codes, sort_keys=True).encode()).hexdigest(),
            git_commit=None, prompt_hashes={}, reference_hashes=references,
            validation_test_results={'hard_gates': result['hard_gates']}, fatal_errors=result['fatal_errors'],
            execution=dict(patient_ids=[patient], modules=[module_name], network_calls=0, database_writes=0),
            execution_record=str(stage), based_on_active_version=active,
            source_scope='Existing saved parsed inputs, checked against source PDF hashes. No OCR or upstream extraction rerun.')
        return store.publish(patient, module_name, revision, result['artifacts'], metadata)
