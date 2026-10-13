"""Strict source support and deterministic metadata reconciliation for MRI only."""
import json
import re
from .radiology_models import RadiologyExtraction
from .radiology_inputs import report_text, procedure_context, examination_date
from .validators import parse_json

HEMORRHAGE = r'\b(?:h[äa]matom\w*|hematom\w*|blutung\w*|h[äa]morrhag\w*|hemorrhag\w*|haemorrhag\w*|basalganglienblutung\w*)\b'
SUPPORT = {
    'lobar': rf'(?=.*{HEMORRHAGE})(?=.*(?:frontal|parietal|temporal|okzipital|occipital|lob[äa]r|kortikal|cortical|mantelkante))',
    'basal ganglia': rf'(?=.*{HEMORRHAGE})(?=.*(?:basalgang|basal gang|putamen|globus pallidus|nucleus caudatus))',
    'subarachnoidal bleeding': rf'(?=.*{HEMORRHAGE})(?=.*(?:subarachnoid|subarachnoidal))',
    'cmb': r'(?:mikroblutung|mikroblutungen|microbleed|microbleeds)',
    'css': r'(?:oberfl[äa]ch\w*\s+sideros|superficial\s+sideros|kortikal\w*\s+sideros)',
    'other': r'\b(?:other|sonstiger ph[äa]notyp)\b',
    'unknown': r'\b(?:unknown|unbekannter ph[äa]notyp)\b',
    'not specified': r'\b(?:not specified|nicht spezifizierter ph[äa]notyp)\b',
}


def normalized_quote(text):
    return ' '.join(text.split())


def phenotype_key(mri_date, finding):
    return (mri_date, finding.type, finding.acute_lesion)


ACUITY_RULE_VERSION = 'phenotype_specific_v1'
NON_ACUTE = re.compile(
    r'\b(?:alt(?:e[nmrs]?)?|chronisch\w*|residu\w*|sequel\w*|abgelaufen\w*|'
    r'h[äa]mosiderin(?:rest\w*|resid\w*)|hemosiderin\s+(?:residu\w*|remnant\w*)|'
    r'fr[üu]her\w*|vorbestehend\w*|zur[üu]ckliegend\w*|posth[äa]morrhag\w*|'
    r'chronic|old|remote|previous)\b|\bZustand\s+nach\b|\bZ\.\s*n\.', re.I)
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
    boundaries = [m.end() for m in re.finditer(r'[.!?]\s+(?=[A-ZÄÖÜ])', report)]
    left = max([0] + [b for b in boundaries if b <= start])
    right = min([len(report)] + [b for b in boundaries if b >= end])
    return report[left:right]


def derive_acuity(phenotype, evidence, report):
    """Apply the user's phenotype conventions, never trust the model's bool."""
    if phenotype in ('cmb', 'css'):
        return False
    context = evidence_context(evidence, report)
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
    if (re.search(r'\bVerdacht\s+auf\s+Amyloidangiopathie\b', report, re.I)
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
        if additional.caa_suspicion_explicit is True and not re.search(r'verdacht|suspekt|suspic|v\.?\s*a\.?', quote, re.I):
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
