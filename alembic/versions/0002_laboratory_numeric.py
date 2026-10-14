"""Preserve exact laboratory decimals and remove misleading measurement defaults."""
from alembic import op
import sqlalchemy as sa

revision = '0002_laboratory_numeric'
down_revision = '0001_initial'
branch_labels = None
depends_on = None

MEASUREMENTS = ('csf_beta42', 'csf_beta4240_ratio', 'csf_tau_total',
                'csf_ptau_total', 'crp', 'hba1c')


def upgrade():
    for column in MEASUREMENTS:
        op.alter_column('laboratory', column, schema='public', existing_type=sa.Integer(),
                        type_=sa.Numeric(), existing_nullable=True, server_default=None,
                        postgresql_using=f'{column}::numeric')
    op.alter_column('laboratory', 'date_of_exam', schema='public', existing_type=sa.Date(),
                    existing_nullable=True, server_default=None)


def downgrade():
    # PostgreSQL numeric::integer rounds. Refuse a lossy downgrade before any DDL.
    op.execute(sa.text('LOCK TABLE public.laboratory IN ACCESS EXCLUSIVE MODE'))
    conditions = ' OR '.join(f'({c} IS NOT NULL AND ({c} <> trunc({c}) OR {c} < -2147483648 OR {c} > 2147483647 OR {c}::text IN (\'NaN\', \'Infinity\', \'-Infinity\')))' for c in MEASUREMENTS)
    op.execute(sa.text("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.laboratory WHERE "
        + conditions + ") THEN RAISE EXCEPTION 'Laboratory downgrade would lose numeric precision or range'; END IF; END $$"))
    for column in MEASUREMENTS:
        op.alter_column('laboratory', column, schema='public', existing_type=sa.Numeric(),
                        type_=sa.Integer(), existing_nullable=True, server_default=sa.text('0'),
                        postgresql_using=f'{column}::integer')
    op.alter_column('laboratory', 'date_of_exam', schema='public', existing_type=sa.Date(),
                    existing_nullable=True, server_default=sa.text('CURRENT_DATE'))
