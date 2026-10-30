"""Add longitudinal cognitive source mentions; no backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

revision = '0009_cognitive_assessment'
down_revision = '0008_encounter_mrs'
branch_labels = None
depends_on = None

# Migration-local constants keep historic schema reproducible.
ENUMS = {'cognitive_test_type': ('MMSE', 'MOCA', 'OTHER'),
         'cognitive_date_precision': ('DAY', 'MONTH', 'YEAR', 'UNKNOWN'),
         'cognitive_date_basis': ('EXPLICIT_TEST_DATE', 'EXPLICIT_MONTH_YEAR', 'EXPLICIT_YEAR', 'CURRENT_SECTION_DATE', 'UNKNOWN'),
         'cognitive_context': ('CURRENT', 'HISTORICAL', 'EXTERNAL_REPORT', 'UNKNOWN')}
DATE_SHAPE = """(
 (date_precision='DAY' AND assessment_date IS NOT NULL
  AND assessment_year=extract(year from assessment_date) AND assessment_month=extract(month from assessment_date)
  AND date_basis IN ('EXPLICIT_TEST_DATE','CURRENT_SECTION_DATE')) OR
 (date_precision='MONTH' AND assessment_date IS NULL AND assessment_year BETWEEN 1000 AND 9999
  AND assessment_month BETWEEN 1 AND 12 AND date_basis='EXPLICIT_MONTH_YEAR') OR
 (date_precision='YEAR' AND assessment_date IS NULL AND assessment_year BETWEEN 1000 AND 9999
  AND assessment_month IS NULL AND date_basis='EXPLICIT_YEAR') OR
 (date_precision='UNKNOWN' AND assessment_date IS NULL AND assessment_year IS NULL
  AND assessment_month IS NULL AND date_basis='UNKNOWN')
) IS TRUE"""
SCORE_SHAPE = """(score >= 0 AND max_score > 0 AND score <= max_score
 AND (test_type NOT IN ('MMSE','MOCA') OR (max_score=30 AND score BETWEEN 0 AND 30))) IS TRUE"""


def upgrade():
    for name, values in ENUMS.items():
        ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    def enum(name):
        return ENUM(*ENUMS[name], name=name, create_type=False)
    op.create_table('cognitive_assessment',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('case_id', sa.Integer(), sa.ForeignKey('case.id'), nullable=False),
        sa.Column('source_document_id', sa.BigInteger(), sa.ForeignKey('source_documents.id'), nullable=False),
        sa.Column('test_type', enum('cognitive_test_type'), nullable=False),
        sa.Column('score', sa.SmallInteger(), nullable=False), sa.Column('max_score', sa.SmallInteger(), nullable=False),
        sa.Column('assessment_date', sa.Date()), sa.Column('assessment_year', sa.SmallInteger()), sa.Column('assessment_month', sa.SmallInteger()),
        sa.Column('date_precision', enum('cognitive_date_precision'), nullable=False),
        sa.Column('date_basis', enum('cognitive_date_basis'), nullable=False),
        sa.Column('context', enum('cognitive_context'), nullable=False), sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False), sa.Column('section_id', sa.Text(), nullable=False),
        sa.Column('source_start', sa.Integer(), nullable=False), sa.Column('source_end', sa.Integer(), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('case_id','source_document_id','section_id','source_start','source_end','test_type', name='uq_cognitive_source_mention'),
        sa.CheckConstraint(SCORE_SHAPE, name='ck_cognitive_score'),
        sa.CheckConstraint(DATE_SHAPE, name='ck_cognitive_date'),
        sa.CheckConstraint('page > 0 AND source_start >= 0 AND source_end > source_start AND length(source_text)>0', name='ck_cognitive_source_span'))
    op.create_index('ix_cognitive_case_date', 'cognitive_assessment', ['case_id','assessment_date','assessment_year','assessment_month'])


def downgrade():
    op.execute('LOCK TABLE cognitive_assessment IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM cognitive_assessment) THEN RAISE EXCEPTION 'Cognitive downgrade would discard source evidence'; END IF; END $$")
    op.drop_table('cognitive_assessment')
    for name in reversed(ENUMS):
        ENUM(name=name).drop(op.get_bind(), checkfirst=True)
