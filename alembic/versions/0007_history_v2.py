"""Add history date precision and canonical-source evidence, without row backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
revision='0007_history_v2'
down_revision='0006_ct_neck_eeg'
branch_labels=None
depends_on=None
ENUMS={'disease_date_precision':('DAY','MONTH','YEAR','ADMISSION_PROXY','UNKNOWN'),'disease_date_basis':('EXPLICIT_DISEASE_DATE','EXPLICIT_MONTH_YEAR','EXPLICIT_YEAR','CURRENT_ADMISSION_PROXY','UNKNOWN'),'clinical_history_evidence_role':('PRIMARY_DIAGNOSIS','CHILD_DIAGNOSIS','PAST_HISTORY','CURRENT_NARRATIVE','CORROBORATING','COPIED_FORWARD')}
def enum(name):return ENUM(*ENUMS[name],name=name,create_type=False)
COLS=['date_disease_year','date_disease_month','date_disease_precision','date_disease_basis','date_disease_text','date_disease_proxy']
def upgrade():
    for name,values in ENUMS.items():ENUM(*values,name=name).create(op.get_bind(),checkfirst=True)
    for name,typ in [('date_disease_year',sa.SmallInteger()),('date_disease_month',sa.SmallInteger()),('date_disease_precision',enum('disease_date_precision')),('date_disease_basis',enum('disease_date_basis')),('date_disease_text',sa.Text()),('date_disease_proxy',sa.Date())]:op.add_column('clinical_history',sa.Column(name,typ,nullable=True))
    op.alter_column('clinical_history','date_disease',server_default=None)
    op.create_check_constraint('ck_history_year_range','clinical_history','date_disease_year IS NULL OR date_disease_year BETWEEN 1 AND 9999')
    op.create_check_constraint('ck_history_month_range','clinical_history','date_disease_month IS NULL OR date_disease_month BETWEEN 1 AND 12')
    op.create_check_constraint('ck_history_precision_shape','clinical_history',"""(
      (date_disease_precision IS NULL AND date_disease_year IS NULL AND date_disease_month IS NULL AND date_disease_basis IS NULL AND date_disease_text IS NULL AND date_disease_proxy IS NULL) OR
      (date_disease_precision='DAY' AND date_disease IS NOT NULL AND date_disease_year=EXTRACT(YEAR FROM date_disease) AND date_disease_month=EXTRACT(MONTH FROM date_disease) AND date_disease_proxy IS NULL AND date_disease_basis='EXPLICIT_DISEASE_DATE') OR
      (date_disease_precision='MONTH' AND date_disease IS NULL AND date_disease_year IS NOT NULL AND date_disease_month IS NOT NULL AND date_disease_proxy IS NULL AND date_disease_basis='EXPLICIT_MONTH_YEAR') OR
      (date_disease_precision='YEAR' AND date_disease IS NULL AND date_disease_year IS NOT NULL AND date_disease_month IS NULL AND date_disease_proxy IS NULL AND date_disease_basis='EXPLICIT_YEAR') OR
      (date_disease_precision='ADMISSION_PROXY' AND date_disease IS NULL AND date_disease_year IS NULL AND date_disease_month IS NULL AND date_disease_proxy IS NOT NULL AND date_disease_basis='CURRENT_ADMISSION_PROXY') OR
      (date_disease_precision='UNKNOWN' AND date_disease IS NULL AND date_disease_year IS NULL AND date_disease_month IS NULL AND date_disease_proxy IS NULL AND date_disease_basis='UNKNOWN')
    ) IS TRUE""")
    op.create_table('clinical_history_evidence',sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),sa.Column('clinical_history_id',sa.Integer(),sa.ForeignKey('clinical_history.id'),nullable=False),sa.Column('source_document_id',sa.BigInteger(),sa.ForeignKey('source_documents.id'),nullable=False),sa.Column('section_id',sa.Text(),nullable=False),sa.Column('item_id',sa.Text(),nullable=False),sa.Column('evidence_text',sa.Text(),nullable=False),sa.Column('date_text',sa.Text(),nullable=True),sa.Column('evidence_role',enum('clinical_history_evidence_role'),nullable=False),sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),sa.UniqueConstraint('clinical_history_id','source_document_id','section_id','item_id',name='uq_history_evidence_source_item'))
def downgrade():
    op.execute('LOCK TABLE clinical_history,clinical_history_evidence IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM clinical_history_evidence) OR EXISTS(SELECT 1 FROM clinical_history WHERE "+' OR '.join(c+' IS NOT NULL' for c in COLS)+") THEN RAISE EXCEPTION 'History v2 downgrade would discard clinical evidence/date precision'; END IF; END $$")
    op.drop_table('clinical_history_evidence')
    for name in ['ck_history_precision_shape','ck_history_month_range','ck_history_year_range']:op.drop_constraint(name,'clinical_history',type_='check')
    for name in reversed(COLS):op.drop_column('clinical_history',name)
    op.alter_column('clinical_history','date_disease',server_default=sa.text('CURRENT_DATE'))
    for name in reversed(ENUMS):ENUM(name=name).drop(op.get_bind(),checkfirst=True)
