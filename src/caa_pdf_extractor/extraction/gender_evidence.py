"""Ranked, source-bound gender evidence; names bind identity, never imply sex."""
import hashlib
import json
import re
from pathlib import Path

VERSION = 'patient_bound_gender_v2_ranked'
RELATIVE = re.compile(
    r'\b(?:Mutter|Vater|Bruder|Brüder|Schwester|Ehemann\w*|Ehefrau\w*|Gatte\w*|Gattin\w*|'
    r'Sohn\w*|Tochter|Nachbar\w*|Pfleger\w*|Informant\w*|Angehörig\w*|Freund\w*|'
    r'Betreuer\w*|Betreuerin\w*|Arzt|Ärztin|Hausarzt|Hausärztin|Zuweiser\w*|Kolleg\w*)\b', re.I)
RECIPIENT = re.compile(r'Sehr\s+geehrt|Empfänger|Adress(?:e|at)|Zuweiser|Hausarzt|Hausärztin|Korrespondenz', re.I)
NARRATIVE = re.compile(r'Anamnese|Epikrise|Verlauf|Therapie|Aufnahme|Status|Befund|Beurteilung|Zusammenfassung|Entlass|Sozial|Rehabilit', re.I)
OTHER_SECTION = re.compile(r'Familienanamnese|Fremdanamnese|Informant|Angehörigenbericht|Bezugsperson', re.I)
NAMED_SPEAKER = re.compile(r'\b(?P<name>[A-ZÄÖÜ][\w-]+(?:[ \t]+[A-ZÄÖÜ][\w-]+){1,3})\s+(?:berichtet|erzählt|sagt)\b')
DOB = re.compile(r'\b(?:geb\.|geboren\s+am)\s*:?\s*\d', re.I)
MARKER = re.compile(
    r'(?P<age>\b\d{1,3}[- ]jährig(?:er|e)\s+Patient(?:in)?\b)|'
    r'(?P<subject>\b(?:der\s+Patient|die\s+Patientin)\b)|'
    r'(?P<named>\b(?:Herrn?|Frau)\s+(?:Dr\.?\s+)?[^\W\d_][\w-]*)', re.I)
INTRO = re.compile(
    r'\bwir\s+berichten\s+über\s+(?P<title>Frau|Herrn?|Ihre[n]?\s+Patient(?:in|en)?)\s+'
    r'(?P<name>[^,\n]{2,100}),\s*(?P<relative>die|der)\b', re.I)
FIELD = re.compile(r'\b(?:Geschlecht|gender|sex)\s*:\s*'
                   r'(männlich|weiblich|male|female|man|woman|other|unspecified|unknown)\b', re.I)
TITLE_PREFIX = r'(?:(?:Prof|Dr|Dipl|Ing|med)\.?\s+|h\.c\.\s+)*'


def _tokens(name):
    name = re.sub(r'^(?:(?:Herrn?|Frau)\s+)?' + TITLE_PREFIX, '', name.strip(), flags=re.I)
    return tuple(re.findall(r'[^\W\d_][\w-]*', name.casefold()))


def _identities(doc):
    """Only DOB identity headers, never a relative's DOB or an address name."""
    names = set()
    for section in doc.sections:
        for line in section.source_text.splitlines():
            dob = DOB.search(line)
            if not dob:
                continue
            prefix = line[:dob.start()].strip(' ,;')
            if RECIPIENT.search(prefix) or RELATIVE.search(prefix):
                continue
            prefix = re.sub(r'^(?:Patient(?:in)?|Name)\s*:\s*', '', prefix, flags=re.I)
            tokens = _tokens(prefix)
            if 2 <= len(tokens) <= 6 and not re.search(r'[:;!?]', prefix):
                names.add(tokens)
    return names


def _intro_identities(doc):
    """An explicit report-about clause can establish identity without a DOB."""
    names = set()
    for section in doc.sections:
        for match in INTRO.finditer(section.source_text):
            prefix = section.source_text[:match.start()]
            if _quoted(prefix) or RECIPIENT.search(section.heading or ''):
                continue
            name = _tokens(match['name'])
            if 2 <= len(name) <= 6:
                names.add(name)
    return names


def _named_identity(line, start, names):
    for name in sorted(names, key=len, reverse=True):
        pattern = r'(?:Herrn?|Frau)\s+' + TITLE_PREFIX + r'\.?\s+'.join(map(re.escape, name)) + r'\b'
        match = re.match(pattern, line[start:], re.I)
        if match:
            return match
    return None


def _quoted(prefix):
    return prefix.count('"') % 2 or prefix.rfind('„') > prefix.rfind('“') or prefix.rfind('«') > prefix.rfind('»')


def extract_gender(bundle):
    candidates, rejected = [], []
    for doc in bundle.documents:
        names = _identities(doc) or _intro_identities(doc)
        # Multiple DOB identities cannot safely anchor weaker narrative grammar.
        bound = len(names) == 1
        for section in doc.sections:
            if section.page_start != section.page_end:
                continue
            narrative = bool(bound and NARRATIVE.search(section.heading or ''))
            other_active = False
            for line_match in re.finditer(r'(?m)^.*$', section.source_text):
                line = line_match[0]
                line_anchored = False
                intro = INTRO.search(line)
                spans = []
                if intro:
                    spans.append((intro.start(), intro.end(), 'intro', intro))
                spans.extend((m.start(), m.end(), m.lastgroup, m) for m in MARKER.finditer(line)
                             if not intro or not (intro.start() <= m.start() < intro.end()))
                spans.extend((m.start(), m.end(), 'EXPLICIT_FIELD', m) for m in FIELD.finditer(line))
                for start, end, kind, match in sorted(spans, key=lambda x: x[0]):
                    before = line[:start]
                    local = re.split(r'[.;!?]', before)[-1]
                    for speaker in NAMED_SPEAKER.finditer(before):
                        name = _tokens(speaker['name'])
                        if name not in names and not re.search(r'\bPatient(?:in)?\b', speaker['name'], re.I):
                            other_active = True
                    full = _named_identity(line, start, names) if kind == 'named' else None
                    value = ('man' if match[1].casefold() in ('männlich', 'male', 'man') else
                             'woman' if match[1].casefold() in ('weiblich', 'female', 'woman') else
                             match[1].casefold()) if kind == 'EXPLICIT_FIELD' else (
                                 'woman' if re.search(r'Patientin|\bFrau\b', match[0], re.I) else 'man')
                    tier, binding, reason = 4, 'UNBOUND', None
                    if kind == 'EXPLICIT_FIELD':
                        tier, binding = 1, 'EXPLICIT_PATIENT_DEMOGRAPHIC_FIELD'
                    elif kind == 'intro':
                        tier, binding = 2, 'FULL_NAME_AND_AGREEING_INTRO_RELATIVE'
                        if not bound or _tokens(match['name']) not in names:
                            reason = 'REJECTED_NON_PATIENT_BOUND'
                        elif match['relative'].casefold() != ('die' if value == 'woman' else 'der'):
                            reason = 'INTRO_GRAMMAR_DISAGREES'
                    elif kind == 'named':
                        dob = DOB.search(line)
                        if full and dob and start < dob.start() and _tokens(line[:dob.start()].strip(' ,;')) in names:
                            tier, binding = 1, 'FULL_NAME_IN_DOB_IDENTITY_HEADER'
                        elif bound and (full or any(re.match(r'(?:Herrn?|Frau)\s+' + TITLE_PREFIX + re.escape(n[-1]) + r'\b', line[start:], re.I) for n in names)):
                            binding = 'PATIENT_SURNAME_IN_CLINICAL_NARRATIVE'
                            if not narrative or not re.search(r'\b(?:wurde|wird|war|ist|erfolgte|erhielt|konnte|stellt|berichtet)\b', line, re.I):
                                reason = 'REJECTED_NON_PATIENT_BOUND'
                        else:
                            reason = 'REJECTED_NON_PATIENT_BOUND'
                    elif kind == 'age':
                        tier, binding = 3, 'AGE_PATIENT_IN_ANCHORED_NARRATIVE'
                        if not narrative:
                            reason = 'REJECTED_NON_PATIENT_BOUND'
                        elif not re.search(r'jähriger\s+Patient\b|jährige\s+Patientin\b', match[0], re.I):
                            reason = 'AGE_GRAMMAR_DISAGREES'
                    else:
                        binding = 'LOCAL_SUBJECT_IN_ANCHORED_NARRATIVE'
                        if not narrative:
                            reason = 'REJECTED_NON_PATIENT_BOUND'
                    if RECIPIENT.search(line) or RECIPIENT.search(section.heading or ''):
                        reason = 'RECIPIENT_OR_SALUTATION'
                    if RELATIVE.search(local):
                        reason = 'REJECTED_NON_PATIENT_BOUND'
                    if tier > 2 and OTHER_SECTION.search(section.heading or ''):
                        reason = 'OTHER_PERSON_SECTION'
                    if _quoted(section.source_text[:line_match.start() + start]):
                        reason = 'QUOTED_OR_INFORMANT_TEXT'
                    if re.search(r'Beispiel|hypothetisch|anderer\s+Patient|andere\s+Patientin', line, re.I):
                        reason = 'NOT_PATIENT_ASSERTION'
                    if kind in ('age', 'subject', 'EXPLICIT_FIELD') and other_active:
                        reason = 'INTERVENING_OTHER_PERSON'
                    proof = {'value': value, 'source_document_id': doc.source_document_id,
                             'section_id': section.section_id, 'page': section.page_start,
                             'evidence': line.strip(), 'marker_text': match[0], 'kind': kind,
                             'offset': line_match.start() + start, 'evidence_tier': tier,
                             'patient_binding_method': binding}
                    if reason:
                        rejected.append({**proof, 'accepted': False, 'reason': reason})
                        if kind == 'named':
                            other_active = True
                    else:
                        candidates.append(proof)
                        if kind in ('intro', 'named'):
                            narrative = True
                            other_active = False
                            line_anchored = True
                # Preserve alternative-person boundaries until a patient name
                # explicitly re-anchors the section's narrative.
                if RELATIVE.search(line) and not line_anchored:
                    other_active = True
                for speaker in NAMED_SPEAKER.finditer(line):
                    if _tokens(speaker['name']) not in names and not re.search(r'\bPatient(?:in)?\b', speaker['name'], re.I):
                        other_active = True

    strongest = min((p['evidence_tier'] for p in candidates), default=None)
    best_values = {p['value'] for p in candidates if p['evidence_tier'] == strongest}
    all_values = {p['value'] for p in candidates}
    # With no identity-level winner, contradictory age/local evidence remains
    # unresolved. Never resolve by counting repeated markers or majority vote.
    conflict = len(best_values) > 1 or (strongest in (3, 4) and len(all_values) > 1)
    conflict |= bool(best_values - {'man', 'woman'} and len(all_values) > 1)
    winner = next(iter(best_values)) if len(best_values) == 1 and not conflict else None
    accepted = []
    for proof in candidates:
        if winner is not None and proof['value'] != winner:
            rejected.append({**proof, 'accepted': False, 'reason': 'REJECTED_LOWER_TIER_CONTRADICTION',
                             'classification': 'NON_PATIENT_OR_TEMPLATE_CONFLICT'})
        else:
            accepted.append({**proof, 'accepted': True, 'reason': 'PATIENT_BOUND_RANKED_EVIDENCE'})
    unique = {(p['kind'], ' '.join(p['evidence'].casefold().split())) for p in accepted}
    qualifies = strongest in (1, 2, 3) or len(unique) >= 2
    value = winner if qualifies else None
    return {'value': value,
            'status': 'CONFLICTING_GENDER' if conflict else 'VERIFIED' if value else 'INSUFFICIENT_PATIENT_BOUND_EVIDENCE',
            'winning_tier': strongest, 'provenance': accepted, 'rejected_markers': rejected,
            'independent_markers': len(unique), 'rule_version': VERSION}


def source_fingerprint(bundle):
    return hashlib.sha256(json.dumps({'bundle': bundle.to_dict(), 'implementation': Path(__file__).read_text(encoding='utf8')},
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply_to_case_output(output, bundle):
    assessment = extract_gender(bundle)
    output['gender_assessment'] = assessment
    if assessment['value']:
        output['update_fields']['gender'] = assessment['value']
        output['provenance']['gender'] = [
            {'value': p['value'], 'source_document_id': p['source_document_id'], 'page': p['page'],
             'section_id': p['section_id'], 'evidence': p['evidence'],
             'extraction_method': 'patient_bound_ranked_gender', 'confidence_rule': assessment['status'],
             'evidence_tier': p['evidence_tier'], 'patient_binding_method': p['patient_binding_method']}
            for p in assessment['provenance']]
        output['demographics']['update_fields']['gender'] = assessment['value']
    elif assessment['status'] == 'CONFLICTING_GENDER':
        output['update_fields'].pop('gender', None)
        output['provenance'].pop('gender', None)
        output['demographics']['update_fields'].pop('gender', None)
        output['source_conflicts'].append({'field': 'gender', 'reason': 'CONFLICTING_PATIENT_BOUND_GENDER'})
        output['demographics']['validation_status'] = 'REVIEW_REQUIRED'
    return output
