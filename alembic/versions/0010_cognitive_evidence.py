"""Separate canonical test events from repeated/unresolved evidence; no data DML."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

revision = '0010_cognitive_evidence'
down_revision = '0009_cognitive_assessment'
branch_labels = None
depends_on = None

ROLES = ('CURRENT_PERFORMED','HISTORICAL_REFERENCE','EXTERNAL_REPORT','COPIED_FORWARD','UNKNOWN_MENTION')
SCORE_SHAPE = """(score >= 0 AND max_score > 0 AND score <= max_score
 AND (test_type NOT IN ('MMSE','MOCA') OR (max_score=30 AND score BETWEEN 0 AND 30))) IS TRUE"""
SOURCE_SHAPE = """page > 0 AND source_start >= 0 AND source_end > source_start
 AND length(evidence_text)=source_end-source_start
 AND score_start >= source_start AND score_end > score_start AND score_end <= source_end"""
ROLE_SHAPE = """((cognitive_assessment_id IS NULL AND evidence_role='UNKNOWN_MENTION') OR
 (cognitive_assessment_id IS NOT NULL AND evidence_role<>'UNKNOWN_MENTION')) IS TRUE"""


def upgrade():
    # Existing source-mention rows require a separate reviewed conversion. Never
    # silently reinterpret populated legacy cognitive rows as canonical events.
    op.execute('LOCK TABLE cognitive_assessment IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM cognitive_assessment) THEN RAISE EXCEPTION 'Canonical evidence migration requires empty cognitive_assessment'; END IF; END $$")
    ENUM(*ROLES, name='cognitive_evidence_role').create(op.get_bind(), checkfirst=True)
    op.create_unique_constraint('uq_cognitive_event_owner_score','cognitive_assessment', ['id','case_id','test_type','score','max_score'])
    op.create_table('cognitive_assessment_evidence',
        sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('cognitive_assessment_id',sa.BigInteger(),nullable=True),
        sa.Column('case_id',sa.Integer(),sa.ForeignKey('case.id'),nullable=False),
        sa.Column('source_document_id',sa.BigInteger(),sa.ForeignKey('source_documents.id'),nullable=False),
        sa.Column('page',sa.Integer(),nullable=False), sa.Column('section_id',sa.Text(),nullable=False),
        sa.Column('source_start',sa.Integer(),nullable=False), sa.Column('source_end',sa.Integer(),nullable=False),
        sa.Column('score_start',sa.Integer(),nullable=False), sa.Column('score_end',sa.Integer(),nullable=False),
        sa.Column('evidence_text',sa.Text(),nullable=False),
        sa.Column('evidence_role',ENUM(*ROLES,name='cognitive_evidence_role',create_type=False),nullable=False),
        sa.Column('test_type',ENUM('MMSE','MOCA','OTHER',name='cognitive_test_type',create_type=False),nullable=False),
        sa.Column('score',sa.SmallInteger(),nullable=False), sa.Column('max_score',sa.SmallInteger(),nullable=False),
        sa.Column('date_text',sa.Text(),nullable=True),
        sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.ForeignKeyConstraint(['cognitive_assessment_id','case_id','test_type','score','max_score'],
            ['cognitive_assessment.id','cognitive_assessment.case_id','cognitive_assessment.test_type',
             'cognitive_assessment.score','cognitive_assessment.max_score'],name='fk_cognitive_evidence_event_owner_score'),
        sa.UniqueConstraint('case_id','source_document_id','section_id','score_start','score_end','test_type',name='uq_cognitive_evidence_score_token'),
        sa.CheckConstraint(SCORE_SHAPE,name='ck_cognitive_evidence_score'),
        sa.CheckConstraint(SOURCE_SHAPE,name='ck_cognitive_evidence_source'),
        sa.CheckConstraint(ROLE_SHAPE,name='ck_cognitive_evidence_role_link'))
    op.create_index('ix_cognitive_evidence_event','cognitive_assessment_evidence',['cognitive_assessment_id'])
    op.create_index('ix_cognitive_evidence_case_document','cognitive_assessment_evidence',['case_id','source_document_id'])


def downgrade():
    op.execute('LOCK TABLE cognitive_assessment_evidence, cognitive_assessment IN ACCESS EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM cognitive_assessment_evidence) OR EXISTS(SELECT 1 FROM cognitive_assessment) THEN RAISE EXCEPTION 'Downgrade would discard canonical identity/evidence'; END IF; END $$")
    op.drop_table('cognitive_assessment_evidence')
    op.drop_constraint('uq_cognitive_event_owner_score','cognitive_assessment',type_='unique')
    ENUM(name='cognitive_evidence_role').drop(op.get_bind(),checkfirst=True)
