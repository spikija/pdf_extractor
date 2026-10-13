"""MRI phenotypes persisted to radiology; descriptive findings are audit-only."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..database.models import Radiology

RADIOLOGY_TYPES = tuple(Radiology.__table__.c.type.type.enums)


class Phenotype(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    type: str = Field(json_schema_extra={"enum": list(RADIOLOGY_TYPES)})
    acute_lesion: bool | None
    evidence: str = Field(min_length=1, max_length=800)

    @field_validator('type')
    @classmethod
    def allowed_type(cls, value):
        if value not in RADIOLOGY_TYPES:
            raise ValueError('Invalid radiology phenotype')
        return value


class AdditionalFindings(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    caa_suspicion_explicit: bool | None
    caa_evidence: str | None = Field(max_length=800)
    microbleed_description: str | None = Field(max_length=800)
    other_findings: list[str] = Field(max_length=30)


class RadiologyExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    patient_id: str = Field(min_length=1)
    procedure_id: str = Field(min_length=1)
    findings: list[Phenotype] = Field(max_length=30)
    additional_findings: AdditionalFindings
    # Accepted solely to reconcile unsolicited metadata in a model response.
    # None of these fields ever determine the normalized event or DB values.
    mri_date: str | None = None
    modality: str | None = None
    source_document_id: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    studies: list[str] | None = None
