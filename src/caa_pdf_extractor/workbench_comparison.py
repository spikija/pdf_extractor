"""Semantic canonical-entity comparison, independent of serialization/provenance order."""
import json
from pathlib import Path
from .workbench_store import read

DISEASE_FIELDS = ('canonical_name', 'first_documented_date', 'first_documented_date_precision',
    'first_suspicion_date', 'first_explicit_diagnosis_date', 'status', 'domain', 'category', 'subcategory', 'modifiers')
EVENT_FIELDS = ('event_type', 'event_date', 'event_date_precision', 'event_date_basis', 'side', 'territory',
    'location', 'etiology', 'etiology_context', 'acute_treatment', 'complications', 'source_type')
MED_FIELDS = ('canonical_drug', 'active_ingredients', 'atc_codes', 'research_class', 'research_subclass',
              'normalized_trade_names')
EXPOSURE_FIELDS = ('document_id', 'encounter_id', 'source_available_date', 'raw_medication', 'normalized_trade_name',
                   'active_ingredients', 'atc_code', 'strength', 'dose', 'schedule', 'route', 'mapping_status')


def load_layer(entry, prefixes):
    for f in (entry or {}).get('artifacts', []):
        p = Path(f['path'])
        if p.suffix == '.json' and p.name.startswith(prefixes):
            return read(p)
    return []


def semantic_entities(entry, module):
    result = []
    if module == 'diagnoses':
        for row in load_layer(entry, ('canonical_disease_identities', 'canonical_diagnoses')):
            result.append(dict(kind='DISEASE', anchor=row['canonical_name'],
                               value={k: row.get(k) for k in DISEASE_FIELDS}, evidence=set(row.get('source_evidence_ids', row.get('evidence_ids', [])))))
        for row in load_layer(entry, ('canonical_clinical_events',)):
            value = {k: row.get(k) for k in EVENT_FIELDS}
            anchor = '|'.join(str(row.get(k) or '?') for k in ('event_type', 'event_date', 'side', 'territory', 'location'))
            result.append(dict(kind='EVENT', anchor=anchor, value=value,
                               evidence=set(row.get('source_evidence_ids', row.get('evidence_ids', [])))))
    else:
        canonical = load_layer(entry, ('canonical_medications',))
        mentions = {r['mention_id']: r for r in load_layer(entry, ('medication_mentions',))}
        exposures = load_layer(entry, ('medication_exposures',))
        for row in canonical:
            value = {k: row.get(k) for k in MED_FIELDS}
            for key in ('active_ingredients', 'atc_codes', 'normalized_trade_names'):
                value[key] = sorted(value[key] or [], key=str)
            linked = [dict(mentions.get(e.get('mention_id'), {}), **e) for e in exposures if e.get('drug_identity_id') == row['drug_identity_id']]
            value['exposures'] = sorted([{k: r.get(k) for k in EXPOSURE_FIELDS} for r in linked], key=lambda r: json.dumps(r, sort_keys=True))
            result.append(dict(kind='MEDICATION', anchor=row['canonical_drug'], value=value, evidence=set(row.get('evidence_ids', []))))
        for row in exposures:
            if not row.get('drug_identity_id'):
                merged = dict(mentions.get(row.get('mention_id'), {}), **row)
                result.append(dict(kind='UNRESOLVED_MEDICATION', anchor=row.get('evidence_id', row['exposure_id']),
                    value={k: merged.get(k) for k in EXPOSURE_FIELDS}, evidence={row.get('evidence_id')}))
    return result


def compare(current, candidate, module):
    before, after = semantic_entities(current, module), semantic_entities(candidate, module)
    remaining = set(range(len(after)))
    result = []
    for old in before:
        matches = [i for i in remaining if after[i]['kind'] == old['kind'] and after[i]['anchor'] == old['anchor']]
        if not matches and old['kind'] == 'EVENT':
            possible = [i for i in remaining if after[i]['kind'] == 'EVENT' and after[i]['value']['event_type'] == old['value']['event_type']]
            overlap = [(len(old['evidence'] & after[i]['evidence']), i) for i in possible]
            if overlap:
                best = max(score for score, _ in overlap)
                matches = [i for score, i in overlap if score == best and score > 0]
                if len(matches) != 1:
                    matches = []  # Ambiguous episodes must not silently merge.
        if len(matches) == 1:
            index = matches[0]
            remaining.remove(index)
            new = after[index]
            changes = {k: {'current': old['value'].get(k), 'candidate': new['value'].get(k)}
                       for k in old['value'].keys() | new['value'].keys() if old['value'].get(k) != new['value'].get(k)}
            result.append(dict(change='CHANGED' if changes else 'UNCHANGED', kind=old['kind'], entity=new['anchor'], changes=changes,
                               current=old['value'], candidate=new['value']))
        else:
            result.append(dict(change='REMOVED', kind=old['kind'], entity=old['anchor'], changes={}, current=old['value'], candidate=None))
    result.extend(dict(change='ADDED', kind=after[i]['kind'], entity=after[i]['anchor'], changes={}, current=None, candidate=after[i]['value']) for i in sorted(remaining))
    return result
