"""Strict medication payload; enum values come from the canonical ORM."""
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..database.models import Medication

MEDICATION_CATEGORIES = tuple(Medication.__table__.c.medication_category.type.enums)


class MedicationItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    medication: str = Field(min_length=1, max_length=1000,
                            description="Complete medication instructions. For table entries copy the provided medication string INCLUDING every dose cell; must equal source_text.")
    date_noted: date | None
    date_stopped: date | None
    medication_category: str | None
    source_document_id: int = Field(gt=0)
    page_number: int = Field(gt=0)
    section_id: str = Field(min_length=1)
    source_table_index: int | None
    source_text: str = Field(min_length=1, max_length=1200)

    @field_validator("medication_category")
    @classmethod
    def allowed_category(cls, value):
        if value is not None and value not in MEDICATION_CATEGORIES:
            raise ValueError("Invalid medication category")
        return value


class MedicationExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    patient_id: str = Field(min_length=1)
    medications: list[MedicationItem]
