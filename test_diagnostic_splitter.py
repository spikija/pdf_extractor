from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from caa_pdf_extractor.preparation.diagnostic_splitter import split_diagnostic_procedures
from caa_pdf_extractor.preparation.models import PreparedSection


def section(text, page=1, heading=None, number=1):
    return PreparedSection(f"doc12_sec{number:02d}", "synthetic", 12, heading,
                           page, page, text, text)


class DiagnosticSplitterTests(unittest.TestCase):
    def test_single_rtg(self):
        text = "Intro\n Röntgen: Thorax liegend (11.12.2015)\nFinding unchanged.\n"
        event, = split_diagnostic_procedures([section(text)])
        self.assertEqual(event.modality, "RTG")
        self.assertEqual(event.date, "2015-12-11")
        self.assertEqual(event.studies, ["Thorax liegend"])
        self.assertEqual(event.result_text, "Finding unchanged.\n")
        self.assertEqual(event.source_text, text[text.index(" Röntgen"):])
        self.assertEqual(event.candidate_target, "radiology")

    def test_composite_ct_shared_date_and_subprocedures(self):
        text = ("Computertomografie: Schädel nativ,\n"
                "Computertomografie: CTA intracranielle Gefäße mit KM,\n"
                "Computertomografie: CTA Halsgefäße mit KM (11.12.2015)\n"
                "Schädel nativ: Finding A.\n"
                "CTA intracranielle Gefäße mit KM:\n"
                "CTA Halsgefäße mit KM: Finding B.\n")
        events = split_diagnostic_procedures([section(text)])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event.modality, event.date), ("CT", "2015-12-11"))
        self.assertEqual(event.studies, ["Schädel nativ", "CTA intracranielle Gefäße mit KM", "CTA Halsgefäße mit KM"])
        self.assertEqual([s.result_text for s in event.subprocedures], ["Finding A.", "", "Finding B."])
        self.assertEqual(event.source_text, text)

    def test_mri_composite_and_see_above(self):
        text = ("Kernspintomographie: Schädel craniell nativ, Kernspintomographie: Schädel craniell mit KM,\n"
                "Kernspintomographie: Diffusion (14.12.2015)\n"
                "Schädel craniell nativ: Comparison from 11.12.2015.\n"
                "Schädel craniell mit KM: siehe oben.\nDiffusion: siehe oben.\n")
        event, = split_diagnostic_procedures([section(text)])
        self.assertEqual(event.date, "2015-12-14")
        self.assertEqual(event.modality, "MRI")
        self.assertEqual(event.studies, ["Schädel craniell nativ", "Schädel craniell mit KM", "Diffusion"])
        self.assertEqual(event.subprocedures[-1].result_text, "siehe oben.")

    def test_result_boundaries_and_missing_result(self):
        text = "CT: Schädel (11.12.2015)\nCT finding.\nMRI: Schädel (14.12.2015)\nRtg: Thorax\n"
        events = split_diagnostic_procedures([section(text)])
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].result_text, "CT finding.\n")
        self.assertEqual(events[1].result_text, "")
        self.assertEqual(events[2].result_text, "")
        self.assertEqual(events[2].date, None)
        self.assertEqual("".join(e.source_text for e in events), text)

    def test_date_never_from_narrative(self):
        for header in ("CT: Schädel", "CT: Schädel (31.02.2015)",
                       "CT: Schädel (11.12.2015) (12.12.2015)"):
            event, = split_diagnostic_procedures([section(header + "\nComparison (11.12.2015).\n")])
            self.assertIsNone(event.date)

    def test_dated_header_comma_does_not_merge_distinct_events(self):
        events = split_diagnostic_procedures([section(
            "CT: Schädel (11.12.2015),\nCT: Thorax (12.12.2015)\nResult\n")])
        self.assertEqual([event.date for event in events], ["2015-12-11", "2015-12-12"])

    def test_spans_pages_and_stops_before_new_topic(self):
        sections = [section("CT: Schädel (11.12.2015)\nFirst page.", 1),
                    section("Second page.\n", 2, number=2),
                    section("Medikation\nOther topic.\n", 2, "Medikation", 3)]
        before = deepcopy(sections)
        event, = split_diagnostic_procedures(sections)
        self.assertEqual((event.page_start, event.page_end), (1, 2))
        self.assertEqual(event.source_text, sections[0].source_text + sections[1].source_text)
        self.assertNotIn("Other topic", event.result_text)
        self.assertEqual(sections, before)

    def test_page_start_header_without_preceding_newline(self):
        events = split_diagnostic_procedures([section("CT: Schädel\nFinding", 1),
                                            section("MRI: Schädel\nOther finding", 2, number=2)])
        self.assertEqual(len(events), 2)
        self.assertEqual((events[0].page_end, events[1].page_start), (1, 2))

    def test_aliases_and_cta_subtype(self):
        for alias, modality in [("Roentgen", "RTG"), ("Rtg", "RTG"),
                                ("Computertomographie", "CT"), ("CTA", "CT"),
                                ("Kernspintomografie", "MRI"), ("MRT", "MRI"), ("MRI", "MRI")]:
            event, = split_diagnostic_procedures([section(f"{alias}: Study\n")])
            self.assertEqual(event.modality, modality)
            self.assertIsNone(event.date)
            self.assertEqual(event.studies, ["CTA Study" if alias == "CTA" else "Study"])

    def test_wrapped_unprefixed_component(self):
        event, = split_diagnostic_procedures([section("CT: CTA Halsgefäße,\nThorax mit KM (11.12.2015)\nResult\n")])
        self.assertEqual(event.date, "2015-12-11")
        self.assertEqual(event.result_text, "Result\n")

    def test_reject_mixed_document_input(self):
        other = section("CT: Study\n")
        other.source_document_id = 99
        with self.assertRaises(ValueError):
            split_diagnostic_procedures([section("CT: Study\n"), other])


if __name__ == "__main__":
    unittest.main()
