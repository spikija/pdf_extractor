"""Serializable, derived preparation records; no clinical database models."""
from dataclasses import asdict, dataclass, field


@dataclass
class PreparedTable:
    page_number: int
    table_index: int
    rows: list
    association: str


@dataclass
class PreparedSection:
    section_id: str
    patient_id: str
    source_document_id: int
    heading: str | None
    page_start: int
    page_end: int
    text: str
    source_text: str
    candidate_targets: list[str] = field(default_factory=list)
    tables: list[PreparedTable] = field(default_factory=list)


@dataclass
class DiagnosticSubprocedure:
    study: str
    result_text: str


@dataclass
class DiagnosticProcedure:
    procedure_id: str
    patient_id: str
    source_document_id: int
    page_start: int
    page_end: int
    modality: str
    date: str | None
    studies: list[str]
    subprocedures: list[DiagnosticSubprocedure]
    result_text: str
    source_text: str
    candidate_target: str = "radiology"


@dataclass
class PreparedDocument:
    source_document_id: int
    filename: str
    relative_path: str
    sha256: str
    document_type: str
    document_date: str | None
    sections: list[PreparedSection]
    excluded_noise: list[dict] = field(default_factory=list)
    diagnostic_procedures: list[DiagnosticProcedure] = field(default_factory=list)


@dataclass
class PatientDocumentBundle:
    patient_id: str
    documents: list[PreparedDocument]
    preparation_version: str = "1.1"

    def to_dict(self) -> dict:
        return asdict(self)
