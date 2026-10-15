"""Focused, conservative explicit patient facts. Unknowns never enter the patch."""
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import re
from .radiology_artifacts import digest

VERSION = 'case_v1'
DOMAINS = {
    'demographics': ('gender', 'date_of_birth', 'years_of_education', 'education_level', 'employment_status'),
    'lifestyle': ('smoking', 'pack_years', 'alcohol'),
    'genetics': ('apoe', 'genes'),
    'caa_classification': ('caa_criteria', 'caa_criteria_stage', 'caa_diagnosis_date'),
    'death_autopsy': ('death_date', 'death_cause', 'autopsy'),
}
FIELDS = tuple(f for fields in DOMAINS.values() for f in fields)
ENUMS = {
    'gender': ('woman', 'man', 'other', 'unspecified', 'unknown'),
    'education_level': ('none', 'elementary', 'high school', 'college', 'university', 'post-graduate', 'other', 'unknown'),
    'employment_status': ('active', 'retirement', 'other', 'unknown'),
    'smoking': ('active', 'former', 'unknown', 'never'),
    'alcohol': ('active', 'former', 'unknown', 'never'),
    'apoe': ('not done', 'pending', 'E2E2', 'E2E3', 'E2E4', 'E3E3', 'E3E4', 'E4E4'),
    'caa_criteria': ('Boston Criteria 1.5', 'Boston Criteria 2.0', 'Edinburgh Criteria', 'Overlapp', 'Unknown', 'Other'),
    'caa_criteria_stage': ('definite (autopsy)', 'probable with supp. pathology', 'probable', 'possible', 'not categorised'),
    'autopsy': ('irrelevant', 'not done', 'done', 'done with neuropathology', 'unknown or pending'),
}
DATE = r'(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b'
CUES = {
    'demographics': r'\b(?:geb\.|Geburtsdatum|date of birth|DOB|Geschlecht|gender|sex|Bildung|Schulbildung|education|Bildungsjahre|Schuljahre|berufstätig|pensioniert|Rentner|employment|education_level|employment_status)',
    'lifestyle': r'\b(?:Raucher|Nichtraucher|Ex-Raucher|Nikotin|smoking|smoked|geraucht|pack[- ]?years|PY|Alkohol|alcohol)',
    'genetics': r'\b(?:APOE|Genetik|genetisch\w*|genetic|molekular\w*|genes)',
    'caa_classification': r'\b(?:CAA|Amyloidangiopathie|Boston|Edinburgh)',
    'death_autopsy': r'\b(?:verstorben|Tod|Todes\w*|death|died|Autopsie|Obduktion|autopsy)',
}

@dataclass(frozen=True)
class CaseInput:
    domain: str
    source_document_id: int
    page: int
    section_id: str
    text: str

    def context(self):
        return asdict(self)


def select_inputs(bundle):
    result = []
    for document in bundle.documents:
        for section in document.sections:
            # Use exact original source lines, never a complete document as input.
            if section.page_start != section.page_end:
                # No exact page attribution is available for multi-page sections.
                continue
            for line in section.source_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                for domain, cue in CUES.items():
                    if re.search(cue, line, re.I):
                        if len(line) > 2000:
                            raise ValueError('Focused case passage exceeds budget')
                        result.append(CaseInput(domain, document.source_document_id,
                                                section.page_start, section.section_id, line))
    return result


def normalize_space(value):
    return ' '.join(value.split())


def iso_date(value):
    if '.' in value:
        day, month, year = map(int, value.split('.'))
        return date(year, month, day).isoformat()
    return date.fromisoformat(value).isoformat()


def validate_value(field, value):
    if field not in FIELDS or value is None:
        raise ValueError('Invalid patch field')
    if field in ENUMS and value not in ENUMS[field]:
        raise ValueError('Invalid enum')
    if field in ('years_of_education', 'pack_years'):
        if type(value) is not int or not 0 <= value <= 200:
            raise ValueError('Invalid numeric fact')
    elif field.endswith('_date') or field == 'date_of_birth':
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Invalid date')
    elif not isinstance(value, str) or not value.strip():
        raise ValueError('Invalid text fact')


def extract(inputs):
    candidates = {}
    def add(item, field, value, evidence):
        validate_value(field, value)
        if field not in DOMAINS[item.domain] or normalize_space(evidence) not in normalize_space(item.text):
            raise ValueError('Invalid evidence/domain')
        candidates.setdefault(field, []).append({'value': value, 'source_document_id': item.source_document_id,
            'page': item.page, 'section_id': item.section_id, 'evidence': evidence,
            'extraction_method': 'deterministic_explicit_phrase'})
    for item in inputs:
        text = item.text
        # Do not attribute relatives, hypothetical plans, questions or negated findings.
        if re.search(r'\b(?:Mutter|Vater|Bruder|Schwester|Tochter|Sohn|mother|father|Familienanamnese|family history|geplant|empfohlen|recommended|planned|kein|keine|nicht|no|not|nie|never)\b|\?', text, re.I):
            # Recognized explicit negative statuses are safe; other negations are omitted.
            if not re.fullmatch(r'\s*(?:APOE(?:[- ]Test)?\s*:?\s*(?:not done|nicht durchgeführt)|(?:Autopsie|Obduktion|autopsy)\s*:\s*not done|(?:smoking|alcohol|Alkohol(?:status|konsum)?)\s*:\s*(?:never|nie)|nie geraucht|never smoked)\s*[.!]?\s*', text, re.I):
                continue
        def rule(field, pattern, convert):
            for match in re.finditer(pattern, text, re.I):
                add(item, field, convert(match), match.group(0))
        if item.domain == 'demographics':
            rule('date_of_birth', r'\b(?:geb\.\s*:?|Geburtsdatum\s*:|date of birth\s*:|DOB\s*:)\s*' + DATE, lambda m: iso_date(m[1]))
            rule('gender', r'\b(?:Geschlecht|gender|sex)\s*:\s*(weiblich|männlich|female|male|woman|man|other|unspecified|unknown)\b',
                 lambda m: {'weiblich': 'woman', 'female': 'woman', 'männlich': 'man', 'male': 'man'}.get(m[1].lower(), m[1].lower()))
            rule('years_of_education', r'\b(?:Bildungsjahre|Schuljahre|years of education)\s*:\s*(\d+)\b(?![.,-]\d)', lambda m: int(m[1]))
            rule('years_of_education', r'(?<![\w.,-])(\d+)\s+(?:Jahre\s+(?:Schulbildung|Ausbildung)|years of education)\b', lambda m: int(m[1]))
            for field, label in [('education_level', r'education(?:_level| level)'), ('employment_status', r'employment(?:_status| status)')]:
                rule(field, rf'\b{label}\s*:\s*(' + '|'.join(map(re.escape, ENUMS[field])) + r')\b', lambda m: m[1].lower())
            rule('employment_status', r'\b(?:pensioniert|Rentner(?:in)?)\b', lambda m: 'retirement')
            rule('employment_status', r'\bberufstätig\b', lambda m: 'active')
        elif item.domain == 'lifestyle':
            rule('smoking', r'\b(?:smoking|Raucherstatus)\s*:\s*(active|former|unknown|never)\b', lambda m: m[1].lower())
            rule('smoking', r'^(?:Raucherstatus\s*:\s*)?(?:aktiver\s+Raucher|Raucher)\b', lambda m: 'active')
            rule('smoking', r'\b(?:Ex[- ]Raucher|Nikotinabusus\s+früher)\b', lambda m: 'former')
            rule('smoking', r'\b(?:Nichtraucher|nie\s+geraucht|never smoked)\b', lambda m: 'never')
            rule('pack_years', r'(?<![\w.,-])(\d+)\s*(?:pack[- ]?years|PY)\b', lambda m: int(m[1]))
            rule('pack_years', r'\bpack[- ]?years\s*:\s*(\d+)\b(?![.,-]\d)', lambda m: int(m[1]))
            rule('alcohol', r'\b(?:Alkohol|alcohol)(?:status|konsum)?\s*:\s*(active|former|unknown|never|aktiv|früher|nie|unbekannt)\b',
                 lambda m: {'aktiv': 'active', 'früher': 'former', 'nie': 'never', 'unbekannt': 'unknown'}.get(m[1].lower(), m[1].lower()))
        elif item.domain == 'genetics':
            rule('apoe', r'\bAPOE(?:[- ]Genotyp)?\s*[:=]?\s*[Eε]?([234])\s*[/ -]?\s*[Eε]([234])\b', lambda m: 'E' + min(m[1], m[2]) + 'E' + max(m[1], m[2]))
            rule('apoe', r'\bAPOE(?:[- ](?:Test|Genotyp))?\s*:?\s*(pending|ausstehend|not done|nicht durchgeführt)\b', lambda m: 'pending' if m[1].lower() in ('pending', 'ausstehend') else 'not done')
            rule('genes', r'\b(?:genes|Genetik|genetischer Befund)\s*:\s*(.+)', lambda m: m[1].strip())
        elif item.domain == 'caa_classification':
            formal = re.search(r'\b(?:CAA|Amyloidangiopathie)\b', text, re.I)
            if formal:
                rule('caa_criteria', r'\bBoston(?:\s+(?:Criteria|Kriterien))?\s*(1\.5|2\.0)\b', lambda m: 'Boston Criteria ' + m[1])
                rule('caa_criteria', r'\bEdinburgh(?:\s+(?:Criteria|Kriterien))\b', lambda m: 'Edinburgh Criteria')
                rule('caa_criteria_stage', r'\b(probable|possible|wahrscheinliche|mögliche)\s+(?:CAA|Amyloidangiopathie)\b', lambda m: 'probable' if m[1].lower() in ('probable', 'wahrscheinliche') else 'possible')
                rule('caa_criteria_stage', r'\bdefinite\s+CAA\s*\(autopsy\)', lambda m: 'definite (autopsy)')
                rule('caa_criteria_stage', r'\bprobable\s+CAA\s+with\s+supporting\s+pathology\b', lambda m: 'probable with supp. pathology')
                rule('caa_diagnosis_date', r'\b(?:CAA[- ]Diagnose(?:datum)?|CAA diagnosis date)\s*:\s*' + DATE, lambda m: iso_date(m[1]))
        elif item.domain == 'death_autopsy':
            rule('death_date', r'\b(?:verstorben(?:\s+am)?|Todesdatum\s*:|death date\s*:|died(?:\s+on)?)\s*' + DATE, lambda m: iso_date(m[1]))
            rule('death_cause', r'\b(?:Todesursache|cause of death|death cause)\s*:\s*(.+)', lambda m: m[1].strip())
            rule('autopsy', r'\b(?:Autopsie|Obduktion|autopsy)\s*:\s*(done with neuropathology|done|not done|unknown or pending|irrelevant)\b', lambda m: m[1].lower())
            rule('autopsy', r'\b(?:Autopsie|Obduktion)\s+(?:durchgeführt|erfolgt)(?:\s+mit\s+Neuropathologie)?\b', lambda m: 'done with neuropathology' if 'neuropathologie' in m[0].lower() else 'done')
    patch, provenance, conflicts = {}, {}, []
    for field, evidence in candidates.items():
        values = {e['value'] for e in evidence}
        if len(values) != 1:
            conflicts.append({'field': field, 'reason': 'SOURCE_CONFLICT', 'values': sorted(values)})
        else:
            patch[field] = evidence[0]['value']
            provenance[field] = evidence
    return {'update_fields': patch, 'provenance': provenance, 'source_conflicts': conflicts,
            **{d: {'update_fields': {f: patch[f] for f in fields if f in patch},
                   'validation_status': 'REVIEW_REQUIRED' if any(c['field'] in fields for c in conflicts) else 'VERIFIED'}
               for d, fields in DOMAINS.items()}}


def conflict_check(current, patch):
    updates, unchanged, conflicts = {}, [], []
    for field, value in patch.items():
        validate_value(field, value)
        old = current[field]
        if old == value:
            unchanged.append(field)
        elif old is None:
            updates[field] = value
        else:
            conflicts.append({'field': field, 'current': old, 'proposed': value,
                              'reason': 'FIELD_CONFLICT'})
    return updates, unchanged, conflicts


def suspicious_defaults(current, entry_date=None):
    return {f: v for f, v in current.items() if
            (f in ('years_of_education', 'pack_years') and v == 0) or
            (f == 'autopsy' and v == 'irrelevant') or
            (f in ('date_of_birth', 'death_date', 'caa_diagnosis_date') and v is not None and v == entry_date)}


def fingerprint(bundle, inputs):
    return digest({'bundle': bundle.to_dict(), 'inputs': [i.context() for i in inputs],
                   'rule_version': VERSION, 'model': 'deterministic', 'model_version': VERSION,
                   'config': {'unknown': 'omit', 'placeholder_policy': 'review', 'granite': False},
                   'implementation': Path(__file__).read_text(encoding='utf-8')})
