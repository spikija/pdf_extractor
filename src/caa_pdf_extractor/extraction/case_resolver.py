"""Resolve a string patient identifier to the existing internal case key."""
from sqlalchemy import text


class CaseResolutionError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def resolve_case_id(patient_id: str, *, session=None, session_factory=None) -> int:
    if not isinstance(patient_id, str):
        raise TypeError("patient_id must remain a string")
    if session is None:
        from ..database import SessionLocal
        with (session_factory or SessionLocal)() as current:
            return resolve_case_id(patient_id, session=current)
    # Shared row locks preserve this mapping for the insertion transaction.
    matches = session.execute(text(
        'SELECT id FROM public."case" WHERE fallnr = :patient_id FOR SHARE'
    ), {"patient_id": patient_id}).all()
    if not matches:
        raise CaseResolutionError("CASE_NOT_FOUND")
    if len(matches) != 1:
        raise CaseResolutionError("CASE_AMBIGUOUS")
    return matches[0][0]
