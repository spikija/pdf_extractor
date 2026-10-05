"""PyMuPDF smoke test; prints only technical counts."""
from pathlib import Path
import pymupdf

pdf = Path(__file__).resolve().parent / "pdf_db" / "2473679415" / "1.pdf"
with pymupdf.open(pdf) as doc:
    page = doc[0]
    blocks = page.get_text("dict", sort=True)["blocks"]
    spans = [span for block in blocks if block.get("type") == 0
             for line in block.get("lines", []) for span in line.get("spans", [])]
    assert spans
    print("pages:", len(doc), "text spans on first page:", len(spans))
