"""Synthetic tests: no clinical text from real patients is printed."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from caa_pdf_extractor.preparation.document_classifier import classify_document, detect_document_date
from caa_pdf_extractor.preparation.patient_bundle import build_patient_bundle, prepare_document
from caa_pdf_extractor.preparation.sectionizer import sectionize, candidate_targets


def page(text, headings=None, tables=None, number=1):
    return {"page_number": number, "text": text, "headings": headings or [],
            "tables": tables or []}


class PreparationTests(unittest.TestCase):
    def test_lossless_boundaries_and_provenance(self):
        pages = [page("Intro\nDiagnosen bei Entlassung\nExample\nEmpfohlene Medikation\nDrug\n",
                      ["Diagnosen bei Entlassung", "Empfohlene Medikation"]),
                 page("Second page without heading\n", number=2)]
        original = deepcopy(pages)
        sections, noise = sectionize(pages, "p1", 12)
        self.assertEqual(pages, original)
        self.assertEqual(noise, [])
        self.assertEqual(len(sections), 4)
        for number in (1, 2):
            self.assertEqual("".join(s.source_text for s in sections if s.page_start == number),
                             pages[number - 1]["text"])
        self.assertEqual(sections[1].candidate_targets, ["clinical_history"])
        self.assertEqual(sections[2].candidate_targets, ["medications"])
        for i, section in enumerate(sections, 1):
            self.assertEqual(section.section_id, f"doc12_sec{i:02d}")
            self.assertEqual(section.patient_id, "p1")
            self.assertEqual(section.source_document_id, 12)

    def test_fallback_blank_and_unmatched_headings(self):
        pages = [page("unchanged\r\n", ["", "not present"]), page("", number=2)]
        sections, _ = sectionize(pages, "p", 1)
        self.assertEqual([s.text for s in sections], ["unchanged\r\n", ""])

    def test_multiline_and_regex_heading(self):
        text = "Prefix\nFindings (CT)\nResults\nLong\nheading\nBody\n"
        sections, _ = sectionize([page(text, ["Findings (CT)", "Long heading", "Long heading"])], "p", 1)
        self.assertEqual([s.heading for s in sections], [None, "Findings (CT)", "Long heading"])
        self.assertEqual("".join(s.source_text for s in sections), text)

    def test_conservative_noise_removal(self):
        pages = [page("Tel: +43 123\nDiagnosen\nUnique body\nwww.example.test\n",
                      ["Diagnosen"], number=i) for i in range(1, 5)]
        sections, noise = sectionize(pages, "p", 1)
        self.assertEqual(len(noise), 8)
        self.assertTrue(all("Tel:" not in s.text and "www." not in s.text for s in sections))
        self.assertEqual(sum("Diagnosen" in s.text for s in sections), 4)
        for i in range(1, 5):
            self.assertEqual("".join(s.source_text for s in sections if s.page_start == i),
                             pages[i - 1]["text"])
        short, noise = sectionize(pages[:2], "p", 1)
        self.assertEqual(noise, [])
        self.assertTrue(any("Tel:" in s.text for s in short))

    def test_contact_line_in_body_is_preserved(self):
        pages = [page("Tel: 123\na\nb\nc\nd\nTel: 123\ne\nf\ng\nh\ni\n", number=i)
                 for i in range(1, 4)]
        sections, noise = sectionize(pages, "p", 1)
        self.assertEqual(len(noise), 3)
        self.assertTrue(all(s.text.count("Tel: 123") == 1 for s in sections))

    def test_table_structure_association_and_copy(self):
        rows = [["Medication", "Dose"], ["Drug ABC", "5 mg"], [None, ""]]
        pages = [page("Diagnosen\nExample\nMedikation\nDrug ABC 5 mg\n",
                      ["Diagnosen", "Medikation"], [{"rows": rows}]),
                 page("unlocated table\n", tables=[{"rows": [["unmatched"]]}], number=2)]
        sections, _ = sectionize(pages, "p", 1)
        tables = [t for s in sections for t in s.tables]
        self.assertEqual(len(tables), 2)
        self.assertEqual(sections[1].tables[0].rows, rows)
        self.assertEqual(tables[0].association, "cell_text_match")
        self.assertEqual(tables[1].association, "page_fallback")
        tables[0].rows[0][0] = "changed"
        self.assertEqual(rows[0][0], "Medication")

    def test_classification_and_uncertainty(self):
        titles = {"discharge_letter": "Entlassungsbrief", "outpatient_letter": "Ambulanzbericht",
                  "radiology_report": "Radiologischer Befund", "laboratory_report": "Laborbefund",
                  "consultation": "Konsiliarbericht"}
        for kind, title in titles.items():
            self.assertEqual(classify_document([page(title)]), kind)
        self.assertEqual(classify_document([page("Entlassungsbrief\nLaborbefund")]), "unknown")
        self.assertEqual(classify_document([page("Arztbrief")]), "unknown")
        self.assertEqual(classify_document([page("Unclear"), page("Laborbefund", number=2)]), "unknown")

    def test_dates_only_explicit_unambiguous_labels(self):
        self.assertEqual(detect_document_date([page("Briefdatum: 05.10.2026")]), "2026-10-05")
        self.assertEqual(detect_document_date([page("Datum: 2026-10-05")]), "2026-10-05")
        for text in ("Geburtsdatum: 05.10.2026", "05.10.2026", "Datum: 31.02.2026",
                     "Datum: 05/10/26", "Datum: 05.10.2026\nDatum: 06.10.2026"):
            self.assertIsNone(detect_document_date([page(text)]))

    def test_multiple_routing_targets(self):
        self.assertEqual(candidate_targets("Familienanamnese und Medikation", ""),
                         ["medications", "family_history"])
        self.assertEqual(candidate_targets("Computertomografie", ""), ["radiology"])
        self.assertEqual(candidate_targets(None, "Body mentioning a drug incidentally"), [])

    def test_document_boundary_determinism_and_immutability(self):
        source = {"document": {"patient_id": "p", "relative_path": "p/1.pdf"},
                  "pages": [page("Entlassungsbrief\nDatum: 05.10.2026\n")]}
        original = deepcopy(source)
        doc = prepare_document("p", 1, "1.pdf", "hash", source)
        self.assertEqual(source, original)
        self.assertEqual(doc, prepare_document("p", 1, "1.pdf", "hash", source))
        with self.assertRaises(ValueError):
            prepare_document("another", 1, "1.pdf", "hash", source)
        from types import SimpleNamespace
        session = MagicMock()
        session.scalars.return_value.all.return_value = [
            SimpleNamespace(id=i, filename=f"{i}.pdf", sha256=str(i), document_json=source)
            for i in (1, 2)]
        factory = MagicMock()
        factory.return_value.__enter__.return_value = session
        bundle = build_patient_bundle("p", factory)
        self.assertEqual(len(bundle.documents), 2)
        self.assertNotEqual(bundle.documents[0].sections[0].section_id,
                            bundle.documents[1].sections[0].section_id)
        json.dumps(bundle.to_dict())
        session.commit.assert_not_called()
        session.add.assert_not_called()
        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
