"""Separate lesion age from interval detection; reserve optional finding details.

Only additive nullable columns, with no backfill or changes to clinical rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
revision='0004_radiology_temporal'
down_revision='0003_radiology_exam_findings_pet'
branch_labels=None
depends_on=None
STATUSES=('ACUTE','SUBACUTE','CHRONIC','INTERVAL_NEW','INDETERMINATE')
DETAILS=('lobar','deep','infratentorial','cortical','subcortical')
def upgrade():
    ENUM(*STATUSES,name='radiology_temporal_status').create(op.get_bind(),checkfirst=True)
    op.add_column('radiology_finding',sa.Column('temporal_status',ENUM(*STATUSES,name='radiology_temporal_status',create_type=False),nullable=True))
    op.add_column('radiology_finding',sa.Column('interval_new',sa.Boolean(),nullable=True))
    op.add_column('radiology_finding',sa.Column('fazekas_grade',sa.SmallInteger(),nullable=True))
    for name in DETAILS:op.add_column('radiology_finding',sa.Column(name,sa.Boolean(),nullable=True))
    op.add_column('radiology_finding',sa.Column('distribution',sa.Text(),nullable=True))
def downgrade():
    names=('temporal_status','interval_new','fazekas_grade',*DETAILS,'distribution')
    op.execute('LOCK TABLE radiology_finding IN ACCESS EXCLUSIVE MODE')
    condition=' OR '.join(name+' IS NOT NULL' for name in names)
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM radiology_finding WHERE "+condition+") THEN RAISE EXCEPTION 'Temporal downgrade would discard clinical data'; END IF; END $$")
    for name in reversed(names):op.drop_column('radiology_finding',name)
    ENUM(name='radiology_temporal_status').drop(op.get_bind(),checkfirst=True)
