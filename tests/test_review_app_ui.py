"""Local integration smoke test; never writes real adjudication into data/review/."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest

from review_app.artifact_repository import discover, select_modules
from review_app.display_models import diagnosis_entities, evidence_for
from review_app.review_repository import ReviewRepository
from review_app.config import ROOT


def test_five_patient_ui_navigation_and_persistence(monkeypatch):
    pytest.importorskip('streamlit')
    from streamlit.testing.v1 import AppTest
    index = discover()
    patients = ['2435608415', '2459345815', '2552817617', '2684469520', '2826947422']
    if not all(pid in index for pid in patients):
        pytest.skip('Local pilot artifacts unavailable')
    output = ROOT / 'data' / 'debug' / 'review_app' / ('smoke_' + uuid.uuid4().hex)
    output.mkdir(parents=True)
    original_init = ReviewRepository.__init__
    def isolated_init(self, root=output / 'review'):
        original_init(self, root)
    monkeypatch.setattr(ReviewRepository, '__init__', isolated_init)
    files = set()
    for pid in patients:
        selected, _, _ = select_modules(index[pid])
        files.update(Path(f['path']) for e in selected.values() for f in e['artifacts'])
        files.update((ROOT / 'pdf_db' / pid).glob('*.pdf'))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    app = str(ROOT / 'review_app' / 'app.py')
    at = AppTest.from_file(app, default_timeout=90).run()
    assert not at.exception
    assert len(next(s for s in at.selectbox if s.label == 'Patient ID').options) >= 5
    for patient in patients:
        next(s for s in at.selectbox if s.label == 'Patient ID').select(patient).run()
        assert not at.exception, [e.message for e in at.exception]
        assert any(s.label == 'Document' for s in at.selectbox)
        assert len(at.dataframe) >= 4
    patient = '2459345815'
    next(s for s in at.selectbox if s.label == 'Patient ID').select(patient).run()
    selected, _, _ = select_modules(index[patient])
    raw = diagnosis_entities(selected['diagnoses'])[0]
    evidence = evidence_for(raw, selected['diagnoses'])[0]
    expected_page = int(evidence['page'])
    next(b for b in at.button if b.label == 'Open source').click().run()
    assert not at.exception
    assert at.session_state[f'{patient}:page'] == expected_page
    assert at.session_state[f'{patient}:active'] == raw['entity_id']
    path = at.session_state[f'{patient}:document']
    # Verify selected path is the exact source bundle mapping, not just any PDF.
    from review_app.patient_repository import patient_context
    context = patient_context(select_modules(index[patient])[1])
    expected = next(d['filename'] for d in context['documents'] if str(d['source_document_id']) == str(evidence['document_id']))
    assert Path(path).name == expected
    next(b for b in at.button if b.label == 'Save review').click().run()
    assert not at.exception
    repo = ReviewRepository()
    assert repo.load(patient, 'diagnoses')[raw['entity_id']]['action'] == 'ACCEPT'
    reloaded = AppTest.from_file(app, default_timeout=90).run()
    next(s for s in reloaded.selectbox if s.label == 'Patient ID').select(patient).run()
    assert not reloaded.exception
    assert any('ACCEPT' in option for s in reloaded.selectbox if s.label == 'Select item to review' for option in s.options)
    # Medication editing is a group overlay; dictionary proposals stay separate.
    from review_app.display_models import medication_entities
    medication = medication_entities(selected['medications'])[0]
    action = next(s for s in reloaded.selectbox if str(s.key).startswith(f'{patient}:medications:') and str(s.key).endswith(':action'))
    action.select('EDIT')
    ingredient = next(t for t in reloaded.text_input if str(t.key).startswith(f'{patient}:medications:') and str(t.key).endswith(':edit:active_ingredients'))
    ingredient.set_value('["SMOKE_TEST_INGREDIENT"]')
    next(c for c in reloaded.checkbox if 'Suggest this mapping' in c.label).check()
    next(b for b in reloaded.button if b.label == 'Save review' and ':medications:' in str(b.key)).click().run()
    assert not reloaded.exception
    med_review = repo.load(patient, 'medications')[medication['entity_id']]
    assert med_review['physician_value']['active_ingredients'] == ['SMOKE_TEST_INGREDIENT']
    assert (repo.root / 'medication_dictionary_suggestions.jsonl').exists()
    # A review from different extraction bytes must be visible as stale, with no old edit applied.
    repo.save(patient, 'diagnoses', raw['entity_id'], 'EDIT', raw,
              {'canonical_name': 'DO_NOT_APPLY_STALE'}, '4.0', 'obsolete-hash')
    reloaded.run()
    assert not reloaded.exception
    assert any('Extraction changed since this review' in w.value for w in reloaded.warning)
    assert not any('DO_NOT_APPLY_STALE' in option for s in reloaded.selectbox if s.label == 'Select item to review' for option in s.options)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert before == after
    (output / 'smoke_report.json').write_text(json.dumps(dict(
        completed_at=datetime.now(timezone.utc).isoformat(), patients_tested=len(patients),
        patient_selection=True, local_pdf_render=True, canonical_diseases=True, clinical_events=True,
        medications=True, medication_edit=True, dictionary_proposal=True, stale_review=True,
        source_navigation=True, save_reload=True, raw_hashes_unchanged=True,
        checked_source_files=len(before), screenshot=False), indent=2), encoding='utf-8')
