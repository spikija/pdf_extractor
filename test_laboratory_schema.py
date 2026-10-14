"""Laboratory metadata and migration safety, without a database dependency."""
import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Numeric
from caa_pdf_extractor.database.models import Laboratory

FIELDS = ('csf_beta42', 'csf_beta4240_ratio', 'csf_tau_total', 'csf_ptau_total', 'crp', 'hba1c')


def migration():
    path = Path(__file__).parent / 'alembic/versions/0002_laboratory_numeric.py'
    spec = importlib.util.spec_from_file_location('laboratory_numeric_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SchemaTests(unittest.TestCase):
    def test_measurements_unconstrained_numeric_decimal(self):
        for name in FIELDS:
            column = Laboratory.__table__.c[name]
            self.assertIsInstance(column.type, Numeric)
            self.assertTrue(column.type.asdecimal)
            self.assertIsNone(column.type.precision)
            self.assertIsNone(column.type.scale)
            self.assertTrue(column.nullable)

    def test_unknowns_have_no_defaults(self):
        for name in (*FIELDS, 'date_of_exam'):
            column = Laboratory.__table__.c[name]
            self.assertIsNone(column.server_default)
            self.assertIsNone(column.default)
            self.assertTrue(column.nullable)

    def test_upgrade_only_laboratory_lossless_casts(self):
        output = StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        module = migration()
        with patch.object(module, 'op', Operations(context)):
            module.upgrade()
        sql = output.getvalue()
        for name in FIELDS:
            self.assertIn(f'ALTER COLUMN {name} TYPE NUMERIC USING {name}::numeric', sql)
            self.assertIn(f'ALTER COLUMN {name} DROP DEFAULT', sql)
        self.assertIn('ALTER COLUMN date_of_exam DROP DEFAULT', sql)
        self.assertNotIn('UPDATE ', sql)
        self.assertNotIn('CREATE TABLE', sql)
        self.assertNotIn('FLOAT', sql)
        self.assertEqual(module.down_revision, '0001_initial')

    def test_downgrade_checks_before_integer_cast(self):
        output = StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        module = migration()
        with patch.object(module, 'op', Operations(context)):
            module.downgrade()
        sql = output.getvalue()
        self.assertLess(sql.index('RAISE EXCEPTION'), sql.index('TYPE INTEGER'))
        for name in FIELDS:
            self.assertIn(f'{name} <> trunc({name})', sql)
            self.assertIn(f'{name} > 2147483647', sql)


if __name__ == '__main__':
    unittest.main()
