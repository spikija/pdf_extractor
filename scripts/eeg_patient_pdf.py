"""Local, reproducible EEG review PDF; only three patients processed by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import pymupdf

from caa_pdf_extractor.eeg_holdout_design import screen_source_text

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def screen(output):
    """Screen every current PDF, without running EEG findings extraction."""
    output.mkdir(parents=True, exist_ok=False)
    folders = sorted(p for p in (ROOT / "pdf_db").iterdir() if p.is_dir())
    rows = []
    for index, folder in enumerate(folders, 1):
        documents = []
        for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf"):
            raw = path.read_bytes()
            row = {"filename": path.relative_to(folder).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
            try:
                with pymupdf.open(stream=raw, filetype="pdf") as pdf:
                    pages = [page.get_text("text", sort=True) for page in pdf]
                status, windows = screen_source_text("\n".join(pages))
                row.update(status=status, page_count=len(pages), performed_source_signals=len(windows))
            except Exception as exc:
                row.update(status="SOURCE_ERROR", error=str(exc))
            documents.append(row)
        rows.append({"fallnr": folder.name, "eligible": any(d["status"] == "EEG_PERFORMED" for d in documents), "documents": documents})
        if index % 50 == 0:
            print(f"Screened {index}/{len(folders)} patients", flush=True)
    errors = [dict(fallnr=r["fallnr"], **d) for r in rows for d in r["documents"] if d["status"] == "SOURCE_ERROR"]
    if errors:
        save(output / "source_errors.json", errors)
        raise ValueError("Source errors must be resolved before drawing the random sample")
    pool = [r["fallnr"] for r in rows if r["eligible"]]
    seed = 20261030
    selected = random.Random(seed).sample(pool, 50)
    save(output / "screening_manifest.json", rows)
    save(output / "selection.json", {
        "patient_folders": len(folders), "eligible_patients": len(pool),
        "eligibility": "Existing source-only EEG performed-report/narrative screener; screening eligibility is provisional, not physician-adjudicated completeness.",
        "selection_method": "Uniform random sample without replacement from sorted eligible fallnr list; no validation-group exclusions or phenotype balancing.",
        "seed": seed, "selected_50_fallnr": selected, "preview_fallnr": selected[:3],
        "extracted_patient_count": 0,
    })
    print(json.dumps({"folders": len(folders), "eligible": len(pool), "preview": selected[:3], "source_errors": len(errors)}), flush=True)


def extract_preview(output):
    from caa_pdf_extractor.extraction.eeg_sources import snapshot
    from caa_pdf_extractor.extraction.eeg_pipeline_v11 import assemble
    selection = json.loads((output / "selection.json").read_text("utf-8"))
    manifest = {r["fallnr"]: r for r in json.loads((output / "screening_manifest.json").read_text("utf-8"))}
    for patient in selection["preview_fallnr"]:
        for document in manifest[patient]["documents"]:
            path = ROOT / "pdf_db" / patient / document["filename"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
                raise ValueError(f"Source changed: {path}")
        folder = output / "preview_patients" / patient
        bundle = snapshot(patient, folder)
        if {d["filename"] for d in bundle["documents"]} != {d["filename"] for d in manifest[patient]["documents"]}:
            raise ValueError("Snapshot missed a source PDF")
        result = assemble(bundle)
        save(folder / "eeg_result.json", result)
        print(json.dumps({"fallnr": patient, "EEG_candidate_records": len(result["eeg_exams"])}), flush=True)
    selection["extracted_patient_count"] = len(selection["preview_fallnr"])
    save(output / "selection.json", selection)


def render(output):
    """Render the three source-checked preview patients without further extraction."""
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

    selection = json.loads((output / "selection.json").read_text("utf-8"))
    context = json.loads((output / "preview_context.json").read_text("utf-8"))
    for label, filename in [("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf"), ("Arial-Italic", "ariali.ttf")]:
        pdfmetrics.registerFont(TTFont(label, str(Path("C:/Windows/Fonts") / filename)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic")
    ink = colors.HexColor("#173042")
    muted = colors.HexColor("#526674")
    teal = colors.HexColor("#007C83")
    pale = colors.HexColor("#EFF6F7")
    styles = {
        "title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=23, leading=28, textColor=ink, spaceAfter=10),
        "patient": ParagraphStyle("patient", fontName="Arial-Bold", fontSize=19, leading=24, textColor=ink, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName="Arial-Bold", fontSize=12, leading=16, textColor=teal, spaceBefore=9, spaceAfter=5),
        "body": ParagraphStyle("body", fontName="Arial", fontSize=10, leading=14, textColor=ink, spaceAfter=5),
        "small": ParagraphStyle("small", fontName="Arial", fontSize=9, leading=12, textColor=muted, spaceAfter=4),
        "source": ParagraphStyle("source", fontName="Arial", fontSize=10, leading=14, textColor=ink, spaceAfter=5, alignment=TA_LEFT),
        "label": ParagraphStyle("label", fontName="Arial-Bold", fontSize=9, leading=12, textColor=muted),
    }
    def p(text, style="body"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    class Report(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            if hasattr(flowable, "patient_id"):
                self.canv.bookmarkPage(flowable.patient_id)
                self.canv.addOutlineEntry("Fallnr " + flowable.patient_id, flowable.patient_id, level=0)
                self.current_patient = flowable.patient_id

    pdf_path = output / "EEG_random50_PREVIEW_3_patients.pdf"
    doc = Report(str(pdf_path), pagesize=(210 * mm, 297 * mm), leftMargin=17 * mm, rightMargin=17 * mm,
                 topMargin=19 * mm, bottomMargin=18 * mm, title="EEG review - random 50 patient sample - three-patient preview", author="Local EEG review")
    doc.current_patient = None

    def page(canvas, document):
        canvas.saveState()
        canvas.setFillColor(muted)
        canvas.setFont("Arial", 8)
        label = "EEG REVIEW  |  PREVIEW"
        canvas.drawString(17 * mm, 285 * mm, label)
        canvas.setStrokeColor(colors.HexColor("#D1DFE3"))
        canvas.line(17 * mm, 282 * mm, 193 * mm, 282 * mm)
        canvas.drawString(17 * mm, 10 * mm, "30 October 2026  |  Source-linked review copy  |  Not physician validated")
        canvas.drawRightString(193 * mm, 10 * mm, str(document.page))
        canvas.restoreState()

    story = [p("EEG review", "title"), p("Random 50-patient sample · Three-patient preview", "h2"), Spacer(1, 5 * mm),
             p(f"Sampling pool: {selection['eligible_patients']} patients with performed-EEG source signals among {selection['patient_folders']} current patient folders."),
             p("Fifty fallnr were drawn uniformly without replacement using seed 20261030. This preview contains the first three in that draw. Only these three patients have been processed for this document."),
             p("Each EEG appears on its own page with fallnr, brief demographics, and a source-based CAA category. The shaded panel shows EEG categories; the original German EEG text follows immediately."),
             p("Present, explicitly absent, uncertain/no definite evidence, and not documented remain distinct. CAA source classifications are reported as stated; Boston or Edinburgh criteria have not been newly computed for this preview."),
             p("Categories come from EEG 1.1 with explicitly noted source-review clarifications. Duplicate source copies are grouped for display. Hospital page furniture and unrelated sections are omitted from the displayed EEG passage; clinical EEG wording is retained. This preview has not been physician validated.", "small"),
             Spacer(1, 6 * mm), p("Preview patients", "h2")]
    for patient in selection["preview_fallnr"]:
        data = json.loads((output / "preview_patients" / patient / "eeg_result.json").read_text("utf-8"))
        hidden = context[patient].get("hidden_duplicate_candidates", [])
        story.append(p(f"Fallnr {patient}  ·  {len(data['eeg_exams']) - len(hidden)} EEG records"))
    audit = []
    status_labels = {"CONFIRMED": "Present / confirmed wording", "EXPLICITLY_ABSENT": "Explicitly absent", "NO_DEFINITE_EVIDENCE": "No definite evidence (not definite absence)", "UNCERTAIN": "Uncertain", "CONFLICTING_EVIDENCE": "Conflicting evidence", "UNKNOWN": "Not documented / unknown"}
    def human(value):
        return str(value).replace("_", " ").lower().capitalize()
    def boolean(value):
        return "Present" if value is True else "Explicitly absent" if value is False else "Not documented / unknown"
    for patient in selection["preview_fallnr"]:
        item = context[patient]
        folder = output / "preview_patients" / patient
        result = json.loads((folder / "eeg_result.json").read_text("utf-8"))
        bundle = json.loads((folder / "source_bundle.json").read_text("utf-8"))
        filenames = {d["source_document_id"]: d["filename"] for d in bundle["documents"]}
        exams = [e for e in result["eeg_exams"] if e["eeg_exam_id"] not in item.get("hidden_duplicate_candidates", [])]
        for index, exam in enumerate(exams, 1):
            story.append(PageBreak())
            heading = p("Fallnr " + patient, "patient")
            if index == 1:
                heading.patient_id = patient
            story += [heading, p(item["demographics"]), p("CAA: " + item["caa_category"]), p(item["caa_evidence"], "small")]
            if index == 1 and item.get("notes"):
                story.append(p(item["notes"], "small"))
            date = exam["eeg_date"] or "Date not documented"
            exam_title = f"EEG {index} of {len(exams)}  |  {date}"
            rows = [
                ("Primary category", human(exam["eeg_primary_finding"])),
                ("All phenotype labels", "; ".join(human(x) for x in exam["clinical_phenotype"]) or "No phenotype assigned / unknown"),
                ("Source abnormality grade", human(exam["source_reported_abnormality_grade"])),
                ("Focal slowing", boolean(exam.get("focal_slowing_present")) + (" · " + human(exam["focal_slowing_side"]) + " · " + ", ".join(human(x) for x in exam["focal_slowing_regions"]) if exam.get("focal_slowing_present") else "")),
                ("Generalized slowing", boolean(exam.get("generalized_slowing_present"))),
                ("Epileptiform evidence", status_labels[exam["epileptiform_evidence_status"]]),
                ("Recorded seizure / status", boolean(exam.get("seizure_recorded")) + " / " + boolean(exam.get("electrographic_status_epilepticus"))),
                ("Assessability / artifacts", human(exam["assessability"]) + " / " + boolean(exam.get("artifact_present"))),
            ]
            foci = [f for f in exam.get("focal_abnormalities", []) if f.get("assertion", "ESTABLISHED") == "ESTABLISHED"]
            if foci:
                rows.append(("Focus details", "; ".join(human(f["finding_type"]) + ": " + human(f["side"]) + " " + ", ".join(human(r) for r in f["regions"]) + " (" + human(f["relationship"]) + ")" for f in foci)))
            if exam.get("rhythmic_delta_activity_present") or exam.get("periodic_discharge_present"):
                rows.append(("Rhythmic / periodic", human(exam["rhythmic_delta_type"]) + " / " + human(exam["periodic_discharge_type"])))
            overrides = item.get("exam_row_overrides", {}).get(exam["eeg_exam_id"], {})
            rows = [(k, overrides.get(k, v)) for k, v in rows]
            if "Source localization" in overrides:
                rows.append(("Source localization", overrides["Source localization"]))
            table = Table([[p(k, "label"), p(v)] for k, v in rows], colWidths=[46 * mm, 124 * mm], hAlign="LEFT")
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), pale), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            story.append(KeepTogether([Spacer(1, 3 * mm), p(exam_title, "h2"), table]))
            if exam.get("source_conflict") and exam["eeg_exam_id"] not in item.get("resolved_copy_groups", []):
                story.append(p(exam["source_conflict"], "small"))
            note = item.get("exam_notes", {}).get(exam["eeg_exam_id"])
            if note:
                story.append(p("Source review note: " + note, "small"))
            source_pages = item.get("source_page_overrides", {}).get(exam["eeg_exam_id"], [exam["source_page_start"], exam["source_page_end"]])
            page_range = str(source_pages[0]) + ("–" + str(source_pages[1]) if source_pages[1] != source_pages[0] else "")
            source_label = f"Original EEG text · {patient}/{filenames[exam['document_id']]} · PDF page {page_range}"
            source_text = item.get("source_display_overrides", {}).get(exam["eeg_exam_id"], exam["eeg_report_text"])
            story += [p(source_label, "h2"), p(exam["source_header"], "small")]
            # Reflow PDF line wraps while preserving interpretation headings.
            formatted_source = re.sub(r"\s*\n\s*(Interpretation:)\s*\n\s*", r"\n\n\1\n", source_text.strip())
            for paragraph in re.split(r"\n\s*\n", formatted_source):
                story.append(p(" ".join(paragraph.split()), "source"))
            copies = list(dict.fromkeys(filenames[c["document_id"]] for c in exam["source_report_copies"]))
            if len(exam["source_report_copies"]) > 1:
                story.append(p("Source copies grouped in this candidate: " + ", ".join(copies), "small"))
            audit.append({"fallnr": patient, "eeg_exam_id": exam["eeg_exam_id"], "source_file": filenames[exam["document_id"]], "page_start": source_pages[0], "page_end": source_pages[1], "display_source_sha256": hashlib.sha256(source_text.encode()).hexdigest()})
    doc.build(story, onFirstPage=page, onLaterPages=page)
    save(output / "pdf_source_audit.json", audit)
    print(str(pdf_path), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["screen", "preview", "render"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "screen":
        screen(args.output.resolve())
    elif args.action == "preview":
        extract_preview(args.output.resolve())
    else:
        render(args.output.resolve())
