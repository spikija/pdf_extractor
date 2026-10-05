"""Read-only patient bundle builder and content-free debug summary CLI."""
import argparse
from collections import Counter
from pathlib import Path

from .document_classifier import classify_document, detect_document_date
from .models import PatientDocumentBundle, PreparedDocument
from .sectionizer import sectionize


def prepare_document(patient_id: str, source_document_id: int, filename: str,
                     sha256: str, document_json: dict) -> PreparedDocument:
    metadata = document_json["document"]
    if str(metadata["patient_id"]) != patient_id:
        raise ValueError("Source document does not belong to requested patient")
    pages = document_json.get("pages", [])
    sections, noise = sectionize(pages, patient_id, source_document_id)
    return PreparedDocument(
        source_document_id=source_document_id, filename=filename,
        relative_path=metadata["relative_path"], sha256=sha256,
        document_type=classify_document(pages), document_date=detect_document_date(pages),
        sections=sections, excluded_noise=noise,
    )


def build_patient_bundle(patient_id: str, session_factory=None) -> PatientDocumentBundle:
    from sqlalchemy import select
    from ..database import SessionLocal
    from ..database.models import SourceDocument

    patient_id = str(patient_id)
    if not patient_id:
        raise ValueError("patient_id must not be empty")
    # No commits, inserts, updates, or ORM attribute assignments in this layer.
    with (session_factory or SessionLocal)() as session:
        rows = session.scalars(select(SourceDocument).where(
            SourceDocument.document_json["document"]["patient_id"].astext == patient_id
        ).order_by(SourceDocument.id)).all()
        documents = [prepare_document(patient_id, row.id, row.filename, row.sha256,
                                      row.document_json) for row in rows]
    return PatientDocumentBundle(patient_id=patient_id, documents=documents)


def bundle_summary(bundle: PatientDocumentBundle) -> dict:
    targets = Counter(target for document in bundle.documents for section in document.sections
                      for target in section.candidate_targets)
    return {
        "patient_id": bundle.patient_id,
        "number_of_documents": len(bundle.documents),
        "document_types": [document.document_type for document in bundle.documents],
        "sections_per_document": [len(document.sections) for document in bundle.documents],
        "candidate_target_counts": dict(sorted(targets.items())),
        "number_of_tables": sum(len(section.tables) for document in bundle.documents
                                for section in document.sections),
    }


def main(argv=None) -> int:
    import json
    from ..utils import save_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patient_id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    bundle = build_patient_bundle(args.patient_id)
    # Avoid allowing a patient ID to introduce directories into the default path.
    if args.output is None and ("/" in args.patient_id or "\\" in args.patient_id
                                or args.patient_id in (".", "..")):
        parser.error("Specify --output for a patient ID containing path separators")
    output = args.output or Path(__file__).resolve().parents[3] / "data" / "prepared" / f"{args.patient_id}.json"
    save_json(bundle.to_dict(), output)
    print(json.dumps(bundle_summary(bundle), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
