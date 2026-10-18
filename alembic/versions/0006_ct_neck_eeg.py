"""Add CT phenotypes, neck summary and dedicated nullable EEG research fields."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
revision='0006_ct_neck_eeg'
down_revision='0005_radiology_negatives'
branch_labels=None
depends_on=None
ENUMS={
 'neck_ultrasound_summary':('NO_STENOSIS','STENOSIS_LT_50','STENOSIS_GE_50','OCCLUSION','INDETERMINATE'),
 'eeg_status':('not_performed','performed','unknown'),
 'eeg_primary_finding':('normal','focal_slowing','generalized_slowing','epileptiform_discharges','rhythmic_periodic_pattern','electrographic_seizure','other_abnormality','uninterpretable'),
 'eeg_laterality':('left','right','bilateral','generalized','not_localizable','unknown'),
 'eeg_tfne_timing':('during_episode','post_episode','interictal','unknown')}
ADDITIONS={'radiology_finding_type':('EDEMA','MASS_EFFECT','HYDROCEPHALUS','CALCIFICATION'),
 'radiology_negative_finding_type':('NO_EDEMA','NO_MASS_EFFECT','NO_HYDROCEPHALUS','NO_CALCIFICATION','NO_TERRITORIAL_INFARCT')}
OLD={'radiology_finding_type':('CMB','CSS','ICH','SAH','ISCHEMIC_LESION','WMH','MICROANGIOPATHY','OTHER','UNKNOWN'),
 'radiology_negative_finding_type':('NO_ACUTE_ISCHEMIA','NO_ACUTE_HEMORRHAGE','NO_ICH','NO_SAH','NO_NEW_CMB','NO_CSS','NO_ENHANCEMENT','NO_MASS_LESION','NO_VASCULITIS_ENHANCEMENT','NO_VASCULAR_CALIBER_ABNORMALITY','OTHER')}
def enum(name):return ENUM(*ENUMS[name],name=name,create_type=False)
def upgrade():
    for name,values in ENUMS.items():ENUM(*values,name=name).create(op.get_bind(),checkfirst=True)
    for name,values in ADDITIONS.items():
        for value in values:op.execute("ALTER TYPE "+name+" ADD VALUE IF NOT EXISTS '"+value+"'")
    op.add_column('examinations',sa.Column('neck_ultrasound_summary',enum('neck_ultrasound_summary'),nullable=True))
    op.create_table('eeg_examination',sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('case_id',sa.Integer(),sa.ForeignKey('case.id'),nullable=False),
        sa.Column('source_document_id',sa.BigInteger(),sa.ForeignKey('source_documents.id'),nullable=False),
        sa.Column('procedure_id',sa.Text(),nullable=False),sa.Column('eeg_date',sa.Date(),nullable=True),
        *[sa.Column(name,enum(name),nullable=name!='eeg_status') for name in ('eeg_status','eeg_primary_finding','eeg_laterality','eeg_tfne_timing')],
        *[sa.Column(name,sa.Boolean(),nullable=True) for name in ('eeg_clinical_event_captured','eeg_focal_slowing','eeg_epileptiform_discharges','eeg_rhythmic_periodic_pattern','eeg_electrographic_seizure')],
        sa.Column('eeg_report_text',sa.Text(),nullable=False),sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.UniqueConstraint('case_id','source_document_id','procedure_id',name='uq_eeg_source_procedure'))
def downgrade():
    op.execute('LOCK TABLE eeg_examination, examinations, radiology_finding, radiology_negative_finding IN ACCESS EXCLUSIVE MODE')
    conditions=["EXISTS(SELECT 1 FROM eeg_examination)","EXISTS(SELECT 1 FROM examinations WHERE neck_ultrasound_summary IS NOT NULL)"]
    for name,values in ADDITIONS.items():
        table='radiology_negative_finding' if 'negative' in name else 'radiology_finding'
        conditions.append('EXISTS(SELECT 1 FROM '+table+' WHERE finding_type::text IN ('+','.join("'"+v+"'" for v in values)+'))')
    op.execute("DO $$ BEGIN IF "+' OR '.join(conditions)+" THEN RAISE EXCEPTION 'CT/EEG downgrade would discard clinical data'; END IF; END $$")
    op.drop_table('eeg_examination');op.drop_column('examinations','neck_ultrasound_summary')
    for name,values in OLD.items():
        table='radiology_negative_finding' if 'negative' in name else 'radiology_finding'
        op.execute('ALTER TYPE '+name+' RENAME TO '+name+'_expanded')
        ENUM(*values,name=name).create(op.get_bind())
        op.execute('ALTER TABLE '+table+' ALTER COLUMN finding_type TYPE '+name+' USING finding_type::text::'+name)
        ENUM(name=name+'_expanded').drop(op.get_bind())
    for name in reversed(ENUMS):ENUM(name=name).drop(op.get_bind(),checkfirst=True)
