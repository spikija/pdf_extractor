"""Derived patient document preparation, independent of clinical extraction."""
from .models import PatientDocumentBundle, PreparedDocument, PreparedSection, PreparedTable


def build_patient_bundle(patient_id, session_factory=None):
    from .patient_bundle import build_patient_bundle as build
    return build(patient_id, session_factory)


__all__ = ["PatientDocumentBundle", "PreparedDocument", "PreparedSection",
           "PreparedTable", "build_patient_bundle"]
