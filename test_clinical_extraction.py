import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from caa_pdf_extractor.extraction.validators import validate_output, deduplicate, parse_json
from caa_pdf_extractor.extraction.clinical_history import select_sections, extract_patient
from caa_pdf_extractor.llm.granite import GraniteClient
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection
from caa_pdf_extractor.extraction.debug_export import run_debug_record, save_dry_run_debug
from caa_pdf_extractor.extraction.case_resolver import resolve_case_id, CaseResolutionError


def source(doc=1):
    text = "Diagnosen\nExample disease (11.12.2015)\n"
    return PreparedSection(f"doc{doc}_sec01", "123", doc, "Diagnosen", 1, 1,
                           text, text, ["clinical_history"])


def output(doc=1):
    return {"patient_id": "123", "clinical_history": [{
        "disease": "Example disease", "date_disease": "2015-12-11", "disease_category": None,
        "source_document_id": doc, "page_number": 1, "section_id": f"doc{doc}_sec01",
        "source_text": "Example disease (11.12.2015)"}]}


def bundle(count=1):
    return PatientDocumentBundle("123", [PreparedDocument(
        i, f"{i}.pdf", f"123/{i}.pdf", str(i), "unknown", None, [source(i)])
        for i in range(1, count + 1)])


class ValidationTests(unittest.TestCase):
    def test_valid_strict_json_and_deduplication(self):
        result = validate_output(json.dumps(output()), "123", [source()])
        self.assertEqual(result.clinical_history[0].date_disease.isoformat(), "2015-12-11")
        self.assertEqual(len(deduplicate(result.clinical_history * 2)), 1)
        altered = result.clinical_history[0].model_copy(update={"disease": "Example disease variant"})
        self.assertEqual(len(deduplicate(result.clinical_history + [altered])), 2)
        different_date = result.clinical_history[0].model_copy(update={"date_disease": None})
        self.assertEqual(len(deduplicate(result.clinical_history + [different_date])), 2)

    def test_reject_unsupported_values(self):
        changes = [{"source_document_id": "1"}, {"source_document_id": True},
                   {"disease_category": "cancer"}, {"page_number": 2},
                   {"section_id": "invented"}, {"source_text": "invented"},
                   {"disease": "invented"}, {"date_disease": "2016-01-01"},
                   {"date_disease": "2015-02-31"}, {"unexpected": 1}]
        for change in changes:
            value = output()
            value["clinical_history"][0].update(change)
            with self.assertRaises(ValueError):
                validate_output(json.dumps(value), "123", [source()])
        with self.assertRaises(ValueError):
            validate_output(json.dumps(output()), "other", [source()])

    def test_reject_commentary_duplicate_keys_and_nonfinite(self):
        for raw in ('```json\n{}\n```', '{"x":1,"x":2}', '{"x":NaN}', '{} commentary'):
            with self.assertRaises(ValueError):
                parse_json(raw)

    def test_local_transport_and_cloud_rejection(self):
        for url in ('https://example.org', 'http://localhost.evil:11434',
                    'http://localhost:11434', 'http://127.0.0.1:11434/path'):
            with self.assertRaises(ValueError):
                GraniteClient(base_url=url)
        client = GraniteClient()
        with patch.object(client, '_request', return_value={"models": [
            {"name": client.model_name, "remote_host": "remote", "details": {"family": "granite"}}]}):
            with self.assertRaises(RuntimeError):
                client.check_model()

    def test_input_selection_excludes_radiology_and_lab(self):
        b = bundle()
        for target in ('radiology', 'laboratory', 'medications'):
            s = source()
            s.heading = target
            s.candidate_targets = [target]
            b.documents[0].sections.append(s)
        self.assertEqual(len(select_sections(b)), 1)


class DebugExportTests(unittest.TestCase):
    def test_unicode_decoded_response_and_unchanged_metadata(self):
        from copy import deepcopy
        raw = {'patient_id': '123', 'dry_run': True, 'section_ids': ['doc1_sec01'],
               'response': json.dumps({'disease': 'Röntgen'}, ensure_ascii=False),
               'normalized_output': [{'disease': 'Röntgen'}], 'clinical_rows_inserted': 0,
               'patient_validation_status': 'SUCCESS', 'custom_metadata': 'preserved'}
        original = deepcopy(raw)
        run = SimpleNamespace(raw_output=raw, id=1, source_document_id=1, model_name='granite',
                              model_version='digest', prompt_version='v1', status='SUCCESS',
                              started_at=None, completed_at=None, validation_errors=None)
        record = run_debug_record(run)
        with tempfile.TemporaryDirectory() as root:
            path = save_dry_run_debug('123', 'v1', [record], status='SUCCESS',
                                     runtime='Ollama', model_name='granite', model_version='digest',
                                     debug_root=Path(root))
            content = path.read_text(encoding='utf-8')
            self.assertIn('Röntgen', content)
            self.assertIn('\n  "patient_id"', content)
            exported = json.loads(content)
        self.assertEqual(raw, original)
        self.assertEqual(exported['granite_response'], {'disease': 'Röntgen'})
        self.assertEqual(exported['runs'][0]['custom_metadata'], 'preserved')

    def test_multiple_documents_and_invalid_response_preserved(self):
        records = []
        for i, response in enumerate(['{"clinical_history": []}', 'invalid JSON'], 1):
            run = SimpleNamespace(raw_output={'response': response, 'section_ids': [f'doc{i}_sec01'],
                                              'normalized_output': []}, id=i, source_document_id=i,
                                  model_name='granite', model_version='digest', prompt_version='v1',
                                  status='FAILED_PARSE', started_at=None, completed_at=None,
                                  validation_errors=[{'type': 'invalid_json'}])
            records.append(run_debug_record(run))
        with tempfile.TemporaryDirectory() as root:
            path = save_dry_run_debug('123', 'v1', records, status='FAILED_PARSE',
                                     runtime='Ollama', model_name='granite', model_version='digest',
                                     debug_root=Path(root))
            exported = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(exported['granite_response'], [{'clinical_history': []}, 'invalid JSON'])
        self.assertEqual(exported['section_ids'], ['doc1_sec01', 'doc2_sec01'])
        self.assertEqual(exported['patient_validation_status'], 'FAILED_PARSE')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.debug_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.debug_temp.cleanup)
        patcher = patch('caa_pdf_extractor.extraction.debug_export.DEBUG_ROOT', Path(self.debug_temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def setup_factory(self):
        from caa_pdf_extractor.database.models import ExtractionRun, Case
        self.runs = {}
        self.added = []
        session = MagicMock()
        def add(row):
            self.added.append(row)
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1
                self.runs[row.id] = row
        session.add.side_effect = add
        session.get.side_effect = lambda cls, key: self.runs[key]
        session.execute.return_value.all.return_value = [(12345,)]
        session.scalars.return_value = []
        factory = MagicMock()
        factory.begin.return_value.__enter__.return_value = session
        factory.return_value.__enter__.return_value = session
        return factory

    def client(self, responses):
        return SimpleNamespace(runtime="fake-local", model_name="123-granite",
                               model_version="test-digest", generate_json=MagicMock(side_effect=responses))

    def test_dry_run_and_success_audit(self):
        from caa_pdf_extractor.database.models import ExtractionRun
        factory = self.setup_factory()
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            result = extract_patient('123', client=self.client([json.dumps(output())]), session_factory=factory)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['diagnosis_count'], 1)
        self.assertEqual(result['inserted'], 0)
        self.assertTrue(all(isinstance(row, ExtractionRun) for row in self.added))
        self.assertNotIn('source_text', result['diagnoses'][0])
        self.assertEqual(self.runs[1].status, 'SUCCESS')
        self.assertEqual(self.runs[1].raw_output['response'], json.dumps(output()))
        exported = json.loads((Path(self.debug_temp.name) / '123' /
                               'clinical_history_clinical_history_v1.json').read_text(encoding='utf-8'))
        self.assertEqual(exported['granite_response'], output())
        self.assertEqual(exported['patient_validation_status'], 'SUCCESS')
        self.assertEqual(exported['section_ids'], ['doc1_sec01'])
        self.assertEqual(exported['runs'][0]['run_id'], 1)
        self.assertEqual(exported['normalized_output'], self.runs[1].raw_output['normalized_output'])

    def test_failures_preserve_raw_and_block_patient_inserts(self):
        from caa_pdf_extractor.database.models import ExtractionRun
        for response, status in [('bad json', 'FAILED_PARSE'), (json.dumps({}), 'FAILED_VALIDATION'),
                                 (RuntimeError('do not log source'), 'FAILED_MODEL')]:
            factory = self.setup_factory()
            with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle(2)):
                result = extract_patient('123', dry_run=False,
                                         client=self.client([json.dumps(output()), response]), session_factory=factory)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['diagnoses'], [])
            self.assertTrue(all(isinstance(row, ExtractionRun) for row in self.added))
            self.assertEqual(self.runs[2].status, status)
            self.assertEqual(self.runs[2].raw_output['response'], None if isinstance(response, Exception) else response)

    def test_validated_write_and_null_missing_dates(self):
        from caa_pdf_extractor.database.models import ClinicalHistory
        factory = self.setup_factory()
        value = output()
        value['clinical_history'][0]['date_disease'] = None
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            result = extract_patient('123', dry_run=False,
                                     client=self.client([json.dumps(value)]), session_factory=factory)
        self.assertEqual(result['inserted'], 1)
        clinical = [row for row in self.added if isinstance(row, ClinicalHistory)]
        self.assertEqual(len(clinical), 1)
        self.assertEqual(clinical[0].case_id, 12345)
        self.assertEqual(str(clinical[0].date_disease), 'NULL')
        self.assertFalse(list(Path(self.debug_temp.name).rglob('*.json')))

    def test_existing_diagnosis_is_not_reinserted(self):
        from datetime import date
        from caa_pdf_extractor.database.models import ClinicalHistory
        factory = self.setup_factory()
        session = factory.begin.return_value.__enter__.return_value
        session.scalars.return_value = [SimpleNamespace(disease='EXAMPLE  disease', date_disease=date(2015, 12, 11))]
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            result = extract_patient('123', dry_run=False,
                                     client=self.client([json.dumps(output())]), session_factory=factory)
        self.assertEqual(result['inserted'], 0)
        self.assertFalse(any(isinstance(row, ClinicalHistory) for row in self.added))

    def test_missing_case_inserts_nothing(self):
        from caa_pdf_extractor.database.models import ClinicalHistory
        factory = self.setup_factory()
        session = factory.begin.return_value.__enter__.return_value
        session.execute.return_value.all.return_value = []
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            result = extract_patient('123', dry_run=False,
                                     client=self.client([json.dumps(output())]), session_factory=factory)
        self.assertEqual(result['status'], 'CASE_NOT_FOUND')
        self.assertIsNone(result['case_id'])
        self.assertIsNone(result['matched_fallnr'])
        self.assertFalse(result['case_exists'])
        self.assertEqual(result['validated_diagnoses'], 1)
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(result['duplicates_skipped'], 0)
        self.assertFalse(any(isinstance(row, ClinicalHistory) for row in self.added))
        statements = [str(call.args[0]) for call in session.execute.call_args_list]
        self.assertTrue(any('WHERE fallnr = :patient_id' in sql for sql in statements))
        self.assertFalse(any('WHERE id = :case_id' in sql for sql in statements))

    def test_identical_commits_are_idempotent(self):
        from caa_pdf_extractor.database.models import ClinicalHistory
        factory = self.setup_factory()
        session = factory.begin.return_value.__enter__.return_value
        session.scalars.side_effect = lambda statement: [row for row in self.added
                                                        if isinstance(row, ClinicalHistory)]
        client = self.client([json.dumps(output()), json.dumps(output())])
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            first = extract_patient('123', dry_run=False, client=client, session_factory=factory)
            second = extract_patient('123', dry_run=False, client=client, session_factory=factory)
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped']), (1, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped']), (0, 1))
        self.assertEqual(second['patient_id'], '123')
        self.assertEqual(second['case_id'], 12345)
        self.assertEqual(second['matched_fallnr'], '123')
        self.assertTrue(second['case_exists'])

    def test_ambiguous_case_inserts_nothing(self):
        from caa_pdf_extractor.database.models import ClinicalHistory
        factory = self.setup_factory()
        factory.begin.return_value.__enter__.return_value.execute.return_value.all.return_value = [(1,), (2,)]
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle', return_value=bundle()):
            result = extract_patient('123', dry_run=False,
                                     client=self.client([json.dumps(output())]), session_factory=factory)
        self.assertEqual(result['status'], 'CASE_AMBIGUOUS')
        self.assertIsNone(result['case_id'])
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertFalse(any(isinstance(row, ClinicalHistory) for row in self.added))

    def test_resolver_preserves_patient_string(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [(12345,)]
        self.assertEqual(resolve_case_id('2473679415', session=session), 12345)
        self.assertEqual(session.execute.call_args.args[1], {'patient_id': '2473679415'})
        for matches, status in [([], 'CASE_NOT_FOUND'), ([(1,), (2,)], 'CASE_AMBIGUOUS')]:
            session.execute.return_value.all.return_value = matches
            with self.assertRaises(CaseResolutionError) as caught:
                resolve_case_id('2473679415', session=session)
            self.assertEqual(caught.exception.status, status)

    def test_no_input_dry_run_still_exports(self):
        factory = self.setup_factory()
        client = self.client([])
        with patch('caa_pdf_extractor.extraction.clinical_history.build_patient_bundle',
                   return_value=PatientDocumentBundle('123', [])):
            result = extract_patient('123', client=client, session_factory=factory)
        self.assertEqual(result['status'], 'NO_INPUT')
        client.generate_json.assert_not_called()
        self.assertEqual(self.added, [])
        path = Path(self.debug_temp.name) / '123' / 'clinical_history_clinical_history_v1.json'
        exported = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(exported['patient_validation_status'], 'NO_INPUT')
        self.assertEqual(exported['normalized_output'], [])


if __name__ == '__main__':
    unittest.main()
