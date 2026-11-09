"""One-time physician DOCX migration, not imported by extraction.

The generated YAML is the runtime authority. Running again requires --output
pointing at an empty directory, so future physician edits cannot be overwritten.
"""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Initial physician mapping only. Future additions belong in YAML, not here.
INITIAL = '''STATUS|STATUS|CONTEXTUAL|Status;Status neurologicus:
ANAMNESE|HISTORY|EXACT_NORMALIZED|Vorgeschichte / aktueller Beobachtungsgrund;Anamnese;Anfallsanamnese:;Aktuelle Anamnese;Fremdanamnese;Aktuelle Fremdanamnese
ASSESSMENT|ASSESSMENT|CONTEXTUAL|Beurteilung
THERAPY_RECOMMENDATION|THERAPY|EXACT_NORMALIZED|Therapieempfehlung
PREMEDICATION|MEDICATION|EXACT_NORMALIZED|Prämedikation:
RECOMMENDED_MEDICATION|MEDICATION|EXACT_NORMALIZED|Empfohlene Medikation
FURTHER_RECOMMENDATIONS|THERAPY|EXACT_NORMALIZED|Weitere empfohlene Maßnahmen
HOSPITAL_SUMMARY|HOSPITAL_SUMMARY|CONTEXTUAL|Zusammenfassung des Aufenthalts;Zusammenfassung des Aufenthaltes
DETAIL_REPORT|DETAIL_REPORT|CONTEXTUAL|Detailbericht
ALLERGY_INTOLERANCE|ALLERGY|EXACT_NORMALIZED|Allergien und Medikamentenunverträglichkeiten;Allergien
EPIKRISIS_COURSE|EPIKRISIS_COURSE|CONTEXTUAL|Epikrise / Verlauf;Epikrise
NEUROLOGICAL_EXAM|NEUROLOGICAL_EXAM|PREFIX|Neurologischer Befund;Neurologischer Status
INTERNAL_MEDICINE_EXAM|INTERNAL_MEDICINE_EXAM|PREFIX|Internistischer Befund
NEUROLOGICAL_EMERGENCY_DEPARTMENT|NEUROLOGICAL_EMERGENCY_DEPARTMENT|PREFIX|Neurologische Notfallambulanz
INTERNAL_MEDICINE_STROKE_CONSULT|INTERNAL_MEDICINE_STROKE_CONSULT|PREFIX|Internistisches Schlaganfall Konsil
PSYCHODIAGNOSTIC_REPORT|PSYCHODIAGNOSTIC_REPORT|PREFIX|Psychodiagnostischer Befund
DERMATOLOGY_REPORT|DERMATOLOGY_REPORT|PREFIX|Dermatologischer Befund
DERMATOLOGY_CONSULT|DERMATOLOGY_CONSULT|PREFIX|Dermatologischer Konsilbefund
INFECTIOUS_DISEASE_CONSULT|INFECTIOUS_DISEASE_CONSULT|PREFIX|Infektiologisches Konsil
ENT_CONSULT|ENT_CONSULT|PREFIX|HNO-Konsil
OPHTHALMOLOGY_CONSULT|OPHTHALMOLOGY_CONSULT|PREFIX|Augenärztliches Konsil
UROLOGY_OUTPATIENT_LETTER|UROLOGY_OUTPATIENT_LETTER|PREFIX|Ambulanzbrief Urologie
VASCULAR_SURGERY_REPORT|VASCULAR_SURGERY_REPORT|PREFIX|Arztbericht Gefäßchirurgie
MRI|MRI|PREFIX|Kernspintomographie:;Kernspintomografie
CT|CT|PREFIX|Computertomografie;Computertomographie
X_RAY|X_RAY|PREFIX|Röntgen vom
CAROTID_ANGIOGRAPHY|ANGIOGRAPHY|PREFIX|Angiografie Carotiden beidseits
ANGIOGRAPHY_MULTI_VESSEL|ANGIOGRAPHY|PREFIX|Angiografie: A. carotis interna li;Angiografie: A. carotis interna re;Angiografie: A. carotis externa li;Angiografie: A. carotis externa re;Angiografie: A. vertebralis rechts;Angiografie: A. vertebralis links
SPECT_BRAIN|SPECT|PREFIX|SPECT HMPAO Gehirn
EEG|EEG|PREFIX|Elektroenzephalografie (EEG)-Befund;Elektroenzephalographie (EEG)-Befund;Routine EEG;EEG;EEG Befund;cEEG;cEEG Kurzbefund;kontinuierliches EEG;continuous EEG;Langzeit EEG;Standard EEG;Schlafentzugs EEG
VIDEO_EEG|EEG|PREFIX|Mobile Video-EEG-Ableitung;Video EEG;Video EEG Monitoring
ECHOCARDIOGRAPHY|ECHOCARDIOGRAPHY|PREFIX|ECHOKARDIOGRAPHIE;Echokardiografie;Herzecho
LONG_TERM_ECG_24H|ECG|PREFIX|24-STUNDEN-EKG;24 H EKG
LONG_TERM_ECG_72H|ECG|PREFIX|72-STUNDEN-EKG
RESTING_ECG|ECG|EXACT_NORMALIZED|RUHE EKG;RUHE ECG
LONG_TERM_ECG|ECG|EXACT_NORMALIZED|LANGZEIT EKG;HOLTER EKG;48 STUNDEN EKG
ECG|ECG|EXACT_NORMALIZED|EKG;ECG;12 KANAL EKG;12 KANAL ECG
NEUROSONOLOGY|NEUROSONOLOGY|PREFIX|Neurosonologie;Duplexsonographie
DOPPLER_DUPLEX|ULTRASOUND|PREFIX|Doppler Duplex
TCD_VASOSPASM|ULTRASOUND|PREFIX|TCD Vasospasmus
ABDOMINAL_ULTRASOUND|ULTRASOUND|PREFIX|Ultraschall-Abdomen
GROIN_ULTRASOUND|ULTRASOUND|PREFIX|Ultraschall Leiste
NEUROLINGUISTIC_ASSESSMENT|NEUROLINGUISTIC_ASSESSMENT|PREFIX|Neurolinguistische Diagnostik
NEUROPSYCHOLOGY|NEUROPSYCHOLOGY|PREFIX|Neuropsychologische Diagnostik
SPEECH_THERAPY|SPEECH_THERAPY|EXACT_NORMALIZED|Bericht der Logopädie:
PHYSIOTHERAPY|PHYSIOTHERAPY|EXACT_NORMALIZED|Bericht der Physiotherapie:
NUTRITION_COUNSELING|NUTRITION_COUNSELING|PREFIX|Ernährungsmed. Beratung
LABORATORY|LABORATORY|PREFIX|Laborwerte;Labor
COLONOSCOPY_EMR|COLONOSCOPY_EMR|PREFIX|Coloskopie + Endoskopische Mucosaresektion
PSYCHIATRY_CONSULT|PSYCHIATRY_CONSULT|PREFIX|Psychiatrisches Konsil;Psychiatrischer Konsil;Psychiatrisches Konsilium
PSYCHIATRIC_STATUS|PSYCHIATRIC_STATUS|EXACT_NORMALIZED|Psychiatrischer Status
SOMATIC_STATUS|SOMATIC_STATUS|EXACT_NORMALIZED|Somatischer Status
MRI_ABBREVIATION|MRI|REGEX|(?:MRT|MRI)(?: [^.!?]{1,160})?
CT_ABBREVIATION|CT|REGEX|(?:CT|CCT|CTA)(?: [^.!?]{1,160})?
DIAGNOSES|DIAGNOSES|PREFIX|Diagnosen
MEDICATION|MEDICATION|EXACT_NORMALIZED|Medikation;Dauermedikation;Entlassungsmedikation;Aufnahmemedikation;Medikamente
PROCEDURE_PLAN|THERAPY|EXACT_NORMALIZED|Prozedere;Procedere;Kontrollen
FINDINGS|FINDINGS|EXACT_NORMALIZED|Befunde
FAMILY_HISTORY|OTHER_HISTORY|PREFIX|Familienanamnese
SOCIAL_HISTORY|OTHER_HISTORY|PREFIX|Sozialanamnese
VEGETATIVE_HISTORY|OTHER_HISTORY|PREFIX|Vegetative Anamnese
CLINICAL_FINDINGS|CLINICAL_FINDINGS|PREFIX|Untersuchungsbefund;Klinischer Befund;Neurostatus;Gefäßstatus;Pulsstatus
OTHER_REPORTS|OTHER_REPORTS|PREFIX|Auszüge aus
LETTER_END|ADMINISTRATIVE|PREFIX|Mit freundlichen'''

BOILERPLATE = [
    ('UNIVERSITY_HOSPITAL', r'Universitätsklinikum\s+der(?:\s+.*)?'),
    ('UNIVERSITY_NEUROLOGY', r'(?:Universitätsklinik|Uniklinik)\s*f\.?\s*Neurologie(?:\s+.*)?'),
    ('PARACELSUS', r'Paracelsus\s+Medizinischen(?:\s+.*)?'),
    ('PRIVATE_UNIVERSITY', r'Privatuniversität'),
    ('PHONE', r'(?:Telefon|Fax)\s*:?\s*\+?\s*\d[\d\s()+/.-]*'),
    ('CONTACT', r'Neurologie\s*@\s*salk\s*\.\s*at\s*\|\s*www\s*\.\s*salk\s*\.\s*at'),
    ('OPERATING_COMPANY', r'Gemeinnützige\s+Salzburger\s+Landeskliniken\s*Betriebsgesellschaft\s*mbH'),
    ('DOPPLER_CAMPUS', r'Christian\s*[- ]?\s*Doppler\s*[- ]?\s*Klinik\s*\|\s*A\s*[- ]?\s*5020\s+Salzburg'),
    ('ADDRESS_UID', r'Ignaz\s*[- ]?\s*Harrer\s*(?:straße|strasse|str\.)\s*79\s*\|\s*UID\s*[-.]?\s*Nr\.?\s*ATU\s*\d+'),
    ('COURT_REGISTER', r'Landesgericht\s+Salzburg\s*\|\s*FN\s*\d[\d\sA-Za-z|+()/.-]*'),
    ('PAGE_IDENTITY_BOILERPLATE', r'[^;\n]{2,120},\s*geb\s*\.?\s*:?\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}\s*;\s*Seite\s*(?P<page_number>\d+)\s*(?:von\s*\d+)?'),
    ('PAGE_NUMBER', r'Seite\s+\d+(?:\s*(?:von|/)\s*\d+)?'),
    ('UNIVERSITY_HOSPITAL_LEGACY', r'Universitätsklinikum\s+Salzburg'),
    ('HALLEIN', r'Landesklinik\s+Hallein'),
]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError('Migration output must be empty; never overwrite physician YAML')
    source = ROOT/'docs/SectionStop_Dictionary_Manual.docx'
    with ZipFile(source) as z:
        root = E.fromstring(z.read('word/document.xml'))
        paragraphs = [''.join(t.text or '' for t in p.findall('.//{*}t')) for p in root.findall('.//{*}p')]
    manual = paragraphs[paragraphs.index('Sections:')+1:]
    manual = manual[:next(i for i, s in enumerate(manual) if s.startswith('Special stops:'))]
    # Wrapped angiography enumeration is a single source entry.
    source_entries = []; pending = ''
    for line in manual:
        if pending or line.startswith('Angiografie:'):
            pending += (' ' if pending else '') + line
            if pending.endswith('*'):
                source_entries.append(pending); pending = ''
        elif line.strip():
            source_entries.append(line.strip())
    sections = []
    for line in INITIAL.splitlines():
        canonical, family, kind, variants = line.split('|', 3)
        contextual = kind == 'CONTEXTUAL'
        allowed = canonical in ('ANAMNESE', 'HOSPITAL_SUMMARY')
        eeg_action = 'KEEP' if family == 'EEG' else 'CONTEXTUAL' if contextual else 'STOP'
        sections.append(dict(canonical_section=canonical, section_family=family, variants=variants.split(';'),
            match_type=kind, boundary_strength='CONTEXTUAL' if contextual else 'HARD',
            module_actions=dict(EEG=eeg_action, TFNE_CLINICAL_HISTORY='INCLUDE' if allowed else 'STOP',
                RADIOLOGY='INCLUDE' if family in ('MRI','CT','X_RAY','ANGIOGRAPHY','SPECT') else 'STOP',
                CLINICAL_HISTORY='START_NEW_SECTION'),
            parent_context=[], date_suffix_allowed=True, continuation_allowed=kind=='PREFIX', case_sensitive=False,
            active=True, notes='Context resolved by module, never an unconditional global stop.' if contextual else 'Physician dictionary or confirmed existing regression variant.'))
        if canonical.startswith('LONG_TERM_ECG_'):
            sections[-1]['subtype'] = 'ECG_'+canonical.rsplit('_',1)[1]
        if canonical in ('RESTING_ECG','LONG_TERM_ECG','VIDEO_EEG'):
            sections[-1]['subtype'] = canonical
        if canonical in ('MRI_ABBREVIATION', 'CT_ABBREVIATION'):
            sections[-1]['require_date_for_continuation'] = True
    cfg = dict(version=1, source_document=str(source.relative_to(ROOT)), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        manual_entries=source_entries,
        prose_guard_regex=r'\b(?:wurde[n]?|wird|werden|war|waren|zeigte[n]?|empfohlen|erwähnt|ergab|ergaben|erfolgte|soll|konnte[n]?)\b',
        continuation_line_regex=r'^(?:\(?\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}\)?\s*:??|(?:Kernspintomographie|Angiografie):|(?:SWI|Diffusion|Schädel|Hirnstamm)\b)',
        unknown_heading_regex=r'(?:[A-ZÄÖÜ][A-Za-zÄÖÜäöüß /-]{2,80}:|[A-ZÄÖÜ][A-ZÄÖÜ /-]{3,80})', sections=sections)
    (args.output/'section_dictionary.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),encoding='utf-8')
    patterns = [dict(pattern_id=key, regex=regex, strong=True,
        patient_identity_footer=key=='PAGE_IDENTITY_BOILERPLATE') for key, regex in BOILERPLATE]
    boiler = dict(version=1, header_max_fraction=.16, footer_min_fraction=.85, minimum_repeated_pages=2,
        clinical_protection_regex=r'\bEEG\b|cEEG|Theta|Delta|epilep|Hirnfunktions|Interpretation|beurteilbar|Grundaktiv|sharp|spike|Anfall|Reaktion|\b(?:Befund|Therapie|klinisch|wurde|zeigte|keine|stabil|erfolgte|hat|ist|war|klagte|Termine|Kontrollen)\b',
        patterns=patterns)
    (args.output/'page_boilerplate.yaml').write_text(yaml.safe_dump(boiler,allow_unicode=True,sort_keys=False),encoding='utf-8')
    (args.output/'migration_source_inventory.json').write_text(json.dumps(dict(manual_entries=source_entries),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(manual_entries_ingested=len(source_entries),rules=len(sections),output=str(args.output))))


if __name__ == '__main__':
    main()
