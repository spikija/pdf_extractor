"""Extract patient-folder PDFs to JSON and PostgreSQL provenance tables."""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .utils import parse_pdf, save_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def discover_pdfs(pdf_root: Path) -> list[Path]:
    return sorted(p for p in pdf_root.rglob("*")
                  if p.is_file() and p.suffix.lower() == ".pdf")


def get_patient_id(pdf_path: Path, pdf_root: Path) -> str:
    relative = pdf_path.relative_to(pdf_root)
    if len(relative.parts) < 2:
        raise ValueError("PDF must be inside a patient folder")
    return relative.parts[0]


def get_relative_path(pdf_path: Path, pdf_root: Path) -> str:
    return pdf_path.relative_to(pdf_root).as_posix()


def store_document(document_json: dict, session_factory=None) -> str:
    from .database import SessionLocal
    from .database.models import ExtractionRun, SourceDocument

    metadata = document_json["document"]
    with (session_factory or SessionLocal).begin() as session:
        statement = insert(SourceDocument).values(
            filename=metadata["filename"], sha256=metadata["sha256"],
            file_size_bytes=metadata["file_size_bytes"],
            parser=metadata["parser"], parser_version=metadata["parser_version"],
            document_json=document_json,
        ).on_conflict_do_nothing(index_elements=["sha256"]).returning(SourceDocument.id)
        document_id = session.scalar(statement)
        if document_id is None:
            # Repair legacy records that contain metadata but no parsed payload.
            existing = session.scalar(select(SourceDocument).where(
                SourceDocument.sha256 == metadata["sha256"]).with_for_update())
            if existing.document_json is not None:
                return "SKIPPED"
            existing.document_json = document_json
            existing.parser = metadata["parser"]
            existing.parser_version = metadata["parser_version"]
            existing.file_size_bytes = metadata["file_size_bytes"]
            document_id = existing.id
        stats = document_json["statistics"]
        session.add(ExtractionRun(
            source_document_id=document_id, model_name=metadata["parser"],
            model_version=metadata["parser_version"],
            prompt_version=document_json["schema_version"],
            status="needs_ocr" if stats["pages_without_text"] else "completed",
            completed_at=datetime.now(timezone.utc),
            raw_output={"statistics": stats, "relative_path": metadata["relative_path"],
                        "patient_id": metadata["patient_id"]},
        ))
    return "INGESTED"


def ingest_pdf(pdf_path: Path, pdf_root: Path, *, parsed_root: Path | None = None,
               dry_run: bool = False, session_factory=None) -> dict:
    try:
        patient_id = get_patient_id(pdf_path, pdf_root)
        relative_path = get_relative_path(pdf_path, pdf_root)
        payload = parse_pdf(pdf_path, relative_path, patient_id)
        metadata = payload["document"]
        output = (parsed_root or PROJECT_ROOT / "data" / "parsed") / patient_id
        save_json(payload, output / f"{pdf_path.stem}__{metadata['sha256'][:8]}.json")
        status = "PARSED" if dry_run else store_document(payload, session_factory)
        return {"status": status, "page_count": metadata["page_count"],
                "sha256": metadata["sha256"], "stats": payload["statistics"]}
    except Exception as exc:
        # SQL exception strings can contain entire medical JSON payloads.
        return {"status": "FAILED", "error": type(exc).__name__}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-root", type=Path, default=PROJECT_ROOT / "pdf_db")
    parser.add_argument("--parsed-root", type=Path, default=PROJECT_ROOT / "data" / "parsed")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Parse and save JSON without database writes")
    args = parser.parse_args(argv)
    if not args.pdf_root.is_dir() or (args.limit is not None and args.limit < 1):
        parser.error("PDF root must exist and limit must be positive")
    files = discover_pdfs(args.pdf_root)
    if args.limit is not None:
        files = files[:args.limit]
    if not files:
        print("No PDF files found")
        return 1
    counts = {}
    for number, path in enumerate(files, 1):
        result = ingest_pdf(path, args.pdf_root, parsed_root=args.parsed_root, dry_run=args.dry_run)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
        stats = result.get("stats", {})
        print(f"{number}/{len(files)} {status} pages={result.get('page_count', 0)} "
              f"characters={stats.get('total_characters', 0)} "
              f"pages_without_text={stats.get('pages_without_text', 0)}"
              + (f" error={result['error']}" if "error" in result else ""), flush=True)
    print(f"Results: {counts}")
    return int(bool(counts.get("FAILED")))


if __name__ == "__main__":
    raise SystemExit(main())
