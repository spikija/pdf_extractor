"""Local PostgreSQL verification with temporary tables; no clinical rows inserted."""
from decimal import Decimal
from sqlalchemy import text
from caa_pdf_extractor.database.connection import get_engine


def test_migration_preserves_legacy_integer_zero_null_and_historical_date():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from unittest.mock import patch
    from test_laboratory_schema import migration, FIELDS
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            columns = ', '.join(f'{name} INTEGER DEFAULT 0' for name in FIELDS)
            connection.execute(text('CREATE TEMP TABLE laboratory_legacy_probe (id INTEGER, date_of_exam DATE DEFAULT CURRENT_DATE, ' + columns + ') ON COMMIT DROP'))
            connection.execute(text("INSERT INTO laboratory_legacy_probe (id,date_of_exam,crp,hba1c) VALUES (1,'2015-12-14',0,42),(2,NULL,NULL,NULL)"))
            operations = Operations(MigrationContext.configure(connection))
            class TemporaryOperations:
                def alter_column(self, table, column, **kwargs):
                    kwargs['schema'] = 'pg_temp'
                    operations.alter_column('laboratory_legacy_probe', column, **kwargs)
            module = migration()
            with patch.object(module, 'op', TemporaryOperations()):
                module.upgrade()
            connection.execute(text('INSERT INTO laboratory_legacy_probe (id) VALUES (3)'))
            rows = connection.execute(text('SELECT * FROM laboratory_legacy_probe ORDER BY id')).mappings().all()
            assert rows[0]['crp'] == Decimal('0')
            assert rows[0]['hba1c'] == Decimal('42')
            assert isinstance(rows[0]['crp'], Decimal)
            assert rows[0]['date_of_exam'].isoformat() == '2015-12-14'
            assert rows[1]['crp'] is None
            assert rows[1]['hba1c'] is None
            assert rows[1]['date_of_exam'] is None
            assert all(rows[2][name] is None for name in (*FIELDS, 'date_of_exam'))
        finally:
            transaction.rollback()


def test_lossy_downgrade_refused_before_ddl_on_temporary_table():
    import pytest
    from sqlalchemy.exc import DBAPIError
    from unittest.mock import MagicMock, patch
    from test_laboratory_schema import migration, FIELDS
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            columns = ', '.join(f'{name} NUMERIC' for name in FIELDS)
            connection.execute(text('CREATE TEMP TABLE laboratory_downgrade_probe (' + columns + ') ON COMMIT DROP'))
            connection.execute(text("INSERT INTO laboratory_downgrade_probe (crp) VALUES (0.01)"))
            proxy = MagicMock()
            proxy.execute.side_effect = lambda statement: connection.execute(text(str(statement).replace('public.laboratory', 'pg_temp.laboratory_downgrade_probe')))
            module = migration()
            with connection.begin_nested(), patch.object(module, 'op', proxy):
                with pytest.raises(DBAPIError, match='lose numeric precision'):
                    module.downgrade()
                proxy.alter_column.assert_not_called()
                # Roll back the failed savepoint before further SQL.
                raise _RollbackProbe()
        except _RollbackProbe:
            assert connection.execute(text('SELECT crp FROM laboratory_downgrade_probe')).scalar() == Decimal('0.01')
        finally:
            transaction.rollback()


class _RollbackProbe(Exception):
    pass


def test_numeric_roundtrip_and_null_defaults_on_temporary_schema_clone():
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text('CREATE TEMP TABLE laboratory_numeric_probe (LIKE public.laboratory INCLUDING DEFAULTS) ON COMMIT DROP'))
            for identifier, crp, hba1c, ratio in (
                (1, Decimal('0.01'), Decimal('5.90'), Decimal('0.07321')),
                (2, Decimal('0.42'), None, None),
                (3, Decimal('0'), None, None),
            ):
                connection.execute(text('INSERT INTO laboratory_numeric_probe (id,crp,hba1c,csf_beta4240_ratio) VALUES (:id,:crp,:hba1c,:ratio)'),
                    {'id': identifier, 'crp': crp, 'hba1c': hba1c, 'ratio': ratio})
            rows = connection.execute(text('SELECT * FROM laboratory_numeric_probe ORDER BY id')).mappings().all()
            assert rows[0]['crp'] == Decimal('0.01') and isinstance(rows[0]['crp'], Decimal)
            assert rows[1]['crp'] == Decimal('0.42')
            assert rows[0]['hba1c'] == Decimal('5.90')
            assert str(rows[0]['hba1c']) == '5.90'
            assert rows[0]['csf_beta4240_ratio'] == Decimal('0.07321')
            assert rows[2]['crp'] == Decimal('0')
            for row in rows:
                assert row['date_of_exam'] is None
                assert row['csf_beta42'] is None
                assert row['csf_tau_total'] is None
                assert row['csf_ptau_total'] is None
            assert rows[1]['hba1c'] is None
        finally:
            transaction.rollback()
