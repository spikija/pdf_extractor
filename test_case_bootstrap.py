from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from caa_pdf_extractor.case_bootstrap import (
    _get_or_create, bootstrap_cases, validate_schema, BootstrapSchemaError,
    get_or_create_case_for_patient,
)
from caa_pdf_extractor.extraction.case_resolver import CaseResolutionError


def schema():
    return {"generated_id": True, "fallnr_unique": True, "required_fields": [],
            "columns": [{"name": name, "nullable": nullable}
                        for name, nullable in [("id", False), ("fallnr", True),
                                               ("entry_stamp", False), ("death_date", True),
                                               ("date_of_birth", True), ("gender", True)]]}


class CaseBootstrapTests(unittest.TestCase):
    def test_dry_run_no_insert(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        result = _get_or_create('2473679415', False, session, schema())
        self.assertTrue(result.would_create)
        self.assertFalse(result.created)
        self.assertIsNone(result.case_id)
        session.scalar.assert_not_called()

    def test_insert_generated_id_and_null_optional_fields(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        session.scalar.return_value = 42
        result = _get_or_create('2473679415', True, session, schema())
        self.assertEqual(result.case_id, 42)
        self.assertEqual(result.fallnr, '2473679415')
        self.assertTrue(result.created)
        from sqlalchemy.dialects import postgresql
        compiled = session.scalar.call_args.args[0].compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params, {'fallnr': '2473679415', 'gender': None,
                                         'death_date': None, 'date_of_birth': None})
        self.assertIn('ON CONFLICT (fallnr) DO NOTHING', str(compiled))
        self.assertIn('RETURNING "case".id', str(compiled))

    def test_existing_reused_and_ambiguous_rejected(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [(42,)]
        result = _get_or_create('2473679415', True, session, schema())
        self.assertEqual(result.case_id, 42)
        self.assertFalse(result.created)
        session.scalar.assert_not_called()
        session.execute.return_value.all.return_value = [(42,), (43,)]
        with self.assertRaises(CaseResolutionError):
            _get_or_create('2473679415', True, session, schema())

    def test_concurrent_insert_reuses_winner(self):
        session = MagicMock()
        session.execute.return_value.all.side_effect = [[], [(42,)]]
        session.scalar.return_value = None
        result = _get_or_create('2473679415', True, session, schema())
        self.assertEqual(result.case_id, 42)
        self.assertFalse(result.created)

    def test_required_field_blocks_before_insert(self):
        factory = MagicMock()
        required = {**schema(), 'required_fields': ['required_clinical_value']}
        with patch('caa_pdf_extractor.case_bootstrap.inspect_case_schema', return_value=required):
            with self.assertRaises(BootstrapSchemaError):
                get_or_create_case_for_patient('2473679415', True, session_factory=factory)
        factory.begin.return_value.__enter__.return_value.scalar.assert_not_called()
        factory.begin.return_value.__enter__.return_value.execute.assert_not_called()
        for key in ('generated_id', 'fallnr_unique'):
            with self.assertRaises(BootstrapSchemaError):
                validate_schema({**schema(), key: False})

    def test_batch_ambiguity_preflight_blocks_all_inserts(self):
        factory = MagicMock()
        session = factory.begin.return_value.__enter__.return_value
        session.execute.return_value.all.side_effect = [[], [(42,), (43,)]]
        with patch('caa_pdf_extractor.case_bootstrap.inspect_case_schema', return_value=schema()), \
             patch('caa_pdf_extractor.case_bootstrap.imported_patient_ids', return_value=['first', 'second']):
            summary, results = bootstrap_cases(True, session_factory=factory)
        self.assertEqual(summary['ambiguous_cases'], 1)
        self.assertEqual(results, [])
        session.scalar.assert_not_called()

    def test_idempotent_bootstrap(self):
        factory = MagicMock()
        session = factory.begin.return_value.__enter__.return_value
        session.execute.return_value.all.side_effect = [[], [(42,)]]
        session.scalar.return_value = 42
        with patch('caa_pdf_extractor.case_bootstrap.inspect_case_schema', return_value=schema()):
            first = get_or_create_case_for_patient('2473679415', True, session_factory=factory)
            second = get_or_create_case_for_patient('2473679415', True, session_factory=factory)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.case_id, second.case_id)
        session.scalar.assert_called_once()


if __name__ == '__main__':
    unittest.main()
