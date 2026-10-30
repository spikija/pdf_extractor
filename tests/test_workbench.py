import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from caa_pdf_extractor.workbench_store import WorkbenchStore, read, sha, integrity, fingerprint, active_entries
from caa_pdf_extractor.workbench_runner import run_module, configure_sources
from caa_pdf_extractor.workbench_comparison import compare
from review_app.artifact_repository import select_modules, module_hash
from review_app.review_repository import ReviewRepository, overlay

PATIENT = '2435608415'


def meta(version='4.0', gates=None):
    return dict(module_version=version, source_pdf_hashes={}, parser_version='saved', upstream_module_versions={},
                code_hash='test-code', prompt_hashes={}, validation_test_results={'hard_gates': gates or {'ok': True}}, fatal_errors=[])


def payload(name='AF', date='2020-01-01'):
    return {'canonical_disease_identities.json': json.dumps([dict(diagnosis_identity_id='dx1', canonical_name=name,
             first_documented_date=date, review_required=True)]),
            'canonical_clinical_events.json': json.dumps([dict(event_id='ev1', event_type='ISCHEMIC_STROKE',
             event_date='2006', side='RIGHT', territory='MCA', review_required=True)])}


def adjudications(entry):
    from caa_pdf_extractor.workbench_store import candidate_entities
    return {r['entity_id']: {'action': 'ACCEPT', 'source_artifact_hash': fingerprint(entry)} for r in candidate_entities(entry, 'diagnoses')}


def test_stable_pointer_candidate_promotion_rejection_and_invalidation(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    downstream = {name: dict(module=name, version='legacy', status='EXTRACTED', artifact='', artifacts=[], created_at='2020')
                  for name in ('CAA', 'CHA2DS2_VA', 'ORBIT', 'OAC_decision_analysis')}
    first = store.publish(PATIENT, 'diagnoses', '4.0', payload(), meta(), validated=True, legacy=downstream)
    assert integrity(first)
    candidate = store.publish(PATIENT, 'diagnoses', '4.0+run1', payload(date='2021-01-01'), meta())
    assert store.manifest(PATIENT)['modules']['diagnoses']['active_validated_version'] == '4.0'
    selected, _, errors = select_modules([store.patient_dir(PATIENT) / 'module_manifest.json'])
    assert not errors and selected['diagnoses']['version'] == '4.0'
    with pytest.raises(ValueError, match='confirmation'):
        store.promote(PATIENT, 'diagnoses', candidate['version'], expected_active='4.0')
    with pytest.raises(ValueError, match='adjudication'):
        store.promote(PATIENT, 'diagnoses', candidate['version'], confirmed=True, expected_active='4.0')
    with pytest.raises(ValueError, match='changed'):
        store.promote(PATIENT, 'diagnoses', candidate['version'], confirmed=True, expected_active='wrong')
    store.promote(PATIENT, 'diagnoses', candidate['version'], confirmed=True, reviews=adjudications(candidate), expected_active='4.0')
    manifest = store.manifest(PATIENT)
    assert manifest['modules']['diagnoses']['active_validated_version'] == candidate['version']
    assert manifest['modules']['events']['active_validated_version'] == candidate['version']
    assert manifest['modules']['diagnoses']['versions']['4.0']['status'] == 'VALIDATED'
    assert all(manifest['modules'][m]['versions']['legacy']['status'] == 'STALE_DEPENDENCY' for m in downstream)
    assert integrity(first) and integrity(candidate)
    rejected = store.publish(PATIENT, 'diagnoses', '4.0+run2', payload(), meta())
    store.reject(PATIENT, 'diagnoses', rejected['version'])
    assert store.versions(PATIENT, 'diagnoses')[rejected['version']]['status'] == 'REJECTED'
    assert integrity(rejected)
    assert [e['action'] for e in store.manifest(PATIENT)['audit']] == ['MIGRATE', 'RUN', 'PROMOTE', 'RUN', 'REJECT']
    log = store.root / 'module_run_audit.jsonl'
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(events) == 5 and events[-1]['action'] == 'REJECT'
    log.unlink()
    store.sync_audit()
    assert [json.loads(line) for line in log.read_text().splitlines()] == events


def test_promotion_blocks_failed_gates_hashes_fatal_errors_and_changed_sources(tmp_path):
    for case in ('gates', 'hash', 'fatal', 'source'):
        store = WorkbenchStore(tmp_path / case)
        metadata = meta(gates={'ok': False} if case == 'gates' else None)
        if case == 'fatal':
            metadata['fatal_errors'] = ['fatal']
        source = tmp_path / 'input.pdf'
        source.write_bytes(b'pdf')
        metadata['source_pdf_hashes'] = {str(source): sha(source)}
        candidate = store.publish(PATIENT, 'diagnoses', '4.0+run', payload(), metadata)
        records = adjudications(candidate)
        if case == 'hash':
            Path(candidate['artifacts'][0]['path']).write_text('[]')
        if case == 'source':
            source.write_bytes(b'changed')
        with pytest.raises(ValueError):
            store.promote(PATIENT, 'diagnoses', candidate['version'], confirmed=True, reviews=records)
        assert store.manifest(PATIENT)['modules']['diagnoses']['active_validated_version'] is None


def test_medication_activation_invalidates_only_applicable_dependents(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    dependencies = {name: dict(module=name, version='old', status='EXTRACTED', artifact='', artifacts=[], created_at='2020')
                    for name in ('ORBIT', 'OAC_decision_analysis', 'antithrombotic_exposure_analysis', 'CAA')}
    files = {'canonical_medications.json': '[]', 'medication_exposures.json': '[]'}
    store.publish(PATIENT, 'medications', '3.0', files, meta('3.0'), validated=True, legacy=dependencies)
    candidate = store.publish(PATIENT, 'medications', '3.0+run', files, meta('3.0'))
    store.promote(PATIENT, 'medications', candidate['version'], confirmed=True, expected_active='3.0')
    modules = store.manifest(PATIENT)['modules']
    assert all(modules[name]['versions']['old']['status'] == 'STALE_DEPENDENCY' for name in dependencies if name != 'CAA')
    assert modules['CAA']['versions']['old']['status'] == 'EXTRACTED'


def test_stable_integrity_failure_never_falls_back_to_debug(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    entry = store.publish(PATIENT, 'diagnoses', '4.0', payload(), meta(), validated=True)
    debug = tmp_path / 'debug_manifest.json'
    debug.write_text(json.dumps({'diagnoses': entry}))
    Path(entry['artifacts'][0]['path']).write_text('[]')
    selected, latest, errors = select_modules([debug, store.patient_dir(PATIENT) / 'module_manifest.json'])
    assert 'diagnoses' not in selected and errors


def test_semantic_comparison_and_stale_current_review(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    old = store.publish(PATIENT, 'diagnoses', '4.0', payload(), meta(), validated=True)
    new = store.publish(PATIENT, 'diagnoses', '4.0+run', payload(date='2021-01-01'), meta())
    changes = compare(old, new, 'diagnoses')
    assert {c['change'] for c in changes} == {'CHANGED', 'UNCHANGED'}
    assert changes[0]['changes']['first_documented_date']['candidate'] == '2021-01-01'
    repo = ReviewRepository(tmp_path / 'review')
    r = repo.save(PATIENT, 'diagnoses', 'dx1', 'EDIT', {'canonical_name': 'AF'}, {'canonical_name': 'OLD_EDIT'}, '4', module_hash(old))
    assert overlay({'canonical_name': 'AF'}, r, module_hash(new))['physician_status'] == 'STALE_REVIEW'
    assert overlay({'canonical_name': 'AF'}, r, module_hash(new))['canonical_name'] == 'AF'
    candidate_repo = ReviewRepository(repo.root / 'candidates' / new['version'])
    assert candidate_repo.load(PATIENT, 'diagnoses') == {}
    before = sha(repo.root / PATIENT / 'diagnoses_review.json')
    candidate_repo.save(PATIENT, 'diagnoses', 'dx1', 'ACCEPT', {}, None, new['version'], fingerprint(new))
    assert sha(repo.root / PATIENT / 'diagnoses_review.json') == before


def synthetic_sources(tmp_path, patient=PATIENT):
    source, pdfs = tmp_path / 'source', tmp_path / 'pdfs'
    source.mkdir()
    pdf = pdfs / patient / '1.pdf'
    pdf.parent.mkdir(parents=True)
    import fitz
    with fitz.open() as doc:
        doc.new_page().insert_text((40, 40), 'Synthetic workbench test source')
        doc.save(pdf)
    bundle = dict(patient_id=patient, documents=[dict(source_document_id=1, filename='1.pdf', sha256=sha(pdf), document_date='2020-01-01',
         sections=[dict(section_id='doc1_sec1', heading='Diagnosen', text='Vorhofflimmern\nArterielle Hypertonie', page_start=1)])])
    for name, value in [('source_bundle.json', bundle), ('source_mentions.json', {'mentions': [], 'audit_only': []}),
                        ('encounters.json', []), ('patient_summary.json', {})]:
        (source / name).write_text(json.dumps(value), encoding='utf-8')
    (source / 'medications.csv').write_text('patient_id,raw_medication,source_document_id,page,section_id,evidence_id\n' + patient + ',Crestor 10 mg 1 0 0,1,1,doc1_sec2,e1\n', encoding='utf-8')
    return source, pdfs


def test_real_shared_runner_is_single_patient_offline_candidate_only(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    source, pdfs = synthetic_sources(tmp_path)
    context = configure_sources(store, PATIENT, source, pdfs)
    before = {str(p): sha(p) for p in source.iterdir()}
    dx = run_module(PATIENT, 'diagnoses', store=store)
    md = read(dx['metadata_path'])
    assert dx['status'] == 'CANDIDATE' and all(md['validation_test_results']['hard_gates'].values())
    assert md['execution']['patient_ids'] == [PATIENT]
    assert not store.manifest(PATIENT)['modules']['diagnoses']['active_validated_version']
    med = run_module(PATIENT, 'medications', store=store)
    assert med['module_version'] == '3.0'
    assert all(read(med['metadata_path'])['validation_test_results']['hard_gates'].values())
    assert {str(p): sha(p) for p in source.iterdir()} == before
    assert sorted(p.name for p in store.root.iterdir() if p.is_dir()) == [PATIENT]
    with pytest.raises(ValueError, match='one'):
        run_module(['2435608415', '2552817617'], 'diagnoses', store=store)
    with pytest.raises(ValueError):
        run_module('9999999999', 'diagnoses', store=store)
    with pytest.raises(ValueError):
        run_module(PATIENT, 'CAA', store=store)
    with pytest.raises(ValueError, match='Unsafe'):
        store.publish(PATIENT, 'diagnoses', '../../bad', payload(), meta())
    Path(next(iter(context['source_pdf_hashes']))).write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        run_module(PATIENT, 'diagnoses', store=store)


def test_worker_guard_blocks_network_database_and_external_writes(tmp_path):
    source = '''
import socket, sqlite3
from pathlib import Path
from caa_pdf_extractor.workbench_worker import guard
guard(Path(__import__('sys').argv[1]))
checks = [lambda: socket.create_connection(('127.0.0.1', 1)), lambda: sqlite3.connect(':memory:'),
          lambda: Path(__import__('sys').argv[2]).write_text('forbidden')]
for check in checks:
    try: check()
    except RuntimeError: continue
    raise SystemExit('Guard failed')
print('all blocked')
'''
    process = subprocess.run([sys.executable, '-B', '-c', source, str(tmp_path / 'output'), str(tmp_path / 'outside.txt')], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == 'all blocked' and not (tmp_path / 'outside.txt').exists()


def test_medication_semantics_added_removed_and_migration_fingerprint(tmp_path):
    store = WorkbenchStore(tmp_path / 'stable')
    old_artifacts = {
        'canonical_medications.json': json.dumps([{'drug_identity_id': 'm1', 'canonical_drug': 'ROSUVASTATIN', 'active_ingredients': ['rosuvastatin'], 'atc_codes': ['C10AA07']}]),
        'medication_exposures.json': json.dumps([{'exposure_id': 'e1', 'mention_id': 'a', 'drug_identity_id': 'm1', 'dose': '10 mg', 'schedule': '1 0 0'}]),
        'medication_mentions.json': json.dumps([{'mention_id': 'a', 'raw_medication': 'Crestor 10 mg'}])}
    old = store.publish(PATIENT, 'medications', '3.0', old_artifacts, meta('3.0'), validated=True)
    new_artifacts = dict(old_artifacts)
    new_artifacts['medication_exposures.json'] = json.dumps([{'exposure_id': 'e1', 'mention_id': 'a', 'drug_identity_id': 'm1', 'dose': '20 mg', 'schedule': '1 0 0'}])
    new = store.publish(PATIENT, 'medications', '3.0+run', new_artifacts, meta('3.0'))
    difference = compare(old, new, 'medications')
    assert difference[0]['change'] == 'CHANGED' and 'exposures' in difference[0]['changes']
    assert compare(None, old, 'medications')[0]['change'] == 'ADDED'
    assert compare(old, None, 'medications')[0]['change'] == 'REMOVED'
    migrated_store = WorkbenchStore(tmp_path / 'migrated')
    copied = migrated_store.publish(PATIENT, 'medications', '3.0',
        {Path(f['path']).name: Path(f['path']).read_bytes() for f in old['artifacts']}, meta('3.0'), validated=True)
    assert fingerprint(copied) == fingerprint(old)
    assert compare(old, copied, 'medications')[0]['change'] == 'UNCHANGED'


def test_ui_confirmation_candidate_review_promotion_and_rejection(tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from caa_pdf_extractor.workbench_store import ROOT, candidate_entities
    from review_app import artifact_repository, pdf_utils
    from review_app.views import workbench
    st.cache_data.clear()

    store = WorkbenchStore(tmp_path / 'stable')
    source, pdfs = synthetic_sources(tmp_path)
    configure_sources(store, PATIENT, source, pdfs)
    store.publish(PATIENT, 'diagnoses', '4.0', payload(), meta(), validated=True)
    initial_med = {'canonical_medications.json': '[]', 'medication_mentions.json': '[]', 'medication_exposures.json': '[]'}
    store.publish(PATIENT, 'medications', '3.0', initial_med, meta('3.0'), validated=True)
    original_discover = artifact_repository.discover
    monkeypatch.setattr(artifact_repository, 'discover', lambda: original_discover((store.root,)))
    monkeypatch.setattr(workbench, 'WorkbenchStore', lambda: store)
    original_docs = pdf_utils.documents
    monkeypatch.setattr(pdf_utils, 'documents', lambda patient, metadata: original_docs(patient, metadata, pdfs))
    original_init = ReviewRepository.__init__
    def isolated(self, root=tmp_path / 'review'):
        original_init(self, root)
    monkeypatch.setattr(ReviewRepository, '__init__', isolated)
    at = AppTest.from_file(str(ROOT / 'review_app/app.py'), default_timeout=60).run()
    assert not at.exception
    for module, original_active in [('diagnoses', '4.0'), ('medications', '3.0')]:
        prefix = f'{PATIENT}:{module}:workbench'
        initial = set(store.versions(PATIENT, module))
        next(b for b in at.button if b.key == prefix + ':request').click().run()
        assert set(store.versions(PATIENT, module)) == initial
        next(b for b in at.button if b.key == prefix + ':confirm').click().run()
        assert not at.exception, [e.message for e in at.exception]
        new = set(store.versions(PATIENT, module)) - initial
        assert len(new) == 1
        revision = new.pop()
        entry = store.versions(PATIENT, module)[revision]
        assert store.manifest(PATIENT)['modules'][module]['active_validated_version'] == original_active
        at.run()
        assert not at.exception
        assert len(store.versions(PATIENT, module)) == len(initial) + 1
        assert next(b for b in at.button if b.key == prefix + ':promote').disabled
        if module == 'diagnoses':
            candidate_repo = ReviewRepository(tmp_path / 'review' / 'candidates' / revision)
            for entity in candidate_entities(entry, module):
                candidate_repo.save(PATIENT, module, entity['entity_id'], 'ACCEPT', entity, None, revision, fingerprint(entry))
            at.run()
            next(c for c in at.checkbox if c.key == prefix + f':approve:{revision}').check().run()
            assert not next(b for b in at.button if b.key == prefix + ':promote').disabled
            next(b for b in at.button if b.key == prefix + ':promote').click().run()
            assert not at.exception, [e.message for e in at.exception]
            assert store.manifest(PATIENT)['modules'][module]['active_validated_version'] == revision
        else:
            next(b for b in at.button if b.key == prefix + ':reject').click().run()
            assert not at.exception
            assert store.versions(PATIENT, module)[revision]['status'] == 'REJECTED'
            assert store.manifest(PATIENT)['modules'][module]['active_validated_version'] == original_active
    st.cache_data.clear()

def test_five_pilot_migration_is_byte_preserving_and_idempotent(tmp_path):
    from caa_pdf_extractor.workbench_store import PILOTS
    from review_app.migrate_pilot import migrate
    debug = tmp_path / 'debug'
    pdf_root = tmp_path / 'pdfs'
    before = {}
    for patient in PILOTS:
        inputs = tmp_path / 'inputs' / patient
        inputs.mkdir(parents=True)
        source, pdfs = synthetic_sources(inputs, patient)
        destination = pdf_root / patient / '1.pdf'
        destination.parent.mkdir(parents=True)
        destination.write_bytes((pdfs / patient / '1.pdf').read_bytes())
        manifest = {}
        for module, name, version in [('diagnoses', 'canonical_diagnoses_v3.json', '3.0'),
                                      ('medications', 'canonical_medications_v3.json', '3.0')]:
            path = source / name
            path.write_text('[]', encoding='utf-8')
            manifest[module] = dict(module=module, version=version, status='VALIDATED', artifact=str(path),
                artifacts=[dict(path=str(path), sha256=sha(path))], created_at='2026-01-01', validation={'old_gate': True})
        path = debug / 'snapshot' / 'patient_artifacts' / patient / 'module_manifest.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(manifest), encoding='utf-8')
        before.update({str(p): sha(p) for p in source.iterdir()})
        before[str(path)] = sha(path)
    store = WorkbenchStore(tmp_path / 'stable')
    first = migrate(store, roots=(debug,), pdf_root=pdf_root)
    second = migrate(store, roots=(debug,), pdf_root=pdf_root)
    assert first == second and set(first) == set(PILOTS)
    assert all(sha(path) == digest for path, digest in before.items())
    for patient in PILOTS:
        manifest = store.manifest(patient)
        assert len(manifest['audit']) == 2
        assert len(store.versions(patient, 'diagnoses')) == 1
