"""EEG 1.2 adapter to the physician-maintained shared section ontology."""
from ..section_dictionary import (DATE_SUFFIX, LINES, classify_heading as classify,
    heading_matches, load_dictionary, normalize_heading)


def _legacy(heading):
    section = heading['canonical_section']
    # Preserve pre-dictionary metadata; the tree stores canonical ontology IDs.
    section = {'PSYCHIATRY_CONSULT':'PSYCHIATRIC_CONSULT',
               'NEUROLOGICAL_EXAM':'NEUROLOGICAL_STATUS'}.get(section, section)
    if heading['section_family'] in ('ECG','MRI','CT'):
        section = heading['section_family']
    return dict(heading, section_type=section)


def classify_heading(text):
    found = classify(text)
    return _legacy(found) if found else None


class HeadingSpan:
    """Minimal re.Match-compatible span for existing EEG callers."""
    def __init__(self, text, start, end):
        self.text, self.lo, self.hi = text, start, end
    def start(self): return self.lo
    def end(self): return self.hi
    def __getitem__(self, key):
        if key != 0: raise IndexError(key)
        return self.text[self.lo:self.hi]


def section_headings(text, structured=False, dictionary=None):
    data = dictionary if dictionary is not None else load_dictionary()
    for heading in heading_matches(text, data):
        if heading['canonical_section'] == 'UNKNOWN_SECTION':
            continue
        if structured and heading['heading_core'] in ('EKG','ECG') and heading['source_heading'].strip().endswith(':') and not DATE_SUFFIX.search(heading['source_heading']):
            continue
        yield HeadingSpan(text, heading['start'], heading['end']), _legacy(heading)
