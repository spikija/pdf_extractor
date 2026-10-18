"""Strict records and conservative source-grounded family attribution."""
import json
import re
from pydantic import BaseModel, ConfigDict, Field
from .family_history_inputs import FAMILY, KINSHIP
from .validators import parse_json

RULE_VERSION = 'kinship_v4_patient_paths_exact_anchor'
CATEGORIES = ('first grade', 'second grade', 'unknown', 'not specified')
FIRST = re.compile(r'\b(?:Mutter|Vater|Tochter|Sohn|Schwestern?|Bruder|Brüder|Geschwister|mother|father|daughters?|sons?|sisters?|brothers?|siblings?)\b', re.I)
SECOND = re.compile(r'\b(?:Großmutter|Grossmutter|Großvater|Grossvater|Großeltern|Grosseltern|Tante|Onkel|Nichte|Neffe|Enkel\w*|grandmother|grandfather|grandparents?|aunts?|uncles?|nieces?|nephews?|grandchild(?:ren)?)\b', re.I)
NEGATIVE = re.compile(r'\b(?:negativ\w*|negative|unauffällig|unauffaellig|keine?\b|kein\w*|ohne|verneint|gesund\w*|healthy|no|denies|none)\b', re.I)
GENETIC = re.compile(r'\b(?:APOE|genotyp\w*|genetic\s+test|Gentest|Alle[l]?\w*)\b', re.I)
PATIENT = re.compile(r'\b(?:Patient\w*|patient|Eigenanamnese)\b', re.I)
UNCERTAIN = re.compile(r'\b(?:vielleicht|möglicherweise|unklar\w*|unsicher\w*|possibly|unclear|oder|or)\b', re.I)


def normalize(value):
    return ' '.join(value.split()) if value is not None else None


def relative_category(relative):
    if relative is None:
        return 'not specified'
    if re.fullmatch(r'(?:Brüder|Bruder)\s+des\s+Vaters|Schwester\s+der\s+Mutter|Sohn\s+des\s+Bruders|father[’\x27]s\s+brothers?|mother[’\x27]s\s+sisters?|brother[’\x27]s\s+son',relative,re.I):
        return 'second grade'
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


def resolve_relative(relative,evidence):
    """Patient-perspective paths, independent of model degree and verbatim text."""
    if relative is None:return relative,relative,None
    paths=[(r'(?:Brüder|Bruder)\s+des\s+Vaters|Vater\s+und\s+dessen\s+Brüder',r'Bruder|Brüder','Onkel'),
           (r'Schwester\s+der\s+Mutter|Mutter\s+und\s+deren\s+Schwester',r'Schwester','Tante'),
           (r'Sohn\s+des\s+Bruders',r'Sohn','Neffe'),
           (r'father[’\x27]s\s+brothers?',r'brothers?','uncle'),
           (r'mother[’\x27]s\s+sisters?',r'sisters?','aunt'),
           (r'brother[’\x27]s\s+son',r'son','nephew')]
    for pattern,inner,target in paths:
        m=re.search(pattern,evidence,re.I)
        if m and (re.fullmatch(inner,relative,re.I) or normalize(relative)==normalize(m.group())):
            source=m.group() if normalize(relative)==normalize(m.group()) else re.search(inner,m.group(),re.I).group()
            return target,source,'PATIENT_KINSHIP_PATH'
    if relative=='Bruder' and not re.search(r'\bBruder\b',evidence) and re.search(r'\bBrüder\b',evidence):
        return relative,'Brüder','EXPLICIT_PLURAL_RELATIVE'
    return relative,relative,None


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
        model_relative=relative
        relative,source_relative,relationship_rule=resolve_relative(relative,evidence)
        if not disease or disease not in evidence or (source_relative is not None and (not source_relative or source_relative not in evidence)):
            raise ValueError('Disease/relation must retain exact source wording')
        if relative is not None and not KINSHIP.search(relative):
            raise ValueError('Relative is not an explicitly supported family relationship')
        family_context = any(i.family_context for i in source)
        # Both facts must be attributed together, not cherry-picked across clauses.
        # A separately negated brain-operation history does not negate the
        # explicitly family-attributed stroke preceding that comma. Evidence
        # itself must still match the complete selected source exactly.
        clauses = re.split(r'[.!?;]|,\s*(?=keine Operationen am Gehirn\b)|\b(?:aber|but|während|whereas)\b', evidence)
        supported = [c for c in clauses if disease in c and (source_relative in c if source_relative else (FAMILY.search(c) or family_context))]
        if not supported or not any(not (NEGATIVE.search(c) or GENETIC.search(c) or PATIENT.search(c) or UNCERTAIN.search(c))
                and (KINSHIP.search(c) or FAMILY.search(c) or family_context) for c in supported):
            raise ValueError('No positive family-attributed disease evidence')
        category = relative_category(relative)
        if relative is not None and category == 'unknown' and re.search(r'\b(?:Verwandt\w*|Familie|Angehörige)\b', relative, re.I):
            raise ValueError('Vague relative degree is not explicit')
        if record.relative_category is not None and record.relative_category != category and not (relationship_rule=='PATIENT_KINSHIP_PATH' and record.relative_category in CATEGORIES):
            raise ValueError('Unsupported relative category')
        if record.relative_category != category:
            reconciliations.append({'relative': relative, 'model_value': record.relative_category,
                                    'normalized_value': category, 'rule_version': RULE_VERSION})
        if relationship_rule:
            reconciliations.append({'model_relative':model_relative,'source_relative_text':source_relative,
                'normalized_relative':relative,'normalized_category':category,'normalization_method':relationship_rule,
                'exact_evidence':record.evidence})
        record.relative, record.disease, record.relative_category = relative, disease, category
        records.append(record.model_dump())
    return {'patient_id': patient_id, 'family_history': records,
            'relative_category_reconciliation': reconciliations,
            'negative_statements': [i.context() for i in inputs if FAMILY.search(i.text) and NEGATIVE.search(i.text)]}


def validate_independent_family_history(raw,patient_id,inputs):
    parsed=parse_json(raw)
    if not isinstance(parsed,dict) or parsed.get('patient_id')!=patient_id:raise ValueError('Patient provenance mismatch')
    if set(parsed)!={'patient_id','family_history'} or not isinstance(parsed['family_history'],list):raise ValueError('Invalid family-history envelope')
    normalized=validate_family_history(json.dumps({'patient_id':patient_id,'family_history':[]}),patient_id,inputs)
    normalized['source_reconciliation']=[]
    rejected=[]
    for index,record in enumerate(parsed['family_history']):
        if isinstance(record,dict):
            evidence=normalize(record.get('evidence')) if isinstance(record.get('evidence'),str) else None
            identity_exists=any(i.source_document_id==record.get('source_document_id') and i.section_id==record.get('section_id') and i.page_number==record.get('page_number') for i in inputs)
            anchored=any(i.source_document_id==record.get('source_document_id') and i.section_id==record.get('section_id') and i.page_number==record.get('page_number') and evidence and evidence in normalize(i.text) for i in inputs)
            matches=sum(normalize(i.text).count(evidence) for i in inputs) if evidence else 0
            if not anchored and (matches>1 or (not identity_exists and matches!=1)):
                raise ValueError('Section provenance mismatch or ambiguous exact source')
        try:
            value=dict(record) if isinstance(record,dict) else record
            resolution=None
            if isinstance(value,dict) and isinstance(value.get('evidence'),str):
                evidence=normalize(value['evidence'])
                anchored=[i for i in inputs if i.source_document_id==value.get('source_document_id') and i.section_id==value.get('section_id') and i.page_number==value.get('page_number') and evidence in normalize(i.text)]
                if evidence and not anchored:
                    locations=[i for i in inputs for _ in range(normalize(i.text).count(evidence))]
                    if len(locations)!=1:raise ValueError('Exact family source location is absent or ambiguous')
                    source=locations[0]
                    resolution={'model_section_id':value.get('section_id'),'resolved_section_id':source.section_id,
                        'model_source_document_id':value.get('source_document_id'),'resolved_source_document_id':source.source_document_id,
                        'model_page_number':value.get('page_number'),'resolved_page_number':source.page_number,
                        'resolution_method':'UNIQUE_EXACT_SOURCE_MATCH','exact_evidence':value['evidence']}
                    value.update(source_document_id=source.source_document_id,section_id=source.section_id,page_number=source.page_number)
            result=validate_family_history(json.dumps({'patient_id':patient_id,'family_history':[value]}),patient_id,inputs)
            normalized['family_history'].extend(result['family_history'])
            normalized['relative_category_reconciliation'].extend(result['relative_category_reconciliation'])
            if resolution:normalized['source_reconciliation'].append(resolution)
        except (ValueError,TypeError) as exc:
            rejected.append({'index':index,'record':record,'rejection_reason':str(exc) if type(exc) is ValueError else 'SCHEMA_VALIDATION_FAILED'})
    normalized['rejected_records']=rejected
    return normalized
