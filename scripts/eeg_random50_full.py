"""Frozen random-50 EEG review: local sources, source diagnoses, antiseizure drugs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf

from eeg_patient_pdf import ROOT, save

PREVIEW = ROOT / "data/reports/eeg_random50_20261030_preview"
OUT = ROOT / "data/reports/eeg_random50_20261030_full"


def read(path):
    return json.loads(path.read_text("utf-8"))


def extract():
    from caa_pdf_extractor.extraction.eeg_sources import snapshot
    from caa_pdf_extractor.extraction.eeg_pipeline_v11 import assemble

    OUT.mkdir(parents=True, exist_ok=True)
    selection = read(PREVIEW / "selection.json")
    manifest = {r["fallnr"]: r for r in read(PREVIEW / "screening_manifest.json")}
    for index, patient in enumerate(selection["selected_50_fallnr"], 1):
        folder = OUT / "patients" / patient
        for d in manifest[patient]["documents"]:
            path = ROOT / "pdf_db" / patient / d["filename"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != d["sha256"]:
                raise ValueError(f"Source differs from approved sample: {path}")
        if (folder / "eeg_result.json").exists():
            continue
        if patient in selection["preview_fallnr"]:
            prior = PREVIEW / "preview_patients" / patient
            folder.mkdir(parents=True, exist_ok=True)
            for name in ["source_bundle.json", "eeg_result.json"]:
                (folder / name).write_bytes((prior / name).read_bytes())
            result = read(folder / "eeg_result.json")
        else:
            bundle = snapshot(patient, folder)
            if {d["filename"] for d in bundle["documents"]} != {d["filename"] for d in manifest[patient]["documents"]}:
                raise ValueError("Snapshot omitted a PDF")
            result = assemble(bundle)
            save(folder / "eeg_result.json", result)
        print(f"{index}/50 {patient}: {len(result['eeg_exams'])} EEG candidates", flush=True)
    save(OUT / "selection.json", {**selection, "extracted_patient_count": 50, "approved_changes": "Diagnoses, only antiseizure medications, one patient per page"})


NOISE = re.compile(r"Universit.tsklinikum|Gemeinn.tzige|Privatuniversit.t|Paracelsus|Landesgericht|UID-Nr|Telefon|Telefax|www\.|geb\.:|geboren am|SVNR|Adresse:|^\s*(?:Seite\s+\d+|FN\s+\d+)", re.I)
DX_HEAD = re.compile(r"^(?:(?:weitere|andere)\s+)?(?:Diagnosen?(?:\s+bei\s+Entlassung)?|Entlassungsdiagnosen|Hauptdiagnosen|Nebendiagnosen|Vorerkrankungen|Vordiagnosen)\s*:?\s*$", re.I)
DX_STOP = re.compile(r"^(?:.*medikation\b|Medikamente\b|Therapieempfehl|Weitere\s+empfohlene|Weitere\s+Ma.nahmen|Termine|Kontrollen|Empfohlene\s+Kontroll|Therapie\s*:|Allerg|Noxen|Anamnese|Epikrise|Krankheitsverlauf|Familienanamnese|Sozialanamnese|Aufnahmestatus|Neurologischer|Befunde|Zusammenfassung|Procedere|Prozedere|Beurteilung|Status\b|Mit\s+freundlichen|Vermerk|Bitte\s+beachten|Information|Empfehlungen|Patienteninformation|Sehr\s+geehrt|Salzburg,|Untersuchungsergebnisse|Vorbefunde|Parameter\b|Labor\b|Blutbild|Klinische\s+Chemie|Gerinnung|Internistisch|Elektrolyte|MRT\b|MRI\b|Computertomograf|Neurosonolog|EKG\b|EEG\b|ELEKTROENZEPHALOGRAFIE)", re.I)
DX_DETAIL = re.compile(r"^(?:MMSE|MoCA|Barthel|Fr.hreha|EEG|cEEG|cMRT|MRT|MRI|CT\b|Liquor|Biomarker|.+\bvom\s+\d|Therapie\b|aktuell\b|ED\s*\d|Zuletzt\b|Bei\s+|Ziel\b|Keine\s+Allerg|Kardio.?vaskul.re\s+Risikofaktoren\s*:|Risikofaktoren\s*:)", re.I)
CAA = re.compile(r"Amyloidangiopath\w*|\bCAA\b", re.I)


def normalize(text):
    return " ".join(text.split())


def clean_eeg(text):
    """Display only EEG material, retaining clinical source wording verbatim."""
    lines = []
    for line in text.splitlines():
        value = line.strip()
        if NOISE.search(value):
            continue
        if re.match(r"^(?:Diagnosen|Anamnese|Aufnahmestatus|Status\s*$|Pro[cz]edere|Neuropsycholog|Therapiebericht|Logop.d|Ergotherap|Physiotherap|EKG|ECG|Neuroson|CT\b|MRT\b|MRI\b|Labor|Medikation|Empfohlene|Weitere\s+empfohlene|BAKT[- ]Bef|Vermerk\s*:|Kontrollen\s*$|Konsil\b|\w+\s+Konsil|\w*(?:sonograph|sonograf|konsil))", value, re.I):
            break
        if re.fullmatch(r"Beurteilung\s*:?", value, re.I):
            tail = text[text.find(line) + len(line):].lstrip()
            if not re.match(r"(?:Dieses\s+EEG|(?:Das\s+)?EEG\b|(?:Leicht|Mäßig|Schwer|Minimal|Normal|Ausgeprägt|Generalisiert|Fokal|Diffuse?)\w*\s+(?:abnorm|patholog|EEG|Verlangsam|Hirnfunktions))", tail, re.I):
                break
        lines.append(line)
    return "\n".join(lines).strip()


def diagnosis_rows(bundle):
    rows = []
    for d in bundle["documents"]:
        active = False
        found_main = False
        for s in d["sections"]:
            for line in s["text"].splitlines():
                value = line.strip()
                if NOISE.search(value):
                    continue
                if DX_HEAD.fullmatch(value):
                    if found_main and not active:
                        active = False
                        continue
                    active = True
                    found_main = True
                    continue
                if DX_STOP.match(value):
                    active = False
                if re.match(r"^(?:Vorgeschichte|Sonstiges|K.rperlicher|Vitalparameter|Klinischer\s+Status|GCS\s*[:\d]|RR\s*:)", value, re.I):
                    active = False
                if not active or not value:
                    continue
                marker = re.match(r"^(\d{1,2})[.)]\s+", value)
                bullet = bool(re.match(r"^[-–•]", value))
                value = re.sub(r"^(?:\d{1,2}[.)]|[-–•])\s*", "", value)
                if DX_DETAIL.match(value):
                    continue
                if not marker and not bullet and re.match(r"^[a-zäöüß]", value) and rows and rows[-1]["filename"] == d["filename"]:
                    rows[-1]["text"] += " " + value
                    continue
                if len(value) < 4 or re.fullmatch(r"[\d\W]+", value):
                    continue
                rows.append(dict(text=value, filename=d["filename"], page=s["page_start"], numbered=bool(marker), bullet=bullet))
    # Exact wording deduplication; uncertainty and historical wording are retained.
    seen = set()
    unique = []
    for row in rows:
        key = re.sub(r"[^\w]+", "", row["text"].casefold())
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def drug_patterns():
    mapping = {}
    with (ROOT / "data/reference/medication_dictionary.csv").open(encoding="utf-8-sig") as stream:
        for r in csv.DictReader(stream):
            if r["atc_code"].startswith("N03") or r["research_class"] == "ANTISEIZURE":
                mapping[r["alias"]] = r["active_ingredient"]
    for r in read(ROOT / "data/reference/medication_aliases_v3.json")["entries"]:
        if r["class"] == "ANTISEIZURE":
            for alias in r["aliases"]:
                mapping[alias] = "/".join(r["ingredients"])
    # These generic/brand pairs are already explicit in research_metadata_v2.py.
    for generic, aliases in {
        "Levetiracetam": ["Levetiracetam", "Levetirazetam", "Levebon", "Keppra"],
        "Perampanel": ["Fycompa", "Perampanel"], "Lacosamid": ["Lacosamid", "Lacosamide", "Vimpat"],
        "Valproinsäure": ["Valproat", "Valproinsäure", "Valproate", "Valproic acid", "Depakine", "Depakine Chronosphere"],
        "Lamotrigin": ["Lamotrigin", "Lamictal"], "Carbamazepin": ["Carbamazepin", "Tegretol", "Neurotop"],
        "Topiramat": ["Topiramat", "Topiramate", "Topamax"],
        "Brivaracetam": ["Brivaracetam", "Briviact"], "Zonisamid": ["Zonisamid", "Zonegran"],
        "Clobazam": ["Clobazam", "Frisium"],
        "Gabapentin": ["Gabapentin", "Neurontin"], "Pregabalin": ["Pregabalin", "Lyrica"],
        "Phenobarbital": ["Phenobarbital"],
    }.items():
        for alias in aliases:
            mapping[alias] = generic
    mapping["Convulex"] = "Valproinsäure"
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?![a-zäöüß])", re.I)
    return pattern, {k.casefold(): v for k, v in mapping.items()}


def medication_rows(bundle):
    pattern, aliases = drug_patterns()
    rows = []
    for d in bundle["documents"]:
        for s in d["sections"]:
            for line in s["text"].splitlines():
                if NOISE.search(line):
                    continue
                if re.search(r"(?:mg/l|µg/ml|μg/ml|ng/ml|µmol/l|umol/l)", line, re.I):
                    continue  # Assay/reference-table mentions do not establish medication use.
                for m in pattern.finditer(line):
                    generic = aliases[m[0].casefold()]
                    rows.append(dict(generic=generic, source_name=m[0], filename=d["filename"], page=s["page_start"], source_text=line.strip()))
            # Rescue benzodiazepines qualify only when the same source sentence
            # explicitly links administration to seizure/status management.
            text = " ".join(line.strip() for line in s["text"].splitlines() if not NOISE.search(line))
            for m in re.finditer(r"\b(?:Frisium|Clobazam|Midazolam|Dormicum|Diazepam|Lorazepam|Temesta)\b", text, re.I):
                left = max(text.rfind(". ", 0, m.start()) + 2, m.start() - 180, 0)
                end = text.find(". ", m.end())
                right = min(end + 1 if end >= 0 else len(text), m.end() + 180)
                sentence = text[left:right]
                if re.search(r"Anfall|Anf.ll|Status\s+epilep|Krampf|antikonvuls|antiepilep", sentence, re.I):
                    generic = {"frisium": "Clobazam", "dormicum": "Midazolam", "temesta": "Lorazepam"}.get(m[0].casefold(), m[0].capitalize())
                    rows.append(dict(generic=generic, source_name=m[0], filename=d["filename"], page=s["page_start"], source_text=sentence, rescue_context=True))
    return rows


def demographics(bundle):
    from caa_pdf_extractor.extraction.gender_evidence import extract_gender
    adapted = NS(documents=[NS(source_document_id=d["source_document_id"], sections=[NS(**s, source_text=s["text"], heading=None) for s in d["sections"]]) for d in bundle["documents"]])
    gender = extract_gender(adapted)
    years = set()
    for d in bundle["documents"]:
        for s in d["sections"]:
            for m in re.finditer(r"(?:geboren\s+am|geb\.:)\s*\d{1,2}\.\s*\d{1,2}\.\s*((?:19|20)\d{2})", s["text"], re.I):
                years.add(m[1])
    return {"sex": {"man": "Male", "woman": "Female"}.get(gender["value"], "Sex unknown"), "birth_year": next(iter(years)) if len(years) == 1 else "unknown/conflicting", "gender_status": gender["status"]}


def caa_rows(bundle, dx):
    rows = [dict(r, origin="DIAGNOSIS_LIST") for r in dx if CAA.search(r["text"])]
    for d in bundle["documents"]:
        for s in d["sections"]:
            text = "\n".join(line for line in s["text"].splitlines() if not NOISE.search(line))
            for m in CAA.finditer(text):
                # Bounded sentence containing the entity; original wording retained.
                start = max(text.rfind("\n\n", 0, m.start()), text.rfind(". ", 0, m.start()))
                start = max(0, start + 2 if start >= 0 else 0)
                end = text.find(". ", m.end())
                if end < 0:
                    end = text.find("\n\n", m.end())
                end = min(len(text), end + 1) if end >= 0 else len(text)
                sentence = normalize(text[start:end])
                if len(sentence) > 500:
                    continue
                rows.append(dict(text=sentence, filename=d["filename"], page=s["page_start"], origin="SOURCE_SENTENCE"))
    seen = set()
    unique = []
    for r in rows:
        key = normalize(r["text"]).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def prepare():
    preview_context = read(PREVIEW / "preview_context.json")
    patients = []
    for patient in read(OUT / "selection.json")["selected_50_fallnr"]:
        folder = OUT / "patients" / patient
        bundle = read(folder / "source_bundle.json")
        result = read(folder / "eeg_result.json")
        item = preview_context.get(patient, {})
        dx = diagnosis_rows(bundle)
        drugs = medication_rows(bundle)
        source_caa = caa_rows(bundle, dx)
        names = {d["source_document_id"]: d["filename"] for d in bundle["documents"]}
        groups = {}
        for exam in result["eeg_exams"]:
            text = item.get("source_display_overrides", {}).get(exam["eeg_exam_id"], clean_eeg(exam["eeg_report_text"]))
            key = (exam["eeg_date"], re.sub(r"\W+", "", text.casefold()))
            if not exam["eeg_date"]:
                key = (None, exam["eeg_exam_id"])
            pages = item.get("source_page_overrides", {}).get(exam["eeg_exam_id"], [exam["source_page_start"], exam["source_page_end"]])
            # Source references follow the displayed EEG passage, excluding any
            # later unrelated sections retained by the immutable extractor.
            if text.strip() and exam["eeg_exam_id"] not in item.get("source_page_overrides", {}):
                last = text.splitlines()[-1].strip()
                raw_offset = exam["eeg_report_text"].rfind(last)
                evidence = next((e for e in exam["evidence"] if e["document_id"] == exam["document_id"]), None)
                if raw_offset >= 0 and evidence:
                    end_position = evidence["report_start"] + raw_offset + len(last) - 1
                    source_document = next(d for d in bundle["documents"] if d["source_document_id"] == exam["document_id"])
                    offset = 0
                    for section in source_document["sections"]:
                        offset += len(section["text"]) + 1
                        if end_position < offset:
                            pages = [pages[0], section["page_end"]]
                            break
            ref = {"filename": names[exam["document_id"]], "page_start": pages[0], "page_end": pages[1], "exam_id": exam["eeg_exam_id"]}
            if key in groups:
                groups[key]["references"].append(ref)
                continue
            groups[key] = dict(exam=exam, source_text=text, references=[ref], row_overrides=item.get("exam_row_overrides", {}).get(exam["eeg_exam_id"], {}), note=item.get("exam_notes", {}).get(exam["eeg_exam_id"]))
        patients.append(dict(fallnr=patient, demographics=demographics(bundle), diagnoses=dx, medications=drugs,
                             caa_source_rows=source_caa, caa_category=item.get("caa_category"), caa_evidence=item.get("caa_evidence"), eeg_records=list(groups.values())))
    save(OUT / "patient_report_data.json", patients)
    print("Prepared", len(patients), "patients;", sum(len(p["eeg_records"]) for p in patients), "displayed EEG source records", flush=True)
    save(OUT / "review_inventory.json", [{"fallnr": p["fallnr"], "diagnosis_count": len(p["diagnoses"]), "drug_names": sorted({m["generic"] for m in p["medications"]}), "caa": p["caa_source_rows"], "eeg_count": len(p["eeg_records"])} for p in patients])


CAA_REVIEW = {
    "2680235219": ("Imaging suggests possible CAA; no formal source grade", r"womöglich"),
    "2719894620": ("Clinician-diagnosed CAA; formal grade unspecified", r"Diagnose einer Amyloidangiopathie gestellt"),
    "2919047824": ("Imaging report: probable CAA, Boston 2.0", r"Boston 2\.0"),
    "2949529625": ("Suspected CAA / differential diagnosis", r"Amyloidangiopathie"),
    "2780762822": ("Clinician-diagnosed CAA; formal grade unspecified", r"^Zerebrale|^Amyloidangiopathie"),
    "2831584723": ("CAA considered in differential etiology", r"amyloidangiopathische"),
    "2969951725": ("Source explicitly reports no evidence of CAA", r"Kein Hinweis auf CAA"),
    "2626753718": ("Probable CAA wording; criteria version unspecified", r"wahrscheinliche"),
    "2529565017": ("Imaging suspicion of CAA", r"Verdacht"),
    "2684469520": ("Suspected CAA", r"Verdacht"),
    "2638706819": ("Suspected CAA", r"V\.a\.|Verdacht"),
    "2989797725": ("Imaging differential: CAA / mixed CAA-hypertensive disease", r"Mischbild|DD CAA"),
    "2539815917": ("Source-stated probable CAA under Boston criteria; earlier suspicion/negative wording also present", r"probable"),
    "2540738517": ("CAA in differential diagnosis", r"DD Amyloid"),
    "2546723517": ("Clinician attributes hemorrhage to CAA; no formal grade", r"zurückgeführt"),
    "2629837318": ("Source imaging explicitly reports no evidence of CAA", r"kein.*Hinweis"),
    "2693909420": ("Possible CAA etiology; not a formal possible-CAA classification", r"möglicher|möglich"),
    "2920768024": ("Source-stated probable CAA; Boston 2.0 mentioned", r"Boston 2\.0"),
    "2731738721": ("Source-stated probable CAA, modified Boston criteria", r"modifizierten Boston"),
    "2809074622": ("Suspected CAA", r"V\.a\.|Verdacht"),
    "2722225820": ("Source reports missing evidence for CAA; hypertensive etiology discussed", r"fehlendem Hinweis"),
    "2674159019": ("Source reports histologically confirmed CAA; earlier imaging uncertainty retained", r"histologisch"),
    "2458285015": ("Clinician-diagnosed / most-likely CAA etiology; no formal grade", r"^Amyloidangiopathie$|am ehesten"),
    "2704352920": ("Imaging suspicion of CAA", r"Verdacht"),
    "2562952717": ("Most-likely CAA etiology in source; no formal criteria grade", r"am wahrscheinlichsten"),
    "2846327823": ("Source-stated possible CAA, Boston 2.0; other records use ungraded CAA diagnosis", r"Boston 2\.0"),
    "2611010718": ("Differential / most-likely CAA etiology; no formal criteria grade", r"am ehesten"),
    "2649137819": ("Earlier CAA consideration not confirmed on MRI; source favors hemorrhagic infarction", r"nicht bestätigt"),
    "2897716624": ("Imaging report: probable CAA; criteria version unspecified", r"wahrscheinlichen CAA"),
    "2820712822": ("Imaging appearance of CAA; formal grade unspecified", r"Bild einer Amyloid"),
    "2844665123": ("Source-stated probable CAA, Boston 2.0", r"Boston 2\.0|Bosten"),
    "2871828223": ("Probable CAA wording; criteria version unspecified", r"wahrscheinlicher"),
}


def category(patient):
    if patient.get("caa_category"):
        return patient["caa_category"], patient.get("caa_evidence", "")
    rows = patient["caa_source_rows"]
    if not rows:
        return "Not documented in reviewed source text (not an exclusion)", ""
    if patient["fallnr"] not in CAA_REVIEW:
        raise ValueError("CAA source rows need explicit review: " + patient["fallnr"])
    label, cue = CAA_REVIEW[patient["fallnr"]]
    row = next((r for r in rows if re.search(cue, r["text"], re.I)), None)
    if not row:
        raise ValueError("CAA evidence cue missing: " + patient["fallnr"])
    return label, f"{row['filename']} p.{row['page']}"


def source_ref(record):
    refs = []
    for r in record["references"]:
        value = f"{r['filename']} p.{r['page_start']}"
        if r["page_end"] != r["page_start"]:
            value += f"–{r['page_end']}"
        if value not in refs:
            refs.append(value)
    return "; ".join(refs)


def diagnoses_for_summary(patient):
    # Use main diagnosis-list wording, prioritizing neurologic diagnoses, then
    # comorbidities. Exclude investigation, treatment and narrative fragments.
    rows = []
    seen = set()
    for r in patient["diagnoses"]:
        t = normalize(r["text"])
        if re.match(r"^(?:Ätiolog|Neurologisches Outcome|Neurochirurgie|Anlage|Implantation|Explantation|Elekt|Z\.n\.\s+Revision|Z\.n\.\s+Wechsel|DDDR|Aggregat|Sonden|Vorgeschichte|Klinisch|Akutbildgebung|Bildgebung|Zustand bei|Neurologischer|MMSE|MoCA|Aktuell|Therapie|Barthel|Frühreha|CT\b|cCT\b|Dauermedik|Kardio.?vask|Zerebrovask|prämRS|Keine\s+Allerg|Nach\s+|Herr\b|Frau\b|Der\s+|Die\s+|Das\s+|Wir\s+|Im\s+|Es\s+|Unter\s+|Laut\s+|Pat\.)", t, re.I):
            continue
        if re.search(r"\b(?:Therapie|Medikation|mg\b|Gaben|Dosis|MMSE|NIHSS|Zielwert|Normbereich|mmol/l|g/dl|HbA1c|Hb A1c)\b", t, re.I) and not re.search(r"Epileps|Anfall|Anfäll|Blutung|Infarkt|Diabetes", t, re.I):
            continue
        if len(t) > 220 or t.endswith(":") or re.fullmatch(r"\(?\d+\)?", t):
            continue
        key = re.sub(r"[^\w]", "", t.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    def priority(row):
        t = row["text"]
        return 0 if re.search(r"Amyloid|\bCAA\b|Epilep|Anfall|Anfäll|Status epilep|Blutung|Hämorrhag|Infarkt|Demenz|Encephal|Enzephal|Vaskulitis|Hirn|Schlaganfall|TIA\b", t, re.I) else 1
    rows.sort(key=priority)
    chosen = []
    count = 0
    for r in rows:
        if len(chosen) >= 11 or count + len(r["text"]) > 1050:
            continue
        chosen.append(r)
        count += len(r["text"])
    return chosen, len(rows) - len(chosen)


def excerpt(text, limit):
    text = normalize(text)
    if len(text) <= limit:
        return text, False
    # Prefer the original report's interpretation for a bounded verbatim quote.
    m = re.search(r"Interpretation\s*:\s*", text, re.I)
    selected = text[m.end():] if m else text
    if len(selected) <= limit:
        return selected, True
    stop = selected.rfind(" ", 0, limit)
    return selected[:stop if stop > 0 else limit] + " […]", True


def stopped_mentions(patient):
    notes = {}
    pattern, aliases = drug_patterns()
    for r in patient["medications"]:
        t = r["source_text"]
        for match in pattern.finditer(t):
            if aliases[match[0].casefold()] != r["generic"]:
                continue
            tail = t[match.end():match.end() + 70]
            next_drug = pattern.search(tail)
            if next_drug:
                tail = tail[:next_drug.start()]
            if re.search(r"abgesetzt|ausgeschlichen|ausschleich|beendet|pausiert|nicht\s+eingenommen", tail, re.I):
                notes[r["generic"]] = f"stop/taper wording: {r['filename']}:{r['page']}"
    return notes


def render():
    from html import escape
    from io import BytesIO
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, PageBreak, Spacer
    from reportlab.lib.units import mm

    for label, name in [("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf"), ("Arial-Italic", "ariali.ttf")]:
        pdfmetrics.registerFont(TTFont(label, str(Path("C:/Windows/Fonts") / name)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic")
    ink = colors.HexColor("#173042")
    teal = colors.HexColor("#007C83")
    muted = colors.HexColor("#536776")
    patients = read(OUT / "patient_report_data.json")
    width, height = 210 * mm, 297 * mm
    margin = 13 * mm
    content_width = width - 2 * margin
    gap = 5 * mm
    column_width = (content_width - gap) / 2
    pdf_path = OUT / "EEG_random50_patients_diagnoses_antiepileptics.pdf"
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setTitle("50 random patients with EEG - diagnoses and antiepileptic medications")
    c.setAuthor("Local source-linked review")
    layout = []
    def paragraph(text, size=8, bold=False, color=ink, leading=None):
        style = ParagraphStyle("p", fontName="Arial-Bold" if bold else "Arial", fontSize=size, leading=leading or size * 1.18, textColor=color)
        return Paragraph(text, style)
    def draw(p, x, top, w=content_width):
        _, h = p.wrap(w, height)
        p.drawOn(c, x, top - h)
        return top - h
    def human(v):
        return str(v).replace("_", " ").lower()
    def brief_state(v):
        return "present" if v is True else "absent" if v is False else "unknown"
    for index, patient in enumerate(patients, 1):
        pid = patient["fallnr"]
        label, caa_ref = category(patient)
        y = height - 15 * mm
        c.setFillColor(ink)
        c.setFont("Arial-Bold", 15)
        c.drawString(margin, y, "Fallnr " + pid)
        c.setFont("Arial", 8)
        c.drawRightString(width - margin, y, f"Patient {index}/50 | {len(patient['eeg_records'])} EEG source records")
        y -= 6 * mm
        d = patient["demographics"]
        y = draw(paragraph(escape(f"{d['sex']} · birth year {d['birth_year']}"), 8.2), margin, y) - 3
        y = draw(paragraph("<b>CAA (source-stated):</b> " + escape(label) + (" <font color='#536776'>[" + escape(caa_ref) + "]</font>" if caa_ref else ""), 8.1), margin, y) - 4
        dx, omitted_dx = diagnoses_for_summary(patient)
        dx_text = "; ".join(escape(normalize(r["text"])) + f" <font color='#536776'>[{escape(r['filename'])}:{r['page']}]</font>" for r in dx)
        y = draw(paragraph("<b>Diagnoses — main source lists, condensed:</b> " + (dx_text or "No bounded main diagnosis list found; see source files."), 7.8), margin, y) - 4
        if omitted_dx:
            y = draw(paragraph(f"{omitted_dx} further source-list items retained in the companion document; suspicion and historical wording are preserved.", 7.0, color=muted), margin, y) - 3
        meds = defaultdict(list)
        for r in patient["medications"]:
            ref = f"{r['filename']}:{r['page']}"
            if ref not in meds[r["generic"]]:
                meds[r["generic"]].append(ref)
        stop_notes = stopped_mentions(patient)
        med_text = "; ".join(escape(name) + " <font color='#536776'>[" + escape(refs[-1]) + ("; " + escape(stop_notes[name]) if name in stop_notes else "") + "]</font>" for name, refs in sorted(meds.items()))
        y = draw(paragraph("<b>Antiepileptic medications — named in sources:</b> " + (med_text or "No named antiepileptic medication found in the reviewed sources."), 8.0), margin, y) - 3
        y = draw(paragraph("Medication names include historical/current mentions and seizure-rescue treatment; this is not a reconciled current prescription. Drug indication is not inferred.", 6.9, color=muted), margin, y) - 7
        c.setStrokeColor(colors.HexColor("#C9DADF"))
        c.line(margin, y, width - margin, y)
        y -= 11
        top = y
        bottom = 23 * mm
        available = top - bottom
        chosen = None
        for font, limit in [(8.2, 100000), (8.0, 100000), (7.6, 100000), (8.0, 650), (7.8, 420), (7.5, 260), (7.2, 150), (7.0, 100)]:
            blocks = []
            for num, record in enumerate(patient["eeg_records"], 1):
                e = record["exam"]
                ov = record["row_overrides"]
                primary = ov.get("Primary category", human(e["eeg_primary_finding"]))
                phenotypes = ov.get("All phenotype labels", ", ".join(human(v) for v in e["clinical_phenotype"]))
                ep = {"CONFIRMED": "present", "EXPLICITLY_ABSENT": "absent", "NO_DEFINITE_EVIDENCE": "no definite evidence", "UNKNOWN": "unknown", "UNCERTAIN": "uncertain", "CONFLICTING_EVIDENCE": "conflicting"}[e["epileptiform_evidence_status"]]
                loc = ov.get("Source localization", " ".join([human(e.get("focal_slowing_side", "UNKNOWN")), "/".join(human(r) for r in e.get("focal_slowing_regions", []))]))
                details = f"Epileptiform: {ep}; seizure/status: {brief_state(e.get('seizure_recorded'))}/{brief_state(e.get('electrographic_status_epilepticus'))}."
                if e.get("focal_slowing_present"):
                    details += " Focus: " + loc + "."
                if e.get("assessability") not in (None, "UNKNOWN"):
                    details += " Assessability: " + human(e["assessability"]) + "."
                if phenotypes and phenotypes.casefold() != primary.casefold():
                    details = "Phenotypes: " + phenotypes + ". " + details
                quote, shortened = excerpt(record["source_text"], limit)
                items = [paragraph(escape(f"{num}. {e['eeg_date'] or 'Date unknown'} | {primary}"), font + .3, bold=True, color=teal),
                         paragraph(escape(details), font - .4),
                         paragraph("<b>" + ("Source excerpt" if shortened else "Source text") + " [" + escape(source_ref(record)) + "]:</b> " + escape(quote), font)]
                if record.get("note"):
                    items.append(paragraph("Review note: " + escape(record["note"]), font - .5, color=muted))
                hs = [p.wrap(column_width, available)[1] for p in items]
                blocks.append((items, hs, sum(hs) + 7, shortened))
            col = 0
            used = 0
            fits = True
            positions = []
            for block in blocks:
                if block[2] > available:
                    fits = False
                    break
                if used + block[2] > available:
                    col += 1
                    used = 0
                if col > 1:
                    fits = False
                    break
                positions.append((col, used))
                used += block[2]
            if fits:
                chosen = font, limit, blocks, positions
                break
        if chosen is None:
            raise ValueError("Cannot fit patient legibly on one page: " + pid)
        font, limit, blocks, positions = chosen
        for block, (col, used) in zip(blocks, positions):
            x = margin + col * (column_width + gap)
            yy = top - used
            for p, h in zip(block[0], block[1]):
                p.drawOn(c, x, yy - h)
                yy -= h
        c.setFont("Arial", 6.5)
        c.setFillColor(muted)
        c.drawString(margin, 16 * mm, "EEG 1.1 candidates + noted source clarifications; unknown is not absent. No newly computed Boston/Edinburgh classification.")
        c.drawString(margin, 12 * mm, "Original EEG passages and source-list details: companion PDF. [file:page] references are inside this patient's source folder.")
        c.drawString(margin, 8 * mm, "30 October 2026 | random seed 20261030 | 50 of 241 EEG-screen-positive patients | source review copy, not physician validated")
        c.bookmarkPage(pid)
        c.addOutlineEntry("Fallnr " + pid, pid)
        c.showPage()
        layout.append(dict(fallnr=pid, page=index, EEG_records=len(blocks), body_font=font, excerpt_limit=limit, shortened_EEG_passages=sum(b[3] for b in blocks), diagnosis_rows_displayed=len(dx), further_diagnosis_rows=omitted_dx, antiepileptic_names=sorted(meds)))
    c.save()
    pdf_path.write_bytes(buffer.getvalue())
    save(OUT / "layout_manifest.json", layout)

    detail = OUT / "EEG_random50_full_source_details.pdf"
    styles = {"title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=15, leading=19, spaceAfter=10, textColor=ink),
              "heading": ParagraphStyle("heading", fontName="Arial-Bold", fontSize=10, leading=13, spaceBefore=10, spaceAfter=5, textColor=teal),
              "text": ParagraphStyle("text", fontName="Arial", fontSize=9, leading=12, spaceAfter=6, textColor=ink)}
    def dp(t, style="text"):
        return Paragraph(escape(str(t)), styles[style])
    story = []
    for i, patient in enumerate(patients):
        if i:
            story.append(PageBreak())
        story += [dp("Fallnr " + patient["fallnr"], "title"), dp("Companion source details — longer EEG passages, main diagnosis-list wording, and antiepileptic source references."),
                  dp("EEG labels remain automated candidates with explicit preview clarifications; diagnoses retain source suspicion/history wording. Repeated texts with identical dates are grouped. Other same-date report variants remain separate source records."),
                  dp("Diagnoses: main source-list items", "heading")]
        for r in patient["diagnoses"]:
            story.append(dp(normalize(r["text"]) + f" [{r['filename']} p.{r['page']}]"))
        story.append(dp("Antiepileptic medications: named source mentions", "heading"))
        meds = defaultdict(list)
        for r in patient["medications"]:
            ref = f"{r['filename']} p.{r['page']}"
            if ref not in meds[r["generic"]]:
                meds[r["generic"]].append(ref)
        story.append(dp("; ".join(name + " [" + ", ".join(refs) + "]" for name, refs in sorted(meds.items())) or "No named antiepileptic drug found in reviewed text."))
        for name, note in sorted(stopped_mentions(patient).items()):
            story.append(dp(name + ": explicit source " + note + ". This source statement does not settle the current prescription."))
        story.append(dp("Historical and current mentions are retained without inferring a current prescription or epilepsy diagnosis. Rescue benzodiazepines are included only with source-linked seizure/status context."))
        for num, record in enumerate(patient["eeg_records"], 1):
            e = record["exam"]
            story.append(dp(f"EEG {num} | {e['eeg_date'] or 'Date unknown'} | {source_ref(record)}", "heading"))
            story.append(dp("Category: " + record["row_overrides"].get("Primary category", human(e["eeg_primary_finding"]))))
            story.append(dp(e["source_header"]))
            for block in re.split(r"\n\s*\n", record["source_text"]):
                if block.strip():
                    story.append(dp(normalize(block)))
            if record.get("note"):
                story.append(dp("Source review note: " + record["note"]))
    def footer(canv, doc):
        canv.setFont("Arial", 7)
        canv.setFillColor(muted)
        canv.drawString(15 * mm, 9 * mm, "EEG random 50 | companion source details | source wording, not physician adjudication")
        canv.drawRightString(195 * mm, 9 * mm, str(doc.page))
    SimpleDocTemplate(str(detail), pagesize=(width, height), leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=17 * mm).build(story, onFirstPage=footer, onLaterPages=footer)
    print(str(pdf_path), flush=True)
    print(str(detail), flush=True)
    print("Main PDF pages", len(pymupdf.open(pdf_path)), "minimum body font", min(r["body_font"] for r in layout), flush=True)


def validate():
    from PIL import Image, ImageDraw
    data = read(OUT / "patient_report_data.json")
    layout = read(OUT / "layout_manifest.json")
    selection = read(OUT / "selection.json")
    main = pymupdf.open(OUT / "EEG_random50_patients_diagnoses_antiepileptics.pdf")
    detail = pymupdf.open(OUT / "EEG_random50_full_source_details.pdf")
    assert len(main) == len(data) == 50
    # Remove only running companion footers/page numbers for text comparison.
    detail_text = normalize(" ".join("\n".join(line for line in page.get_text().splitlines() if not line.startswith("EEG random 50 |") and not re.fullmatch(r"\d+", line)) for page in detail))
    checks = []
    images = OUT / "rendered"
    images.mkdir(exist_ok=True)
    for i, (page, patient) in enumerate(zip(main, data)):
        text = page.get_text()
        pid = patient["fallnr"]
        assert re.findall(r"Fallnr (\d+)", text) == [selection["selected_50_fallnr"][i]]
        assert "Antiepileptic medications" in text and "Diagnoses" in text
        assert chr(0xfffd) not in text
        assert not re.search(r"geb\.:|geboren am|SVNR|Adresse:", text, re.I)
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                box = line["bbox"]
                if 60 < box[1] < 780:
                    assert box[0] >= 35 and box[2] <= 561 and box[3] <= 780, (pid, box)
        raw = read(OUT / "patients" / pid / "eeg_result.json")
        raw_by_id = {e["eeg_exam_id"]: e for e in raw["eeg_exams"]}
        for n, r in enumerate(patient["eeg_records"], 1):
            date = r["exam"]["eeg_date"] or "Date unknown"
            assert date in text
            original = re.findall(r"\S+", raw_by_id[r["exam"]["eeg_exam_id"]]["eeg_report_text"])
            pos = 0
            for token in re.findall(r"\S+", r["source_text"]):
                while pos < len(original) and original[pos] != token:
                    pos += 1
                assert pos < len(original), (pid, "source word changed", token)
                pos += 1
            assert normalize(r["source_text"]) in detail_text, (pid, "companion source omission", date)
            assert not re.search(r"(?im)^\s*(?:BAKT-Bef|Abdomensonograph|Neurochirurgisch\w*\s*Konsil|Beurteilung\s*$)", r["source_text"]), (pid, "unrelated source section")
        page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(images / f"patient_{i+1:02}_{pid}.png")
        checks.append(dict(page=i+1, fallnr=pid, EEG_records=len(patient["eeg_records"]), one_patient_per_page=True, margins_ok=True, displayed_EEG_words_preserved=True, companion_EEG_text_roundtrip=True))
    manifest = {r["fallnr"]: r for r in read(PREVIEW / "screening_manifest.json")}
    sources = 0
    for pid in selection["selected_50_fallnr"]:
        for d in manifest[pid]["documents"]:
            assert hashlib.sha256((ROOT / "pdf_db" / pid / d["filename"]).read_bytes()).hexdigest() == d["sha256"]
            sources += 1
    summary = dict(status="COMPLETE_LOCAL_REVIEW_COPY", sample_unchanged=True, patients=50, summary_PDF_pages=len(main), companion_PDF_pages=len(detail),
                   raw_EEG_candidates=sum(len(read(OUT / "patients" / p["fallnr"] / "eeg_result.json")["eeg_exams"]) for p in data),
                   displayed_EEG_source_records=sum(len(p["eeg_records"]) for p in data), unchanged_source_PDFs_checked=sources,
                   minimum_primary_body_font_pt=min(r["body_font"] for r in layout), shortened_EEG_quotes_on_summary_pages=sum(r["shortened_EEG_passages"] for r in layout),
                   clinical_database_writes=0, cloud_patient_calls=0, source_pdf_writes=0,
                   category_status="Automated candidates plus explicit source clarifications; not physician adjudicated", pages=checks)
    save(OUT / "final_validation.json", summary)
    sheet = Image.new("RGB", (1440, 1320), "#dde5e8")
    drawing = ImageDraw.Draw(sheet)
    for k, n in enumerate([1, 2, 11, 20, 43, 47]):
        path = next(images.glob(f"patient_{n:02}_*.png"))
        im = Image.open(path).convert("RGB")
        im.thumbnail((455, 640))
        x, y = (k % 3) * 480 + 12, (k // 3) * 660 + 18
        sheet.paste(im, (x, y))
        drawing.text((x, y - 15), f"Patient page {n}", fill="black")
    sheet.save(OUT / "contact_sheet.png")
    print(json.dumps({k: v for k, v in summary.items() if k != "pages"}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["extract", "prepare", "render", "validate"])
    args = parser.parse_args()
    if args.action == "extract":
        extract()
    elif args.action == "prepare":
        prepare()
    elif args.action == "render":
        render()
    else:
        validate()
