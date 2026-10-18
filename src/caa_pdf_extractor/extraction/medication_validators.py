"""Strict provenance and date checks, without inferred drug classification."""
import json
import re
from .medication_models import MedicationExtraction
from .medication_inputs import normalize_medication, NON_MEDICATION
from .validators import parse_json


def medication_key(item):
    return (normalize_medication(item.medication), item.date_noted,
            item.date_stopped, item.medication_category)


def deduplicate_medications(items):
    result, seen = [], set()
    for item in items:
        key = medication_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _date_supported(value, snippet, kind):
    if value is None:
        return True
    if re.search(r"\b(?:aufnahme\w*|entlass\w*|geburt\w*|dokument\w*|admission|discharge|birth)\b", snippet, re.I):
        return False
    d = value
    date_pattern = rf"(?:{d.isoformat()}|0?{d.day}\.0?{d.month}\.{d.year})"
    label = (r"(?:seit|ab|beginn|begonnen|verordnet|start|date_noted)" if kind == "start"
             else r"(?:bis|abgesetzt|gestoppt|beendet|ende|stop|date_stopped)")
    # Dates must be attached to a start/stop label in the supporting medication
    # line. Admission/discharge dates cannot satisfy this check.
    return bool(re.search(rf"\b{label}\b\s*(?:am|zum|:)?\s*{date_pattern}(?!\d)", snippet, re.I))


def validate_medications(raw, patient_id, inputs):
    parsed = parse_json(raw)
    result = MedicationExtraction.model_validate_json(json.dumps(parsed))
    if result.patient_id != patient_id:
        raise ValueError("Patient provenance mismatch")
    by_id = {item.section_id: item for item in inputs}
    for item in result.medications:
        source = by_id.get(item.section_id)
        if (source is None or source.patient_id != patient_id
                or item.source_document_id != source.source_document_id):
            raise ValueError("Section provenance mismatch")
        snippet = normalize_medication(item.source_text)
        medication = normalize_medication(item.medication)
        if not medication or not snippet or NON_MEDICATION.search(medication):
            raise ValueError("Unsupported medication")
        if item.source_table_index is not None:
            table = next((t for t in source.tables if t['table_index'] == item.source_table_index
                          and t['page_number'] == item.page_number), None)
            if table is None or snippet not in table['source_rows'] or medication != snippet:
                raise ValueError("Medication table quotation mismatch")
        else:
            # Require a single supporting source line, not a concatenation of
            # unrelated lines that could attach another medication's date.
            lines = [normalize_medication(line) for line in source.text.splitlines()]
            if item.page_number != source.page_number or snippet not in lines or medication not in snippet:
                raise ValueError("Medication source quotation mismatch")
            # Do not allow strength or regimen to be truncated from a quoted line.
            if not snippet.startswith(medication):
                raise ValueError("Medication wording mismatch")
            suffix = snippet[len(medication):].strip()
            if suffix and not re.match(r"^(?:[;,]\s*)?(?:seit|ab|beginn|begonnen|verordnet|start|bis|abgesetzt|gestoppt|beendet|ende|stop|category|kategorie)\b", suffix, re.I):
                raise ValueError("Medication instructions omitted")
        if item.medication_category is not None and item.medication_category not in snippet:
            raise ValueError("Medication category not explicit in source")
        if (not _date_supported(item.date_noted, snippet, "start")
                or not _date_supported(item.date_stopped, snippet, "stop")):
            raise ValueError("Medication date not explicitly associated")
        if item.date_noted and item.date_stopped and item.date_stopped < item.date_noted:
            raise ValueError("Medication stop date precedes start date")
        item.medication = medication
    return result


def validate_independent_medications(raw,patient_id,inputs):
    from .radiology_artifacts import digest
    parsed=parse_json(raw)
    if not isinstance(parsed,dict) or parsed.get('patient_id')!=patient_id:raise ValueError('Patient provenance mismatch')
    if set(parsed)!={'patient_id','medications'} or not isinstance(parsed['medications'],list):raise ValueError('Invalid medication envelope')
    accepted=[];rejected=[];metadata=[]
    for index,record in enumerate(parsed['medications']):
        if isinstance(record,dict):
            selected=[s for s in inputs if s.section_id==record.get('section_id') and s.source_document_id==record.get('source_document_id')]
            if len(selected)!=1:raise ValueError('Section provenance mismatch')
            source=selected[0]
            tables=[t for t in source.tables if t['table_index']==record.get('source_table_index') and t['page_number']==record.get('page_number')]
            if record.get('source_table_index') is not None and len(tables)!=1:raise ValueError('Ambiguous medication table identity')
        try:
            if not isinstance(record,dict):raise ValueError('Invalid medication record')
            value=dict(record);anchor=None
            if record.get('source_table_index') is not None:
                table=tables[0]
                matches=[(n,text,cells) for n,(text,cells) in enumerate(zip(table['source_rows'],table['rows'])) if normalize_medication(str(record.get('medication','')))==text]
                if len(matches)!=1:raise ValueError('Medication semantics do not identify one exact source row')
                n,text,cells=matches[0]
                anchor={'source_document_id':source.source_document_id,'section_id':source.section_id,
                    'page_number':table['page_number'],'table_index':table['table_index'],'row_index':n,
                    'source_cells':cells,'source_row':text}
                anchor['row_fingerprint']=digest(anchor)
                value['source_text']=text
            result=validate_medications(json.dumps({'patient_id':patient_id,'medications':[value]}),patient_id,inputs)
            accepted.extend(result.medications)
            metadata.append({'record':result.medications[0].model_dump(mode='json'),'source_row':anchor,
                             'model_source_text':record.get('source_text'),'evidence_method':'exact_table_row' if anchor else 'strict_narrative_quotation'})
        except (ValueError,TypeError,KeyError) as exc:
            rejected.append({'index':index,'record':record,'rejection_reason':str(exc) if type(exc) is ValueError else type(exc).__name__})
    # A bounded medication section can contain a visibly column-aligned schedule
    # without a prepared table. Keep its literal line and every written dose;
    # never fill an absent fourth column or reuse invented model digits.
    for source in inputs:
        if source.tables or not re.search(r'medikation|medikament|medication',source.heading or source.text[:100],re.I):
            continue
        for line in source.text.splitlines():
            text=line.strip()
            if not re.fullmatch(r'[A-ZÄÖÜ][\wÄÖÜäöüß-]*(?:\s+[A-Za-zÄÖÜäöüß-]+){0,3}\s+\d+(?:[.,]\d+)?\s*(?:mg|µg|mcg|g)\s{3,}(?:\d+(?:[.,]\d+|/\d+)?\s+){1,3}\d+(?:[.,]\d+|/\d+)?',text):
                continue
            medication=normalize_medication(text)
            if any(normalize_medication(r.medication)==medication for r in accepted):continue
            value={'medication':medication,'medication_category':None,'date_noted':None,'date_stopped':None,
                'source_document_id':source.source_document_id,'page_number':source.page_number,'section_id':source.section_id,
                'source_table_index':None,'source_text':text}
            result=validate_medications(json.dumps({'patient_id':patient_id,'medications':[value]}),patient_id,inputs)
            accepted.extend(result.medications)
            metadata.append({'record':result.medications[0].model_dump(mode='json'),'source_row':None,
                'model_source_text':None,'evidence_method':'deterministic_narrative_medication_span',
                'normalization_method':'deterministic_source_span','exact_source_span':text,
                'source_start':source.text.find(text),'source_end':source.text.find(text)+len(text)})
    return MedicationExtraction(patient_id=patient_id,medications=accepted),rejected,metadata
