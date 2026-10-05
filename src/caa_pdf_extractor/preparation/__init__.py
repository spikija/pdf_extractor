"""Derived patient document preparation, independent of clinical extraction."""
from .models import (PatientDocumentBundle, PreparedDocument, PreparedSection, PreparedTable,
                     DiagnosticProcedure, DiagnosticSubprocedure)
from .diagnostic_splitter import split_diagnostic_procedures


def build_patient_bundle(patient_id, session_factory=None):
    from .patient_bundle import build_patient_bundle as build
    return build(patient_id, session_factory)


__all__ = ["PatientDocumentBundle", "PreparedDocument", "PreparedSection",
           "PreparedTable", "DiagnosticProcedure", "DiagnosticSubprocedure",
           "split_diagnostic_procedures", "build_patient_bundle"]
