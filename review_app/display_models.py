"""Canonical entities only; source mentions remain linked evidence, never rows."""
from review_app.artifact_repository import layer
from collections import Counter
from caa_pdf_extractor.extraction.assertion_review import checked_evidence


def diagnosis_entities(entry, events=False):
    if events:
        rows = layer(entry, 'canonical_clinical_events')
    else:
        rows = layer(entry,'patient_diagnoses') or layer(entry, 'canonical_disease_identities') or layer(entry, 'canonical_diagnoses')
    result = []
    seen = set()
    frequencies = Counter(r.get('event_id') for r in rows) if events else Counter()
    for row_number, raw in enumerate(rows):
        identity = raw.get('event_id') if events else raw.get('patient_diagnosis_id',raw.get('diagnosis_identity_id'))
        if events and frequencies[identity] > 1:
            # A pipeline collision must not silently hide a source row or share an adjudication key.
            result.append(dict(raw, entity_id=f'{identity}:RAW_ROW:{row_number}',
                canonical_event_id=identity, review_blocked=True,
                pipeline_warning='Duplicate canonical event ID: ambiguous partial-date grouping; source row retained for inspection'))
            continue
        if not identity or identity in seen:
            continue
        if not events:
            ids = set(raw.get('source_evidence_ids', raw.get('evidence_ids', [])))
            linked = [m for m in layer(entry, 'diagnosis_mentions') if m.get('evidence_id') in ids]
            if linked:
                checked = [checked_evidence(m) for m in linked]
                if (raw.get('canonical_name') == 'ATRIAL_FIBRILLATION' or any(m['assertion_status'] != 'SOURCE_ONLY' for m in checked)) and not any(m.get('assertion_status') == 'ESTABLISHED' and m.get('explicit_positive') for m in checked):
                    continue
        seen.add(identity)
        result.append(dict(raw, entity_id=identity,
                           document_count=raw.get('document_count', raw.get('source_document_count'))))
    if not events:
        result.extend(dict(r,entity_id=r['candidate_id'],canonical_name='UNMAPPED_DIAGNOSIS_CANDIDATE',
            status='REVIEW_REQUIRED') for r in layer(entry,'unmapped_diagnosis_candidates'))
    else:
        result.extend(dict(r,entity_id=r['candidate_id']) for r in layer(entry,'clinical_event_candidates'))
        for row in result:row.setdefault('admission_linked',False)
        result.sort(key=lambda r:(r.get('event_date') or '9999',r['entity_id']))
    return result


def admission_entities(entry):
    return [dict(r,entity_id=r['admission_id'],canonical_name=r.get('probable_admission_diagnosis') or 'UNRESOLVED_ADMISSION')
        for r in layer(entry,'admission_clinical_summary')]


def medication_entities(entry):
    mentions = layer(entry, 'medication_mentions')
    exposures = layer(entry, 'medication_exposures')
    canonical = layer(entry, 'canonical_medications')
    groups = {r['drug_identity_id']: dict(r, entity_id=r['drug_identity_id'], exposures=[]) for r in canonical}
    by_mention = {m['mention_id']: m for m in mentions}
    for exposure in exposures:
        mention = by_mention.get(exposure.get('mention_id'), {})
        key = exposure.get('drug_identity_id') or exposure['exposure_id']
        if key not in groups:
            groups[key] = dict(mention, entity_id=key, canonical_drug=mention.get('canonical_drug') or 'UNRESOLVED', exposures=[])
        groups[key]['exposures'].append(dict(mention, **exposure))
    return list(groups.values())


def evidence_for(raw, entry):
    if 'admission_id' in raw:return raw.get('evidence',[])
    if raw.get('candidate_type')=='CLINICAL_EVENT_CANDIDATE':return raw.get('evidence') or [raw]
    if raw.get('candidate_type')=='UNMAPPED_DIAGNOSIS_CANDIDATE':
        return [dict(raw,evidence_id=raw['candidate_id'])]
    if 'exposures' in raw:
        return raw['exposures']
    ids = set(raw.get('source_evidence_ids', raw.get('evidence_ids', [])))
    mentions = layer(entry, 'diagnosis_mentions')
    related = [m for m in mentions if m.get('evidence_id') in ids]
    name = raw.get('canonical_name')
    if name:
        related += [m for m in layer(entry, 'diagnostic_assertion_timeline') if m.get('canonical_name') == name
                    and m.get('evidence_id') not in {r.get('evidence_id') for r in related}]
    return [checked_evidence(m) for m in related]
