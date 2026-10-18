"""Add source-identified exams, atomic findings and nullable PET classifications.

Legacy radiology rows remain untouched. No clinical backfill is performed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

revision='0003_radiology_exam_findings_pet'
down_revision='0002_laboratory_numeric'
branch_labels=None
depends_on=None

# Migration values are frozen here, independently of future application enums.
ENUMS={
 'imaging_modality':('MRI','CT','CTA','MRA','PET_CT_AMYLOID','PET_CT_FDG','OTHER'),
 'date_source':('REPORT_EXPLICIT','DOCUMENT_DATE','DOCUMENT_DISCHARGE_DATE','UNKNOWN'),
 'radiology_pattern':('SINGLE_FINDING','MULTIPLE_HEMORRHAGIC_FINDINGS','ISCHEMIC_AND_HEMORRHAGIC_FINDINGS','MULTIPLE_MIXED_FINDINGS','OTHER'),
 'radiology_finding_type':('CMB','CSS','ICH','SAH','ISCHEMIC_LESION','WMH','MICROANGIOPATHY','OTHER','UNKNOWN'),
 'amyloid_pet_result':('POSITIVE','NEGATIVE','INDETERMINATE','NON_DIAGNOSTIC'),
 'fdg_pet_result':('NORMAL','ABNORMAL','INDETERMINATE','NON_DIAGNOSTIC'),
 'fdg_pet_pattern':('AD_TYPICAL','AD_ATYPICAL','FTD_PATTERN','DLB_PATTERN','PCA_PATTERN','PPA_PATTERN','VASCULAR_PATTERN','MIXED_PATTERN','NONSPECIFIC','OTHER','NO_SPECIFIC_PATTERN'),
}
def enum(name):return ENUM(*ENUMS[name],name=name,create_type=False)
def upgrade():
    for name,values in ENUMS.items():ENUM(*values,name=name).create(op.get_bind(),checkfirst=True)
    op.create_table('radiology_exam',
        sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('case_id',sa.Integer(),sa.ForeignKey('case.id'),nullable=False),
        sa.Column('source_document_id',sa.BigInteger(),sa.ForeignKey('source_documents.id'),nullable=False),
        sa.Column('procedure_id',sa.Text(),nullable=False),sa.Column('modality',enum('imaging_modality'),nullable=False),
        sa.Column('exam_date',sa.Date(),nullable=True),sa.Column('exam_date_source',enum('date_source'),nullable=False),
        sa.Column('report_text',sa.Text()),sa.Column('overall_pattern',enum('radiology_pattern')),
        sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.UniqueConstraint('case_id','source_document_id','procedure_id',name='uq_radiology_exam_source_procedure'))
    op.create_table('radiology_finding',
        sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('radiology_exam_id',sa.BigInteger(),sa.ForeignKey('radiology_exam.id'),nullable=False),
        sa.Column('finding_type',enum('radiology_finding_type'),nullable=False),sa.Column('acute_lesion',sa.Boolean()),
        sa.Column('location',sa.Text()),sa.Column('laterality',sa.Text()),sa.Column('evidence_text',sa.Text(),nullable=False),
        sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_index('ix_radiology_finding_exam','radiology_finding',['radiology_exam_id'])
    for column in ('amyloid_pet_result','fdg_pet_result','fdg_pet_pattern'):
        op.add_column('examinations',sa.Column(column,enum(column),nullable=True))

def downgrade():
    # Refuse a destructive downgrade once the new schema contains clinical data.
    op.execute("LOCK TABLE radiology_finding, radiology_exam, examinations IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM radiology_exam)
      OR EXISTS (SELECT 1 FROM radiology_finding)
      OR EXISTS (SELECT 1 FROM examinations WHERE amyloid_pet_result IS NOT NULL
        OR fdg_pet_result IS NOT NULL OR fdg_pet_pattern IS NOT NULL)
      THEN RAISE EXCEPTION 'Radiology/PET downgrade would discard clinical data'; END IF; END $$""")
    for column in ('amyloid_pet_result','fdg_pet_result','fdg_pet_pattern'):op.drop_column('examinations',column)
    op.drop_table('radiology_finding');op.drop_table('radiology_exam')
    for name in reversed(ENUMS):ENUM(name=name).drop(op.get_bind(),checkfirst=True)
