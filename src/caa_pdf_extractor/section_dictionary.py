"""Config-driven heading recognition; offsets always refer to unchanged source text.

Only EEG 1.2 and TFNE opt into this service. Historical parsers remain frozen.
"""
import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / 'config'
DATE_SUFFIX = re.compile(r'\s*(?:vom\s+)?\(?\s*(?:\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*(?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|\.{3}|…)\s*\)?\s*:?\s*$', re.I)
LINES = re.compile(r'(?m)^[^\S\n]*[^\s\n][^\n]*')


@lru_cache(maxsize=8192)
def normalize_heading(text, strip_date=True, case_sensitive=False):
    value = unicodedata.normalize('NFKC', text).replace('\u00ad', '')
    value = re.sub(r'[‐‑‒–—−]', '-', value).replace('“', '"').replace('”', '"').replace('’', "'")
    value = re.sub(r'\s+', ' ', value).strip().rstrip(':').strip()
    if strip_date:
        value = DATE_SUFFIX.sub('', value).strip().rstrip(':').strip()
    value = re.sub(r'\s*([:/(),+.-])\s*', r'\1', value)
    value = re.sub(r'[-\s]+', ' ', value).strip()
    return value if case_sensitive else value.upper()


def _compact(text):
    return re.sub(r'\s+', '', text)


def load_dictionary(path=None):
    """Read afresh so physician edits take effect on the next document, no restart."""
    path = Path(path or CONFIG / 'section_dictionary.yaml')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    required = {'canonical_section', 'section_family', 'variants', 'match_type',
                'boundary_strength', 'module_actions', 'parent_context',
                'date_suffix_allowed', 'continuation_allowed', 'case_sensitive', 'active', 'notes'}
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported section dictionary')
    seen = set()
    for entry in data['sections']:
        if required - entry.keys():
            raise ValueError('Incomplete section rule: ' + str(entry.get('canonical_section')))
        if entry['match_type'] not in {'EXACT_NORMALIZED', 'PREFIX', 'REGEX', 'CONTEXTUAL'}:
            raise ValueError('Invalid heading match type')
        if entry['boundary_strength'] not in {'HARD', 'SOFT', 'CONTEXTUAL'}:
            raise ValueError('Invalid boundary strength')
        if not isinstance(entry['variants'], list) or not entry['variants']:
            raise ValueError('A section requires variants')
        for variant in entry['variants']:
            key = (entry['canonical_section'], normalize_heading(variant))
            if key in seen:
                raise ValueError('Duplicate canonical variant: ' + variant)
            seen.add(key)
            if entry['match_type'] == 'REGEX':
                re.compile(variant)
    return data


def classify_heading(text, dictionary=None, parent_context=None):
    data = dictionary if dictionary is not None else load_dictionary()
    if re.match(data.get('non_heading_prefix_regex', r'(?!)'), text):
        return None
    # Prefix rules recognize a line heading, never an incidental prose mention.
    prose = data.get('prose_guard_regex')
    matches = []
    for entry in data['sections']:
        if not entry['active']:
            continue
        case = entry['case_sensitive']
        core = normalize_heading(text, entry['date_suffix_allowed'], case)
        for variant in entry['variants']:
            kind = entry['match_type']
            label = normalize_heading(variant, entry['date_suffix_allowed'], case)
            if kind == 'REGEX':
                hit = bool(re.fullmatch(variant, core, 0 if case else re.I))
                if entry.get('require_date_for_continuation') and ' ' in core and not DATE_SUFFIX.search(text.strip().rstrip(':')):
                    hit = False
            elif kind == 'PREFIX':
                hit = bool(re.match(re.escape(label) + r'(?=$|[\s:,(.])', core))
                # In "Röntgen vom (date)" the date suffix consumes "vom",
                # which also belongs to the physician's declared prefix.
                if not hit and label.endswith(' VOM') and DATE_SUFFIX.search(text):
                    hit = core == label[:-4]
                if not hit:  # OCR letter spacing, but not arbitrary word prefixes.
                    hit = _compact(core) == _compact(label)
            else:
                hit = _compact(core) == _compact(label)
            if not hit:
                continue
            if kind in ('PREFIX', 'REGEX') and prose and re.search(prose, text, re.I):
                continue
            contexts = entry['parent_context']
            if contexts and parent_context and parent_context not in contexts:
                continue
            matches.append((len(label), dict(entry, source_heading=text, heading_core=core,
                match_rule=kind + ':' + variant, confidence=.85 if entry['boundary_strength'] == 'CONTEXTUAL' else .99)))
    return max(matches, key=lambda pair: pair[0])[1] if matches else None


def heading_matches(text, dictionary=None, probable_headings=()):
    """Yield exact source spans; join broken heading cores over up to three lines.

    Continuation lines are joined only if separately incomplete or an indented
    modality/date continuation. Ordinary clinical paragraphs stay body text.
    """
    data = dictionary if dictionary is not None else load_dictionary()
    lines = list(LINES.finditer(text)); i = 0
    while i < len(lines):
        line = lines[i]; end_i = i
        found = classify_heading(line[0], data)
        if not found:
            for j in range(i + 1, min(i + 3, len(lines))):
                joined = text[line.start():lines[j].end()]
                candidate = classify_heading(joined, data)
                if candidate:
                    found = candidate; end_i = j; break
        if found and found['continuation_allowed']:
            for j in range(end_i + 1, min(i + 4, len(lines))):
                next_text = lines[j][0].strip()
                # Long enumerations may wrap immediately after a colon/comma.
                previous = text[line.start():lines[j-1].end()].rstrip()
                continuation = ((found['section_family'] in ('MRI', 'ANGIOGRAPHY') and previous.endswith((':', ','))) or
                    bool(re.match(data.get('continuation_line_regex', r'(?!)'), next_text, re.I)))
                if not continuation or classify_heading(lines[j][0], data):
                    break
                end_i = j
        if found:
            source = text[line.start():lines[end_i].end()]
            found = dict(found, source_heading=source)
            yield dict(start=line.start(), end=lines[end_i].end(), **found)
        elif line[0].strip() in probable_headings or _probable_heading(line[0], data):
            yield dict(start=line.start(), end=line.end(), source_heading=line[0],
                canonical_section='UNKNOWN_SECTION', section_family='UNKNOWN_SECTION',
                confidence=.5, match_rule='PROBABLE_HEADING', boundary_strength='SOFT',
                module_actions={}, parent_context=[])
        i = end_i + 1


def _probable_heading(text, data):
    value = text.strip()
    return (len(value) <= 120 and bool(re.fullmatch(data['unknown_heading_regex'], value))
            and not re.search(data.get('prose_guard_regex', r'(?!)'), value, re.I))


def eeg_action(heading, body_before='', body_after='', structured=False):
    """Resolve contextual peer sections without truncating EEG interpretations."""
    action = heading['module_actions'].get('EEG', 'KEEP')
    core = heading.get('heading_core', '')
    if structured and core in ('EKG', 'ECG') and not DATE_SUFFIX.search(heading['source_heading'].strip().rstrip(':')):
        return 'KEEP'
    if action != 'CONTEXTUAL':
        return action
    section = heading['canonical_section']
    if section == 'ASSESSMENT':
        if len(body_before.strip()) < 3 or re.search(r'dieses\s+Abschnittes|insgesamt', heading['source_heading'], re.I):
            return 'KEEP'
        if re.match(r'\s*(?:Dieses\s+EEG|(?:Leicht|Mäßig|Abnorm|Normal|Gering)\w*\s+.*?EEG)', body_after, re.I):
            return 'KEEP'
        return 'STOP' if re.search(r'Interpretation\s*:', body_before, re.I) or re.match(r'\s*1\.', body_after) else 'KEEP'
    if section == 'STATUS':
        return 'STOP' if re.match(r'\s*(?:Neurologisch|Wach|Bewusst|Allgemeinzustand|Kreislauf|1\.)', body_after, re.I) else 'KEEP'
    # These document-level sections are peers once an EEG body has started.
    return 'STOP' if body_before.strip() else 'KEEP'


def build_section_tree(pages, document_id='', dictionary=None):
    """A shallow ordered tree with page and global offsets in CLEANED_PAGE_TEXT."""
    data = dictionary if dictionary is not None else load_dictionary()
    text = ''; ranges = []; probable = []
    for index, page in enumerate(pages, 1):
        start = len(text); text += page['text']
        ranges.append((start, len(text), page.get('page', page.get('page_start', index))))
        probable.extend(page.get('headings', []))
        text += '\n'
    def page_at(offset):
        return next((p for a, b, p in reversed(ranges) if a <= offset), 1)
    matches = list(heading_matches(text, data, probable)); sections = []
    for i, match in enumerate(matches):
        end = matches[i+1]['start'] if i+1 < len(matches) else len(text)
        start = match['start']
        sid = hashlib.sha256(f'{document_id}:{start}:{match["canonical_section"]}'.encode()).hexdigest()[:16]
        sections.append(dict(section_id=f'{document_id}:SECTION:{sid}',
            canonical_section=match['canonical_section'], section_family=match['section_family'],
            source_heading=match['source_heading'], start_page=page_at(start), end_page=page_at(max(start, end-1)),
            start_offset=start, end_offset=end, body_start_offset=match['end'],
            confidence=match['confidence'], heading_confidence=match['confidence'], match_rule=match['match_rule'],
            boundary_strength=match['boundary_strength'], module_actions=match['module_actions'],
            parent_section_id=None, source_layer='CLEANED_PAGE_TEXT'))
    return sections
