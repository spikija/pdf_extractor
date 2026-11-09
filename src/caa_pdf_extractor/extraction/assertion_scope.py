"""Deterministic, concept-bound assertion validation after semantic classification.

Source negation wins over model labels. A negative current search cannot erase a
separately established chronic diagnosis. This module performs no I/O/model calls.
"""
import re

VERSION = 'ASSERTION_SCOPE_1.3'
AF_PATTERN = re.compile(r'vorhofflimmer\w*|\bVHF\b|atrial\s+fibrillation|\bAF\b', re.I)
BOUNDARY = re.compile(r';|[.!?]\s+(?=[A-ZÄÖÜ])|\n\s*\n|,(?=\s*(?:aktuell|jedoch|aber|kein|ohne))|\b(?:wobei|jedoch|aber)\b', re.I)
NONEXCLUSION = re.compile(r'\b(?:kann|konnte|könne|lässt sich|ist)?\s*nicht\s+(?:sicher\s+)?aus(?:ge)?schlossen\b|\bnicht auszuschließen\b', re.I)
PREFIX_NEGATION = re.compile(r'\b(?:kein\w*|ohne|no|without|nie)\s+(?:(?:hinweis\w*|nachweis|detektion|anhalt|evidenz|evidence)\s+(?:auf|von|für|of)\s+)?(?:[\w-]+\s+){0,4}$', re.I)
POST_NEGATION = re.compile(r'^\s*(?:(?:konnte|kann|wurde|war|ist|bisher|aktuell|bislang|hierbei|auch|im|vorliegenden|langzeit-ekg|\d+[ -]*stunden[ -]*ekg|ekg|monitoring|holter)\s+){0,10}(?:nicht|kein\w*)\s+(?:detektiert|detektierbar|nachweisbar|nachgewiesen|festgestellt|dokumentiert|beobachtet|gefunden|erkennbar|bestätigt|vorhanden)\b', re.I)
UNCERTAINTY = re.compile(r'\bv\s*\.\s*a\s*\.|\bDD\b|differentialdiagnos\w*|verdacht|suspekt|wahrscheinlich|möglich\w*|könnte|vereinbar mit|fragestellung|ausschluss|risiko|gefahr|frage nach|hinweisend', re.I)
POSITIVE_MODIFIER = re.compile(r'\b(?:bekannt\w*|vorbekannt\w*|anamnestisch\w*)\s*$', re.I)


def _masked(text):
    return re.sub(r'\b(?:v\s*\.\s*a\s*\.|z\s*\.\s*n\s*\.|z\s*\.\s*b\s*\.)',
                  lambda m: m[0].replace('.', '·'), text, flags=re.I)


def concept_clause(text, start, end):
    """Bound a target span by sentence/clause changes, preserving V.a./Z.n."""
    masked = _masked(text)
    prior = list(BOUNDARY.finditer(masked[:start]))
    left = prior[-1].end() if prior else 0
    following = BOUNDARY.search(masked, end)
    right = following.start() if following else len(text)
    prefix = text[left:start]
    # A new contextual predicate owns the following concept, not a preceding negative finding.
    resets = list(re.finditer(r'\b(?:bei|mit|bei bekannt\w*)\s+', prefix, re.I))
    if resets:
        left += resets[-1].end()
    # Closing brackets end e.g. '(potentielle Gefahr VHF) Im Holter kein VHF'.
    closing = text.find(')', end, right)
    if closing >= 0 and '(' in text[left:start]:
        right = closing
    return text[left:right], start - left, end - left, left


def validate_assertion(text, start, end, *, model_assertion=None, positive_context=True):
    if not (0 <= start < end <= len(text)):
        raise ValueError('A source-bound concept span is required')
    clause, local_start, local_end, offset = concept_clause(text, start, end)
    before, after = clause[:local_start], clause[local_end:]
    before = re.sub(r'\s+', ' ', before)
    after = re.sub(r'(?<=-)\s+(?=\w)', '', after)
    after = re.sub(r'\s+', ' ', after)
    # A positive modifier belongs to this concept; negatives about another object after it do not.
    known = bool(POSITIVE_MODIFIER.search(before) or re.match(r'\s+(?:bekannt|vorbekannt)\b', after, re.I))
    prefix_negative = bool(PREFIX_NEGATION.search(before))
    postfix_negative = bool(POST_NEGATION.search(after))
    nonexclusion = bool(NONEXCLUSION.search(after[:110]))
    prospective_exclusion = bool(re.match(r'^\s*(?:ausgeschlossen\s+(?:sein|werden)\s+(?:soll|muss)|(?:soll|muss)\s+ausgeschlossen\s+werden)', after, re.I))
    completed_exclusion = bool(re.match(r'^\s*(?:(?:wurde|konnte|ist|war)\s+)?(?:sicher\s+)?ausgeschlossen\b', after, re.I))
    caa=bool(re.search(r'amyloidangiopathie|\bCAA\b',text[start:end],re.I))
    phenotype_negative=caa and bool(re.search(r'nicht\s+(?:zum|zu\s+dem)\s+Muster\b[^.;]*amyloidangiopathie\s*passend|nicht\s+(?:mit|zu)\b[^.;]*(?:amyloidangiopathie|\bCAA\b)[^.;]*(?:vereinbar|passend|kompatibel)',clause,re.I))
    not_assessable=caa and bool(re.search(r'keine\s+(?:sichere|suffiziente|verlässliche)\s+Aussage',clause,re.I))
    workup=caa and bool(re.search(r'(?:zur|zwecks)\s+(?:weiterführenden\s+)?Abklärung|\bworkup\b',before,re.I))
    if phenotype_negative:
        state,reason='NEGATED','PHENOTYPE_ARGUES_AGAINST_DISEASE'
    elif not_assessable:
        state,reason='NOT_ASSESSABLE','NONDIAGNOSTIC_EXAMINATION'
    elif workup:
        state,reason='DIFFERENTIAL','WORKUP_TARGET'
    elif nonexclusion:
        state, reason = 'UNCERTAIN', 'CONCEPT_CANNOT_BE_EXCLUDED'
    elif prospective_exclusion:
        state, reason = 'UNCERTAIN', 'PROSPECTIVE_EXCLUSION_SEARCH'
    elif completed_exclusion:
        state, reason = 'NEGATED', 'EXPLICIT_COMPLETED_EXCLUSION'
    elif prefix_negative or postfix_negative:
        state, reason = 'NEGATED', 'EXPLICIT_CONCEPT_NEGATION'
    elif UNCERTAINTY.search(before) or re.search(r'\bsuspiz\w*',before,re.I) or re.match(r'\s+(?:unklar|fraglich|möglich|suspekt)\b', after, re.I):
        state, reason = 'UNCERTAIN', 'CONCEPT_UNCERTAINTY_OR_SEARCH'
    elif positive_context or known:
        proposed = {'SUSPECTED': 'UNCERTAIN', 'ASSERTED': 'ESTABLISHED'}.get(model_assertion, model_assertion)
        state = proposed if proposed in ('NEGATED', 'UNCERTAIN', 'SOURCE_ONLY','NOT_ASSESSABLE','DIFFERENTIAL','CONDITIONAL') else 'ESTABLISHED'
        reason = 'POSITIVE_SOURCE_CONTEXT' if state == 'ESTABLISHED' else 'SEMANTIC_CLASSIFICATION_RETAINED'
    else:
        state, reason = 'SOURCE_ONLY', 'NO_POSITIVE_DIAGNOSTIC_CONTEXT'
    prior = list(BOUNDARY.finditer(_masked(text)[:start]))
    outer_before = text[prior[-1].end() if prior else 0:start]
    conditional=bool(re.search(r'\b(?:sollte\s+sich|sofern|falls|wenn|im\s+Falle|bei\s+Auftreten)\b',outer_before,re.I))
    if conditional and state not in ('NEGATED','NOT_ASSESSABLE'):
        state,reason='CONDITIONAL','HYPOTHETICAL_FUTURE_DECISION_TRIGGER'
    if state == 'ESTABLISHED' and re.search(r'\b(?:sofern|falls|wenn|gegebenenfalls|ggf\.?|im Falle|bei Auftreten|zur Suche|zur Detektion)\b', outer_before, re.I):
        state, reason = 'UNCERTAIN', 'CONDITIONAL_OR_SEARCH_CONTEXT'
    if state == 'ESTABLISHED' and re.search(r'\b(?:wie\s+bei|vereinbar\s+mit)\s*$', outer_before, re.I):
        state, reason = 'UNCERTAIN', 'SUGGESTIVE_FINDING_COMPARISON'
    scope = 'HYPOTHETICAL_FUTURE' if state=='CONDITIONAL' else 'CURRENT_ASSESSMENT' if state == 'NEGATED' else 'HISTORICAL_OR_CHRONIC' if known else 'SOURCE_DOCUMENTATION'
    return dict(assertion_status=state, deterministic_assertion=state, model_assertion=model_assertion,
                semantic_relation='WORKUP_TARGET' if workup else 'PHENOTYPE_NON_SUPPORT' if phenotype_negative else 'NONDIAGNOSTIC_EXAM' if not_assessable else 'SOURCE_ASSERTION',
                CAA_SUPPORT='NEGATIVE' if phenotype_negative else None,
                temporal_status='HISTORICAL' if re.search(r'zustand nach|\bz\s*\.\s*n\s*\.|anamnestisch|früher',clause,re.I) else 'SOURCE_DOCUMENTATION',
                scope=scope, explicit_positive=state == 'ESTABLISHED', rule=reason,
                source_clause=clause, clause_offset=offset, rule_version=VERSION)


def af_observation(text, start, end, *, observation_date=None, model_assertion=None, positive_context=True):
    result = validate_assertion(text, start, end, model_assertion=model_assertion, positive_context=positive_context)
    negative = result['assertion_status'] == 'NEGATED'
    # Investigation absence is distinct from a direct contradictory diagnosis assertion.
    nearby = text[max(0, start-250):min(len(text), end+150)]
    search = bool(re.search(r'monitor|holter|langzeit|telemetr|\bEKG\b|\bECG\b|nicht\s+(?:detektiert|detektierbar|nachweisbar|festgestellt|dokumentiert)|kein\w*\s+(?:hinweis|nachweis|anhalt)', result['source_clause'] + ' ' + nearby, re.I))
    modality = 'HOLTER' if re.search(r'holter|langzeit|24[ -]*stunden[ -]*ekg', text, re.I) else 'TELEMETRY' if re.search(r'telemetr', text, re.I) else 'MONITORING' if re.search(r'monitor', text, re.I) else 'ECG' if re.search(r'\b(?:EKG|ECG)\b', text, re.I) else 'OTHER'
    result.update(concept='ATRIAL_FIBRILLATION', observation_date=observation_date,
                  AF_PRESENT=False if negative else True if result['assertion_status'] == 'ESTABLISHED' else None,
                  AF_ESTABLISHED_FROM_THIS_EVIDENCE=result['explicit_positive'],
                  observation_type='AF_SEARCH' if search else 'AF_ASSERTION', result='NEGATIVE' if negative else result['assertion_status'],
                  source_modality=modality, display_label='NEGATIVE OBSERVATION' if negative else result['assertion_status'])
    if re.search(r'soft\s+signs',text,re.I) and re.search(r'möglich\w*|verdacht',text,re.I):
        result.update(assertion_status='UNCERTAIN',deterministic_assertion='UNCERTAIN',explicit_positive=False,
            AF_ESTABLISHED_FROM_THIS_EVIDENCE=False,AF_PRESENT=None,result='UNCERTAIN',
            objective_observation='NEGATIVE' if re.search(r'nicht\s+nachgewiesen|kein\w*\s+nachweis',text,re.I) else None)
    if result['assertion_status'] == 'UNCERTAIN' and re.search(r'gefahr|risiko', result['source_clause'][:start-result['clause_offset']], re.I):
        result.update(scope='FUTURE_RISK', observation_type='RISK_CONTEXT')
    return result


def resolve_af(observations, *, index_date=None):
    rows = [r for r in observations if r.get('concept', r.get('canonical_name')) == 'ATRIAL_FIBRILLATION']
    if index_date:
        rows = [r for r in rows if r.get('observation_date') and r['observation_date'] <= index_date]
    positive = [r for r in rows if r.get('assertion_status') == 'ESTABLISHED' and r.get('explicit_positive') is True]
    negative = [r for r in rows if r.get('assertion_status') == 'NEGATED']
    uncertain = [r for r in rows if r.get('assertion_status') in ('UNCERTAIN', 'SUSPECTED') and r.get('observation_type') != 'RISK_CONTEXT']
    conflict = any(n.get('observation_type') != 'AF_SEARCH' and n.get('observation_date') and
                   n.get('observation_date') == p.get('observation_date') for n in negative for p in positive)
    state = 'CONFLICTING_AF_EVIDENCE' if conflict else 'CONFIRMED_AF' if positive else 'SUSPECTED_AF' if uncertain else 'NO_CONFIRMED_AF'
    days = sorted(r['observation_date'] for r in positive if r.get('observation_date'))
    return dict(AF_state=state, confirmed_af=state == 'CONFIRMED_AF', af_index_date=days[0] if days and state == 'CONFIRMED_AF' else None,
                positive_evidence_count=len(positive), negative_observation_count=len(negative),
                uncertain_evidence_count=len(uncertain), review_required=conflict or bool(uncertain))
