"""Add sensory presentation and longitudinal CAA diagnostic events; no backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM,JSONB

revision='0011_sensory_caa_diagnostic'
down_revision='0010_cognitive_evidence'
branch_labels=None
depends_on=None
ENUMS={
 'sensory_polarity':('POSITIVE','NEGATIVE','MIXED','UNKNOWN'),
 'sensory_type':('PARESTHESIA','HYPOESTHESIA','HYPERESTHESIA','DYSESTHESIA','NUMBNESS','TINGLING','BURNING','OTHER','UNKNOWN'),
 'sensory_laterality':('LEFT','RIGHT','BILATERAL','MIDLINE','NOT_LOCALIZABLE','UNKNOWN'),
 'sensory_distribution':('FACE','MOUTH','TONGUE','UPPER_EXTREMITY','LOWER_EXTREMITY','TRUNK','HEMIBODY','MULTIFOCAL','OTHER','UNKNOWN'),
 'caa_event_type':('SUSPICION','CLINICAL_DIAGNOSIS','CRITERIA_CLASSIFICATION','PATHOLOGY_CONFIRMATION'),
 'caa_event_status':('SUSPECTED','POSSIBLE','PROBABLE','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE','DIAGNOSED_UNSPECIFIED','UNKNOWN'),
 'caa_event_criteria':('BOSTON_1_0','BOSTON_1_5','BOSTON_2_0','OTHER','UNSPECIFIED'),
 'caa_event_precision':('DAY','MONTH','YEAR','UNKNOWN'),
 'caa_event_date_basis':('EXPLICIT_DIAGNOSIS_DATE','EXPLICIT_MONTH_YEAR','EXPLICIT_YEAR','CURRENT_DIAGNOSTIC_ENCOUNTER','UNKNOWN')}
OLD_REASONS=('FOCAL_NEUROLOGICAL_DEFICIT','EPILEPTIC_EVENT','COGNITIVE_DECLINE','OTHER','UNKNOWN')
DATE_SHAPE="""((date_precision='DAY' AND event_date IS NOT NULL
 AND event_year=extract(year from event_date) AND event_month=extract(month from event_date)
 AND date_basis IN ('EXPLICIT_DIAGNOSIS_DATE','CURRENT_DIAGNOSTIC_ENCOUNTER')) OR
 (date_precision='MONTH' AND event_date IS NULL AND event_year BETWEEN 1000 AND 9999
 AND event_month BETWEEN 1 AND 12 AND date_basis='EXPLICIT_MONTH_YEAR') OR
 (date_precision='YEAR' AND event_date IS NULL AND event_year BETWEEN 1000 AND 9999
 AND event_month IS NULL AND date_basis='EXPLICIT_YEAR') OR
 (date_precision='UNKNOWN' AND event_date IS NULL AND event_year IS NULL
 AND event_month IS NULL AND date_basis='UNKNOWN')) IS TRUE"""
DURATION_SHAPE="sensory_duration_minutes IS NULL OR (sensory_duration_minutes>=0 AND sensory_duration_minutes::text NOT IN ('NaN','Infinity','-Infinity'))"
STATUS_SHAPE="""((event_type='SUSPICION' AND caa_status IN ('SUSPECTED','UNKNOWN')) OR
 (event_type='CLINICAL_DIAGNOSIS' AND caa_status IN ('DIAGNOSED_UNSPECIFIED','POSSIBLE','PROBABLE','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE','UNKNOWN')) OR
 (event_type='CRITERIA_CLASSIFICATION' AND caa_status IN ('POSSIBLE','PROBABLE','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE','UNKNOWN')) OR
 (event_type='PATHOLOGY_CONFIRMATION' AND caa_status IN ('PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE'))) IS TRUE"""
SENSORY_COLUMNS=('admission_sensory_symptoms','sensory_symptom_present','sensory_polarity','sensory_type',
 'sensory_laterality','sensory_distribution','sensory_spreading','sensory_transient','sensory_duration_minutes',
 'sensory_stereotyped_recurrent','sensory_assessment_json')


def enum(name):
    return ENUM(*ENUMS[name],name=name,create_type=False)


def upgrade():
    op.execute("ALTER TYPE admission_reason_primary ADD VALUE IF NOT EXISTS 'SENSORY_SYMPTOMS'")
    for name,values in ENUMS.items():
        ENUM(*values,name=name).create(op.get_bind(),checkfirst=True)
    for name in ('admission_sensory_symptoms','sensory_symptom_present','sensory_spreading','sensory_transient','sensory_stereotyped_recurrent'):
        op.add_column('hospital_encounter',sa.Column(name,sa.Boolean(),nullable=True))
    for name in ('sensory_polarity','sensory_type','sensory_laterality','sensory_distribution'):
        op.add_column('hospital_encounter',sa.Column(name,enum(name),nullable=True))
    op.add_column('hospital_encounter',sa.Column('sensory_duration_minutes',sa.Numeric(),nullable=True))
    op.add_column('hospital_encounter',sa.Column('sensory_assessment_json',JSONB(),nullable=True))
    op.create_check_constraint('ck_encounter_sensory_duration','hospital_encounter',DURATION_SHAPE)
    op.create_table('caa_diagnostic_event',
      sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
      sa.Column('case_id',sa.Integer(),sa.ForeignKey('case.id'),nullable=False),
      sa.Column('source_document_id',sa.BigInteger(),sa.ForeignKey('source_documents.id'),nullable=False),
      sa.Column('event_type',enum('caa_event_type'),nullable=False),
      sa.Column('caa_status',enum('caa_event_status'),nullable=False),
      sa.Column('criteria_system',enum('caa_event_criteria'),nullable=False),
      sa.Column('event_date',sa.Date()),sa.Column('event_year',sa.SmallInteger()),sa.Column('event_month',sa.SmallInteger()),
      sa.Column('date_precision',enum('caa_event_precision'),nullable=False),
      sa.Column('date_basis',enum('caa_event_date_basis'),nullable=False),
      sa.Column('evidence_text',sa.Text(),nullable=False),sa.Column('page',sa.Integer(),nullable=False),
      sa.Column('section_id',sa.Text(),nullable=False),sa.Column('source_start',sa.Integer(),nullable=False),sa.Column('source_end',sa.Integer(),nullable=False),
      sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),
      sa.UniqueConstraint('case_id','source_document_id','section_id','source_start','source_end','event_type','caa_status','criteria_system',name='uq_caa_diagnostic_source_event'),
      sa.CheckConstraint(DATE_SHAPE,name='ck_caa_diagnostic_date'),
      sa.CheckConstraint(STATUS_SHAPE,name='ck_caa_diagnostic_status'),
      sa.CheckConstraint('page>0 AND source_start>=0 AND source_end>source_start AND length(evidence_text)=source_end-source_start',name='ck_caa_diagnostic_source'))
    op.create_index('ix_caa_diagnostic_case_date','caa_diagnostic_event',['case_id','event_date','event_year','event_month'])


def downgrade():
    op.execute('LOCK TABLE caa_diagnostic_event, hospital_encounter IN ACCESS EXCLUSIVE MODE')
    condition=' OR '.join(c+' IS NOT NULL' for c in SENSORY_COLUMNS)
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM caa_diagnostic_event) OR EXISTS(SELECT 1 FROM hospital_encounter WHERE admission_reason_primary::text='SENSORY_SYMPTOMS' OR "+condition+") THEN RAISE EXCEPTION 'Downgrade would discard sensory/CAA evidence'; END IF; END $$")
    op.drop_table('caa_diagnostic_event')
    op.drop_constraint('ck_encounter_sensory_duration','hospital_encounter',type_='check')
    for name in reversed(SENSORY_COLUMNS):
        op.drop_column('hospital_encounter',name)
    op.execute('ALTER TABLE hospital_encounter ALTER COLUMN admission_reason_primary TYPE text USING admission_reason_primary::text')
    op.execute('DROP TYPE admission_reason_primary')
    ENUM(*OLD_REASONS,name='admission_reason_primary').create(op.get_bind())
    op.execute('ALTER TABLE hospital_encounter ALTER COLUMN admission_reason_primary TYPE admission_reason_primary USING admission_reason_primary::admission_reason_primary')
    for name in reversed(ENUMS):
        ENUM(name=name).drop(op.get_bind(),checkfirst=True)
