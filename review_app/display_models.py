"""Canonical entities only; source mentions remain linked evidence, never rows."""
from review_app.artifact_repository import layer


def diagnosis_entities(entry, events=False):
    if events:
        rows = layer(entry, 'canonical_clinical_events')
    else:
        rows = layer(entry, 'canonical_disease_identities') or layer(entry, 'canonical_diagnoses')
    result = []
    seen = set()
    for raw in rows:
        identity = raw.get('event_id') if events else raw.get('diagnosis_identity_id')
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(dict(raw, entity_id=identity,
                           document_count=raw.get('document_count', raw.get('source_document_count'))))
    return result


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
    if 'exposures' in raw:
        return raw['exposures']
    ids = set(raw.get('source_evidence_ids', raw.get('evidence_ids', [])))
    mentions = layer(entry, 'diagnosis_mentions')
    return [m for m in mentions if m.get('evidence_id') in ids]
