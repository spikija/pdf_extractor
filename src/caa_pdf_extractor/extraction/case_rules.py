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
    'demographics': r'\b(?:geb\.|Geburtsdatum|geboren\s+am|date of birth|DOB|Geschlecht|gender|sex|Bildung|Schulbildung|education|Bildungsjahre|Schuljahre|berufstätig|pensioniert|Rentner|employment|education_level|employment_status)',
    'lifestyle': r'\b(?:Raucher|Nichtraucher|Nieraucher|Ex-Raucher|Nikotin|smoking|smoked|former smoker|current smoker|raucht|geraucht|pack[- ]?years|p/y|PY|Alkohol|alcohol|C2\s*:)',
    'genetics': r'\b(?:APOE|Genetik|genetisch\w*|genetic|molekular\w*|genes)',
    'caa_classification': r'\b(?:CAA|Amyloidangiopathie|Boston|Edinburgh)',
    'death_autopsy': r'\b(?:Exitus\s+letalis|verstorben|verstarb|Tod|Todes\w*|death|died|Autopsie|Obduktion|autopsy)',
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
            lines = section.source_text.splitlines()
            for index, original_line in enumerate(lines):
                line = original_line.strip()
                if not line:
                    continue
                for domain, cue in CUES.items():
                    if re.search(cue, line, re.I):
                        passage = line
                        if domain=='death_autopsy' and re.search(r'\bam\s*$',line,re.I) and index+1<len(lines):
                            passage+='\n'+lines[index+1]
                        if domain == 'caa_classification':
                            for following in lines[index+1:index+4]:
                                if not following.strip() or re.search(r'[.!?]["\s]*$',passage):
                                    break
                                passage += '\n' + following
                        if len(passage) > 2000:
                            raise ValueError('Focused case passage exceeds budget')
                        result.append(CaseInput(domain, document.source_document_id,
                                                section.page_start, section.section_id, passage))
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


def stage_classification(text):
    stages = set(m.lower() for m in re.findall(r'\b(?:probable|possible|wahrscheinlich\w*|möglich\w*)\b',text,re.I))
    if len(stages)>1:
        return 'MULTIPLE_STAGES'
    if re.search(r'\b(?:falls|wenn|je nach|bedingt|conditional|if)\b',text,re.I):
        return 'CONDITIONAL_STAGE'
    if re.search(r'\b(?:mindestens|zumindest|at least|unklar|fraglich)\b|\?',text,re.I):
        return 'AMBIGUOUS'
    return 'EXPLICIT_UNCONDITIONAL_STAGE' if stages else 'AMBIGUOUS'


def extract(inputs):
    candidates = {}
    stage_assessments, alcohol_assessments, demographic_assessments = [], [], []
    def add(item, field, value, evidence):
        validate_value(field, value)
        if field not in DOMAINS[item.domain] or normalize_space(evidence) not in normalize_space(item.text):
            raise ValueError('Invalid evidence/domain')
        candidates.setdefault(field, []).append({'value': value, 'source_document_id': item.source_document_id,
            'page': item.page, 'section_id': item.section_id, 'evidence': evidence,
            'extraction_method': 'deterministic_explicit_phrase',
            **({'approximate': bool(re.search(r'\b(?:ca\.|circa|approximately|about)\s*'+re.escape(str(value)),item.text,re.I))} if field=='pack_years' else {})})
    for item in inputs:
        text = item.text
        if item.domain=='death_autopsy' and re.search(r'\b(?:falls|wenn|fraglich|Verdacht|vermutlich|vielleicht|möglicherweise|droht|befürchtet|erwartet|prognostiziert|geplant|empfohlen)\b|\?',text,re.I):
            demographic_assessments.append({**item.context(),'field':'death_autopsy','classification':'CONDITIONAL_AUDIT_ONLY'})
            continue
        if item.domain=='death_autopsy':
            from .death_rules import explicit_death_dates
            for candidate in explicit_death_dates(text):add(item,'death_date',candidate['value'],candidate['evidence'])
        if item.domain == 'lifestyle' and re.search(r'Alkohol|alcohol|\bC2\s*:',text,re.I):
            # A present denial does not establish lifetime abstinence or an owned
            # transition from another source's consumption statement.
            alcohol_assessments.append({**item.context(), 'temporally_unresolved':bool(re.search(r'vermutlich|vielleicht|möglicherweise|possibly|unclear|\bAlkohol\s*:\s*(?:aktuell\s+)?gar\s*nicht\b',text,re.I))})
        if item.domain == 'lifestyle':
            for m in re.finditer(r'(?<![\w.,])(\d+)\s*[-–]\s*(\d+)\s*(?:p/y|PY|pack[- ]?years)\b',text,re.I):
                demographic_assessments.append({**item.context(),'field':'pack_years','classification':'EXPLICIT_RANGE_AUDIT_ONLY','range':[int(m[1]),int(m[2])],'evidence':m[0]})
            if re.search(r'\b(?:Nichtraucher|Nikotinkarenz)\b|\bNikotin\s*:\s*(?:negiert|negativ|neg\.?|nein)\b',text,re.I):
                demographic_assessments.append({**item.context(),'field':'smoking','classification':'NO_LIFETIME_STATUS_FROM_CURRENT_DENIAL_OR_ADVICE'})
            if re.search(r'\b(?:falls|wenn|fraglich|vermutlich|vielleicht|möglicherweise|hypothetisch|geplant|empfohlen)\b|\?',text,re.I):
                demographic_assessments.append({**item.context(),'field':'lifestyle','classification':'CONDITIONAL_AUDIT_ONLY'})
                continue
        # Do not attribute relatives, hypothetical plans, questions or negated findings.
        if re.search(r'\b(?:Mutter|Vater|Bruder|Schwester|Tochter|Sohn|mother|father|Familienanamnese|family history|geplant|empfohlen|recommended|planned|kein|keine|nicht|no|not|nie|never)\b|\?', text, re.I):
            # Recognized explicit negative statuses are safe; other negations are omitted.
            if not re.fullmatch(r'\s*(?:APOE(?:[- ]Test)?\s*:?\s*(?:not done|nicht durchgeführt)|(?:Autopsie|Obduktion|autopsy)\s*:\s*not done|(?:smoking|alcohol|Alkohol(?:status|konsum)?)\s*:\s*(?:never|nie)|Nikotin\s*:\s*nicht\s+bekannt|nie geraucht|never smoked)\s*[.!]?\s*', text, re.I):
                continue
        def rule(field, pattern, convert):
            for match in re.finditer(pattern, text, re.I):
                if field=='pack_years' and (re.search(r'\d\s*[-–]\s*$',text[:match.start()]) or re.match(r'\s*[-–]\s*\d',text[match.end():])):
                    continue
                add(item, field, convert(match), match.group(0))
        if item.domain == 'demographics':
            rule('date_of_birth', r'\b(?:geb\.\s*:?|geboren\s+am|Geburtsdatum\s*:|date of birth\s*:|DOB\s*:)\s*' + DATE, lambda m: iso_date(m[1]))
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
            rule('smoking', r'\b(?:Nikotin(?:abusus)?\s+(?:ex\b|bis\s+\d{4}\b)|former smoker)\b', lambda m: 'former')
            rule('smoking', r'\b(?:raucht\s+aktuell|current smoker)\b', lambda m: 'active')
            rule('smoking', r'\b(?:Nieraucher|nie\s+geraucht|never smoked)\b', lambda m: 'never')
            rule('smoking', r'^\s*Nikotin\s*:\s*nicht\s+bekannt\b',lambda m:'unknown')
            rule('smoking', r'\bNikotinabusus,\s*faktisch\s+negativ\s*\(zuletzt\s+vor\s+(?:etwa\s+)?\d+\s+Jahren\)',lambda m:'former')
            rule('pack_years', r'(?<![\w.,-])(\d+)\s*(?:pack[- ]?years|PY)\b', lambda m: int(m[1]))
            rule('pack_years', r'(?<![\w.,-])(\d+)\s*p/y\b', lambda m: int(m[1]))
            rule('pack_years', r'\bpack[- ]?years\s*:\s*(\d+)\b(?![.,-]\d)', lambda m: int(m[1]))
            rule('alcohol', r'\b(?:Alkohol|alcohol)(?:status|konsum)?\s*:\s*(active|former|unknown|never|aktiv|früher|nie|unbekannt)\b',
                 lambda m: {'aktiv': 'active', 'früher': 'former', 'nie': 'never', 'unbekannt': 'unknown'}.get(m[1].lower(), m[1].lower()))
            former = re.search(r'\b(?:früher|ehemalig\w*|former)\b.*(?:Alkohol|alcohol).*(?:jetzt|aktuell|nun|currently)\s+abstinent',text,re.I)
            if former:
                add(item,'alcohol','former',former.group())
            elif not re.search(r'vermutlich|vielleicht|möglicherweise|possibly',text,re.I):
                rule('alcohol',r'\b(?:Alkohol(?:konsum)?|alcohol)\s*(?:wird\s+)?(?:verneint|abstinent|denied)\b',lambda m:'never')
                rule('alcohol',r'\bAlkohol\s*:\s*nein\b',lambda m:'never')
                if not re.search(r'\b(?:früher|damals|ehemalig\w*|former|previously)\b',text,re.I):
                    rule('alcohol',r'\bC2\s*:\s*selten\b',lambda m:'active')
                qualifier=r'(?:regelmäßig\w*|gelegentlich\w*|mäßig\w*|leicht\s+(?:erhöh\w*|übermäßig\w*)|etwas\s+zu\s+viel|regel-\s*und\s+leicht\s+übermäßig\w*)'
                rule('alcohol',rf'\b{qualifier}\s+(?:Alkohol(?:konsum)?|alcohol)\b',lambda m:'active')
                rule('alcohol',rf'\b(?:Alkohol(?:konsum)?|alcohol)\s*:?\s*{qualifier}\b',lambda m:'active')
        elif item.domain == 'genetics':
            rule('apoe', r'\bAPOE(?:[- ]Genotyp)?\s*[:=]?\s*[Eε]?([234])\s*[/ -]?\s*[Eε]([234])\b', lambda m: 'E' + min(m[1], m[2]) + 'E' + max(m[1], m[2]))
            rule('apoe', r'\bAPOE(?:[- ](?:Test|Genotyp))?\s*:?\s*(pending|ausstehend|not done|nicht durchgeführt)\b', lambda m: 'pending' if m[1].lower() in ('pending', 'ausstehend') else 'not done')
            rule('genes', r'\b(?:genes|Genetik|genetischer Befund)\s*:\s*(.+)', lambda m: m[1].strip())
        elif item.domain == 'caa_classification':
            formal = re.search(r'\b(?:CAA|Amyloidangiopathie)\b', text, re.I)
            if formal:
                rule('caa_criteria', r'\bBoston(?:[-\s]+(?:Criteria|Kriterien))?\s*(1\.5|2\.0)\b', lambda m: 'Boston Criteria ' + m[1])
                rule('caa_criteria', r'\bEdinburgh(?:\s+(?:Criteria|Kriterien))\b', lambda m: 'Edinburgh Criteria')
                stage = stage_classification(text)
                stage_assessments.append({**item.context(),'classification':stage})
                if stage == 'EXPLICIT_UNCONDITIONAL_STAGE':
                    rule('caa_criteria_stage', r'\b(probable|possible|wahrscheinliche|mögliche)\s+(?:CAA|Amyloidangiopathie)\b', lambda m: 'probable' if m[1].lower() in ('probable', 'wahrscheinliche') else 'possible')
                rule('caa_criteria_stage', r'\bdefinite\s+CAA\s*\(autopsy\)', lambda m: 'definite (autopsy)')
                rule('caa_criteria_stage', r'\bprobable\s+CAA\s+with\s+supporting\s+pathology\b', lambda m: 'probable with supp. pathology')
                rule('caa_diagnosis_date', r'\b(?:CAA[- ]Diagnose(?:datum)?|CAA diagnosis date)\s*:\s*' + DATE, lambda m: iso_date(m[1]))
        elif item.domain == 'death_autopsy':
            rule('death_cause', r'\b(?:Todesursache|cause of death|death cause)\s*:\s*(.+)', lambda m: m[1].strip())
            rule('death_cause',r'\bExitus\s+letalis\s+bei\s+(zentralem\s+Regulationsversagen)\b',lambda m:m[1])
            rule('autopsy', r'\b(?:Autopsie|Obduktion|autopsy)\s*:\s*(done with neuropathology|done|not done|unknown or pending|irrelevant)\b', lambda m: m[1].lower())
            rule('autopsy', r'\b(?:Autopsie|Obduktion)\s+(?:durchgeführt|erfolgt)(?:\s+mit\s+Neuropathologie)?\b', lambda m: 'done with neuropathology' if 'neuropathologie' in m[0].lower() else 'done')
    patch, provenance, conflicts = {}, {}, []
    for field, evidence in candidates.items():
        values = {e['value'] for e in evidence}
        if field=='pack_years' and any(not a['range'][0]<=v<=a['range'][1] for a in demographic_assessments if a.get('field')=='pack_years' and 'range' in a for v in values):
            conflicts.append({'field':field,'reason':'SOURCE_RANGE_CONFLICT','values':sorted(values)})
            continue
        if field=='alcohol' and (len(values)>1 or any(a['temporally_unresolved'] for a in alcohol_assessments)):
            alcohol_assessments.append({'disposition':'OMITTED_UNRESOLVED_OR_CONTRADICTORY','candidates':evidence})
            continue
        if len(values) != 1:
            conflicts.append({'field': field, 'reason': 'SOURCE_CONFLICT', 'values': sorted(values)})
        else:
            patch[field] = evidence[0]['value']
            provenance[field] = evidence
    return {'update_fields': patch, 'provenance': provenance, 'source_conflicts': conflicts,
            'stage_assessments':stage_assessments,'alcohol_assessments':alcohol_assessments,
            'demographic_assessments':demographic_assessments,
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
                   'implementation': Path(__file__).read_text(encoding='utf-8'),
                   'death_implementation':Path(__file__).with_name('death_rules.py').read_text(encoding='utf-8')})
