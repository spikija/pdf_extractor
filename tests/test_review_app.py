import hashlib
import json
from pathlib import Path

import fitz
import pytest

from review_app.artifact_repository import select_modules, module_hash, discover
from review_app.display_models import diagnosis_entities, medication_entities
from review_app.review_repository import ReviewRepository, overlay
from review_app.pdf_utils import documents, resolve_source, render_page


def artifact(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding='utf-8')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def entry(file, version='4.0', created='2026-01-01', status='VALIDATED'):
    return dict(artifact=file['path'], artifacts=[file], version=version, created_at=created, status=status)


def test_latest_validated_and_invalidation(tmp_path):
    old = entry(artifact(tmp_path, 'old.json', []))
    new = entry(artifact(tmp_path, 'new.json', []), created='2026-02-01')
    paths = []
    for i, e in enumerate([old, new]):
        p = tmp_path / f'm{i}.json'
        p.write_text(json.dumps({'diagnoses': e}))
        paths.append(p)
    assert select_modules(paths)[0]['diagnoses'] == new
    Path(new['artifact']).write_text('[1]')
    selected, _, errors = select_modules(paths)
    assert selected['diagnoses'] == old and errors
    p = tmp_path / 'm2.json'
    p.write_text(json.dumps({'diagnoses': dict(old, status='STALE_DEPENDENCY', created_at='2026-03-01')}))
    assert 'diagnoses' not in select_modules(paths + [p])[0]


def test_review_roundtrip_overlay_stale_audit_reset_and_proposal(tmp_path):
    repo = ReviewRepository(tmp_path / 'review')
    raw = {'entity_id': 'd1', 'canonical_name': 'OLD'}
    r = repo.save('123', 'diagnoses', 'd1', 'EDIT', raw, {'canonical_name': 'NEW'}, '4', 'hash1')
    assert repo.load('123', 'diagnoses')['d1'] == r
    assert overlay(raw, r, 'hash1')['canonical_name'] == 'NEW'
    stale = overlay(raw, r, 'hash2')
    assert stale['canonical_name'] == 'OLD' and stale['physician_status'] == 'STALE_REVIEW'
    assert raw['canonical_name'] == 'OLD'
    repo.save('123', 'diagnoses', 'd1', 'RESET', raw, None, '4', 'hash1')
    assert overlay(raw, repo.load('123', 'diagnoses')['d1'], 'hash1')['physician_status'] == 'NOT_REVIEWED'
    logs = [json.loads(line) for line in (repo.root / 'review_audit.jsonl').read_text().splitlines()]
    assert logs[1]['previous_value'] == r
    # Authoritative audit recovers a missing projection after interruption.
    (repo.root / '123' / 'diagnoses_review.json').unlink()
    assert repo.load('123', 'diagnoses')['d1']['action'] == 'RESET'
    repo.save('123', 'medications', 'm1', 'EDIT', {}, {'active_ingredients': ['X']}, '3', 'h', suggest=True)
    assert json.loads((repo.root / 'medication_dictionary_suggestions.jsonl').read_text())['physician_value']['active_ingredients'] == ['X']
    with pytest.raises(ValueError):
        repo.save('../escape', 'diagnoses', 'd', 'ACCEPT', {}, None, '4', 'h')


def test_grouping_models(tmp_path):
    file = artifact(tmp_path, 'canonical_disease_identities_v4.json', [
        {'diagnosis_identity_id': 'a', 'canonical_name': 'AF', 'mention_count': 6},
        {'diagnosis_identity_id': 'a', 'canonical_name': 'AF', 'mention_count': 6}])
    assert len(diagnosis_entities(entry(file))) == 1
    event = artifact(tmp_path, 'canonical_clinical_events_v4.json', [
        {'event_id': 's1', 'event_type': 'ISCHEMIC_STROKE', 'event_date': '2006'},
        {'event_id': 's2', 'event_type': 'ISCHEMIC_STROKE', 'event_date': '2015'}])
    assert len(diagnosis_entities(entry(event), events=True)) == 2
    files = [artifact(tmp_path, 'canonical_medications_v3.json', [{'drug_identity_id': 'm', 'canonical_drug': 'ROSUVASTATIN'}]),
             artifact(tmp_path, 'medication_mentions_v3.json', [{'mention_id': 'a', 'normalized_trade_name': 'CRESTOR'}, {'mention_id': 'b', 'normalized_trade_name': 'ROSUVALAN'}]),
             artifact(tmp_path, 'medication_exposures_v3.json', [{'exposure_id': 'e1', 'mention_id': 'a', 'drug_identity_id': 'm'}, {'exposure_id': 'e2', 'mention_id': 'b', 'drug_identity_id': 'm'}, {'exposure_id': 'e3', 'mention_id': 'unknown', 'drug_identity_id': None}])]
    groups = medication_entities(dict(artifacts=files))
    assert len(groups) == 2 and len(groups[0]['exposures']) == 2
    assert groups[1]['canonical_drug'] == 'UNRESOLVED'


def test_pdf_resolution_and_render(tmp_path):
    folder = tmp_path / '123'
    folder.mkdir()
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.save(folder / '1.pdf')
    docs = documents('123', [{'filename': '1.pdf', 'source_document_id': 42}], tmp_path)
    path, page = resolve_source(docs, 42, '2')
    assert page == 2 and render_page(path, 2, Path(path).stat().st_mtime_ns).startswith(b'\x89PNG')
    with pytest.raises(ValueError):
        resolve_source(docs, 43, 1)
    with pytest.raises(ValueError):
        resolve_source(docs, 42, 3)


def test_pilot_artifacts_and_v4_regressions():
    index = discover()
    pilots = ['2435608415', '2459345815', '2552817617', '2684469520', '2826947422']
    if not all(p in index for p in pilots):
        pytest.skip('Local patient artifacts unavailable')
    for pid in pilots:
        selected, _, _ = select_modules(index[pid])
        assert 'medications' in selected and 'diagnoses' in selected
        assert medication_entities(selected['medications'])
    selected, _, _ = select_modules(index['2459345815'])
    diseases = diagnosis_entities(selected['diagnoses'])
    names = {d['canonical_name'] for d in diseases}
    assert {'ATRIAL_FIBRILLATION', 'ARTERIAL_HYPERTENSION'} <= names
    assert len(diseases) == len(names)
    events = diagnosis_entities(selected['events'], True)
    assert any(e['event_type'] == 'ISCHEMIC_STROKE' and str(e['event_date']).startswith('2015') and e['side'] == 'RIGHT' and e['territory'] == 'MCA' for e in events)
    selected, _, _ = select_modules(index['2684469520'])
    diseases = diagnosis_entities(selected['diagnoses'])
    assert len(diseases) < 100
    assert all(d.get('diagnosis_identity_id') and d.get('canonical_name') for d in diseases)
    assert not any(d.get('assertion_status') == 'NEGATED' for d in diseases)


def test_review_has_no_database_imports():
    import ast
    root = Path(__file__).resolve().parents[1] / 'review_app'
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            assert not any(any(term in n for term in ('sqlalchemy', 'psycopg', 'requests', 'httpx')) for n in names)
            assert not any(n.startswith('caa_pdf_extractor') and not n.startswith('caa_pdf_extractor.workbench_') for n in names)
