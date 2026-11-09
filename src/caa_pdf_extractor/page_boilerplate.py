"""Geometry-gated page furniture recognition, configured separately from sections."""
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

from .section_dictionary import CONFIG


def load_boilerplate(path=None):
    data = yaml.safe_load(Path(path or CONFIG/'page_boilerplate.yaml').read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('Unsupported page boilerplate dictionary')
    for rule in data['patterns']:
        re.compile(rule['regex'])
    return data


def normalize_boilerplate(text):
    value = unicodedata.normalize('NFKC', text).replace('\u00ad','')
    value = re.sub(r'[‐‑‒–—−]', '-', value)
    return re.sub(r'\s+', ' ', value).strip()


def classify_boilerplate(text, config=None):
    """Recognition is a candidate; suppression additionally requires geometry."""
    config = config if config is not None else load_boilerplate()
    value = normalize_boilerplate(text)
    for rule in config['patterns']:
        match = re.fullmatch(rule['regex'], value, re.I)
        if match:
            page = match.groupdict().get('page_number')
            return dict(pattern_id=rule['pattern_id'], strong=rule['strong'],
                patient_identity_footer=rule.get('patient_identity_footer', False),
                page_number=int(page) if page else None)
    return None


def signature(text, config=None):
    if any((classify_boilerplate(line, config) or {}).get('patient_identity_footer') for line in text.splitlines()):
        return 'patient-identity-page-number'
    return re.sub(r'\W+', '', re.sub(r'\d+', '#', normalize_boilerplate(text).casefold()))


def clean_pages(pages, config=None):
    """Remove a block only if EVERY nonempty line is known, nonclinical furniture."""
    config = config if config is not None else load_boilerplate()
    protected = re.compile(config['clinical_protection_regex'], re.I)
    def margin(page, block):
        _, y0, _, y1 = block['bbox']
        return ('HEADER' if y1 <= page['height']*config['header_max_fraction'] else
                'FOOTER' if y0 >= page['height']*config['footer_min_fraction'] else None)
    seen = defaultdict(set)
    for page in pages:
        for block in page['blocks']:
            if margin(page, block):
                seen[signature(block['text'], config)].add(page['page'])
    cleaned = []; exclusions = []
    for page in pages:
        kept = []; spans = []; offset = 0
        for block in page['blocks']:
            text = block['text']; sig = signature(text, config)
            recognized = [classify_boilerplate(line, config) for line in text.splitlines() if line.strip()]
            repeated = len(seen[sig]) >= config['minimum_repeated_pages']
            known = bool(recognized) and all(recognized)
            strong = known and all(r['strong'] for r in recognized)
            location = margin(page, block)
            if location and known and (repeated or strong) and not protected.search(text):
                exclusions.append(dict(excluded_block_type=location,
                    exclusion_reason='REPEATED_MARGIN_BOILERPLATE' if repeated else 'MARGIN_STRONG_BOILERPLATE',
                    page=page['page'], bbox=block['bbox'], normalized_signature=sig,
                    repeated_on_pages=sorted(seen[sig]), text=text, block_id=block['block_id'],
                    matched_patterns=[r['pattern_id'] for r in recognized],
                    patient_identity_footer=any(r['patient_identity_footer'] for r in recognized),
                    page_number=next((r['page_number'] for r in recognized if r['page_number'] is not None), None)))
            else:
                kept.append(text); spans.append(dict(start=offset, end=offset+len(text), bbox=block['bbox'], block_id=block['block_id']))
                offset += len(text)+1
        cleaned.append(dict(page=page['page'], text='\n'.join(kept), block_spans=spans))
    return cleaned, exclusions
