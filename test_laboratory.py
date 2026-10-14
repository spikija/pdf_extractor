"""Synthetic laboratory evidence, units, Decimal and verified-commit tests."""
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import ExtractionRun, Laboratory
from caa_pdf_extractor.extraction.laboratory import _extract_text_patient as extract_patient
from caa_pdf_extractor.extraction.laboratory_inputs import select_laboratory_inputs
from caa_pdf_extractor.extraction.laboratory_models import validate_laboratory, parse_decimal, signature, FIELDS
from caa_pdf_extractor.llm.backend import get_client
from caa_pdf_extractor.llm.laboratory_prompts import laboratory_prompt
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection


def bundle(text='Labor 04.01.2016\nCRP 0.01 mg/dL'):
    section = PreparedSection('doc1_sec01', '123', 1, 'Labor', 2, 2, text, text)
    return PatientDocumentBundle('123', [PreparedDocument(1, 'synthetic.pdf', '123/synthetic.pdf',
        'hash', 'unknown', None, [section])])


def payload(inputs, values=None, units=None, roles=None):
    values = values or ['0.01'] * len(inputs)
    units = units or ['mg/dL'] * len(inputs)
    roles = roles or ['single'] * len(inputs)
    return {'patient_id': '123', 'measurements': [
        {'candidate_id': i.candidate_id, 'biomarker': i.biomarker, 'raw_numeric_string': value,
         'unit': unit, 'date_of_exam': i.associated_date, 'evidence': i.text, 'role': role}
        for i, value, unit, role in zip(inputs, values, units, roles)]}


class ValidationTests(unittest.TestCase):
    def validate(self, text='Labor 04.01.2016\nCRP 0.01 mg/dL', values=None, units=None, roles=None):
        inputs = select_laboratory_inputs(bundle(text))
        return validate_laboratory(json.dumps(payload(inputs, values, units, roles)), '123', inputs)

    def test_crp_decimal(self):
        result = self.validate()
        self.assertEqual(Decimal(result['laboratory'][0]['crp']), Decimal('0.01'))
        self.assertTrue(result['persistence_allowed'])

    def test_hba1c_decimal(self):
        result = self.validate('Labor 04.01.2016\nHbA1c 5.90 %', ['5.90'], ['%'])
        self.assertEqual(result['laboratory'][0]['hba1c'], '5.90')

    def test_csf_beta42(self):
        result = self.validate('Liquor 04.01.2016\nBeta-Amyloid 1-42 520 pg/mL', ['520'], ['pg/mL'])
        self.assertEqual(result['laboratory'][0]['csf_beta42'], '520')

    def test_csf_ratio_decimal(self):
        result = self.validate('Liquor 04.01.2016\nAβ42/40 0.07321', ['0.07321'], [''])
        self.assertEqual(result['laboratory'][0]['csf_beta4240_ratio'], '0.07321')

    def test_total_tau(self):
        result = self.validate('Liquor 04.01.2016\nTau gesamt 430 pg/mL', ['430'], ['pg/mL'])
        self.assertEqual(result['laboratory'][0]['csf_tau_total'], '430')

    def test_ptau(self):
        result = self.validate('Liquor 04.01.2016\nphospho-Tau 62 pg/mL', ['62'], ['pg/mL'])
        self.assertEqual(result['laboratory'][0]['csf_ptau_total'], '62')

    def test_missing_values_null(self):
        record = self.validate()['laboratory'][0]
        self.assertTrue(all(record[name] is None for name in FIELDS if name != 'crp'))

    def test_no_targets_no_null_row(self):
        inputs = select_laboratory_inputs(bundle('Natrium 140 mmol/L'))
        self.assertEqual(inputs, [])
        result = validate_laboratory(json.dumps({'patient_id': '123', 'measurements': []}), '123', inputs)
        self.assertEqual(result['laboratory'], [])

    def test_explicit_date(self):
        self.assertEqual(self.validate()['laboratory'][0]['date_of_exam'], '2016-01-04')

    def test_missing_date_null(self):
        self.assertIsNone(self.validate('CRP 0.01 mg/dL')['laboratory'][0]['date_of_exam'])

    def test_date_explicitly_on_measurement_row(self):
        result = self.validate('CRP (04.01.2016) mg/dL 0.01')
        self.assertEqual(result['laboratory'][0]['date_of_exam'], '2016-01-04')

    def test_document_admission_and_discharge_dates_not_used(self):
        for label in ('Dokument', 'Aufnahme Labor', 'Entlassung Labor'):
            result = self.validate(label + ' 04.01.2016\nCRP 0.01 mg/dL')
            self.assertIsNone(result['laboratory'][0]['date_of_exam'])

    def test_two_dates_two_observations(self):
        result = self.validate('Labor 04.01.2016\nCRP 0.01 mg/dL\nLabor 05.01.2016\nHbA1c 5.90 %', ['0.01', '5.90'], ['mg/dL', '%'])
        self.assertEqual([r['date_of_exam'] for r in result['laboratory']], ['2016-01-04', '2016-01-05'])

    def test_same_explicit_date_grouped(self):
        result = self.validate('Labor 04.01.2016\nCRP 0.01 mg/dL\nHbA1c 5.90 %', ['0.01', '5.90'], ['mg/dL', '%'])
        self.assertEqual(len(result['laboratory']), 1)
        self.assertEqual(result['laboratory'][0]['hba1c'], '5.90')

    def test_ifcc_audit_only_with_percent(self):
        result = self.validate('Labor 04.01.2016\nHbA1c 5.90 %\nHbA1c (IFCC) 41 mmol/mol', ['5.90', '41'], ['%', 'mmol/mol'])
        self.assertEqual(len(result['laboratory']), 1)
        self.assertEqual(result['laboratory'][0]['hba1c'], '5.90')
        self.assertTrue(result['measurements'][1]['audit_only'])
        self.assertTrue(result['persistence_allowed'])

    def test_ifcc_only_blocked_no_conversion(self):
        result = self.validate('HbA1c (IFCC) 41 mmol/mol', ['41'], ['mmol/mol'])
        self.assertFalse(result['persistence_allowed'])
        self.assertEqual(result['laboratory'], [])

    def test_unsupported_crp_unit_blocks(self):
        result = self.validate('CRP 0.42 mg/L', ['0.42'], ['mg/L'])
        self.assertFalse(result['persistence_allowed'])
        self.assertIn('UNIT_MISMATCH', result['persistence_issues'][0]['issue'])

    def test_summary_dates_unresolved_and_min_max_audit_only(self):
        result = self.validate('Laborwerte von 01.01.2016 bis 05.01.2016\nErster Wert Letzter Wert Min. Wert Max. Wert\nCRP 0.01 mg/dL', roles=['first'])
        self.assertIsNone(result['laboratory'][0]['date_of_exam'])
        self.assertFalse(result['persistence_allowed'])
        result = self.validate('Erster Wert Letzter Wert Min. Wert Max. Wert\nCRP 0.01 mg/dL', roles=['minimum'])
        self.assertEqual(result['laboratory'], [])
        self.assertTrue(result['measurements'][0]['audit_only'])

    def test_exact_evidence_required(self):
        inputs = select_laboratory_inputs(bundle()); data = payload(inputs)
        data['measurements'][0]['evidence'] = 'CRP was 0.01'
        with self.assertRaises(ValueError):
            validate_laboratory(json.dumps(data), '123', inputs)

    def test_synthetic_context_concatenation_is_not_verbatim(self):
        inputs = select_laboratory_inputs(bundle()); data = payload(inputs)
        data['measurements'][0]['evidence'] = inputs[0].context_text
        with self.assertRaises(ValueError):
            validate_laboratory(json.dumps(data), '123', inputs)

    def test_reference_range_not_result(self):
        inputs = select_laboratory_inputs(bundle('CRP 0.00-0.50 mg/dL 0.42')); data = payload(inputs, ['0.50'])
        with self.assertRaises(ValueError):
            validate_laboratory(json.dumps(data), '123', inputs)

    def test_invented_date_rejected(self):
        inputs = select_laboratory_inputs(bundle('CRP 0.01 mg/dL')); data = payload(inputs)
        data['measurements'][0]['date_of_exam'] = '2026-10-14'
        with self.assertRaises(ValueError):
            validate_laboratory(json.dumps(data), '123', inputs)

    def test_decimal_not_float_and_serialization_roundtrip(self):
        for value in ('0.01', '0.42', '5.90', '0.07321'):
            parsed = parse_decimal(value)
            self.assertIsInstance(parsed, Decimal)
            self.assertEqual(Decimal(json.loads(json.dumps({'value': str(parsed)}))['value']), parsed)
        for value in (0.01, 5.9, 'NaN', 'Infinity', '0.01 mg/dL'):
            with self.assertRaises(ValueError):
                parse_decimal(value)

    def test_decimal_comma_and_true_zero_preserved(self):
        self.assertEqual(parse_decimal('0,01'), Decimal('0.01'))
        self.assertEqual(self.validate('CRP 0 mg/dL', ['0'])['laboratory'][0]['crp'], '0')

    def test_signature_partial_fields_distinguished(self):
        record = self.validate()['laboratory'][0]
        other = {**record, 'hba1c': '5.90'}
        self.assertNotEqual(signature(record), signature(other))
        self.assertEqual(signature(record), signature({**record, 'crp': '0.010'}))

    def test_mock_backend_without_granite(self):
        with patch.dict(os.environ, {'LLM_BACKEND': 'mock'}): client = get_client()
        inputs = select_laboratory_inputs(bundle())
        result = validate_laboratory(client.generate_json(laboratory_prompt('123', inputs)), '123', inputs)
        self.assertEqual(result['laboratory'], [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.bundle = bundle(); self.rows = []; self.runs = {}
        self.session = MagicMock()
        def add(row):
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1; self.runs[row.id] = row
            else:
                row.id = len(self.rows) + 1; self.rows.append(row)
        self.session.add.side_effect = add
        self.session.get.side_effect = lambda cls, key: self.runs.get(key)
        self.session.scalars.side_effect = lambda stmt: [r for r in self.rows if r.deleted is False]
        self.session.execute.return_value.all.return_value = [(42,)]
        owner = self
        class Transaction:
            def __enter__(self):
                self.rows_before = list(owner.rows); self.runs_before = dict(owner.runs)
                return owner.session
            def __exit__(self, typ, value, tb):
                if typ:
                    owner.rows[:] = self.rows_before
                    owner.runs.clear(); owner.runs.update(self.runs_before)
                else:
                    for row in owner.rows:
                        for name in (*FIELDS, 'date_of_exam'):
                            if isinstance(getattr(row, name), Null): setattr(row, name, None)
                return False
        self.factory = MagicMock(); self.factory.begin.side_effect = Transaction
        self.client = SimpleNamespace(runtime='fake', model_name='fake', model_version='1', generate_json=MagicMock())

    def extract(self, *, dry_run=True, response=None):
        self.client.generate_json.reset_mock()
        self.client.generate_json.side_effect = None if dry_run else AssertionError('Commit inference forbidden')
        self.client.generate_json.return_value = response if response is not None else json.dumps(payload(select_laboratory_inputs(self.bundle)))
        with patch('caa_pdf_extractor.extraction.laboratory.build_patient_bundle', return_value=self.bundle), \
             patch('caa_pdf_extractor.extraction.laboratory.get_client', side_effect=AssertionError('Factory forbidden')):
            result = extract_patient('123', dry_run=dry_run, client=self.client, session_factory=self.factory, debug_root=self.temp.name)
        if not dry_run: self.client.generate_json.assert_not_called()
        return result

    def artifact(self):
        return sorted(Path(self.temp.name, '123', 'verified').glob('laboratory_*.json'))[-1]

    def test_dry_run_then_no_granite_commit_and_second_idempotent(self):
        self.assertEqual(self.extract()['status'], 'SUCCESS'); self.assertEqual(self.rows, [])
        first, second = self.extract(dry_run=False), self.extract(dry_run=False)
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped']), (1, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped']), (0, 1))
        self.assertEqual(self.rows[0].crp, Decimal('0.01'))
        self.assertIsInstance(self.rows[0].crp, Decimal)
        self.assertFalse(second['granite_called'])

    def test_nulls_explicitly_inserted_never_defaulted(self):
        self.bundle = bundle('CRP 0.01 mg/dL')
        self.extract(); self.extract(dry_run=False)
        self.assertIsNone(self.rows[0].date_of_exam)
        for field in FIELDS:
            if field != 'crp': self.assertIsNone(getattr(self.rows[0], field))

    def test_unit_mismatch_blocks_commit_without_inference(self):
        self.bundle = bundle('CRP 0.42 mg/L')
        response = json.dumps(payload(select_laboratory_inputs(self.bundle), ['0.42'], ['mg/L']))
        self.assertEqual(self.extract(response=response)['status'], 'SUCCESS')
        self.assertEqual(self.extract(dry_run=False)['status'], 'PERSISTENCE_BLOCKED')
        self.assertEqual(self.rows, [])

    def test_missing_stale_artifacts_and_empty_source_tracking(self):
        self.assertEqual(self.extract(dry_run=False)['status'], 'NO_VERIFIED_EXTRACTION')
        self.bundle = bundle('Natrium 140')
        self.extract(response=json.dumps({'patient_id': '123', 'measurements': []}))
        self.assertEqual(self.extract(dry_run=False)['status'], 'SUCCESS')
        self.assertEqual(self.rows, [])
        self.bundle = bundle('Natrium 141')
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')

    def test_malformed_granite_json(self):
        self.assertEqual(self.extract(response='not JSON')['status'], 'FAILED_PARSE')
        self.assertEqual(self.rows, [])

    def test_case_not_found(self):
        self.session.execute.return_value.all.return_value = []
        self.assertEqual(self.extract()['status'], 'CASE_NOT_FOUND')
        self.client.generate_json.assert_not_called()

    def test_tampered_artifact_even_rehashed_rejected(self):
        from caa_pdf_extractor.extraction.radiology_artifacts import digest
        self.extract(); path = self.artifact(); data = json.loads(path.read_text())
        data.pop('artifact_sha256'); data['normalized_output']['laboratory'][0]['crp'] = '999'
        data['artifact_sha256'] = digest(data); path.write_text(json.dumps(data))
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_changed_model_stale(self):
        self.extract(); self.client.model_version = 'changed'
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')


if __name__ == '__main__':
    unittest.main()
