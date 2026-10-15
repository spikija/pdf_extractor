"""Strict records and conservative source-grounded family attribution."""
import json
import re
from pydantic import BaseModel, ConfigDict, Field
from .family_history_inputs import FAMILY, KINSHIP
from .validators import parse_json

RULE_VERSION = 'kinship_v2'
CATEGORIES = ('first grade', 'second grade', 'unknown', 'not specified')
FIRST = re.compile(r'\b(?:Mutter|Vater|Tochter|Sohn|Schwestern?|Bruder|Brüder|Geschwister|mother|father|daughter|son|sister|brother|sibling)\b', re.I)
SECOND = re.compile(r'\b(?:Großmutter|Grossmutter|Großvater|Grossvater|Tante|Onkel|Nichte|Neffe|Enkel\w*|grandmother|grandfather|grandparent|aunt|uncle|niece|nephew|grandchild)\b', re.I)
NEGATIVE = re.compile(r'\b(?:negativ\w*|negative|unauffällig|unauffaellig|keine?\b|kein\w*|ohne|verneint|gesund\w*|healthy|no|denies|none)\b', re.I)
GENETIC = re.compile(r'\b(?:APOE|genotyp\w*|genetic\s+test|Gentest|Alle[l]?\w*)\b', re.I)
PATIENT = re.compile(r'\b(?:Patient\w*|patient|Eigenanamnese)\b', re.I)
UNCERTAIN = re.compile(r'\b(?:vielleicht|möglicherweise|unklar\w*|unsicher\w*|possibly|unclear|oder|or)\b', re.I)


def normalize(value):
    return ' '.join(value.split()) if value is not None else None


def relative_category(relative):
    if relative is None:
        return 'not specified'
    if re.search(r'\bVerwandt\w*\b', relative, re.I):
        if re.search(r'\b(?:ersten|1\.)\s+Grades\b', relative, re.I):
            return 'first grade'
        if re.search(r'\b(?:zweiten|2\.)\s+Grades\b', relative, re.I):
            return 'second grade'
    # No project convention exists for half-siblings; do not infer a degree.
    if re.search(r'\b(?:Halb\w*|half[- ]?\w*)\b', relative, re.I):
        return 'unknown'
    first_matches, second_matches = FIRST.findall(relative), SECOND.findall(relative)
    first, second = bool(first_matches), bool(second_matches)
    if (first and second) or len(first_matches) + len(second_matches) > 1:
        raise ValueError('Ambiguous kinship mapping')
    return 'first grade' if first else 'second grade' if second else 'unknown'


class FamilyHistoryRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    relative: str | None = Field(max_length=300)
    relative_category: str | None = Field(json_schema_extra={'enum': [*CATEGORIES, None]})
    disease: str = Field(min_length=1, max_length=1000)
    source_document_id: int = Field(gt=0)
    page_number: int = Field(gt=0)
    section_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1, max_length=4000)


class FamilyHistoryExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    patient_id: str
    family_history: list[FamilyHistoryRecord] = Field(max_length=100)


def record_key(record):
    return tuple(normalize(record[field]).casefold() if record[field] is not None else None
                 for field in ('relative', 'disease', 'relative_category'))


def validate_family_history(raw, patient_id, inputs):
    extraction = FamilyHistoryExtraction.model_validate_json(json.dumps(parse_json(raw)))
    if extraction.patient_id != patient_id:
        raise ValueError('Patient provenance mismatch')
    records, reconciliations = [], []
    for record in extraction.family_history:
        evidence = normalize(record.evidence)
        source = [i for i in inputs if i.source_document_id == record.source_document_id
                  and i.section_id == record.section_id and i.page_number == record.page_number
                  and evidence in normalize(i.text)]
        if not evidence or not source:
            raise ValueError('Unsupported source quotation/provenance')
        relative, disease = normalize(record.relative), normalize(record.disease)
        if not disease or disease not in evidence or (relative is not None and (not relative or relative not in evidence)):
            raise ValueError('Disease/relation must retain exact source wording')
        if relative is not None and not KINSHIP.search(relative):
            raise ValueError('Relative is not an explicitly supported family relationship')
        family_context = any(i.family_context for i in source)
        # Both facts must be attributed together, not cherry-picked across clauses.
        clauses = re.split(r'[.!?;]|\b(?:aber|but|während|whereas)\b', evidence)
        supported = [c for c in clauses if disease in c and (relative in c if relative else (FAMILY.search(c) or family_context))]
        if not supported or not any(not (NEGATIVE.search(c) or GENETIC.search(c) or PATIENT.search(c) or UNCERTAIN.search(c))
                and (KINSHIP.search(c) or FAMILY.search(c) or family_context) for c in supported):
            raise ValueError('No positive family-attributed disease evidence')
        category = relative_category(relative)
        if relative is not None and category == 'unknown' and re.search(r'\b(?:Verwandt\w*|Familie|Angehörige)\b', relative, re.I):
            raise ValueError('Vague relative degree is not explicit')
        if record.relative_category is not None and record.relative_category != category:
            raise ValueError('Unsupported relative category')
        if record.relative_category != category:
            reconciliations.append({'relative': relative, 'model_value': record.relative_category,
                                    'normalized_value': category, 'rule_version': RULE_VERSION})
        record.relative, record.disease, record.relative_category = relative, disease, category
        records.append(record.model_dump())
    return {'patient_id': patient_id, 'family_history': records,
            'relative_category_reconciliation': reconciliations,
            'negative_statements': [i.context() for i in inputs if FAMILY.search(i.text) and NEGATIVE.search(i.text)]}
