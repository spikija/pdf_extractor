"""Synthetic medication tests; no PostgreSQL, patient PDFs or Granite required."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import ExtractionRun, Medication
from caa_pdf_extractor.extraction.medication_inputs import select_medication_inputs, render_row
from caa_pdf_extractor.extraction.medication_models import MEDICATION_CATEGORIES
from caa_pdf_extractor.extraction.medication_validators import validate_medications, deduplicate_medications
from caa_pdf_extractor.extraction.medications import extract_patient
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection, PreparedTable


def section(text='Ramipril 5 mg 1-0-0', *, sid='doc1_sec01', doc=1, heading='Medikation', tables=None, page=1):
    return PreparedSection(sid, '123', doc, heading, page, page, text, text,
                           ['medications'] if heading == 'Medikation' else [], tables or [])


def bundle(*sections):
    docs = sorted({s.source_document_id for s in sections})
    return PatientDocumentBundle('123', [PreparedDocument(d, f'{d}.pdf', f'123/{d}.pdf', str(d),
        'unknown', None, [s for s in sections if s.source_document_id == d]) for d in docs])


def payload(medication='Ramipril 5 mg 1-0-0', **changes):
    item = dict(medication=medication, date_noted=None, date_stopped=None, medication_category=None,
                source_document_id=1, page_number=1, section_id='doc1_sec01',
                source_table_index=None, source_text=medication)
    item.update(changes)
    return {'patient_id': '123', 'medications': [item]}


def validate(data, *sections):
    return validate_medications(json.dumps(data), '123', select_medication_inputs(bundle(*sections)))


class InputAndValidationTests(unittest.TestCase):
    def test_structured_table_and_continuation(self):
        row = ['Ramipril 5 mg', '1', '0', '0', '0']
        first = section(tables=[PreparedTable(1, 1, [row], 'cell_text_match')])
        second = section('Page header', sid='doc1_sec02', heading=None, page=2,
                         tables=[PreparedTable(2, 1, [['Pantoprazol 40 mg', '1', '0', '0', '0']], 'cell_text_match')])
        unrelated = section('Laboratory values', sid='doc1_sec03', heading='Laborwerte', page=2)
        inputs = select_medication_inputs(bundle(first, second, unrelated))
        self.assertEqual([x.section_id for x in inputs], ['doc1_sec01', 'doc1_sec02'])
        self.assertEqual(inputs[0].text, '')
        self.assertEqual(inputs[0].tables[0]['source_rows'], ['Ramipril 5 mg 1 0 0 0'])
        item = validate(payload(render_row(row), source_table_index=1), first).medications[0]
        self.assertEqual(item.medication, 'Ramipril 5 mg 1 0 0 0')

    def test_non_medication_table_rows_excluded(self):
        s = section(tables=[PreparedTable(1, 1, [['Medikament', 'Früh'],
            ['Kompressionsbandage für 14 Tage', ''], ['Ramipril 5 mg', '1']], 'cell_text_match')])
        self.assertEqual(select_medication_inputs(bundle(s))[0].tables[0]['source_rows'], ['Ramipril 5 mg 1'])

    def test_free_text_strength_and_schedule_preserved(self):
        value = 'Ramipril  5 mg  1-0-0'
        item = validate(payload(value), section(value)).medications[0]
        self.assertEqual(item.medication, 'Ramipril 5 mg 1-0-0')
        for truncated in ('Ramipril', 'Ramipril 5 mg'):
            with self.assertRaises(ValueError):
                validate(payload(truncated, source_text=value), section(value))
        with self.assertRaises(ValueError):
            validate(payload('Ramipril'), section(value))

    def test_table_regimen_cannot_be_truncated_or_changed(self):
        s = section(tables=[PreparedTable(1, 1, [['Ramipril 5 mg', '1', '0', '0', '0']], 'cell_text_match')])
        for text in ('Ramipril 5 mg', 'Ramipril 10 mg 1 0 0 0'):
            with self.assertRaises(ValueError):
                validate(payload(text, source_text='Ramipril 5 mg 1 0 0 0', source_table_index=1), s)

    def test_missing_dates_remain_none(self):
        item = validate(payload(), section()).medications[0]
        self.assertIsNone(item.date_noted)
        self.assertIsNone(item.date_stopped)

    def test_explicit_start_and_stop_date(self):
        value = 'Ramipril 5 mg 1-0-0 seit 11.12.2015; abgesetzt am 14.12.2015'
        item = validate(payload('Ramipril 5 mg 1-0-0', source_text=value,
            date_noted='2015-12-11', date_stopped='2015-12-14'), section(value)).medications[0]
        self.assertEqual(item.date_stopped.isoformat(), '2015-12-14')
        self.assertEqual(item.date_noted.isoformat(), '2015-12-11')

    def test_document_and_unassociated_dates_rejected(self):
        value = 'Ramipril 5 mg 1-0-0 Entlassung 14.12.2015'
        with self.assertRaises(ValueError):
            validate(payload(value, date_noted='2015-12-14'), section(value))
        value = 'Ramipril 5 mg 1-0-0 seit 11.12.2015'
        with self.assertRaises(ValueError):
            validate(payload(value, date_stopped='2015-12-11'), section(value))
        with self.assertRaises(ValueError):
            validate(payload(date_noted='2015-12-11'), section('Ramipril 5 mg 1-0-0\nPantoprazol seit 11.12.2015'))

    def test_valid_enum_category_requires_explicit_evidence(self):
        self.assertEqual(len(MEDICATION_CATEGORIES), 10)
        value = 'Ramipril 5 mg 1-0-0; Kategorie: blood pressure control'
        item = validate(payload(value, medication_category='blood pressure control'), section(value)).medications[0]
        self.assertEqual(item.medication_category, 'blood pressure control')
        with self.assertRaises(ValueError):
            validate(payload(medication_category='blood pressure control'), section())

    def test_invalid_category_handling(self):
        with self.assertRaises(ValueError):
            validate(payload(medication_category='ACE inhibitor'), section())

    def test_provenance_strictness(self):
        for changes in ({'source_document_id': 2}, {'source_document_id': '1'},
                        {'page_number': 2}, {'section_id': 'missing'}, {'source_table_index': 1}):
            with self.assertRaises(ValueError):
                validate(payload(**changes), section())
        p = payload()
        p['patient_id'] = 'wrong'
        with self.assertRaises(ValueError):
            validate(p, section())

    def test_duplicate_detection_keeps_different_regimens(self):
        first = validate(payload(), section()).medications[0]
        duplicate = first.model_copy(update={'medication': ' Ramipril  5 mg 1-0-0 '})
        different = first.model_copy(update={'medication': 'Ramipril 5 mg 0-0-1'})
        self.assertEqual(len(deduplicate_medications([first, duplicate, different])), 2)
        changed_category = first.model_copy(update={'medication_category': 'unknown'})
        self.assertEqual(len(deduplicate_medications([first, changed_category])), 2)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runs, self.rows = {}, []
        self.session = MagicMock()
        def add(row):
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1
                self.runs[row.id] = row
            else:
                self.rows.append(row)
        self.session.add.side_effect = add
        self.session.get.side_effect = lambda cls, key: self.runs[key]
        self.session.execute.return_value.all.return_value = [(42,)]
        self.session.scalars.side_effect = lambda statement: list(self.rows)
        self.factory = MagicMock()
        self.factory.begin.return_value.__enter__.return_value = self.session
        self.factory.return_value.__enter__.return_value = self.session

    def run_extract(self, response=None, *, dry_run=True, source_bundle=None):
        responses = response if isinstance(response, list) else [response or json.dumps(payload())]
        client = SimpleNamespace(runtime='fake-local', model_name='fake', model_version='1',
                                 generate_json=MagicMock(side_effect=responses))
        with patch('caa_pdf_extractor.extraction.medications.build_patient_bundle',
                   return_value=source_bundle or bundle(section())):
            return extract_patient('123', dry_run=dry_run, client=client,
                                   session_factory=self.factory, debug_root=self.temp.name)

    def test_dry_run_inserts_no_clinical_rows(self):
        result = self.run_extract()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(result['case_id'], 42)
        self.assertEqual(result['validated_medications'], 1)
        self.assertEqual(self.rows, [])
        exported = json.loads(Path(self.temp.name, '123', 'medications_medications_v1.json').read_text())
        self.assertEqual(exported['runs'][0]['granite_response'], payload())
        self.assertEqual(exported['normalized_output'][0]['medication'], 'Ramipril 5 mg 1-0-0')

    def test_successful_commit_internal_key_and_explicit_sql_nulls(self):
        self.run_extract()
        result = self.run_extract(dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 1)
        row = self.rows[0]
        self.assertEqual(row.case_id, 42)
        self.assertIsInstance(row.date_noted, Null)
        self.assertIsInstance(row.date_stopped, Null)
        self.assertIsInstance(row.medication_category, Null)
        self.assertEqual(row.medication, 'Ramipril 5 mg 1-0-0')

    def test_second_commit_idempotent(self):
        self.run_extract()
        first = self.run_extract(dry_run=False)
        # Simulate database round-trip: explicit SQL NULL values become None.
        for row in self.rows:
            row.date_noted = row.date_stopped = row.medication_category = None
        second = self.run_extract(dry_run=False)
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped']), (1, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped']), (0, 1))
        self.assertEqual(len(self.rows), 1)

    def test_case_not_found(self):
        self.session.execute.return_value.all.return_value = []
        result = self.run_extract(dry_run=False)
        self.assertEqual(result['status'], 'CASE_NOT_FOUND')
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(self.rows, [])

    def test_case_ambiguous(self):
        self.session.execute.return_value.all.return_value = [(42,), (43,)]
        result = self.run_extract(dry_run=False)
        self.assertEqual(result['status'], 'CASE_AMBIGUOUS')
        self.assertEqual(self.rows, [])

    def test_malformed_granite_json_retained(self):
        result = self.run_extract('not JSON', dry_run=True)
        self.assertEqual(result['status'], 'FAILED_PARSE')
        self.assertEqual(self.rows, [])
        self.assertEqual(self.runs[1].raw_output['response'], 'not JSON')
        self.assertIsNotNone(self.runs[1].validation_errors)

    def test_validation_failure_inserts_nothing(self):
        result = self.run_extract(json.dumps(payload(medication_category='invalid')), dry_run=True)
        self.assertEqual(result['status'], 'FAILED_VALIDATION')
        self.assertEqual(self.rows, [])
        self.assertIsNotNone(self.runs[1].validation_errors)

    def test_any_document_failure_blocks_entire_patient(self):
        b = bundle(section(), section(sid='doc2_sec01', doc=2))
        result = self.run_extract([json.dumps(payload()), 'not JSON'], dry_run=True, source_bundle=b)
        self.assertEqual(result['validated_medications'], 0)
        self.assertEqual(self.rows, [])
        self.assertEqual(self.runs[1].raw_output['normalized_output'], [])
        self.assertEqual(self.runs[2].status, 'FAILED_PARSE')

    def test_model_failure(self):
        result = self.run_extract([RuntimeError('Synthetic failure')], dry_run=True)
        self.assertEqual(result['status'], 'FAILED_MODEL')
        self.assertEqual(self.rows, [])

    def test_no_input(self):
        result = self.run_extract(source_bundle=bundle(section(heading='Laborwerte')))
        self.assertEqual(result['status'], 'NO_INPUT')
        self.assertEqual(self.runs, {})


if __name__ == '__main__':
    unittest.main()
