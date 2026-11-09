"""Read-only assertion guards for existing artifacts; never modifies extracted bytes."""
from pathlib import Path
import json
import re
from .assertion_scope import validate_assertion, af_observation


def layer(entry, prefix):
    for f in entry.get('artifacts', []):
        path = Path(f['path'])
        if path.suffix == '.json' and path.name.startswith(prefix):
            return json.loads(path.read_text(encoding='utf-8-sig'))
    return []


def checked_evidence(row):
    from .diagnosis_events_v4 import concepts, classify_concept
    raw_text = row.get('raw_text', row.get('evidence_text', ''))
    text = re.sub(r'\s+', ' ', row.get('full_source_text') or raw_text)
    name = row.get('canonical_candidate', row.get('canonical_name'))
    hits = [hit for hit in concepts(text) if hit[2] == name]
    raw_normalized = re.sub(r'\s+', ' ', raw_text).strip()
    located = text.find(raw_normalized) if raw_normalized else -1
    if located >= 0:
        hits = [hit for hit in hits if located <= hit[0] and hit[1] <= located + len(raw_normalized)]
    if not hits:
        return dict(row, assertion_status='SOURCE_ONLY', scope='UNRESOLVED_SOURCE', explicit_positive=False)
    # A row describing multiple same-concept assertions needs evidence-level adjudication.
    decisions = [classify_concept(text, a, b, event=event)[4] for a, b, _, event in hits]
    if len({d['assertion_status'] for d in decisions}) > 1:
        return dict(row, assertion_status='UNCERTAIN', scope='MIXED_SOURCE_ASSERTIONS', explicit_positive=False)
    result = decisions[0]
    if name == 'ATRIAL_FIBRILLATION':
        a, b, _, _ = hits[0]
        result = af_observation(text, a, b, observation_date=row.get('document_date', row.get('assertion_date')))
        if result['assertion_status'] != decisions[0]['assertion_status']:
            result.update(decisions[0], AF_ESTABLISHED_FROM_THIS_EVIDENCE=decisions[0]['explicit_positive'])
    return dict(row, stored_assertion_status=row.get('assertion_status'), **result)


def unsafe_af_identity(entry):
    """Existing canonical AF with no independently positive linked source is unsafe."""
    diseases = layer(entry, 'canonical_disease_identities') or layer(entry, 'canonical_diagnoses')
    af = [d for d in diseases if d.get('canonical_name') == 'ATRIAL_FIBRILLATION']
    if not af:
        return False
    mentions = layer(entry, 'diagnosis_mentions')
    positive = [checked_evidence(m) for m in mentions if m.get('canonical_candidate') == 'ATRIAL_FIBRILLATION']
    return not any(m['assertion_status'] == 'ESTABLISHED' and m.get('explicit_positive') for m in positive)
