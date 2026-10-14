from datetime import date
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..database.models import Examination

TYPES = tuple(Examination.__table__.c.type.type.enums)
LOCATIONS = tuple(Examination.__table__.c.location.type.enums)
NECK_RESULTS = tuple(Examination.__table__.c.neck_vessel_result.type.enums)


class ExaminationRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    type: str | None = Field(default=None, json_schema_extra={'enum': [*TYPES, None]})
    location: str | None = Field(default=None, json_schema_extra={'enum': [*LOCATIONS, None]})
    result: str = Field(min_length=1, max_length=6000)
    neck_vessel_result: str | None = Field(default=None, json_schema_extra={'enum': [*NECK_RESULTS, None]})
    date_examination: date | None = None
    source_document_id: int | None = Field(default=None, gt=0)
    page_number: int | None = Field(default=None, gt=0)
    section_id: str | None = Field(default=None, min_length=1)
    evidence: str | None = Field(default=None, min_length=1, max_length=6500)

    @field_validator('type','location','neck_vessel_result')
    @classmethod
    def allowed_value(cls, value, info):
        allowed = {'type': TYPES, 'location': LOCATIONS, 'neck_vessel_result': NECK_RESULTS}[info.field_name]
        if value is not None and value not in allowed: raise ValueError('Invalid examination enum')
        return value


class ExaminationExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    patient_id: str
    examination_id: str
    records: list[ExaminationRecord] = Field(max_length=1)
