import sys
from pathlib import Path
sys.path.insert(0, "C:\\app\\pdf_extractor\\src")
from caa_pdf_extractor.utils import parse_pdf

pdf_path = Path(r"C:\app\pdf_extractor\pdf_db\2473679415\1.pdf")
print("exists:", pdf_path.exists())
if pdf_path.exists():
    rel_path = pdf_path.relative_to(Path(r"C:\app\pdf_extractor\pdf_db"))
    patient_id = str(rel_path.parts[0])
    result = parse_pdf(pdf_path, str(rel_path), patient_id)
    print("SUCCESS")
    print("page_count:", result["document"]["page_count"])
    assert result["schema_version"] == "2.0"
    assert result["pages"][0]["text"].strip()
    print("Schema and text validated")