"""Add encounter phenotypes/mRS, without clinical backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB

revision = '0008_encounter_mrs'
down_revision = '0007_history_v2'
branch_labels = None
depends_on = None
ENUMS = {'admission_reason_primary': ('FOCAL_NEUROLOGICAL_DEFICIT', 'EPILEPTIC_EVENT', 'COGNITIVE_DECLINE', 'OTHER', 'UNKNOWN'),
         'mrs_source_type': ('EXPLICIT', 'ESTIMATED'), 'mrs_confidence': ('HIGH', 'MODERATE', 'LOW')}


def enum(name):
    return ENUM(*ENUMS[name], name=name, create_type=False)


def shape(prefix):
    return f"""(({prefix} IS NULL AND {prefix}_min IS NULL AND {prefix}_max IS NULL) OR
      ({prefix} BETWEEN 0 AND 5 AND {prefix}_min IS NULL AND {prefix}_max IS NULL) OR
      ({prefix} IS NULL AND {prefix}_min BETWEEN 0 AND 5 AND {prefix}_max BETWEEN 0 AND 5
       AND {prefix}_min < {prefix}_max)) IS TRUE"""


def upgrade():
    for name, values in ENUMS.items():
        ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
    columns = [sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
               sa.Column('case_id', sa.Integer(), sa.ForeignKey('case.id'), nullable=False),
               sa.Column('source_document_id', sa.BigInteger(), sa.ForeignKey('source_documents.id'), nullable=False),
               sa.Column('admission_date', sa.Date()), sa.Column('discharge_date', sa.Date()),
               sa.Column('admission_reason_primary', enum('admission_reason_primary'), nullable=False)]
    columns += [sa.Column('admission_' + name, sa.Boolean()) for name in ('focal_neurological_deficit', 'epileptic_event', 'cognitive_decline', 'other')]
    columns += [sa.Column('admission_reason_text', sa.Text())]
    columns += [sa.Column(prefix + suffix, sa.SmallInteger()) for prefix in ('pre_event_mrs', 'admission_mrs') for suffix in ('', '_min', '_max')]
    for prefix in ('', 'pre_event_'):
        columns += [sa.Column(prefix + 'mrs_source_type', enum('mrs_source_type')), sa.Column(prefix + 'mrs_confidence', enum('mrs_confidence')), sa.Column(prefix + 'mrs_evidence_text', sa.Text())]
    columns += [sa.Column('assessment_json', JSONB(), nullable=False), sa.Column('deleted', sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.UniqueConstraint('case_id', 'source_document_id', name='uq_encounter_case_source'),
                sa.CheckConstraint('discharge_date IS NULL OR admission_date IS NULL OR discharge_date >= admission_date', name='ck_encounter_date_order'),
                sa.CheckConstraint(shape('pre_event_mrs'), name='ck_encounter_pre_event_mrs'),
                sa.CheckConstraint(shape('admission_mrs'), name='ck_encounter_admission_mrs'),
                sa.CheckConstraint("mrs_confidence IS DISTINCT FROM 'LOW' OR admission_mrs IS NULL", name='ck_encounter_low_no_exact'),
                sa.CheckConstraint("pre_event_mrs_confidence IS DISTINCT FROM 'LOW' OR pre_event_mrs IS NULL", name='ck_encounter_pre_low_no_exact')]
    op.create_table('hospital_encounter', *columns)


def downgrade():
    op.execute('LOCK TABLE hospital_encounter IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM hospital_encounter) THEN RAISE EXCEPTION 'Encounter downgrade would discard clinical evidence'; END IF; END $$")
    op.drop_table('hospital_encounter')
    for name in reversed(ENUMS):
        ENUM(name=name).drop(op.get_bind(), checkfirst=True)
