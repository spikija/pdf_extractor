"""Strict source support and deterministic metadata reconciliation for MRI only."""
import json
import re
from .radiology_models import RadiologyExtraction
from .radiology_inputs import report_text, procedure_context, examination_date
from .validators import parse_json

HEMORRHAGE = r'\b(?:ICB|ICH|h[äa]matom\w*|hematom\w*|blutung\w*|h[äa]morrhag\w*|hemorrhag\w*|haemorrhag\w*|basalganglienblutung\w*)\b'
SUPPORT = {
    'lobar': rf'(?=.*{HEMORRHAGE})(?=.*(?:frontal|frontolateral|parietal|temporal|okzipital|occipital|lob[äa]r|kortikal|cortical|mantelkante))',
    'basal ganglia': rf'(?=.*{HEMORRHAGE})(?=.*(?:basalgang|basal gang|putamen|globus pallidus|nucleus caudatus))',
    'subarachnoidal bleeding': rf'(?=.*{HEMORRHAGE})(?=.*(?:subarachnoid|subarachnoidal))',
    'cmb': r'\b(?:mikroblutung(?:en)?|microbleeds?|microhemorrhages?|microhaemorrhages?|punktf[öo]rmige[nrms]?\s+H[äa]mosiderinablagerung(?:en)?|Mikro[-\s]H[äa]mosiderinresidu(?:um|en))\b',
    'css': r'(?:(?:oberfl[äa]ch\w*|superfiziell\w*|kortikal\w*|sulk[au]l\w*|leptomeningeal\w*)\s+(?:sideros\w*|h[äa]mosideros\w*|h[äa]mosiderinablagerung\w*)|(?:cortical\s+)?superficial\s+(?:siderosis|hemosiderosis))',
    'other': r'\b(?:other|sonstiger ph[äa]notyp)\b',
    'unknown': r'\b(?:unknown|unbekannter ph[äa]notyp)\b',
    'not specified': r'\b(?:not specified|nicht spezifizierter ph[äa]notyp)\b',
}


CAA_CLAIM = re.compile(
    r'(?:Verdacht\s+auf|suspekt\s+f[üu]r|suspic\w*|v\.?\s*a\.?)\s+(?:eine[rn]?\s+)?(?:zerebral\w*\s+)?Amyloidangiopathie'
    r'|vereinbar\s+mit\s+(?:eine[rn]?\s+)?(?:zerebral\w*\s+)?Amyloidangiopathie'
    r'|Amyloidangiopathie\s+wahrscheinlich', re.I)


def normalized_quote(text):
    return ' '.join(text.split())


def phenotype_key(mri_date, finding):
    return (mri_date, finding.type, finding.acute_lesion)


ACUITY_RULE_VERSION = 'event_clause_resorption_v2'
NON_ACUTE = re.compile(
    r'\bchron\.|\b(?:alt(?:e[nmrs]?)?|chronisch\w*|residu\w*|sequel\w*|abgelaufen\w*|'
    r'h[äa]mosiderin(?:rest\w*|resid\w*)|hemosiderin\s+(?:residu\w*|remnant\w*)|'
    r'fr[üu]her\w*|stattgehabt\w*|vorbestehend\w*|zur[üu]ckliegend\w*|posth[äa]morrhag\w*|'
    r'resorbiert\w*|chronic|old|remote|previous)\b|\bZustand\s+nach\b|\bZ\.\s*n\.'
    r'|\bnach\s+Resorption\b|\bDefekt\w*\s+nach\s+(?:einer\s+)?Blutung', re.I)
ACUTE = re.compile(r'\b(?:akut\w*|frisch\w*|neu(?:e[nmrs]?)?|rezent\w*|aktuell\w*|acute|current|fresh)\b', re.I)


def evidence_context(evidence, report):
    """Retain modifiers from the same source sentence even if a quote crops them.

    A historical comparison in another sentence must not make a current hematoma
    chronic. Sentence boundaries require whitespace, so dates/measurements remain
    intact. This context is used for acuity only; evidence stays verbatim.
    """
    evidence, report = normalized_quote(evidence), normalized_quote(report)
    start = report.find(evidence)
    if start < 0:
        return evidence
    end = start + len(evidence)
    boundaries = [m.end() for m in re.finditer(r'(?<!chron)[.!?]\s+(?=[A-ZÄÖÜ])', report)]
    left = max([0] + [b for b in boundaries if b <= start])
    right = min([len(report)] + [b for b in boundaries if b >= end])
    return report[left:right]


def derive_acuity(phenotype, evidence, report):
    """Apply the user's phenotype conventions, never trust the model's bool."""
    if phenotype in ('cmb', 'css'):
        return False
    context = evidence_context(evidence, report)
    # Split independent events before evaluating temporal modifiers. A quote
    # cropped to the current event must not inherit an adjacent old bleed.
    clauses = re.split(r';|\b(?:aber|jedoch|während)\b|(?:,|\bund\b)\s*(?=(?:akut\w*|frisch\w*|neu\w*|alt\w*|chronisch\w*|nach Resorption|Zustand nach)\b)', context, flags=re.I)
    supported = [c for c in clauses if re.search(SUPPORT.get(phenotype, r'(?!)'), c, re.I)]
    quoted = [c for c in supported if normalized_quote(evidence) in normalized_quote(c)]
    if len(quoted) == 1:
        context = quoted[0]
    elif len(supported) == 1:
        context = supported[0]
    elif len(supported) > 1:
        values = {derive_acuity(phenotype, c.strip(), c.strip()) for c in supported}
        return values.pop() if len(values) == 1 else None
    # An old hemosiderin deposit in an adjunct is a separate lesion, not a
    # chronicity modifier of an explicitly acute macroscopic hemorrhage.
    if re.search(r'\b(?:akut\w*|frisch\w*|acute|current)\s+(?:\w+\s+){0,3}' + HEMORRHAGE, context, re.I):
        context = re.sub(r'\b(?:bei|neben|mit)\s+(?:\w+\s+){0,3}(?:H[äa]mosiderinresidu\w*|Residuen)\b[^;]*', '', context, flags=re.I)
    # Old/previous comparison imaging is not chronicity of the lesion itself.
    context = re.sub(r'\b(?:alt(?:e[nmrs]?)?|old|previous|remote|fr[üu]her\w*)\s+'
                     r'(?=(?:(?:Vergleichs|Sch[äa]del|head)[-\s]*)?(?:CT|MRI|MRT|scan|examination)\b)',
                     '', context, flags=re.I)
    if NON_ACUTE.search(context):
        return False
    if phenotype in ('lobar', 'basal ganglia') and re.search(HEMORRHAGE, context, re.I):
        # A currently described macroscopic ICH/hematoma is acute/current unless
        # its own source statement explicitly establishes chronicity.
        return True
    if ACUTE.search(context):
        return True
    return None


def validate_radiology(raw, patient_id, procedure):
    if procedure.modality != 'MRI' or procedure.patient_id != patient_id:
        raise ValueError('Only patient-matched MRI events may produce radiology phenotypes')
    parsed = parse_json(raw)
    result = RadiologyExtraction.model_validate_json(json.dumps(parsed))
    if result.patient_id != patient_id or result.procedure_id != procedure.procedure_id:
        raise ValueError('Patient/procedure provenance mismatch')
    report = normalized_quote(report_text(procedure))
    def check_quote(value):
        if value is not None and (not normalized_quote(value) or normalized_quote(value) not in report):
            raise ValueError('Unsupported source quotation')
    seen, findings, acuity_reconciliation = set(), [], []
    for item in result.findings:
        check_quote(item.evidence)
        evidence = normalized_quote(item.evidence)
        if not re.search(SUPPORT[item.type], evidence, re.I):
            raise ValueError('Phenotype evidence does not support type')
        if item.type == 'cmb' and re.search(
                r'\b(?:kein\w*|ohne|no)\s+(?:\w+\s+){0,5}(?:'+SUPPORT['cmb']+r')',
                evidence_context(evidence, report), re.I):
            raise ValueError('Negated phenotype')
        # Reject direct negations of the phenotype rather than unrelated negative
        # observations such as no midline shift in the same supporting snippet.
        if re.search(r'\b(?:kein\w*|ohne|no)\s+(?:\w+\s+){0,3}(?:mikroblutung\w*|microbleed\w*|h[äa]matom\w*|blutung\w*|sideros\w*)', evidence, re.I):
            raise ValueError('Negated phenotype')
        derived = derive_acuity(item.type, evidence, report)
        if item.acute_lesion != derived:
            acuity_reconciliation.append({'type': item.type, 'model_value': item.acute_lesion,
                                          'normalized_value': derived, 'rule_version': ACUITY_RULE_VERSION})
        item.acute_lesion = derived
        key = (item.type, item.acute_lesion)
        if key not in seen:
            findings.append(item.model_dump(mode='json'))
            seen.add(key)
    additional = result.additional_findings
    if (CAA_CLAIM.search(report)
            and (additional.caa_suspicion_explicit is False
                 or (result.findings and additional.caa_suspicion_explicit is not True))):
        raise ValueError('Explicit source CAA suspicion omitted or contradicted')
    check_quote(additional.caa_evidence)
    check_quote(additional.microbleed_description)
    for quote in additional.other_findings:
        check_quote(quote)
    if additional.caa_suspicion_explicit is not None:
        quote = additional.caa_evidence or ''
        if not re.search(r'amyloidangiopath|amyloid angiopath', quote, re.I):
            raise ValueError('CAA claim lacks explicit source evidence')
        if additional.caa_suspicion_explicit is True and not CAA_CLAIM.search(quote):
            raise ValueError('CAA suspicion not explicitly stated')
        if additional.caa_suspicion_explicit is True and re.search(r'\b(?:kein\w*|ohne|no)\b', quote, re.I):
            raise ValueError('CAA suspicion is negated')
        if additional.caa_suspicion_explicit is False and not re.search(r'kein|ohne|no ', quote, re.I):
            raise ValueError('CAA absence not explicitly stated')
    if additional.microbleed_description is not None and not re.search(SUPPORT['cmb'], additional.microbleed_description, re.I):
        raise ValueError('Microbleed description lacks explicit evidence')
    context = procedure_context(procedure)
    reconciled = {field: {'model_value': parsed[field], 'prepared_value': context.get('date' if field == 'mri_date' else field)}
                  for field in ('mri_date','modality','source_document_id','page_start','page_end','studies')
                  if field in parsed and parsed[field] != context.get('date' if field == 'mri_date' else field)}
    examination_date(procedure)  # Validate the prepared date, never use a model date.
    return {**context, 'mri_date': procedure.date, 'findings': findings,
            'additional_findings': additional.model_dump(mode='json'),
            'metadata_reconciliation': reconciled,
            'acuity_rule_version': ACUITY_RULE_VERSION, 'acuity_reconciliation': acuity_reconciliation}


def source_sentences(text):
    """Exact spans, preserving source whitespace, dates and internal spelling."""
    start = 0
    for match in re.finditer(r'(?<!chron)[.!?](?:\s+)(?=[A-ZÄÖÜ])|\n\s*\n', text):
        end = match.start()+1 if text[match.start()] in '.!?' else match.start()
        if text[start:end].strip(): yield text[start:end].strip()
        start = match.end()
    if text[start:].strip(): yield text[start:].strip()


def explicit_phenotype_span(phenotype, span):
    """Narrow positive source rule; uncertainty about etiology is not lesion uncertainty."""
    text = normalized_quote(span)
    match = re.search(SUPPORT[phenotype], text, re.I)
    if not match:
        return False
    clauses = re.split(r';|\s+[-–—]\s+(?=(?:länger|Z\.|Zustand|nach|DD|unklar|fraglich)\b)',text,flags=re.I)
    clause = next((c for c in clauses if re.search(SUPPORT[phenotype],c,re.I)),text)
    if '?' in clause or re.search(r'\b(?:fraglich\w*|unklar\w*)\b',clause,re.I):
        return False
    if re.search(r'\b(?:V\.?\s*a\.|Verdacht\s+auf|suspekt\s+auf|fraglich\w*|m[öo]glich\w*|DD)\s*[:.]?\s*(?:' + SUPPORT[phenotype] + ')', clause, re.I):
        return False
    if phenotype == 'lobar':
        # One explicit local statement must support the location, never borrow a
        # cortical location elsewhere in a sentence about a deep hemorrhage.
        if re.search(r'basalgang|thalam|hirnstamm|brainstem|cerebell|kleinhirn', text, re.I):
            return False
        if re.search(r'blutungsresid|blutungsrest|'+SUPPORT['cmb'], text, re.I):
            return False
    if re.search(r'\b(?:kein\w*|ohne|no)\s+(?:\w+\s+){0,4}(?:' + SUPPORT[phenotype] + ')', text, re.I):
        return False
    return True


def validate_independent_radiology(raw, patient_id, procedure):
    """Separate findings/auxiliary fields after checking the trusted envelope."""
    from copy import deepcopy
    from .radiology_models import Phenotype, AdditionalFindings
    parsed = parse_json(raw)
    if (procedure.modality != 'MRI' or procedure.patient_id != patient_id
            or not isinstance(parsed,dict) or parsed.get('patient_id') != patient_id
            or parsed.get('procedure_id') != procedure.procedure_id):
        raise ValueError('Patient/procedure provenance mismatch')
    if not isinstance(parsed.get('findings'),list) or not isinstance(parsed.get('additional_findings'),dict):
        raise ValueError('Invalid radiology envelope')
    allowed = set(RadiologyExtraction.model_fields)
    if set(parsed)-allowed: raise ValueError('Invalid radiology envelope')
    report = report_text(procedure)
    sentences = list(source_sentences(report))
    neutral = {'caa_suspicion_explicit':None,'caa_evidence':None,
               'microbleed_description':None,'other_findings':[]}
    # Positive explicit imaging claims are deterministic source metadata.
    caa = next((s for s in sentences if CAA_CLAIM.search(normalized_quote(s))
                and not re.search(r'\b(?:kein\w*|ohne|no)\b',s,re.I) and len(s)<=800),None)
    if caa: neutral.update(caa_suspicion_explicit=True,caa_evidence=caa)
    def validate(findings, additional):
        payload = {**parsed,'findings':findings,'additional_findings':additional}
        return validate_radiology(json.dumps(payload),patient_id,procedure)
    accepted, rejected, reconciliation = [], [], []
    for n, raw_record in enumerate(parsed['findings']):
        try:
            item = Phenotype.model_validate_json(json.dumps(raw_record)).model_dump(mode='json')
            model_type = item['type']
            exact = normalized_quote(item['evidence']) in normalized_quote(report)
            if exact and item['type'] in ('other','unknown','not specified'):
                matches = [t for t in ('cmb','css','lobar') if explicit_phenotype_span(t,item['evidence'])]
                if len(matches) == 1:
                    item['type']=matches[0]
                    reconciliation.append({'index':n,'model_type':model_type,'normalized_type':item['type'],
                        'normalization_rule':'explicit_mri_phenotype_v1','exact_evidence':item['evidence'],
                        'normalization_method':'deterministic_phenotype_rule'})
            if not exact and item['type']=='cmb':
                # Candidate selects a phenotype; an explicit positive source statement is the evidence.
                for span in sentences:
                    if len(span)>800 or not re.search(SUPPORT['cmb'],span,re.I): continue
                    candidate = {**item,'evidence':span}
                    try: validate([candidate],neutral)
                    except ValueError: continue
                    item=candidate
                    reconciliation.append({'index':n,'model_evidence':raw_record['evidence'],
                        'source_evidence':span,'normalization_method':'explicit_source_phenotype_span'})
                    break
            checked = validate([item],neutral)
            accepted.extend(checked['findings'])
        except (ValueError,TypeError,KeyError) as exc:
            rejected.append({'field':f'findings[{n}]','record':raw_record,'rejection_reason':str(exc)})
    # Enumerate exact focused-MRI facts independently of model coverage. The
    # ordinary strict validator still owns quotation, enums, acuity and provenance.
    for phenotype in ('cmb','css','lobar'):
        if any(item['type']==phenotype for item in accepted):
            continue
        for span in sentences:
            if len(span)>800 or not explicit_phenotype_span(phenotype,span):
                continue
            candidate = {'type':phenotype,'acute_lesion':None,'evidence':span}
            try:
                checked = validate([candidate],neutral)
            except ValueError:
                continue
            accepted.extend(checked['findings'])
            reconciliation.append({'model_type':None,'normalized_type':phenotype,
                'normalization_rule':'explicit_mri_phenotype_v1','exact_evidence':span,
                'normalization_method':'deterministic_source_candidate'})
            break
    additional = deepcopy(neutral)
    raw_additional = parsed['additional_findings']
    if set(raw_additional) != set(AdditionalFindings.model_fields):
        raise ValueError('Invalid additional findings envelope')
    # The CAA pair is one unit; independently of phenotype persistence.
    pair = {k:raw_additional[k] for k in ('caa_suspicion_explicit','caa_evidence')}
    if caa:
        if pair != {k:neutral[k] for k in pair}:
            reconciliation.append({'field':'caa','model_value':pair,'source_evidence':caa,
                                   'normalization_method':'explicit_source_caa_claim'})
    else:
        try:
            checked = validate([],{**neutral,**pair})
            additional.update(pair)
        except (ValueError,TypeError) as exc:
            rejected.append({'field':'additional_findings.caa','record':pair,'rejection_reason':str(exc)})
    micro = raw_additional['microbleed_description']
    try:
        validate([],{**neutral,'microbleed_description':micro})
        additional['microbleed_description']=micro
    except (ValueError,TypeError) as exc:
        rejected.append({'field':'additional_findings.microbleed_description','record':micro,'rejection_reason':str(exc)})
    others = raw_additional['other_findings']
    if not isinstance(others,list) or len(others)>30: raise ValueError('Invalid other findings list')
    for n, quote in enumerate(others):
        try:
            validate([],{**neutral,'other_findings':[quote]})
            additional['other_findings'].append(quote)
        except (ValueError,TypeError) as exc:
            rejected.append({'field':f'additional_findings.other_findings[{n}]','record':quote,'rejection_reason':str(exc)})
    event = validate(accepted,additional)
    # A rejected phenotype with no valid phenotype cannot be disguised as verified empty.
    accepted_count = len(event['findings']) + sum(v is not None for k,v in additional.items() if k!='other_findings') + len(additional['other_findings'])
    status = ('PARTIAL_VALID' if accepted_count else 'FAILED_VALIDATION') if rejected else 'SUCCESS'
    return {**event,'rejected_records':rejected,'validation_status':status,
            'evidence_reconciliation':reconciliation}
