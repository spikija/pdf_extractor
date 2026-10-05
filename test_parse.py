import sys
from pathlib import Path

sys.path.insert(0, r"C:\app\pdf_extractor\src")

from caa_pdf_extractor.utils import parse_pdf, save_json


pdf_root = Path(r"C:\app\pdf_extractor\pdf_db")
pdf_path = pdf_root / "2473679415" / "1.pdf"

assert pdf_path.exists(), f"PDF not found: {pdf_path}"

relative = pdf_path.relative_to(pdf_root)

if len(relative.parts) < 2:
    raise ValueError("PDF must be inside a patient folder")

patient_id = relative.parts[0]
relative_path = relative.as_posix()

result = parse_pdf(
    pdf_path=pdf_path,
    relative_path=relative_path,
    patient_id=patient_id,
)

document = result["document"]
stats = result["statistics"]

print("SUCCESS")
print("schema_version:", result["schema_version"])
print("patient_id:", document["patient_id"])
print("relative_path:", document["relative_path"])
print("page_count:", document["page_count"])
print("total_characters:", stats["total_characters"])
print("tables_detected:", stats["tables_detected"])
print(
    "text_extraction_method:",
    document["text_extraction_method"],
)

# Save compact JSON
parsed_dir = (
    Path(r"C:\app\pdf_extractor\data\parsed")
    / patient_id
)
parsed_dir.mkdir(parents=True, exist_ok=True)

sha256_value = document["sha256"]
filename = f"{pdf_path.stem}__{sha256_value[:8]}.json"
output_path = parsed_dir / filename

save_json(result, output_path)

print("Saved JSON to:", output_path)
print(
    "JSON size bytes:",
    output_path.stat().st_size,
)

# Schema 2.0 validation
assert result["schema_version"] == "2.0"

assert document["patient_id"] == "2473679415"
assert document["relative_path"] == "2473679415/1.pdf"
assert document["filename"] == "1.pdf"
assert "sha256" in document

assert (
    document["page_count"]
    == stats["pages_with_text"]
    + stats["pages_without_text"]
)

assert len(result["pages"]) == document["page_count"]

for page in result["pages"]:
    assert "page_number" in page
    assert "text" in page
    assert "headings" in page
    assert "tables" in page

    # Old verbose keys should no longer be serialized.
    assert "raw_text" not in page
    assert "blocks" not in page
    assert "width" not in page
    assert "height" not in page

print("Validation passed!")