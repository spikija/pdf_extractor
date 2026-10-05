from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ClinicalHistoryItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    disease: str = Field(min_length=1, max_length=500)
    date_disease: date | None
    disease_category: Literal[None]
    source_document_id: int = Field(gt=0)
    page_number: int = Field(gt=0)
    section_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1, max_length=600)


class ClinicalHistoryExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    patient_id: str = Field(min_length=1)
    clinical_history: list[ClinicalHistoryItem]
