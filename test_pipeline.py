import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from caa_pdf_extractor.pdf_ingestion import ingest_pdf, discover_pdfs
from caa_pdf_extractor.utils import parse_pdf


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "patient" / "report.v1.PDF"
        self.pdf.parent.mkdir()
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "Clinical heading", fontname="hebo", fontsize=16)
            page.insert_text((72, 110), "Short body", fontsize=10)
            doc.new_page()
            doc.save(self.pdf)

    def test_text_headings_and_blank_page(self):
        result = parse_pdf(self.pdf, "patient/report.v1.PDF", "patient")
        self.assertIn("Clinical heading", result["pages"][0]["headings"])
        self.assertEqual(result["statistics"]["pages_with_text"], 1)
        self.assertEqual(result["statistics"]["pages_without_text"], 1)
        self.assertNotIn("width", result["pages"][0])

    def test_dry_run_and_discovery(self):
        self.assertEqual(discover_pdfs(self.root), [self.pdf])
        with patch("caa_pdf_extractor.pdf_ingestion.store_document") as store:
            result = ingest_pdf(self.pdf, self.root, parsed_root=self.root / "parsed", dry_run=True)
            store.assert_not_called()
        self.assertEqual(result["status"], "PARSED")
        self.assertEqual(len(list((self.root / "parsed").rglob("report.v1__*.json"))), 1)

    def test_failure_is_isolated(self):
        self.pdf.write_bytes(b"invalid pdf")
        self.assertEqual(ingest_pdf(self.pdf, self.root, dry_run=True)["status"], "FAILED")
        outside = self.root / "outside.pdf"
        self.assertEqual(ingest_pdf(outside, self.root, dry_run=True)["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
