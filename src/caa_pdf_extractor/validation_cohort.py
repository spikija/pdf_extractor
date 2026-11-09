"""Deterministic source screening and a bounded second validation cohort.

Screening flags describe wording, never adjudicated diagnoses. Only registered new
patients may enter the two mature module runners; reference patients are read-only.
"""
import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import pymupdf
from .workbench_store import ROOT, PILOTS, read, sha, atomic
from .extraction.assertion_scope import AF_PATTERN, validate_assertion

FEATURES = ('AF_positive', 'AF_negative', 'AF_uncertain', 'CAA_positive', 'CAA_uncertain',
    'stroke', 'ICH', 'TIA', 'seizure', 'multiple_admissions', 'multiple_PDFs', 'long_chart',
    'simple_chart', 'neurosonology', 'clinical_scale', 'complex_medications', 'format_abnormality', 'copied_diagnoses')


def csv_write(path, rows, fields=None):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=fields or list(rows[0]) if rows else fields or ['patient_id'])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def flags(text, pdf_count, page_count):
    af = Counter(validate_assertion(text, m.start(), m.end())['assertion_status'] for m in AF_PATTERN.finditer(text))
    caa_rx = re.compile(r'amyloidangiopathie|\bCAA\b', re.I)
    caa = Counter(validate_assertion(text, m.start(), m.end())['assertion_status'] for m in caa_rx.finditer(text))
    encounters = len(set(re.findall(r'vo[nm]\s+(\d{1,2}\.\d{1,2}\.\d{4})\s+bis\s+(?:zum\s+)?(\d{1,2}\.\d{1,2}\.\d{4})', text, re.I)))
    doses = len(re.findall(r'\b\d+(?:[.,]\d+)?\s*(?:mg|µg|mcg|IE)\b', text, re.I))
    clinical = re.compile(r'vorhofflim|hyperton|diabet|amyloid|infarkt|ischäm|blutung|epileps|\bTIA\b', re.I)
    lines = [re.sub(r'\W+', ' ', line.casefold()).strip() for line in text.splitlines() if clinical.search(line) and 15 < len(line) < 240]
    counts = Counter(lines)
    features = dict(AF_positive=af['ESTABLISHED'] > 0, AF_negative=af['NEGATED'] > 0,
        AF_uncertain=af['UNCERTAIN'] > 0, CAA_positive=caa['ESTABLISHED'] > 0,
        CAA_uncertain=caa['UNCERTAIN'] > 0, stroke=bool(re.search(r'ischäm\w*.{0,45}(?:infarkt|insult)|mediainfarkt|mediateilinfarkt', text, re.I)),
        ICH=bool(re.search(r'intra[cz]erebral\w*.{0,30}(?:blutung|hämatom)|\bICH\b', text, re.I)),
        TIA=bool(re.search(r'\bTIA\b|transitorisch\w*\s+ischäm', text, re.I)),
        seizure=bool(re.search(r'epileps|epileptisch|status epilepticus', text, re.I)),
        multiple_admissions=encounters >= 2, multiple_PDFs=pdf_count >= 2,
        long_chart=page_count >= 45 or pdf_count >= 8, simple_chart=pdf_count == 1 and page_count <= 6,
        neurosonology=bool(re.search(r'neuroson|duplex|doppler', text, re.I)),
        clinical_scale=bool(re.search(r'\bNIHSS\b|\bmRS\b|modified rankin|rankin.{0,20}\d', text, re.I)),
        complex_medications=doses >= 35,
        format_abnormality=bool(re.search(r'\ufffd|[A-Za-z][0-9]+mg|\w{38,}', text)) or bool(re.search(r'\b(?:199\d|200[0-5])\b', text)),
        copied_diagnoses=any(n >= 3 for n in counts.values()))
    return features, af, caa, encounters, doses


def screen(pdf_root, cached_bundles=None):
    cached_bundles = cached_bundles or {}
    rows = []
    for folder in sorted(pdf_root.iterdir()):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        files = sorted(folder.glob('*.pdf'))
        if not files:
            continue
        pages, texts, errors = 0, [], []
        cached = cached_bundles.get(folder.name)
        cached_docs = {d['filename']: d for d in cached.get('documents', [])} if cached else {}
        sampled = 0
        for path in files:
            try:
                with pymupdf.open(path) as pdf:
                    pages += len(pdf)
                    prior = cached_docs.get(path.name)
                    if prior and prior.get('sha256') == sha(path):
                        texts.append('\n'.join(s.get('text', '') for s in prior['sections']))
                    elif sampled < 12:
                        # Bounded source skim only; rich PDF parsing happens only after selection.
                        for number in sorted({0, min(1, len(pdf)-1), len(pdf)-1}):
                            if number >= 0 and sampled < 12:
                                texts.append(pdf[number].get_text('text', sort=True)); sampled += 1
            except Exception as exc:
                errors.append(type(exc).__name__ + ':' + path.name)
        features, af, caa, encounters, doses = flags('\n'.join(texts), len(files), pages)
        rows.append(dict(patient_id=folder.name, pdf_count=len(files), page_count=pages,
            encounter_count=encounters, selection_reason='',
            AF_signal='|'.join(k for k, v in [('POSITIVE_WORDING',af['ESTABLISHED']),('NEGATIVE_WORDING',af['NEGATED']),('UNCERTAIN_WORDING',af['UNCERTAIN'])] if v) or 'NONE_IN_SCREEN',
            CAA_signal='|'.join(k for k, v in [('POSITIVE_WORDING',caa['ESTABLISHED']),('UNCERTAIN_WORDING',caa['UNCERTAIN'])] if v) or 'NONE_IN_SCREEN',
            stroke_signal=features['stroke'], ICH_signal=features['ICH'], seizure_signal=features['seizure'],
            neurosonology_signal=features['neurosonology'], clinical_scale_signal=features['clinical_scale'],
            medication_complexity=f'{doses} dose-like tokens in screened text; ' + ('HIGH' if features['complex_medications'] else 'LOWER'),
            features=features, screening_errors=errors,
            screening_basis='HASH_MATCHED_SAVED_TEXT_AND_BOUNDED_NATIVE_SKIM' if cached_docs else 'BOUNDED_NATIVE_SKIM',
            sampled_uncached_pages=sampled))
    return rows


def choose(rows, new_count=15, reference_ids=PILOTS):
    if new_count != 15:
        raise ValueError('This phase is bounded to 15 new patients and five references')
    candidates = [r for r in rows if r['patient_id'] not in reference_ids and not r['screening_errors']]
    if len(candidates) < new_count:
        raise ValueError('Insufficient eligible new patients')
    frequency = Counter(f for r in candidates for f, present in r['features'].items() if present)
    covered, selected = Counter(), []
    while len(selected) < new_count:
        def score(row):
            return sum((4 if covered[f] == 0 else 1 / (1 + covered[f])) / math.sqrt(frequency[f])
                       for f, present in row['features'].items() if present)
        best = sorted(candidates, key=lambda r: (-score(r), r['patient_id']))[0]
        best = dict(best, selection_reason='Deterministic weighted heterogeneity: ' + ', '.join(f for f, p in best['features'].items() if p))
        selected.append(best); covered.update(f for f, present in best['features'].items() if present)
        candidates = [r for r in candidates if r['patient_id'] != best['patient_id']]
    refs = [dict(r, selection_reason='Existing physician-reviewed reference; no extraction or review changes') for r in rows if r['patient_id'] in reference_ids]
    if len(refs) != len(reference_ids):
        raise ValueError('Reference PDF patients missing')
    return sorted(refs + selected, key=lambda r: r['patient_id']), [r['patient_id'] for r in selected]


def create_selection(output, pdf_root=ROOT / 'pdf_db'):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    # Existing preparation cache is read-only and is only a screening aid.
    cached = {}
    for path in sorted((ROOT / 'data/debug').glob('full_cohort_*/bundles.json')):
        for bundle in read(path):
            cached[bundle['patient_id']] = bundle
    rows = screen(pdf_root, cached)
    selected, new_ids = choose(rows)
    atomic(output / 'source_screening.json', rows)
    atomic(output / 'selected_patients.json', dict(patient_ids=[r['patient_id'] for r in selected], new_patient_ids=new_ids,
        reference_patient_ids=list(PILOTS), selection_algorithm='WEIGHTED_FEATURE_COVERAGE_V1; numeric-ID tie break',
        source_screening_only=True, available_patient_count=len(rows), max_total_patients=20))
    fields=['patient_id','pdf_count','page_count','encounter_count','selection_reason','AF_signal','CAA_signal',
            'stroke_signal','ICH_signal','seizure_signal','neurosonology_signal','clinical_scale_signal','medication_complexity']
    csv_write(output / 'validation_cohort_selection.csv', [{f:r[f] for f in fields} for r in selected], fields)
    atomic(output / 'screening_coverage.json', {f:sum(r['features'][f] for r in selected) for f in FEATURES})
    return selected, new_ids
