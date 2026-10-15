"""Exact numbered diagnosis spans and conservative event/date associations.

Offsets always address the selected section; no fuzzy quotation repair is used.
"""
from dataclasses import dataclass, asdict
import json
import re
import unicodedata

from .models import ClinicalHistoryExtraction
from .validators import parse_json, validate_output, supported_disease_text

POLICY_VERSION = 'source_diagnosis_items_v2_date_ownership'
NUMBER = re.compile(r'(?m)^[ \t]*\d{1,2}[.)][ \t]+(?=\S)')
CONCEPT = re.compile(r'\b(?:intracerebral\w*\s+(?:Blutung|Hämorrhagie)|Hirnblutung)\b', re.I)


@dataclass
class DiagnosisItem:
    item_id: str
    section_id: str
    source_document_id: int
    page_number: int
    start: int
    end: int
    source_text: str
    item_source_text: str
    clause_start: int
    clause_end: int


def diagnosis_items(sections):
    items = []
    for section in sections:
        if not re.search(r'\bDiagnos\w*', section.heading or section.text[:80], re.I):
            continue
        matches = list(NUMBER.finditer(section.text))
        for n, match in enumerate(matches):
            end = matches[n+1].start() if n+1 < len(matches) else len(section.text)
            start = match.end()
            footer = re.search(r'(?im)^\s*(?:Universitätsklinikum|Gemeinnützige|Universität\s|Seite\s+\d|SALZBURGER\s+LANDESKLINIK)', section.text[start:end])
            if footer: end = start+footer.start()
            while end > start and section.text[end-1].isspace(): end -= 1
            original = section.text[match.start():end]
            clause = section.text[start:end]
            # Only repeated explicit ICH concepts, never a general conjunction splitter.
            conjunction = re.search(r'\s+sowie\s+', clause, re.I)
            spans = [(start, end)]
            if conjunction:
                left, right = clause[:conjunction.start()], clause[conjunction.end():]
                a, b = CONCEPT.search(left), CONCEPT.search(right)
                if a and b and a.group().casefold() == b.group().casefold():
                    spans = [(start, start+conjunction.start()), (start+conjunction.end(), end)]
            for k, (a, b) in enumerate(spans):
                items.append(DiagnosisItem(f'{section.section_id}_item{n+1:02d}_clause{k+1:02d}',
                    section.section_id, section.source_document_id, section.page_start,
                    match.start(), end, section.text[a:b], original, a, b))
    return items


def normalized_label(text):
    value = unicodedata.normalize('NFKC', supported_disease_text(text))
    value = re.sub(r'^\s*\d+[.)]\s*', '', value)
    # Explicit grammatical variant for the diagnosed CAA concept, not fuzzy spelling.
    value = re.sub(r'\bzerebralen\b', 'zerebrale', value)
    return ' '.join(re.sub(r'[^\w\s]', ' ', value).split())


def evidence_span(item):
    """Keep the full item in provenance; evidence is a bounded contiguous prefix.

    End on a source line boundary, never join separate pieces or rewrite text.
    A disease outside this excerpt still fails the ordinary evidence validator.
    """
    text = item.source_text
    if len(text)<=600: return text
    end = text.rfind('\n',0,601)
    if end<=0: raise ValueError('Diagnosis source line exceeds evidence limit')
    return text[:end].rstrip()


def validate_independent(raw, patient_id, sections):
    """Global identity errors abort; individual evidence/schema errors are audited."""
    parsed = parse_json(raw)
    if not isinstance(parsed, dict) or parsed.get('patient_id') != patient_id:
        raise ValueError('Patient provenance mismatch')
    if set(parsed) != {'patient_id', 'clinical_history'} or not isinstance(parsed['clinical_history'], list):
        raise ValueError('Invalid clinical history envelope')
    by_id = {i.item_id: i for i in diagnosis_items(sections)}
    by_section = {s.section_id:s for s in sections}
    accepted, rejected, metadata = [], [], []
    for index, record in enumerate(parsed['clinical_history']):
        if isinstance(record,dict) and 'item_id' in record and record['item_id'] not in by_id:
            raise ValueError('Unknown source item identity')
        if isinstance(record,dict) and 'item_id' not in record and not by_id:
            section = by_section.get(record.get('section_id'))
            if (section is None or section.patient_id != patient_id
                    or record.get('source_document_id') != section.source_document_id
                    or record.get('page_number') != section.page_start):
                raise ValueError('Section provenance mismatch')
        try:
            if not isinstance(record, dict): raise ValueError('Invalid diagnosis record')
            if by_id and 'item_id' not in record:
                raise ValueError('Numbered diagnoses require source item identity; model quotation is not evidence')
            if 'item_id' in record:
                if set(record) != {'item_id', 'disease', 'date_disease'}:
                    raise ValueError('Invalid source-item record schema')
                item = by_id.get(record['item_id'])
                if item is None: raise ValueError('Unknown source item identity')
                disease = record['disease']
                if not isinstance(disease, str) or not normalized_label(disease): raise ValueError('Invalid disease label')
                if normalized_label(disease) not in normalized_label(item.source_text):
                    raise ValueError('Disease not supported by exact source item')
                from .date_attribution import attribute
                day, ownership = attribute(item.source_text, disease, offset=item.clause_start, item_id=item.item_id)
                partial = ownership['partial_date_text']
                value = dict(disease=disease, date_disease=day, disease_category=None,
                    source_document_id=item.source_document_id, page_number=item.page_number,
                    section_id=item.section_id, source_text=evidence_span(item))
                # Same strict schema and provenance checks after source binding.
                result = validate_output(json.dumps({'patient_id':patient_id,'clinical_history':[value]}),patient_id,sections)
                metadata.append({**asdict(item), 'partial_date_text':partial,
                    'record':result.clinical_history[0].model_dump(mode='json'), 'date_validation':ownership,
                    'evidence_source_text':value['source_text'], 'evidence_start':item.clause_start,
                    'evidence_end':item.clause_start+len(value['source_text']),
                    'model_date_disease':record['date_disease'], 'normalized_date_disease':day,
                    'evidence_method':'exact_numbered_source_item', 'policy_version':POLICY_VERSION})
            else:
                # Non-numbered narrative sections retain the strict quotation path.
                from .date_attribution import attribute
                value = dict(record)
                # Validate the original exact quotation/provenance before deriving any date.
                value['date_disease'] = None
                result = validate_output(json.dumps({'patient_id':patient_id,'clinical_history':[value]}),patient_id,sections)
                section = by_section[value['section_id']]
                start = section.text.find(value['source_text'])
                if start < 0:
                    # The ordinary validator permits whitespace normalization.
                    # Re-anchor only that exact token sequence, retaining original
                    # contiguous source whitespace and offsets for attribution.
                    pattern = r'\s+'.join(re.escape(t) for t in value['source_text'].split())
                    matches = list(re.finditer(pattern,section.text))
                    if len(matches)!=1: raise ValueError('Date attribution requires unique contiguous source evidence')
                    start = matches[0].start()
                    value['source_text'] = matches[0].group()
                day, ownership = attribute(value['source_text'],value['disease'],offset=start)
                value['date_disease'] = day
                result = validate_output(json.dumps({'patient_id':patient_id,'clinical_history':[value]}),patient_id,sections)
                metadata.append({'record':result.clinical_history[0].model_dump(mode='json'),
                    'date_validation':ownership, 'partial_date_text':ownership['partial_date_text'],
                    'model_date_disease':record.get('date_disease'), 'normalized_date_disease':day,
                    'evidence_source_text':value['source_text'],'evidence_start':start,
                    'evidence_end':start+len(value['source_text']),'policy_version':POLICY_VERSION})
            accepted.extend(result.clinical_history)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({'index':index, 'record':record, 'rejection_reason':str(exc)})
    return ClinicalHistoryExtraction(patient_id=patient_id, clinical_history=accepted), rejected, metadata
