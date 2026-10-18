"""Explicit negative children and nullable study scope/performance; no backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
revision='0005_radiology_negatives'
down_revision='0004_radiology_temporal'
branch_labels=None
depends_on=None
NEGATIVES=('NO_ACUTE_ISCHEMIA','NO_ACUTE_HEMORRHAGE','NO_ICH','NO_SAH','NO_NEW_CMB','NO_CSS',
    'NO_ENHANCEMENT','NO_MASS_LESION','NO_VASCULITIS_ENHANCEMENT','NO_VASCULAR_CALIBER_ABNORMALITY','OTHER')
SCOPES=('BRAIN','SPINE','HEAD_NECK','OTHER')
def upgrade():
    ENUM(*NEGATIVES,name='radiology_negative_finding_type').create(op.get_bind(),checkfirst=True)
    ENUM(*SCOPES,name='radiology_anatomic_scope').create(op.get_bind(),checkfirst=True)
    op.add_column('radiology_exam',sa.Column('anatomic_scope',ENUM(*SCOPES,name='radiology_anatomic_scope',create_type=False),nullable=True))
    op.add_column('radiology_exam',sa.Column('performed',sa.Boolean(),nullable=True))
    op.create_table('radiology_negative_finding',sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('radiology_exam_id',sa.BigInteger(),sa.ForeignKey('radiology_exam.id'),nullable=False),
        sa.Column('finding_type',ENUM(*NEGATIVES,name='radiology_negative_finding_type',create_type=False),nullable=False),
        sa.Column('location',sa.Text(),nullable=True),sa.Column('evidence_text',sa.Text(),nullable=False),
        sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.text('FALSE')))
    op.create_index('ix_radiology_negative_finding_radiology_exam_id','radiology_negative_finding',['radiology_exam_id'])
def downgrade():
    op.execute('LOCK TABLE radiology_exam, radiology_negative_finding IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM radiology_negative_finding) OR EXISTS (SELECT 1 FROM radiology_exam WHERE performed IS NOT NULL OR anatomic_scope IS NOT NULL) THEN RAISE EXCEPTION 'Negative finding downgrade would discard clinical data'; END IF; END $$")
    op.drop_table('radiology_negative_finding')
    op.drop_column('radiology_exam','performed');op.drop_column('radiology_exam','anatomic_scope')
    ENUM(name='radiology_negative_finding_type').drop(op.get_bind(),checkfirst=True)
    ENUM(name='radiology_anatomic_scope').drop(op.get_bind(),checkfirst=True)
