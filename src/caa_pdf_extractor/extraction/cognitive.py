"""Deterministic cognitive source mentions and conservative longitudinal identity.

No encounter/history/radiology dependency, clinical writer, or default model call.
Every alias occurrence is audited, including mentions with no attributable score.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

VERSION = 'cognitive_mentions_v1_1'
ALIAS = re.compile(r'\b(?:MMSE|MMST|Mini[\s\-‐‑–]*Mental(?:[\s\-‐‑–]*(?:Status|State))?(?:[\s\-‐‑–]*(?:Test|Examination))?|MoCA)\b', re.I)
DAY = re.compile(r'(?<![\d./])(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\d)')
MONTHS = {name.casefold(): number for number, names in enumerate((
    ('Januar','Jänner','Jaenner'), ('Februar',), ('März','Maerz'), ('April',), ('Mai',), ('Juni',),
    ('Juli',), ('August',), ('September',), ('Oktober',), ('November',), ('Dezember',)), 1) for name in names}
MONTH = re.compile(r'\b(?P<name>' + '|'.join(MONTHS) + r')\s+(?P<year>(?:19|20)\d{2})\b|(?<![\d./])(?P<month>0?[1-9]|1[0-2])[/\.](?P<numeric_year>(?:19|20)\d{2})(?![\d./])', re.I)
YEAR = re.compile(r'(?<![\d./])(?:19|20)\d{2}(?![\d./])')
NUMBER = re.compile(r'(?<![\w\d])(?P<score>-?\d+(?:[.,]\d+)?)(?:\s*(?:/|von)\s*(?P<maximum>\d+))?(?:\s*(?P<unit>Punkte[n]?|Pkt\.?|P\.?))?(?!\w)', re.I)
CURRENT = re.compile(r'\b(?:aktuell|derzeit|heute|nun|jetzt|bei (?:der )?Aufnahme)\b', re.I)
PAST = re.compile(r'\b(?:historisch|damals|früher|zuvor|vormals|letztmalig|Vorbefund|Voruntersuchung|Vorjahr|letzten (?:Aufenthalt|Untersuchung))\b', re.I)
EXTERNAL = re.compile(r'\b(?:extern\w*|auswärtig\w*)\b|Vorbefund\s+(?:aus|von)', re.I)
OTHER_TEST = re.compile(r'\b(?:Uhrentest|Uhren[- ]?Test|Clock|DemTect|CERAD|NIHSS|Barthel|HADS|GDS|TMT)\b', re.I)
NEURO_HEADER = re.compile(r'^\s*Neuropsycholog\w*\s+(?:Befund|Untersuchung|Testung|Begutachtung|Konsil)\s*[:(]?\s*(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\s*\)?\s*:?[ \t]*$', re.I)
PEER_HEADER = re.compile(r'^\s*(?:Anamnese|Diagnosen|Zusammenfassung|Neurologischer\s+Befund|Internistisch\w*\s+(?:Befund|Konsil)|EEG|EKG|Labor|Kernspintomogra\w*|Computertomogra\w*|c?MRT\b|PET[- ]?CT|Medikation|Mit freundlichen)', re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def unknown_date():
    return {'assessment_date': None, 'assessment_year': None, 'assessment_month': None,
            'date_precision': 'UNKNOWN', 'date_basis': 'UNKNOWN'}


def dates(text):
    result = []
    for pattern, precision in ((DAY, 'DAY'), (MONTH, 'MONTH'), (YEAR, 'YEAR')):
        for m in pattern.finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b, _ in result):
                continue
            data = unknown_date()
            try:
                if precision == 'DAY':
                    raw = m[0]
                    d = date.fromisoformat(raw) if '-' in raw else date(*map(int, raw.split('.')[::-1]))
                    data.update(assessment_date=d.isoformat(), assessment_year=d.year, assessment_month=d.month, date_precision='DAY', date_basis='EXPLICIT_TEST_DATE')
                elif precision == 'MONTH':
                    year = int(m['year'] or m['numeric_year'])
                    month = MONTHS[m['name'].casefold()] if m['name'] else int(m['month'])
                    data.update(assessment_year=year, assessment_month=month, date_precision='MONTH', date_basis='EXPLICIT_MONTH_YEAR')
                else:
                    data.update(assessment_year=int(m[0]), date_precision='YEAR', date_basis='EXPLICIT_YEAR')
            except ValueError:
                data['invalid_date'] = True
            result.append((m.start(), m.end(), data))
    return sorted(result)


def sentence_end(text, start):
    """Dates/decimal separators and common abbreviations are not boundaries."""
    for m in re.finditer(r'[.!?](?=\s|$)', text[start:]):
        pos = start + m.start()
        if m[0] == '.' and re.search(r'\b(?:ca|bzw|z|n|a|e|Pkt|Dr|Prof)$', text[:pos], re.I):
            continue
        return pos + 1
    return len(text)


def sentence_start(text, end):
    start = max(0, end - 250)
    for m in re.finditer(r'[.!?](?=\s|$)|\n\s*\n', text[:end]):
        if m[0] == '.' and re.search(r'\b(?:ca|bzw|z|n|a|e|Pkt|Dr|Prof)$', text[:m.start()], re.I):
            continue
        start = max(start, m.end())
    while start < end and text[start].isspace():
        start += 1
    return start


def section_dates(doc):
    """Only an explicitly dated neuropsychological examination owns its body."""
    active, output = None, {}
    for sec in doc.sections:
        if sec.heading:
            active = None
            header = NEURO_HEADER.fullmatch(sec.heading)
            if header:
                parsed = dates(header['date'])
                active = parsed[0][2] if parsed and not parsed[0][2].get('invalid_date') else None
        ranges, start = [], 0
        for line in re.finditer(r'(?m)^.*$', sec.source_text):
            header = NEURO_HEADER.fullmatch(line[0])
            if header or PEER_HEADER.match(line[0]):
                if active and line.start() > start:
                    ranges.append((start, line.start(), active))
                parsed = dates(header['date']) if header else []
                active = parsed[0][2] if parsed and not parsed[0][2].get('invalid_date') else None
                start = line.end()
        if active and start < len(sec.source_text):
            ranges.append((start, len(sec.source_text), active))
        output[sec.section_id] = ranges
    return output


def _owner(text, alias_start, alias_end, score, local_start, section_date):
    """A date must directly touch this test/score, never another procedure."""
    before = text[local_start:score.start()]
    after = text[score.end():]
    date_candidates = []
    for a, b, data in dates(before):
        prefix_date = (b <= alias_start-local_start and re.fullmatch(r'[\s:()=-]*', before[b:alias_start-local_start])
                       and not re.search(r'MRT|MRI|\bCT\b|Diagnos|Aufnahme|Entlassung|geb\.|Neuropsycholog', before[:a], re.I))
        if a >= alias_end - local_start or prefix_date:
            date_candidates.append((data, local_start + a, local_start + b))
    tail = re.match(r'\s*(?:(?:Punkte[n]?|Pkt\.?)\s*)?(?:(?:am|im|vom|aus(?: dem Jahr)?|\()\s*)?', after, re.I)
    offset = tail.end() if tail else 0
    for a, b, data in dates(after[offset:]):
        if a == 0:
            date_candidates.append((data, score.end() + offset + a, score.end() + offset + b))
        break
    # A secondary current score cannot inherit the first score's historical year.
    cue_text = text[local_start:score.end()]
    context = 'CURRENT' if CURRENT.search(cue_text) else 'HISTORICAL' if PAST.search(cue_text) else 'UNKNOWN'
    if EXTERNAL.search(cue_text):
        context = 'EXTERNAL_REPORT'
    unique = {json.dumps(d, sort_keys=True) for d, _, _ in date_candidates}
    if len(unique) == 1 and not date_candidates[0][0].get('invalid_date'):
        value, a, b = date_candidates[0]
        if context == 'UNKNOWN' and CURRENT.search(text[score.end():]):
            context = 'HISTORICAL'
        return value, context, [a, b], None
    if date_candidates:
        return unknown_date(), context, None, 'AMBIGUOUS_OR_INVALID_TEST_DATE'
    if section_date and context not in ('HISTORICAL', 'EXTERNAL_REPORT'):
        return {**section_date, 'date_basis': 'CURRENT_SECTION_DATE'}, 'CURRENT', None, None
    return unknown_date(), context, None, None


def extract_document(doc, patient_id):
    assessments, census = [], []
    dated = section_dates(doc)
    for sec in doc.sections:
        aliases = list(ALIAS.finditer(sec.source_text))
        for index, alias in enumerate(aliases):
            text = sec.source_text
            test_type = 'MOCA' if alias[0].casefold() == 'moca' else 'MMSE'
            end = min(len(text), alias.end() + 300, aliases[index+1].start() if index + 1 < len(aliases) else len(text))
            end = min(end, sentence_end(text, alias.end()))
            peer = OTHER_TEST.search(text, alias.end(), end)
            if peer:
                end = peer.start()
            passage = text[alias.start():end]
            occurrence = {'patient_id': str(patient_id), 'source_document_id': doc.source_document_id,
                          'page': sec.page_start, 'section_id': sec.section_id, 'section': sec.heading,
                          'mention_start': alias.start(), 'mention_end': alias.end(), 'raw_text': passage,
                          'test_type': test_type, 'assessments': [], 'accepted': False, 'reason': 'NO_ATTRIBUTABLE_SCORE',
                          'unattributed_candidates': [], 'fallback_eligible': False}
            numeric = list(NUMBER.finditer(text, alias.end(), end))
            date_tokens = dates(text[alias.end():end])
            accepted_first, previous_end = False, alias.end()
            for number in numeric:
                if any(alias.end()+a <= number.start() < alias.end()+b for a,b,_ in date_tokens):
                    continue
                prefix = text[previous_end:number.start()]
                if not accepted_first:
                    cleaned = prefix
                    for a,b,_ in reversed(dates(prefix)):
                        cleaned = cleaned[:a] + ' ' + cleaned[b:]
                    simple = re.fullmatch(r'[\s:;()=-]*(?:(?:von|vom|am|im|beträgt|mit|lag|liegt|ist|bei|aktuell|derzeit|in|der|Testung|Punkte[n]?|Ergebnis|ein|einem)\b[\s:;()=-]*)*', cleaned, re.I)
                    clear_fraction = (number['maximum'] == '30' and len(cleaned) <= 80 and not NUMBER.search(cleaned)
                                      and not re.search(r'nicht|kein|ohne|geplant|Ziel|Referenz|normal|unbekannt', cleaned, re.I))
                    if not simple and not clear_fraction:
                        continue
                    local_start = sentence_start(text, alias.start())
                else:
                    cue = re.search(r'\b(?:aktuell|derzeit|jetzt|nun|zuvor|damals|früher)\b', prefix, re.I)
                    if not cue:
                        if number['maximum'] == '30':
                            occurrence['unattributed_candidates'].append({'score_start': number.start(), 'score_end': number.end(),
                                'raw_numeric_text': number[0], 'reason': 'AMBIGUOUS_ADDITIONAL_TEST_VALUE'})
                            occurrence['fallback_eligible'] = True
                        continue
                    local_start = previous_end + cue.start()
                tail = text[number.end():end]
                reason = None
                raw_score = number['score']
                if re.search(r'[.,]', raw_score):
                    reason = 'NON_INTEGER_SCORE'
                elif re.match(r'\s*(?:%|Jahre|jährig|J\.)', tail, re.I):
                    reason = 'PERCENTAGE_OR_AGE_NOT_SCORE'
                elif re.match(r'\s*[-–]\s*\d', tail):
                    reason = 'SCORE_RANGE_NOT_SCALAR'
                elif re.search(r'\b(?:geplant|angestrebt|Zielwert|soll|würde|falls)\b', text[local_start:end], re.I):
                    reason = 'PLANNED_OR_CONDITIONAL_NOT_MEASURED'
                elif int(raw_score) < 0 or int(raw_score) > 30:
                    reason = 'SCORE_OUT_OF_RANGE'
                elif number['maximum'] and int(number['maximum']) != 30:
                    reason = 'UNSUPPORTED_MAX_SCORE'
                if reason:
                    occurrence['reason'] = reason
                    break
                section_date = next((d for a,b,d in dated[sec.section_id] if a <= number.start() < b), None)
                date_data, context, date_span, issue = _owner(text[:end], alias.start(), alias.end(), number, local_start, section_date)
                # Neither date nor context is inferred from letter metadata.
                source_start = min(alias.start(), local_start)
                source_end = max(number.end(), date_span[1] if date_span else number.end())
                if date_span:
                    source_start = min(source_start, date_span[0])
                evidence = text[source_start:source_end]
                row = {'patient_id': str(patient_id), 'source_document_id': doc.source_document_id,
                       'test_type': test_type, 'score': int(raw_score), 'max_score': 30,
                       **date_data, 'context': context, 'source_text': evidence,
                       'page': sec.page_start, 'section_id': sec.section_id, 'source_start': source_start, 'source_end': source_end,
                       'score_start': number.start(), 'score_end': number.end(), 'date_source_span': date_span,
                       'context_source_start': local_start, 'context_source_end': number.end(),
                       'accepted': True, 'reason': issue or 'EXPLICIT_TEST_SCORE', 'temporal_review_required': bool(issue),
                       'mention_id': f'{doc.source_document_id}:{sec.section_id}:{number.start()}:{number.end()}',
                       'normalization_method': 'DETERMINISTIC_EXPLICIT_SCORE'}
                assessments.append(row)
                occurrence['assessments'].append(row)
                occurrence.update(accepted=True, reason='EXPLICIT_TEST_SCORE')
                accepted_first, previous_end = True, number.end()
            census.append(occurrence)
    return {'assessments': assessments, 'occurrences': census}


def canonical_groups(assessments):
    """Candidate identities only. Coarse/unknown dates never authorize new tests."""
    groups = defaultdict(list)
    for row in assessments:
        if row['date_precision'] != 'UNKNOWN':
            # Time-context can legitimately become historical in a later letter.
            context = 'EXTERNAL_REPORT' if row['context'] == 'EXTERNAL_REPORT' else 'DATED_ASSESSMENT_REFERENCE'
            key = (row['patient_id'], row['test_type'], row['score'], row['max_score'], row['date_precision'],
                   row['assessment_date'], row['assessment_year'], row['assessment_month'], context)
        else:
            key = (row['patient_id'], row['test_type'], 'UNRESOLVED', row['mention_id'])
        groups[key].append(row)
    result = []
    for key, mentions in groups.items():
        known = mentions[0]['date_precision'] != 'UNKNOWN'
        result.append({'canonical_candidate_id': digest(key), 'identity_basis': list(key),
                       'status': 'DATED_IDENTITY_CANDIDATE' if known else 'UNRESOLVED_UNDATED_IDENTITY',
                       'source_mentions': [r['mention_id'] for r in mentions],
                       'copied_forward_candidate': len({r['source_document_id'] for r in mentions}) > 1,
                       'coarse_date_requires_review': known and mentions[0]['date_precision'] != 'DAY',
                       'new_measurement_authorized': False})
    return result


def extract_bundle(bundle):
    outputs = [extract_document(doc, bundle.patient_id) for doc in bundle.documents]
    rows = [r for output in outputs for r in output['assessments']]
    return {'patient_id': bundle.patient_id, 'version': VERSION,
            'policy_fingerprint': digest({'policy':Path(__file__).read_text(encoding='utf-8'),
                                          'schema':Path(__file__).with_name('cognitive_schema.py').read_text(encoding='utf-8')}),
            'source_fingerprint': digest(bundle.to_dict()), 'assessments': rows,
            'occurrences': [r for output in outputs for r in output['occurrences']],
            'canonical_candidates': canonical_groups(rows), 'Granite_calls': 0, 'clinical_writes': 0}
