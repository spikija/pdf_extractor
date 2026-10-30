"""Select cervical/cerebral ultrasound, preserving non-neurological reports."""
import re
from dataclasses import asdict
from .examination_inputs import select_examination_inputs
from .neck_ultrasound import extend_cervical_reports
from .neurosonology_v23 import parse_exam, VESSELS


def is_neurosonology(exam):
    if not re.search(r'ultrasound|sonogr|neuroson|duplex|doppler', exam.type + ' ' + exam.header, re.I):
        return False
    if exam.location in ('heart', 'abdomen', 'body'):
        return False
    if exam.location in ('brain', 'neck vessels'):
        return True
    if re.search(r'Neuroson|Transkran|Transcran|Transtemporal|Extrakrani|Extracrani|Carotis|Karotis|Halsgef', exam.header, re.I):
        return True
    # Generic duplex of an arm/leg vein is not a neurosonology examination.
    if re.search(r'Extremit|Armvene|Beinvene|femoral|popliteal', exam.header, re.I):
        return False
    return any(re.search(pattern, exam.text, re.I) for pattern in VESSELS.values())


def extract_bundle(bundle):
    rows, other = [], []
    for exam in extend_cervical_reports(select_examination_inputs(bundle), bundle):
        if is_neurosonology(exam):
            rows.extend(parse_exam(dict(patient_id=bundle.patient_id, id=exam.examination_id, date=exam.date_examination,
                source_document_id=exam.source_document_id, page=exam.page_number, section=exam.section_id, report=exam.text)))
        else:
            other.append(asdict(exam))
    return rows, other
