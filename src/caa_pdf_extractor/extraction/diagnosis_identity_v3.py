"""Patient disease identities over immutable source mentions; no event grouping."""
import re
import unicodedata
from collections import defaultdict
from datetime import date

from ..module_artifacts import digest

VERSION = '3.0'


def key(text):
    text = unicodedata.normalize('NFKD', text.casefold().replace('ß', 'ss'))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in text if not unicodedata.combining(c)))


# Source synonym families; no substring merging across these disease boundaries.
RULES = [
    (r'abra|amyloidbetarelatedangiitis', 'AMYLOID_BETA_RELATED_ANGIITIS', 'NEUROLOGIC', 'INFLAMMATORY_AUTOIMMUNE'),
    (r'caari|inflammator.*amyloid|amyloid.*inflammator|amyloid.*relatedinflammation', 'CAA_RELATED_INFLAMMATION', 'NEUROLOGIC', 'INFLAMMATORY_AUTOIMMUNE'),
    (r'amyloidangiopath|cerebralamyloidangiopathy|^caa$', 'CEREBRAL_AMYLOID_ANGIOPATHY', 'NEUROLOGIC', 'CEREBROVASCULAR'),
    (r'vorhofflimm|^vhf$|^af$|atrialfibrillation', 'ATRIAL_FIBRILLATION', 'CARDIOVASCULAR', 'ARRHYTHMIA'),
    (r'vorhofflatter|atrialflutter', 'ATRIAL_FLUTTER', 'CARDIOVASCULAR', 'ARRHYTHMIA'),
    (r'(?:diabetes|dm).*?(?:typ(?:e)?2|typii\b)|^dmii$|^dm2$|^t2dm$', 'DIABETES_MELLITUS_TYPE_2', 'ENDOCRINE_METABOLIC', 'DIABETES'),
    (r'osteopor', 'OSTEOPOROSIS', 'MUSCULOSKELETAL', 'BONE_DISORDER'),
    (r'cholelith|cholecystolith|cholezystolith', 'CHOLELITHIASIS', 'GASTROINTESTINAL_HEPATIC', 'BILIARY_DISEASE'),
    (r'obstruktiv.*schlafapno|obstructivesleepapno|^osas$|^osa$', 'OBSTRUCTIVE_SLEEP_APNEA', 'PULMONARY', 'SLEEP_RELATED_BREATHING_DISORDER'),
    (r'hypothyre|hypothyroid', 'HYPOTHYROIDISM', 'ENDOCRINE_METABOLIC', 'THYROID_DISORDER'),
    (r'pulmonal.*hyperton|pulmonaryhypertension', 'PULMONARY_HYPERTENSION', 'PULMONARY', 'PULMONARY_VASCULAR_DISEASE'),
    (r'mammakarzinom|mammacarcinom|breastcancer', 'BREAST_CANCER', 'ONCOLOGIC_HEMATOLOGIC', 'SOLID_TUMOR'),
    (r'hypertonie|arterialhypertension|arteriellehypertonie', 'ARTERIAL_HYPERTENSION', 'CARDIOVASCULAR', 'HYPERTENSION'),
    (r'zerebral.*(?:vaskulitis|arteriitis)|cerebral.*(?:vasculitis|arteriitis)|^cnsvasculitis$', 'CNS_VASCULITIS', 'NEUROLOGIC', 'INFLAMMATORY_AUTOIMMUNE'),
    (r'restlesslegs', 'RESTLESS_LEGS_SYNDROME', 'NEUROLOGIC', 'MOVEMENT_DISORDER'),
    (r'polyneuropath|^pnp', 'POLYNEUROPATHY', 'NEUROLOGIC', 'PERIPHERAL_NERVE'),
    (r'symptomatischeepilepsie|symptomaticepilepsy', 'SYMPTOMATIC_EPILEPSY', 'NEUROLOGIC', 'EPILEPSY_SEIZURE'),
    (r'depressi', 'DEPRESSION', 'PSYCHIATRIC', 'MOOD_DISORDER'),
    (r'(?:carotis|carotid).*stenos|stenos.*(?:carotis|carotid)|cavk', 'CAROTID_ARTERY_STENOSIS', 'CARDIOVASCULAR', 'ARTERIAL_DISEASE'),
    (r'herzinsuffizienz|heartfailure', 'HEART_FAILURE', 'CARDIOVASCULAR', 'HEART_FAILURE'),
    (r'lichenruberplanus', 'LICHEN_PLANUS', 'DERMATOLOGIC', 'INFLAMMATORY_SKIN_DISEASE'),
]


def identities(label, original=None):
    """First source family is primary; secondary disease references are separate.

    Temporal and assertion eligibility are applied by assemble, never inferred here.
    """
    text = key(label)
    found = []
    for pattern, name, domain, category in RULES:
        if re.search(pattern, text):
            if name == 'CEREBRAL_AMYLOID_ANGIOPATHY' and any(x['canonical_name'] in
                    {'CAA_RELATED_INFLAMMATION', 'AMYLOID_BETA_RELATED_ANGIITIS'} for x in found):
                continue
            if name == 'ARTERIAL_HYPERTENSION' and re.search(r'pulmonal|portal|intrakraniell', text):
                continue
            if name == 'POLYNEUROPATHY' and 'demyelinis' in text:
                name = 'DEMYELINATING_POLYNEUROPATHY'
            found.append(dict(canonical_name=name, display_name=name.replace('_', ' ').capitalize(),
                              domain=domain, category=category, subcategory=name,
                              grouping_rule='V3_SOURCE_SYNONYM'))
    # A compound epilepsy/CAA label describes epilepsy as its primary disease.
    if 'epilepsie' in text:
        found.sort(key=lambda x: x['canonical_name'] != 'SYMPTOMATIC_EPILEPSY')
    if found:
        return found
    original = original or {}
    candidate = original.get('canonical_name') or label
    # Preserve fallback taxonomy. Never merge unrelated OTHER diagnoses.
    name = re.sub(r'[^A-Z0-9]+', '_', unicodedata.normalize('NFKD', candidate.upper())
                  .encode('ascii', 'ignore').decode()).strip('_')
    return [dict(canonical_name=name, display_name=candidate,
                 domain=original.get('domain') or 'UNKNOWN', category=original.get('category') or 'UNKNOWN',
                 subcategory=original.get('subcategory') or 'UNKNOWN', grouping_rule='NORMALIZED_SOURCE_IDENTITY')]


def assertion(row, text):
    kind = row.get('candidate_type', 'UNCLASSIFIED_SOURCE')
    if kind == 'NEGATED_DIAGNOSIS' or re.search(r'\b(?:kein\w*|ohne|no|without)\s+(?:hinweis\s+auf\s+)?', text, re.I):
        return 'NEGATED'
    if kind == 'UNCERTAIN' or re.search(r'\bverdacht|\bv\s*\.\s*a\s*\.|\bsuspected', text, re.I):
        return 'UNCERTAIN'
    return 'ASSERTED' if kind in {'DIAGNOSIS_DISEASE', 'ACUTE_CLINICAL_EVENT'} else 'SOURCE_ONLY'


def supported_date(mention, document_date, *, allow_history=True):
    temporal = mention.get('temporal', {})
    if allow_history:
        value = temporal.get('date_disease')
        if value and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return value, 'DAY', 'SOURCE_EXPLICIT_DISEASE_DATE'
        year = temporal.get('date_disease_year')
        month = temporal.get('date_disease_month')
        if year:
            return (f'{year:04}-{month:02}', 'MONTH', 'SOURCE_EXPLICIT_DISEASE_PERIOD') if month else (
                str(year), 'YEAR', 'SOURCE_EXPLICIT_DISEASE_PERIOD')
    return (document_date, 'DAY', 'DOCUMENTATION_DATE_ONSET_UNKNOWN') if document_date else (None, 'UNKNOWN', 'UNKNOWN')


def qualifiers(text):
    k = key(text)
    af = []
    if 'paroxysmal' in k: af.append('PAROXYSMAL')
    if re.search(r'longstandingpersistent|langanhaltendpersist', k): af.append('LONG_STANDING_PERSISTENT')
    elif 'persist' in k: af.append('PERSISTENT')
    if 'permanent' in k: af.append('PERMANENT')
    return dict(af_types=af, STEROID_INDUCED=bool(re.search(r'(?:cortison|kortison|steroid).*induziert', k)),
                source_phenotype_text=text)


def assemble(patient_id, rows, history, document_dates):
    """One row per original source mention, plus audit-only sources, all retained."""
    by_evidence = {}
    for row in rows:
        for m in row.get('source_mentions', []):
            by_evidence[m['item_id']] = row
    mentions = []
    groups = defaultdict(list)
    for n, source in enumerate([*history['mentions'], *history.get('audit_only', [])]):
        evidence_id = source.get('item_id') or f'audit:{n}'
        row = by_evidence.get(evidence_id, {})
        label = row.get('raw_candidate') or source.get('disease') or source.get('evidence_text', '')
        raw = source.get('evidence_text', label)
        state = assertion(row, label)
        mapped = identities(label, row)
        doc = str(source['source_document_id'])
        documentation = document_dates.get(doc)
        period, precision, basis = supported_date(source, documentation)
        temporal_status = 'HISTORICAL' if re.search(r'\bz\s*\.\s*n\s*\.|\bst\s*\.\s*p\s*\.|zustand nach', label, re.I) else 'UNKNOWN'
        # Date binding is to this disease mention only, never dates in its neighbours.
        item = dict(mention_id=f'{patient_id}:DXM:{digest([evidence_id, n])[:14]}', patient_id=patient_id,
                    document_id=source['source_document_id'], page=source.get('page'), section=source.get('section_id'),
                    evidence_id=evidence_id, raw_text=raw, raw_candidate=label,
                    candidate_type=row.get('candidate_type') or source.get('disposition', 'UNCLASSIFIED_SOURCE'),
                    canonical_candidate=mapped[0]['canonical_name'], domain=mapped[0]['domain'],
                    category=mapped[0]['category'], subcategory=mapped[0]['subcategory'],
                    assertion_status=state, temporal_status=temporal_status, certainty='UNCERTAIN' if state == 'UNCERTAIN' else 'SOURCE_REPORTED',
                    date_or_period=period, date_precision=precision, date_basis=basis,
                    document_date=documentation, source_record_id=row.get('record_id'),
                    diagnosis_identity_ids=[], qualifiers=qualifiers(label), original_source=source,
                    original_taxonomy={k: row.get(k) for k in ('canonical_name', 'domain', 'category', 'subcategory')})
        mentions.append(item)
        if row.get('candidate_type') == 'DIAGNOSIS_DISEASE' and state == 'ASSERTED':
            # The legacy group may incorrectly call isolated siderosis CAA. Use source identity.
            if key(label).startswith('superfiziellesiderose'):
                mapped = [dict(canonical_name='SUPERFICIAL_SIDEROSIS', display_name='Superficial siderosis',
                               domain='NEUROLOGIC', category='CEREBROVASCULAR', subcategory='SUPERFICIAL_SIDEROSIS',
                               grouping_rule='SOURCE_OVERRIDES_INCOMPATIBLE_LEGACY_GROUP')]
                item.update(canonical_candidate='SUPERFICIAL_SIDEROSIS', domain='NEUROLOGIC', category='CEREBROVASCULAR', subcategory='SUPERFICIAL_SIDEROSIS')
            for identity in mapped:
                groups[identity['canonical_name']].append((item, identity))

    # Link disease references in event/uncertain rows without promoting them or changing events.
    for item in mentions:
        existing = {name for name in groups if any(x['canonical_name'] == name for x in identities(item['raw_candidate']))}
        if item['assertion_status'] != 'NEGATED':
            for name in existing:
                if all(m['mention_id'] != item['mention_id'] for m, _ in groups[name]):
                    groups[name].append((item, groups[name][0][1]))

    diagnoses = []
    for name, members in sorted(groups.items()):
        identity_id = f'{patient_id}:DX:{digest(name)[:14]}'
        first = members[0][1]
        positive = [m for m, _ in members if m['candidate_type'] == 'DIAGNOSIS_DISEASE' and m['assertion_status'] == 'ASSERTED']
        # Secondary references can support linking, not dates/status of the primary disease.
        dated = []
        for m in positive:
            if m['canonical_candidate'] == name and m['date_or_period']:
                dated.append((m['date_or_period'], m['date_precision'], m['date_basis'], m['evidence_id']))
            elif m['document_date']:
                # A date attached to epilepsy, for example, is not a CAA onset date.
                dated.append((m['document_date'], 'DAY', 'DOCUMENTATION_DATE_ONSET_UNKNOWN', m['evidence_id']))
        dated.sort(key=lambda x: x[0])  # partial ISO periods stay partial, no invented Jan 1
        last = sorted(m['document_date'] for m in positive if m['document_date'])
        for m, _ in members:
            m['diagnosis_identity_ids'].append(identity_id)
        af_types = sorted({v for m in positive for v in m['qualifiers']['af_types']}) if name == 'ATRIAL_FIBRILLATION' else []
        diagnoses.append(dict(diagnosis_identity_id=identity_id, patient_id=patient_id,
            **{k: first[k] for k in ('canonical_name', 'display_name', 'domain', 'category', 'subcategory')},
            first_documented_date=dated[0][0] if dated else None,
            first_documented_date_precision=dated[0][1] if dated else 'UNKNOWN',
            first_documented_date_basis=dated[0][2] if dated else 'UNKNOWN',
            first_date_evidence_id=dated[0][3] if dated else None,
            last_documented_date=last[-1] if last else None, onset_date=None,
            status='HISTORICAL' if positive and all(m['temporal_status'] == 'HISTORICAL' for m in positive) else 'UNKNOWN',
            evidence_ids=sorted({m['evidence_id'] for m, _ in members}),
            mention_ids=[m['mention_id'] for m, _ in members], mention_count=len(members),
            asserted_disease_mention_count=len(positive), source_document_count=len({m['document_id'] for m, _ in members}),
            af_type=af_types[0] if len(af_types) == 1 else 'UNSPECIFIED' if name == 'ATRIAL_FIBRILLATION' else None,
            af_types_observed=af_types,
            modifiers={'STEROID_INDUCED': any(m['qualifiers']['STEROID_INDUCED'] for m in positive)} if name == 'DIABETES_MELLITUS_TYPE_2' else {},
            source_qualifiers=[dict(evidence_id=m['evidence_id'], **m['qualifiers']) for m in positive],
            grouping_rule=first['grouping_rule'], review_required=True,
            review_reasons=['SOURCE_MODEL_ELIGIBILITY_RETAINED', 'PATIENT_IDENTITY_REVIEW_REQUIRED'], rule_version=VERSION))
    return mentions, diagnoses
