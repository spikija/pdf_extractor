import json
import re
from hashlib import sha256
from pathlib import Path
from statistics import median

import pymupdf


def get_sha256(file_path: Path) -> str:
    digest = sha256()

    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _is_bold(span: dict) -> bool:
    flags = int(span.get("flags", 0))
    font_name = str(span.get("font", "")).lower()

    return bool(flags & 16) or "bold" in font_name


def _extract_blocks(page) -> list[dict]:
    """
    Extract text blocks, lines and spans from PyMuPDF's dict representation.
    """

    text_dict = page.get_text("dict", sort=True)

    pymupdf_blocks = text_dict.get("blocks", [])
    output_blocks = []

    all_font_sizes = []

    # First pass: collect font sizes from text blocks.
    for block in pymupdf_blocks:
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")
                if isinstance(size, (int, float)):
                    all_font_sizes.append(float(size))

    median_font_size = median(all_font_sizes) if all_font_sizes else 0.0

    # Second pass: construct our JSON representation.
    block_index = 0

    for block in pymupdf_blocks:
        # type 0 = text, type 1 = image
        if block.get("type") != 0:
            continue

        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue

        lines_json = []
        block_text_parts = []

        block_has_bold = False
        block_has_large_font = False

        for line in block.get("lines", []):
            line_bbox = line.get("bbox")
            spans_json = []
            line_text_parts = []

            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue

                span_bbox = span.get("bbox")
                font_size = float(span.get("size", 0.0))
                bold = _is_bold(span)

                if bold:
                    block_has_bold = True

                if (
                    median_font_size > 0
                    and font_size >= median_font_size * 1.15
                ):
                    block_has_large_font = True

                spans_json.append(
                    {
                        "text": text,
                        "bbox": list(span_bbox) if span_bbox else None,
                        "font": span.get("font"),
                        "font_size": font_size,
                        "flags": int(span.get("flags", 0)),
                        "is_bold": bold,
                    }
                )

                line_text_parts.append(text)

            line_text = "".join(line_text_parts).strip()

            if line_text:
                block_text_parts.append(line_text)

            lines_json.append(
                {
                    "bbox": list(line_bbox) if line_bbox else None,
                    "text": line_text,
                    "spans": spans_json,
                }
            )

        block_text = "\n".join(block_text_parts).strip()

        # Conservative typography-based heading heuristic.
        possible_heading = False

        if block_text:
            if (
                len(block_text) <= 120
                and (block_has_bold or block_has_large_font)
            ):
                possible_heading = True

        output_blocks.append(
            {
                "block_index": block_index,
                "bbox": list(bbox),
                "text": block_text,
                "lines": lines_json,
                "possible_heading": possible_heading,
                "repeated_header": False,
                "repeated_footer": False,
            }
        )

        block_index += 1

    return output_blocks


def _extract_tables(page) -> list[dict]:
    """
    Table extraction must never cause the PDF parser itself to fail.
    Returns list of {"rows": [...]}.
    """
    output = []

    try:
        finder = page.find_tables()
        tables = getattr(finder, "tables", [])

        for table in tables:
            try:
                rows = table.extract()
            except Exception:
                rows = []

            # Do not store bbox or table_index
            output.append({"rows": rows})

    except Exception:
        # Table detection is auxiliary.
        return []

    return output


def parse_pdf(
    pdf_path: Path,
    relative_path: str,
    patient_id: str,
) -> dict:
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF: {pdf_path.name}")

    pages_json = []

    total_characters = 0
    pages_with_text = 0
    total_tables = 0
    total_blocks = 0
    total_headings = 0

    with pymupdf.open(pdf_path) as document:
        page_count = document.page_count

        for page_number, page in enumerate(document, start=1):
            raw_text = page.get_text(
                "text",
                sort=True,
            )
            character_count = len(raw_text)

            meaningful_text = bool(raw_text.strip())
            if meaningful_text:
                pages_with_text += 1

            total_characters += character_count

            blocks = _extract_blocks(page)
            page_headings = [
                block["text"] for block in blocks
                if block["possible_heading"]
                and not re.match(r"^\d+\.\s", block["text"])
            ]

            total_blocks += len(blocks)
            total_headings += len(page_headings)

            # Get tables (compact)
            tables = _extract_tables(page)

            total_tables += len(tables)

            page_json = {
                "page_number": page_number,
                "text": raw_text,
                "headings": page_headings,
                "tables": tables,
            }
            pages_json.append(page_json)

    pages_without_text = page_count - pages_with_text

    file_hash = get_sha256(pdf_path)

    return {
        "schema_version": "2.0",
        "document": {
            "patient_id": str(patient_id),
            "filename": pdf_path.name,
            "relative_path": relative_path,
            "sha256": file_hash,
            "file_size_bytes": pdf_path.stat().st_size,
            "page_count": page_count,
            "parser": "PyMuPDF",
            "parser_version": pymupdf.__version__,
            "text_extraction_method": "native",
        },
        "pages": pages_json,
        "statistics": {
            "total_characters": total_characters,
            "pages_with_text": pages_with_text,
            "pages_without_text": pages_without_text,
            "tables_detected": total_tables,
            "blocks_detected": total_blocks,
            "possible_headings": total_headings,
        },
    }


def save_json(
    data: dict,
    output_path: Path,
) -> None:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )