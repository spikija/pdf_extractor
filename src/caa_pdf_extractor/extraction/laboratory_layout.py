"""Deterministic six-biomarker PDF geometry inspection; clinical writes disabled."""
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from ..config import pdf_db_path
from ..database import SessionLocal
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT
from .laboratory_inputs import MATCHERS, select_laboratory_inputs
from .laboratory_models import NUMBER, parse_decimal
from .radiology_artifacts import artifact_directory, digest

METHOD_VERSION = 'laboratory_layout_v2'
ROW_TOLERANCE = 2.0
DATE_TOKEN = re.compile(r'^(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})$')
UNITS = {'mg/dl', 'mg/l', '%', 'mmol/mol', 'mmol/m', 'pg/ml', 'ng/l', 'ng/ml', 'ratio', 'dimensionless'}
ROLES = {'erster': 'first', 'first': 'first', 'letzter': 'last', 'last': 'last',
         'min.': 'minimum', 'min': 'minimum', 'minimum': 'minimum',
         'max.': 'maximum', 'max': 'maximum', 'maximum': 'maximum',
         'referenz': 'reference', 'referenzbereich': 'reference', 'normbereich': 'reference',
         'reference': 'reference'}


@dataclass(frozen=True)
class Token:
    token_index: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_number: int

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    def provenance(self):
        return asdict(self)


def page_tokens(words, page_number):
    tokens = []
    for index, word in enumerate(words):
        x0, y0, x1, y1, text = word[:5]
        if (not isinstance(text, str) or not text or page_number <= 0
                or not all(math.isfinite(n) for n in (x0, y0, x1, y1))
                or x0 >= x1 or y0 >= y1):
            raise ValueError('Invalid PDF word/bbox provenance')
        tokens.append(Token(index, text, x0, y0, x1, y1, page_number))
    return tokens


def bbox(tokens):
    return [min(t.x0 for t in tokens), min(t.y0 for t in tokens),
            max(t.x1 for t in tokens), max(t.y1 for t in tokens)] if tokens else None


def visual_rows(tokens):
    rows = []
    for token in sorted(tokens, key=lambda t: (t.cy, t.x0, t.token_index)):
        if rows and abs(token.cy - sum(t.cy for t in rows[-1]) / len(rows[-1])) <= ROW_TOLERANCE:
            rows[-1].append(token)
        else:
            rows.append([token])
    return [sorted(row, key=lambda t: (t.x0, t.token_index)) for row in rows]


def parsed_date(text):
    if not DATE_TOKEN.fullmatch(text):
        return None
    try:
        return (date.fromisoformat(text) if '-' in text else date(*map(int, text.split('.')[::-1]))).isoformat()
    except ValueError:
        return None


def table_header(row):
    unit = next((t for t in row if t.text.casefold() in ('einheit', 'unit')), None)
    parameter = next((t for t in row if t.text.casefold() in ('parameter', 'analyte', 'biomarker')), None)
    if unit is None or parameter is None:
        return None
    cells = []
    for index, token in enumerate(row):
        role, explicit = ROLES.get(token.text.casefold()), parsed_date(token.text)
        if token.x0 <= unit.x1 or not (role or explicit):
            continue
        refs = [token]
        if role and index + 1 < len(row) and row[index + 1].text.casefold() in ('wert', 'value'):
            refs.append(row[index + 1])
        bounds = bbox(refs)
        cells.append({'text': ' '.join(t.text for t in refs), 'bbox': bounds,
                      'center': (bounds[0] + bounds[2]) / 2, 'role': role or 'measurement',
                      'date': explicit, 'tokens': refs})
    cells.sort(key=lambda c: c['center'])
    if not cells:
        return None
    return {'unit': unit, 'parameter': parameter, 'row': row, 'cells': cells,
            'y': max(t.y1 for t in row)}


def column_for(value, header):
    cells = header['cells']
    centers = [cell['center'] for cell in cells]
    distances = [abs(value.cx - center) for center in centers]
    index = min(range(len(cells)), key=lambda i: distances[i])
    boundaries = [(a + b) / 2 for a, b in zip(centers, centers[1:])]
    ambiguous = any(value.x0 <= edge <= value.x1 or abs(value.cx - edge) <= 1 for edge in boundaries)
    if len(cells) == 1:
        ambiguous |= distances[index] > 30
    else:
        ambiguous |= distances[index] > max(centers[i + 1] - centers[i] for i in range(len(centers) - 1)) / 2
    return index, cells[index], ambiguous


def date_for_column(value, header, rows, cell):
    if cell['date']:
        return cell['date'], cell['tokens'], False
    possible = []
    height = max(t.y1 - t.y0 for t in header['row'])
    top = min(t.y0 for t in header['row'])
    for row in rows:
        # A separate date-header line must be immediately above this table,
        # contain only date/header/time tokens, and occupy its value columns.
        if not (top - height * 1.5 <= max(t.y1 for t in row) <= top):
            continue
        if not all(parsed_date(t.text) or re.fullmatch(r'\d{1,2}:\d{2}|Datum|Date|Entnahme|Sample', t.text, re.I) for t in row):
            continue
        for token in row:
            if token.x0 <= header['unit'].x1 or not parsed_date(token.text):
                continue
            _, assigned, ambiguous = column_for(token, header)
            if assigned is cell:
                possible.append((parsed_date(token.text), token, ambiguous))
    if len(possible) != 1:
        return None, [p[1] for p in possible], bool(possible)
    return (None if possible[0][2] else possible[0][0]), [possible[0][1]], possible[0][2]


def extract_page(tokens, source_document_id):
    rows = visual_rows(tokens)
    headers = [h for row in rows if (h := table_header(row))]
    output = []
    for row in rows:
        text = ' '.join(t.text for t in row)
        marker = next((name for name, regex in MATCHERS.items() if regex.search(text)), None)
        if marker is None or re.search(r'HbA1c[- ]Wert', text, re.I):
            continue
        # A planned interval, treatment target or narrative number is not a result cell.
        if re.search(r'\b(?:Kontrolle|Laborkontrolle|Zielwert|Zielbereich|Hausarzt|Monaten?|Tagen?)\b', text, re.I):
            continue
        before = [h for h in headers if h['y'] < min(t.y0 for t in row)]
        header = max(before, key=lambda h: h['y']) if before else None
        if header is None:
            continue
        # An analyte must occupy the label column, not a note in a value cell.
        left = [t for t in row if t.x1 < header['unit'].x0] if header else row
        label_text = ' '.join(t.text for t in left)
        match = MATCHERS[marker].search(label_text)
        if match is None:
            continue
        labels = []
        offset = 0
        for token in left:
            if offset < match.end() and offset + len(token.text) > match.start():
                labels.append(token)
            offset += len(token.text) + 1
        ifcc = marker == 'hba1c' and any('IFCC' in t.text.upper() for t in left)
        if ifcc:
            labels.extend(t for t in left if 'IFCC' in t.text.upper() and t not in labels)
        competing = sum(len(regex.findall(label_text)) for regex in MATCHERS.values())
        # Ratio aliases also contain the beta42 alias; this nested match is one label.
        if marker == 'csf_beta4240_ratio':
            competing -= len(MATCHERS['csf_beta42'].findall(label_text))
        unit_tokens = [t for t in row if t.text.casefold() in UNITS and t.x0 > max(t.x1 for t in labels)
                       and (header is None or t.x0 < header['cells'][0]['bbox'][0])]
        unit_ambiguous = len(unit_tokens) > 1
        if len(unit_tokens) == 1 and unit_tokens[0].text.casefold() == 'mmol/m':
            parent = unit_tokens[0]
            fragments = [t for t in tokens if t.text == 'ol' and abs(t.x0 - parent.x0) < 2
                         and parent.y1 < t.cy <= parent.y1 + 2 * (parent.y1 - parent.y0)]
            if len(fragments) == 1:
                unit_tokens.extend(fragments)
            else:
                unit_ambiguous = True
        unit_text = ''.join(t.text for t in unit_tokens)
        values = [t for t in row if NUMBER.fullmatch(t.text) and t.x0 > max(t.x1 for t in labels)
                  and (header is None or t.x0 > header['unit'].x1)]
        column_counts = defaultdict(int)
        assignments = {}
        for value in values:
            assigned = column_for(value, header) if header else (None, None, True)
            assignments[value.token_index] = assigned
            column_counts[assigned[0]] += 1
        for value in values:
            index, cell, column_ambiguous = assignments[value.token_index]
            issues = []
            if header is None or column_ambiguous or column_counts[index] > 1 or competing != 1 or unit_ambiguous:
                issues.append('AMBIGUOUS_LAYOUT')
            role = cell['role'] if cell else 'unresolved'
            associated, date_refs, date_ambiguous = date_for_column(value, header, rows, cell) if cell else (None, [], False)
            if date_ambiguous:
                issues.append('AMBIGUOUS_DATE_COLUMN')
            canonical_unit = unit_text.casefold()
            if marker == 'crp' and canonical_unit != 'mg/dl':
                issues.append('UNIT_MISMATCH')
            elif marker == 'hba1c' and not ifcc and canonical_unit != '%':
                issues.append('UNIT_MISMATCH')
            elif marker.startswith('csf_') and marker != 'csf_beta4240_ratio' and canonical_unit not in ('pg/ml', 'ng/ml', 'ng/l'):
                issues.append('UNIT_MISMATCH')
            elif marker == 'csf_beta4240_ratio' and canonical_unit not in ('', 'ratio', 'dimensionless'):
                issues.append('UNIT_MISMATCH')
            if ifcc and canonical_unit != 'mmol/mol':
                issues.append('UNIT_MISMATCH')
            refs = row + unit_tokens + (header['row'] if header else []) + date_refs
            candidate = {'candidate_id': f'doc{source_document_id}_p{value.page_number}_token{value.token_index}',
                'source_document_id': source_document_id, 'page_number': value.page_number,
                'biomarker': marker, 'biomarker_label_text': ' '.join(t.text for t in labels),
                'biomarker_label_bbox': bbox(labels), 'raw_numeric_text': value.text,
                'decimal_value': str(parse_decimal(value.text)), 'numeric_value_bbox': bbox([value]),
                'unit_text': unit_text, 'unit_bbox': bbox(unit_tokens),
                'unit_fragments': [t.provenance() for t in unit_tokens],
                'column_role': role, 'column_header_text': cell['text'] if cell else None,
                'column_header_bbox': cell['bbox'] if cell else None,
                'date_of_exam': associated, 'date_header_text': ' '.join(t.text for t in date_refs) or None,
                'date_header_bbox': bbox(date_refs),
                'x_association': {'value_center': value.cx, 'column_center': cell['center'] if cell else None,
                                  'distance': abs(value.cx - cell['center']) if cell else None},
                'reconstructed_row_text': text, 'extraction_method': 'layout', 'method_version': METHOD_VERSION,
                'source_tokens': [t.provenance() for t in sorted(set(refs), key=lambda t: t.token_index)],
                'audit_only': ifcc or role in ('minimum', 'maximum', 'reference') or 'UNIT_MISMATCH' in issues,
                'issues': sorted(set(issues)), 'validation_status': 'VALID' if not issues else 'AMBIGUOUS_LAYOUT' if any('AMBIGUOUS' in s for s in issues) else 'UNIT_MISMATCH',
                'date_status': 'RESOLVED' if associated else 'AMBIGUOUS_DATE_COLUMN' if date_ambiguous else 'NO_EXPLICIT_COLUMN_DATE',
                'clinical_persistence_authorized': False}
            output.append(candidate)
    return output


def validate_candidate(candidate, tokens):
    """Replay geometry against actual page tokens, including every coordinate."""
    expected = next((c for c in extract_page(tokens, candidate['source_document_id'])
                     if c['candidate_id'] == candidate['candidate_id']), None)
    geometry = {k: v for k, v in candidate.items() if k != 'pdf_sha256'}
    if expected is None or digest(expected) != digest(geometry):
        raise ValueError('Laboratory token/bbox association does not match PDF geometry')
    return candidate['validation_status']


def inspect_patient(patient_id, *, session_factory=None, debug_root=None, pdf_root=None):
    artifact_directory(patient_id, debug_root)
    factory = session_factory or SessionLocal
    root = Path(pdf_root or pdf_db_path()).resolve()
    summary = {'patient_id': patient_id, 'resolved_case_id': None, 'status': 'SUCCESS',
               'extraction_method': 'layout', 'granite_called': False, 'clinical_rows_inserted': 0,
               'verified_artifact_created': False, 'clinical_commit_performed': False}
    candidates, sources = [], []
    try:
        with factory.begin() as session:
            summary['resolved_case_id'] = resolve_case_id(patient_id, session=session)
        bundle = build_patient_bundle(patient_id, factory)
        pages = defaultdict(set)
        for selected in select_laboratory_inputs(bundle):
            section = next(s for d in bundle.documents for s in d.sections if s.section_id == selected.section_id)
            pages[selected.source_document_id].update(range(section.page_start, section.page_end + 1))
        import pymupdf
        for document in bundle.documents:
            if document.source_document_id not in pages:
                continue
            path = (root / document.relative_path).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Source PDF outside configured PDF root')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != document.sha256:
                raise ValueError('Source PDF fingerprint differs from ingested source')
            with pymupdf.open(path) as pdf:
                for page_number in sorted(pages[document.source_document_id]):
                    tokens = page_tokens(pdf[page_number - 1].get_text('words'), page_number)
                    found = extract_page(tokens, document.source_document_id)
                    for candidate in found:
                        validate_candidate(candidate, tokens)
                        candidate['pdf_sha256'] = sha
                    candidates.extend(found)
                    relevant = {t['token_index'] for c in found for t in c['source_tokens']}
                    ranges = [row for row in visual_rows(tokens) if any(t.text.casefold() == 'laborwerte' for t in row)
                              and any(t.text.casefold() == 'bis' for t in row)]
                    relevant.update(t.token_index for row in ranges for t in row)
                    sources.append({'source_document_id': document.source_document_id, 'page_number': page_number,
                        'pdf_sha256': sha, 'relevant_tokens': [t.provenance() for t in tokens if t.token_index in relevant],
                        'excluded_lab_date_ranges': [{'text': ' '.join(t.text for t in row), 'bbox': bbox(row),
                            'reason': 'Overall range is not an individual column date'} for row in ranges]})
        unknown_dates = [c['candidate_id'] for c in candidates if c['date_of_exam'] is None and not c['audit_only']]
        summary.update(candidate_count=len(candidates), validation_status='VALID' if all(c['validation_status'] == 'VALID' for c in candidates) else 'REVIEW_REQUIRED',
                       unresolved_date_candidates=unknown_dates,
                       clinical_review_status='REVIEW_REQUIRED',
                       clinical_artifact_status='NOT_CREATED: layout/date observations await review')
    except CaseResolutionError as exc:
        summary.update(status=exc.status, validation_status=exc.status)
    except Exception as exc:
        summary.update(status='FAILED_LAYOUT', validation_status='FAILED_LAYOUT', error_type=type(exc).__name__)
    output = {**summary, 'candidates': candidates, 'source_pages': sources}
    path = Path(debug_root or DEBUG_ROOT) / patient_id / 'laboratory_layout_candidates.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {**summary, 'layout_debug_artifact': str(path)}
