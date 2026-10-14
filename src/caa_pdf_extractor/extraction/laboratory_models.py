"""Decimal parsing, strict quotations and explicit persistence blockers."""
from datetime import date
from decimal import Decimal
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .laboratory_inputs import MATCHERS, DATE
from .validators import parse_json

RULE_VERSION = 'laboratory_decimal_units_v1'
FIELDS = tuple(MATCHERS)
NUMBER = re.compile(r'^[+-]?\d+(?:[.,]\d+)?$')


def normalize(value):
    return ' '.join(value.split())


class Measurement(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    candidate_id: str
    biomarker: Literal['csf_beta42', 'csf_beta4240_ratio', 'csf_tau_total', 'csf_ptau_total', 'crp', 'hba1c']
    raw_numeric_string: str = Field(min_length=1, max_length=100)
    unit: str = Field(max_length=100)
    evidence: str = Field(min_length=1, max_length=4000)
    date_of_exam: str | None
    role: Literal['single', 'first', 'last', 'minimum', 'maximum', 'unresolved']


class LaboratoryExtraction(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    patient_id: str
    measurements: list[Measurement] = Field(max_length=100)


def parse_decimal(raw):
    # Strings only: never parse clinical decimals through binary floats.
    if not isinstance(raw, str) or not NUMBER.fullmatch(raw):
        raise ValueError('Unsupported raw numeric string')
    value = Decimal(raw.replace(',', '.'))
    if not value.is_finite() or value < 0:
        raise ValueError('Invalid laboratory numeric value')
    return value


def signature(record):
    return (record['date_of_exam'], *(Decimal(record[name]) if record[name] is not None else None for name in FIELDS))


def validate_laboratory(raw, patient_id, inputs):
    extraction = LaboratoryExtraction.model_validate_json(json.dumps(parse_json(raw)))
    if extraction.patient_id != patient_id:
        raise ValueError('Patient provenance mismatch')
    candidates = {i.candidate_id: i for i in inputs}
    measurements, observations, issues = [], [], []
    seen = set()
    percent_keys = set()
    for m in extraction.measurements:
        i = candidates.get(m.candidate_id)
        if i is None or m.biomarker != i.biomarker:
            raise ValueError('Candidate/biomarker provenance mismatch')
        evidence, unit = normalize(m.evidence), normalize(m.unit)
        if not evidence or evidence != normalize(i.text):
            raise ValueError('Unsupported exact laboratory evidence')
        if unit and unit not in evidence:
            raise ValueError('Unsupported source unit quotation')
        value = parse_decimal(m.raw_numeric_string)
        marker = MATCHERS[m.biomarker].search(i.text)
        # Exclude label digits, reference ranges, date digits, and values before unit.
        tail = i.text[marker.end():]
        if unit:
            if unit not in tail:
                raise ValueError('Unit not associated with candidate row')
            before_unit = re.sub(r'^\s*\(IFCC\)\s*', '', tail[:tail.index(unit)], flags=re.I).strip(' :=')
            after_unit = tail[tail.index(unit) + len(unit):]
            # Ordinary prose is value then unit; grid rows are unit then values.
            tail = before_unit if before_unit == m.raw_numeric_string else after_unit
        tail = DATE.sub('', tail)
        token = re.compile(r'(?<![\d.,<>\-])' + re.escape(m.raw_numeric_string) + r'(?![\d.,\-])')
        if not token.search(tail):
            raise ValueError('Value missing from measurement cells (reference ranges excluded)')
        if m.date_of_exam != i.associated_date:
            raise ValueError('Date not explicitly associated with laboratory measurement')
        if m.date_of_exam is not None:
            date.fromisoformat(m.date_of_exam)
        if not i.summary_layout and m.role not in ('single', 'unresolved'):
            raise ValueError('Summary role not supported by source header')
        identity = (m.candidate_id, m.raw_numeric_string, m.role)
        if identity in seen:
            continue
        seen.add(identity)
        entry = {**m.model_dump(), 'parsed_decimal': str(value),
                 'source_document_id': i.source_document_id, 'page_number': i.page_number,
                 'section_id': i.section_id, 'evidence': m.evidence, 'audit_only': False,
                 'persistable': True, 'persistence_issues': []}
        if m.biomarker == 'hba1c' and unit == '%' and m.role not in ('minimum', 'maximum'):
            percent_keys.add((i.source_document_id, i.section_id, m.role, m.date_of_exam))
        measurements.append(entry)
    grouped = {}
    for entry in measurements:
        i = candidates[entry['candidate_id']]
        unit = entry['unit'].casefold().replace(' ', '')
        biomarker = entry['biomarker']
        problems = entry['persistence_issues']
        if entry['role'] in ('minimum', 'maximum'):
            entry['audit_only'] = True
        if biomarker == 'hba1c' and (unit in ('mmol/mol', 'mmol/m') or 'IFCC' in i.text.upper()):
            entry['audit_only'] = True
            key = (i.source_document_id, i.section_id, entry['role'], entry['date_of_exam'])
            if entry['role'] not in ('minimum', 'maximum') and key not in percent_keys:
                problems.append('UNIT_MISMATCH: IFCC-only HbA1c requires an approved conversion')
        elif biomarker == 'crp' and unit != 'mg/dl':
            problems.append('UNIT_MISMATCH: CRP requires explicit mg/dL')
        elif biomarker == 'hba1c' and unit != '%':
            problems.append('UNIT_MISMATCH: HbA1c requires explicit percent')
        elif biomarker == 'csf_beta4240_ratio' and unit not in ('', 'ratio', 'dimensionless'):
            problems.append('UNIT_MISMATCH: ratio must be dimensionless without scaling')
        elif biomarker.startswith('csf_') and biomarker != 'csf_beta4240_ratio' and unit not in ('pg/ml', 'ng/l', 'ng/ml'):
            problems.append('UNIT_MISMATCH: CSF source unit is not clearly interpretable')
        if not entry['audit_only'] and i.summary_layout:
            # Flattened first/last/min/max columns do not prove a sample/date link.
            problems.append('LAYOUT_ASSOCIATION_UNVERIFIED')
            if entry['date_of_exam'] is None:
                problems.append('DATE_ASSOCIATION_UNRESOLVED')
        if entry['role'] == 'unresolved':
            problems.append('LAYOUT_ASSOCIATION_UNVERIFIED')
        entry['persistable'] = not entry['audit_only'] and not problems
        issues.extend({'candidate_id': entry['candidate_id'], 'issue': p} for p in problems)
        if not entry['audit_only']:
            # Only merge fields with an explicit common date and source group.
            key = (i.source_document_id, i.section_id, entry['date_of_exam'], entry['role']) if entry['date_of_exam'] else (entry['candidate_id'], entry['role'])
            observation = grouped.get(key)
            if observation is None:
                observation = {name: None for name in FIELDS}
                observation['date_of_exam'] = entry['date_of_exam']
                grouped[key] = observation
                observations.append(observation)
            if observation[biomarker] is not None and Decimal(observation[biomarker]) != Decimal(entry['parsed_decimal']):
                issues.append({'candidate_id': entry['candidate_id'], 'issue': 'CONFLICTING_VALUES_IN_OBSERVATION_GROUP'})
                # Preserve the distinct candidate instead of overwriting evidence.
                observation = {name: None for name in FIELDS}
                observation['date_of_exam'] = entry['date_of_exam']
                observations.append(observation)
            observation[biomarker] = entry['parsed_decimal']
    return {'patient_id': patient_id, 'laboratory': observations,
            'measurements': measurements, 'persistence_issues': issues,
            'persistence_allowed': not issues}
