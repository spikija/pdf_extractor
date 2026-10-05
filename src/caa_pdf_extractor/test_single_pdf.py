from pathlib import Path

from caa_pdf_extractor.utils import parse_pdf


def main():
    pdf_root = Path(r"C:\app\pdf_extractor\pdf_db")
    pdf_path = pdf_root / "2473679415" / "1.pdf"

    patient_id = "2473679415"
    relative_path = pdf_path.relative_to(pdf_root).as_posix()

    result = parse_pdf(
        pdf_path=pdf_path,
        relative_path=relative_path,
        patient_id=patient_id,
    )

    stats = result["statistics"]
    document = result["document"]

    print("SUCCESS")
    print("page_count:", document["page_count"])
    print("total_characters:", stats["total_characters"])
    print("blocks_detected:", stats["blocks_detected"])
    print("tables_detected:", stats["tables_detected"])
    print("possible_headings:", stats["possible_headings"])
    print("text_extraction_method:", document["text_extraction_method"])


if __name__ == "__main__":
    main()