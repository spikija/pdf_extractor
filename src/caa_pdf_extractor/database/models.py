from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Integer,
    JSON, Numeric, SmallInteger, Text, UniqueConstraint, text
)
from sqlalchemy.dialects.postgresql import JSONB
from ..extraction.imaging_schema import (IMAGING_MODALITIES, DATE_SOURCES, RADIOLOGY_PATTERNS,
    FINDING_TYPES, AMYLOID_RESULTS, FDG_RESULTS, FDG_PATTERNS, TEMPORAL_STATUSES,
    NEGATIVE_FINDING_TYPES, ANATOMIC_SCOPES)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..extraction.clinical_schema import NECK_SUMMARIES,EEG_STATUSES,EEG_PRIMARY,EEG_LATERALITY,EEG_TIMING


class Base(DeclarativeBase):
    pass

class Case(Base):
    __tablename__ = 'case'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    fallnr: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    gender: Mapped[str | None] = mapped_column(Enum('woman', 'man', 'other', 'unspecified', 'unknown', '', name='gender_enum_5ac919f3', native_enum=True), nullable=True)
    entry_stamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    years_of_education: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('0'))
    education_level: Mapped[str | None] = mapped_column(Enum('none', 'elementary', 'high school', 'college', 'university', 'post-graduate', 'other', 'unknown', name='education_level_enum_7efaf01a', native_enum=True), nullable=True)
    employment_status: Mapped[str | None] = mapped_column(Enum('active', 'retirement', 'other', 'unknown', name='employment_status_enum_9e21aa67', native_enum=True), nullable=True)
    family_history: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    death_date: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    death_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    apoe: Mapped[str | None] = mapped_column(Enum('not done', 'pending', 'E2E2', 'E2E3', 'E2E4', 'E3E3', 'E3E4', 'E4E4', name='apoe_enum_4069a327', native_enum=True), nullable=True)
    genes: Mapped[str | None] = mapped_column(Text, nullable=True)
    smoking: Mapped[str | None] = mapped_column(Enum('active', 'former', 'unknown', 'never', name='smoking_enum_01d27a4f', native_enum=True), nullable=True)
    pack_years: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('0'))
    alcohol: Mapped[str | None] = mapped_column(Enum('active', 'former', 'unknown', 'never', name='alcohol_enum_58e3c766', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    caa_criteria: Mapped[str | None] = mapped_column(Enum('Boston Criteria 1.5', 'Boston Criteria 2.0', 'Edinburgh Criteria', 'Overlapp', 'Unknown', 'Other', name='caa_criteria_enum_bf701ae9', native_enum=True), nullable=True)
    caa_criteria_stage: Mapped[str | None] = mapped_column(Enum('definite (autopsy)', 'probable with supp. pathology', 'probable', 'possible', 'not categorised', name='caa_criteria_stage_enum_fc49e696', native_enum=True), nullable=True)
    caa_diagnosis_date: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    autopsy: Mapped[str | None] = mapped_column(Enum('irrelevant', 'not done', 'done', 'done with neuropathology', 'unknown or pending', name='autopsy_enum_d73b6252', native_enum=True), nullable=True, server_default=text("'irrelevant'"))
    data_dumped: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))

class ClinicalHistory(Base):
    __tablename__ = 'clinical_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    date_disease: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    disease: Mapped[str | None] = mapped_column(Text, nullable=True)
    hospitalized: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    date_admissioned: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    clinical_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    mri_field_strength: Mapped[str | None] = mapped_column(Text, nullable=True, server_default=text("'-99'"))
    mri_flair: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    mri_t2: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    mmse: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('-99'))
    moca: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('-99'))
    rr_systolic: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('-99'))
    rr_diastolic: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('-99'))
    bmi: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text('-99'))
    date_discharged: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    hospitalized_where: Mapped[str | None] = mapped_column(Enum('cdk', 'other', 'unknown', name='hospitalized_where_enum_660d0340', native_enum=True), nullable=True)
    disease_category: Mapped[str | None] = mapped_column(Enum('intracerebral bleeding', 'ischemic stroke', 'vascular disease', 'cancer', 'other', 'unknown', 'not specified', 'cognitive disorder (dementia)', 'epilepsy', 'transitory neurological episodes (TFNES)', 'metabolic disease', 'arterial hypertension', 'atrial fibrillation', name='disease_category_enum_2cb9966b', native_enum=True), nullable=True)
    rx_mode: Mapped[str | None] = mapped_column(Enum('ct', 'mri', 'other', 'unknown', 'a ', '', name='rx_mode_enum_565bcb50', native_enum=True), nullable=True)
    mri_location: Mapped[str | None] = mapped_column(Enum('cdk', 'other', 'unknown', name='mri_location_enum_5d3b71eb', native_enum=True), nullable=True)
    mri_blood_sequences: Mapped[str | None] = mapped_column(Enum('swi', 'gre', 't2star', 'unknown', 'other', name='mri_blood_sequences_enum_08cb699d', native_enum=True), nullable=True)
    symptoms: Mapped[str | None] = mapped_column(Enum('none', 'headache or dizziness', 'focal neurological deficit', 'cognitive decline', 'gait disorder', 'mood change', 'seizures', 'unknown ', 'not specified', name='symptoms_enum_b56e8af4', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    epilepsy_type: Mapped[str | None] = mapped_column(Enum('focal seizures', 'generalised seizures', 'other', 'unknown', name='epilepsy_type_enum_5db7ea2f', native_enum=True), nullable=True)
    epileptic_status: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    aria_type: Mapped[str | None] = mapped_column(Enum('ARIA-E', 'ARIA-H', 'ARIA-E + H', 'ABRA', 'Combination', 'none', name='aria_type_enum_f672347c', native_enum=True), nullable=True, server_default=text("'none'"))

class Examination(Base):
    __tablename__ = 'examinations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    type: Mapped[str | None] = mapped_column(Enum('ultrasound', 'pet ct amyloid', 'pet ct fdg', 'spect', 'mri body', 'other', name='type_enum_3269a85d', native_enum=True), nullable=True)
    location: Mapped[str | None] = mapped_column(Enum('neck vessels', 'brain', 'heart', 'body', 'abdomen', 'other', name='location_enum_32c78b5f', native_enum=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    neck_vessel_result: Mapped[str | None] = mapped_column(Enum('ica > 50perc right stenosis', 'ica > 50perc left stenosis', 'acm right > 50 perc stenosis', 'acm left >50perc stenosis', 'other cerebral vessel stenosis', 'unknown', name='neck_vessel_result_enum_a1381347', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    date_examination: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    amyloid_pet_result: Mapped[str | None] = mapped_column(Enum(*AMYLOID_RESULTS, name='amyloid_pet_result'), nullable=True)
    fdg_pet_result: Mapped[str | None] = mapped_column(Enum(*FDG_RESULTS, name='fdg_pet_result'), nullable=True)
    fdg_pet_pattern: Mapped[str | None] = mapped_column(Enum(*FDG_PATTERNS, name='fdg_pet_pattern'), nullable=True)
    neck_ultrasound_summary: Mapped[str | None] = mapped_column(Enum(*NECK_SUMMARIES,name='neck_ultrasound_summary'),nullable=True)


class EEGExamination(Base):
    __tablename__='eeg_examination'
    __table_args__=(UniqueConstraint('case_id','source_document_id','procedure_id',name='uq_eeg_source_procedure'),)
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    case_id: Mapped[int]=mapped_column(Integer,ForeignKey('case.id'),nullable=False)
    source_document_id: Mapped[int]=mapped_column(BigInteger,ForeignKey('source_documents.id'),nullable=False)
    procedure_id: Mapped[str]=mapped_column(Text,nullable=False)
    eeg_date: Mapped[date | None]=mapped_column(Date,nullable=True)
    eeg_status: Mapped[str]=mapped_column(Enum(*EEG_STATUSES,name='eeg_status'),nullable=False)
    eeg_primary_finding: Mapped[str | None]=mapped_column(Enum(*EEG_PRIMARY,name='eeg_primary_finding'),nullable=True)
    eeg_laterality: Mapped[str | None]=mapped_column(Enum(*EEG_LATERALITY,name='eeg_laterality'),nullable=True)
    eeg_tfne_timing: Mapped[str | None]=mapped_column(Enum(*EEG_TIMING,name='eeg_tfne_timing'),nullable=True)
    eeg_clinical_event_captured: Mapped[bool | None]=mapped_column(Boolean,nullable=True)
    eeg_focal_slowing: Mapped[bool | None]=mapped_column(Boolean,nullable=True)
    eeg_epileptiform_discharges: Mapped[bool | None]=mapped_column(Boolean,nullable=True)
    eeg_rhythmic_periodic_pattern: Mapped[bool | None]=mapped_column(Boolean,nullable=True)
    eeg_electrographic_seizure: Mapped[bool | None]=mapped_column(Boolean,nullable=True)
    eeg_report_text: Mapped[str]=mapped_column(Text,nullable=False)
    deleted: Mapped[bool]=mapped_column(Boolean,nullable=False,server_default=text('FALSE'))


class RadiologyExam(Base):
    __tablename__ = 'radiology_exam'
    __table_args__ = (UniqueConstraint('case_id','source_document_id','procedure_id',name='uq_radiology_exam_source_procedure'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    anatomic_scope: Mapped[str | None] = mapped_column(Enum(*ANATOMIC_SCOPES,name='radiology_anatomic_scope'), nullable=True)
    performed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey('case.id'), nullable=False)
    source_document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('source_documents.id'), nullable=False)
    procedure_id: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str] = mapped_column(Enum(*IMAGING_MODALITIES, name='imaging_modality'), nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exam_date_source: Mapped[str] = mapped_column(Enum(*DATE_SOURCES, name='date_source'), nullable=False)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_pattern: Mapped[str | None] = mapped_column(Enum(*RADIOLOGY_PATTERNS, name='radiology_pattern'), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))


class RadiologyNegativeFinding(Base):
    __tablename__ = 'radiology_negative_finding'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    radiology_exam_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('radiology_exam.id'), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(Enum(*NEGATIVE_FINDING_TYPES,name='radiology_negative_finding_type'), nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))


class RadiologyFinding(Base):
    __tablename__ = 'radiology_finding'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    radiology_exam_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('radiology_exam.id'), nullable=False)
    finding_type: Mapped[str] = mapped_column(Enum(*FINDING_TYPES, name='radiology_finding_type'), nullable=False)
    acute_lesion: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    temporal_status: Mapped[str | None] = mapped_column(Enum(*TEMPORAL_STATUSES, name='radiology_temporal_status'), nullable=True)
    interval_new: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fazekas_grade: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lobar: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deep: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    infratentorial: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cortical: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subcortical: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    distribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    laterality: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

class FamilyHistory(Base):
    __tablename__ = 'family_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    relative: Mapped[str | None] = mapped_column(Text, nullable=True)
    disease: Mapped[str | None] = mapped_column(Text, nullable=True)
    relative_category: Mapped[str | None] = mapped_column(Enum('first grade', 'second grade', 'unknown', 'not specified', 'Option', name='relative_category_enum_6d45e69e', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))

class Laboratory(Base):
    __tablename__ = 'laboratory'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    date_of_exam: Mapped[date | None] = mapped_column(Date, nullable=True)
    csf_beta42: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    csf_beta4240_ratio: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    csf_tau_total: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    csf_ptau_total: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    crp: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    hba1c: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))

class Medication(Base):
    __tablename__ = 'medications'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    date_noted: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    medication: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_stopped: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    medication_category: Mapped[str | None] = mapped_column(Enum('blood pressure control', 'antithrombotic', 'antidiabetic', 'anticoagulant old', 'anticoagulant new', 'beta blocker', 'diuretic', 'cancer medications', 'other', 'unknown', name='medication_category_enum_1f8daa6a', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))

class Radiology(Base):
    __tablename__ = 'radiology'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    case_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('case.id'), nullable=True)
    acute_lesion: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    type: Mapped[str | None] = mapped_column(Enum('lobar', 'basal ganglia', 'subarachnoidal bleeding', 'cmb', 'css', 'other', 'unknown', 'not specified', name='type_enum_bf3e5d20', native_enum=True), nullable=True)
    deleted: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))
    mri_date: Mapped[date | None] = mapped_column(Date, nullable=True, server_default=text('CURRENT_DATE'))
    pid: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_on_hdd: Mapped[bool | None] = mapped_column(Boolean, nullable=True, server_default=text('FALSE'))

class State(Base):
    __tablename__ = 'states'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    field: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)

class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    parser: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(Text)
    document_json: Mapped[dict | None] = mapped_column(JSONB)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    validation_errors: Mapped[dict | list | None] = mapped_column(JSONB)
