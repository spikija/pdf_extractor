"""Synthetic case_v1 facts, evidence, artifacts and commit safety regressions."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from caa_pdf_extractor.extraction import case
from caa_pdf_extractor.extraction.case_rules import *
from caa_pdf_extractor.extraction.case_resolver import CaseResolutionError
from caa_pdf_extractor.database.models import ExtractionRun
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection


def bundle(text):
    section = PreparedSection('s1', '123', 1, 'Anamnese', 1, 1, text, text)
    return PatientDocumentBundle('123', [PreparedDocument(1, 'test.pdf', '123/test.pdf', 'hash', 'unknown', None, [section])])


def facts(text):
    return extract(select_inputs(bundle(text)))


@pytest.mark.parametrize('text,expected', [
    ('geb. 05.02.1949', {'date_of_birth': '1949-02-05'}),
    ('geb.: 05.02.1949', {'date_of_birth': '1949-02-05'}),
    ('Geburtsdatum: 05.02.1949', {'date_of_birth': '1949-02-05'}),
    ('76 Jahre alt', {}),
    ('Geschlecht: weiblich', {'gender': 'woman'}),
    ('Geschlecht: männlich', {'gender': 'man'}),
    ('Anna Maria', {}),
    ('Bildungsjahre: 12', {'years_of_education': 12}),
    ('Bildungsjahre: 12.5', {}),
    ('Bildungsjahre: 10-12', {}),
    ('12 Jahre Schulbildung', {'years_of_education': 12}),
    ('Anamnese unauffällig', {}),
    ('Beruf: Professor', {}),
    ('education level: university', {'education_level': 'university'}),
    ('employment status: retirement', {'employment_status': 'retirement'}),
    ('Raucher', {'smoking': 'active'}),
    ('aktiver Raucher', {'smoking': 'active'}),
    ('Ex-Raucher', {'smoking': 'former'}),
    ('Nikotinabusus früher', {'smoking': 'former'}),
    ('Nichtraucher', {}),  # Current non-smoking does not establish lifetime never.
    ('never smoked', {'smoking': 'never'}),
    ('nie geraucht', {'smoking': 'never'}),
    ('30 pack-years', {'pack_years': 30}),
    ('12.5 pack-years', {}),
    ('pack-years: 12.5', {}),
    ('pack-years: 10-20', {}),
    ('pack-years: 20', {'pack_years': 20}),
    ('20 Zigaretten/Tag seit 30 Jahren', {}),
    ('Alkohol: aktiv', {'alcohol': 'active'}),
    ('Alkohol: nie', {'alcohol': 'never'}),
    ('Alkohol: unbekannt', {'alcohol': 'unknown'}),
    ('Leberzirrhose, Gamma-GT erhöht', {}),
    ('APOE E3E4', {'apoe': 'E3E4'}),
    ('APOE: E4/E3', {'apoe': 'E3E4'}),
    ('APOE pending', {'apoe': 'pending'}),
    ('APOE not done', {'apoe': 'not done'}),
    ('APOE nicht durchgeführt', {'apoe': 'not done'}),
    ('CAA und familiäre Demenz', {}),
    ('Genetik: APP negativ', {'genes': 'APP negativ'}),
    ('probable CAA according to Boston 2.0', {'caa_criteria': 'Boston Criteria 2.0', 'caa_criteria_stage': 'probable'}),
    ('possible CAA according to Boston 1.5', {'caa_criteria': 'Boston Criteria 1.5', 'caa_criteria_stage': 'possible'}),
    ('Verdacht auf Amyloidangiopathie', {}),
    ('MRI 05.02.2020: Verdacht auf Amyloidangiopathie', {}),
    ('CAA-Diagnosedatum: 05.02.2020', {'caa_diagnosis_date': '2020-02-05'}),
    ('verstorben am 05.02.2020', {'death_date': '2020-02-05'}),
    ('Todesursache: Pneumonie', {'death_cause': 'Pneumonie'}),
    ('Autopsie durchgeführt', {'autopsy': 'done'}),
    ('Autopsie durchgeführt mit Neuropathologie', {'autopsy': 'done with neuropathology'}),
    ('Autopsie: not done', {'autopsy': 'not done'}),
    ('Neuropathologie: APP positiv', {}),
    ('Kein Hinweis auf probable CAA', {}),
    ('Mutter: Raucher', {}),
    ('kein Raucher', {}),
    ('Autopsie geplant', {}),
    ('APOE E3E4 nicht nachgewiesen', {}),
    ('Autopsie nicht durchgeführt', {}),
])
def test_explicit_facts(text, expected):
    output = facts(text)
    assert output['update_fields'] == expected
    for field, evidence in output['provenance'].items():
        assert evidence
        assert evidence[0]['evidence'] in text
        assert evidence[0]['page'] == 1
        assert evidence[0]['section_id'] == 's1'
        assert evidence[0]['source_document_id'] == 1
        validate_value(field, expected[field])


def test_source_conflicts():
    result = facts('geb. 05.02.1949\ngeb. 06.02.1949')
    assert result['update_fields'] == {}
    assert result['source_conflicts'][0]['reason'] == 'SOURCE_CONFLICT'


def test_only_known_fields():
    result = facts('geb. 05.02.1949')
    assert set(result['update_fields']) == {'date_of_birth'}
    assert 'family_history' not in result['update_fields']


@pytest.mark.parametrize('current,value,expected', [(None, 12, 'update'), (12, 12, 'same'), (13, 12, 'conflict'), (0, 12, 'conflict')])
def test_existing_values(current, value, expected):
    updates, same, conflicts = conflict_check({'years_of_education': current}, {'years_of_education': value})
    assert bool(updates) == (expected == 'update')
    assert bool(same) == (expected == 'same')
    assert bool(conflicts) == (expected == 'conflict')


def test_invalid_patch():
    for field, value in [('fallnr', '123'), ('gender', ''), ('pack_years', True), ('date_of_birth', None)]:
        with pytest.raises(ValueError):
            validate_value(field, value)


def test_multi_page_no_guessed_page():
    b = bundle('geb. 05.02.1949')
    b.documents[0].sections[0].page_end = 2
    assert select_inputs(b) == []


class Harness:
    def __init__(self):
        self.row = SimpleNamespace(id=1, entry_stamp=datetime(2020, 1, 1, tzinfo=timezone.utc), family_history=False,
                                   **{f: None for f in FIELDS})
        self.runs = {}
        self.session = MagicMock()
        self.factory = MagicMock()
        self.factory.begin.return_value.__enter__.return_value = self.session
        self.session.scalar.return_value = None
        self.session.add.side_effect = self.add
        self.session.get.side_effect = lambda model, run_id: self.runs.get(run_id)
    def add(self, run):
        if isinstance(run, ExtractionRun):
            run.id = len(self.runs) + 1
            self.runs[run.id] = run
    def read(self, *args):
        return self.row, {f: case.serial(getattr(self.row, f)) for f in FIELDS}
    def run(self, root, dry_run=True, text='geb. 05.02.1949'):
        with patch.object(case, 'read_case', side_effect=self.read), patch.object(case, 'build_patient_bundle', return_value=bundle(text)):
            return case.extract_patient('123', dry_run=dry_run, session_factory=self.factory, debug_root=root,
                                        client=SimpleNamespace(generate_json=lambda *a: pytest.fail('Granite called')))


def test_commit_and_idempotency(tmp_path):
    h = Harness()
    dry = h.run(tmp_path)
    assert dry['validation_status'] == 'VERIFIED'
    assert h.row.date_of_birth is None
    first = h.run(tmp_path, False)
    assert first['status'] == 'SUCCESS'
    assert first['fields_changed'] == 1
    second = h.run(tmp_path, False)
    assert second['status'] == 'SUCCESS'
    assert second['fields_changed'] == 0
    assert second['granite_called'] is False


def test_stale_artifact(tmp_path):
    h = Harness()
    h.run(tmp_path)
    result = h.run(tmp_path, False, 'geb. 06.02.1949')
    assert result['status'] == 'VERIFIED_EXTRACTION_STALE'
    assert h.row.date_of_birth is None


def test_commit_conflict_blocks_all_fields(tmp_path):
    h = Harness()
    h.run(tmp_path, text='geb. 05.02.1949\nGeschlecht: woman')
    h.row.gender = 'man'
    result = h.run(tmp_path, False, text='geb. 05.02.1949\nGeschlecht: woman')
    assert result['status'] == 'FIELD_CONFLICT'
    assert h.row.date_of_birth is None
    assert h.row.gender == 'man'


def test_missing_artifact(tmp_path):
    assert Harness().run(tmp_path, False)['status'] == 'NO_VERIFIED_EXTRACTION'


def test_tampered_artifact(tmp_path):
    h = Harness()
    result = h.run(tmp_path)
    path = Path(result['verified_artifact'])
    data = json.loads(path.read_text())
    data['update_fields']['gender'] = 'man'
    path.write_text(json.dumps(data))
    assert h.run(tmp_path, False)['status'] == 'VERIFIED_EXTRACTION_INVALID'


def test_missing_audit(tmp_path):
    h = Harness()
    h.run(tmp_path)
    h.runs.clear()
    assert h.run(tmp_path, False)['status'] == 'VERIFIED_EXTRACTION_INVALID'


def test_case_not_found(tmp_path):
    with patch.object(case, 'read_case', side_effect=CaseResolutionError('CASE_NOT_FOUND')):
        result = case.extract_patient('123', session_factory=Harness().factory, debug_root=tmp_path)
    assert result['status'] == 'CASE_NOT_FOUND'


def test_defaults_require_review(tmp_path):
    h = Harness()
    h.row.years_of_education = 0
    result = h.run(tmp_path, text='Bildungsjahre: 12')
    assert result['validation_status'] == 'REVIEW_REQUIRED'
    assert result['suspicious_legacy_defaults']['years_of_education'] == 0
    assert h.row.years_of_education == 0
