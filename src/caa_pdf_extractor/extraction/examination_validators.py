import json
from datetime import date
from .examination_models import ExaminationExtraction
from .validators import parse_json
from .stenosis import supported_categories


def whitespace(value):
    return ' '.join(value.split())


def validate_examinations(raw, item):
    result = ExaminationExtraction.model_validate_json(json.dumps(parse_json(raw)))
    if result.patient_id != item.patient_id or result.examination_id != item.examination_id:
        raise ValueError('Patient/examination provenance mismatch')
    records, classification_reconciliation = [], []
    for record in result.records:
        prepared = {'source_document_id': item.source_document_id, 'section_id': item.section_id,
                    'page_number': item.page_number, 'type': item.type, 'location': item.location,
                    'date_examination': date.fromisoformat(item.date_examination) if item.date_examination else None}
        for name, expected in prepared.items():
            if name in record.model_fields_set and getattr(record, name) != expected:
                raise ValueError('Examination metadata not supported by source')
            setattr(record, name, expected)
        quotation = record.evidence if record.evidence is not None else record.result
        source, evidence, value = whitespace(item.text), whitespace(quotation), whitespace(record.result)
        if not value or not evidence or evidence not in source or value not in evidence:
            raise ValueError('Unsupported source quotation')
        if value == whitespace(item.header): raise ValueError('Header alone is not an examination result')
        if record.type != item.type or record.location != item.location:
            raise ValueError('Examination type/location not supported by header')
        expected_date = item.date_examination
        if (record.date_examination.isoformat() if record.date_examination else None) != expected_date:
            raise ValueError('Examination date not supported by header')
        applicable = item.type == 'ultrasound' and item.location in ('neck vessels','brain')
        categories = supported_categories(source) if applicable else set()
        if len(categories)>1:
            raise ValueError('Multiple explicit stenosis categories; scalar mapping is ambiguous')
        chosen = next(iter(categories)) if categories else None
        if chosen is not None and chosen not in supported_categories(evidence):
            raise ValueError('Explicit stenosis category missing from supporting quotation')
        if record.neck_vessel_result is not None and record.neck_vessel_result != chosen:
            raise ValueError('Neck-vessel category not explicitly supported; normal findings must stay NULL')
        if record.neck_vessel_result != chosen:
            classification_reconciliation.append({'model_value':record.neck_vessel_result,
                                                 'normalized_value':chosen,'rule_version':'explicit_stenosis_v1'})
        record.neck_vessel_result = chosen
        record.result = value
        record.evidence = quotation
        records.append(record.model_dump(mode='json'))
    return {**item.context(), 'records': records, 'neck_vessel_reconciliation':classification_reconciliation}


def record_key(record):
    """Identity within one case among non-deleted rows; result is payload."""
    return (record['date_examination'], record['type'], record['location'],
            record['neck_vessel_result'])
