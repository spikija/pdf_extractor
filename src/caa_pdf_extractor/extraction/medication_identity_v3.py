"""Local product cleaning, explicit alias resolution and longitudinal exposures.

No network/model calls. Unsafe prefix or edit-distance guesses never resolve drugs.
"""
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from ..module_artifacts import digest, sha

ROOT = Path(__file__).resolve().parents[3]
DICTIONARY = ROOT / 'data/reference/medication_dictionary.csv'
ALIASES = ROOT / 'data/reference/medication_aliases_v3.json'
VERSION = '3.1'
COMMON_BRANDS = ('Thyrex Novalgin Durotiv Depakine Lovenox Detrusitol Forxiga Ozempic Victoza Neupro '
                 'Crestor Sevikar Vimpat Concor Pantoloc Euthyrox Wellbutrin Seroquel Circadin Buscopan '
                 'Sirdalud Citalostad Diclobene Restex').split() + ['Depakine Chronosphere']


def key(text):
    text = unicodedata.normalize('NFKD', text.casefold().replace('ß', 'ss'))
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


FORM = (r'retardgranulat(?:\s+in\s+beuteln)?|filmtabletten|augentropfen|tropfen|'
        r'pflaster|tabletten|kapseln|dragees|retard|creme|\bkps\b|\bbtl\.?|\bat\b|\btr\b|\bxr\b|\bcr\b|\bmr\b')
UNITS = r'(?:mg|mcg|[µμu]g|g|ml|i\.?\s*e\.?|mmol|tropfen|gtt\.?)'
NUMBER = r'\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?'


def clean(raw):
    text = unicodedata.normalize('NFKC', raw)
    # OCR joins are repaired only at an explicit numeric-strength/unit boundary.
    text = re.sub(r'(?<=[A-Za-zäöüÄÖÜ])(?=\d+(?:[.,]\d+)?\s*(?:mg|[μu]g|ml))', ' ', text, flags=re.I)
    text = re.sub(r'(?<=\d)(mg|[μu]g|ml)(?=s\.?c|i\.?v|[0-4](?:\s|$))', r'\1 ', text, flags=re.I)
    text = re.sub(r'(?<=[a-z])(?=XR\b)', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    strengths = re.findall(rf'\b{NUMBER}\s*{UNITS}(?:\s*/\s*(?:{NUMBER}\s*)?(?:ml|h|stunden?))?', text, re.I)
    route = re.findall(r'\bs\.?\s*c\.?|\bi\.?\s*v\.?|\bp\.?\s*o\.?|\boral\b|\btransdermal\b', text, re.I)
    form = re.findall(FORM, text, re.I)
    # A digit within a product token (D3) is retained; strength/schedule begins at a word boundary.
    boundary = re.search(rf'\b{NUMBER}\s*{UNITS}|(?<![\w]){NUMBER}(?=\s|$)|\b\d+\s*x\b|\bbei\s|\bseit\s|\bfür\s|\balle\s', text, re.I)
    head = text[:boundary.start()] if boundary else text
    head = re.sub(FORM, ' ', head, flags=re.I)
    head = re.sub(r'\((?:neu|gesteigert|abgesetzt)\)', '', head, flags=re.I)
    head = re.sub(r'\s+', ' ', head).strip(' .,;-')
    schedule = re.findall(r'(?<![\w.,])(?:[01234½¼¾](?:/2)?)(?:\s*[-–]?\s+[01234½¼¾](?:/2)?){2,}(?!\w)|\b[01234](?:-[01234]){2,}\b', text)
    frequency = re.findall(r'\b(?:\d+\s*x\s*(?:pro\s*)?(?:woche|wöchentlich|täglich)|alle\s+\d+\s+(?:h|stunden|monate)|sonntags|mittwochs|donnerstags|abends)\b', text, re.I)
    duration = re.findall(r'\b(?:für|über|nach|in weiteren)\s+\d+\s+(?:tage[n]?|wochen|monate[n]?)', text, re.I)
    return dict(raw_medication=raw, cleaned_product_name=head.upper(), normalized_trade_name=head.upper(),
                strength='; '.join(strengths) or None, dose=None,
                dose_status='NOT_INFERRED_FROM_SCHEDULE', units=re.findall(UNITS, ' '.join(strengths), re.I),
                route='; '.join(route) or None, formulation='; '.join(form) or None,
                schedule='; '.join(schedule + frequency) or None,
                prn=bool(re.search(r'bei\s+(?:bedarf|schmerz|übelkeit|bauchkr)|\bprn\b', text, re.I)),
                duration='; '.join(duration) or None, separated_source_instructions=text[len(text[:boundary.start()]):] if boundary else None)


def subclass(code):
    pairs = [('B01AA', 'VKA'), ('B01AF', 'DOAC_FACTOR_XA'), ('B01AE', 'DOAC_DIRECT_THROMBIN'),
             ('B01AB01', 'UFH'), ('B01AB', 'LMWH'), ('C10AA', 'STATIN'), ('C07', 'BETA_BLOCKER'),
             ('C09A', 'ACE_INHIBITOR'), ('C09C', 'ARB'), ('C08', 'CALCIUM_CHANNEL_BLOCKER'),
             ('C03C', 'LOOP_DIURETIC'), ('C03A', 'THIAZIDE_OR_THIAZIDE_LIKE'), ('N06AB', 'SSRI')]
    return next((value for prefix, value in pairs if code.startswith(prefix)), 'OTHER')


class Resolver:
    def __init__(self, dictionary=DICTIONARY, aliases=ALIASES):
        self.dictionary_path = Path(dictionary)
        self.alias_path = Path(aliases)
        with self.dictionary_path.open(encoding='utf-8-sig', newline='') as f:
            self.base = list(csv.DictReader(f))
        self.reference = json.loads(self.alias_path.read_text(encoding='utf-8'))
        self.local = defaultdict(list)
        self.generic = defaultdict(list)
        self.exact_brand = defaultdict(list)
        self.safe = defaultdict(list)
        base_by_alias = defaultdict(list)
        for row in self.base:
            base_by_alias[key(row['alias'])].append(row)
        curated = {}
        for item in self.reference['entries']:
            refs = base_by_alias.get(key(item.get('reference_alias', '')), [])
            codes = {r['atc_code'] for r in refs if r['atc_code']}
            row = dict(active_ingredients=item['ingredients'], research_class=item['class'],
                       research_subclass=item['subclass'], therapeutic_class=item['subclass'] if item['subclass'] != 'OTHER' else item['class'],
                       atc_code=next(iter(codes)) if len(codes) == 1 else None,
                       reference_source=item.get('source', self.reference['source']), atc_reference_source=refs[0]['source'] if len(codes) == 1 else None)
            for alias in item['aliases']:
                curated[key(alias)] = row
        for row in self.base:
            normalized = curated.get(key(row['alias'])) or curated.get(key(row['active_ingredient']))
            drug = normalized or dict(active_ingredients=[row['active_ingredient']], research_class=row['research_class'],
                research_subclass=subclass(row['atc_code']), therapeutic_class=row['therapeutic_class'], atc_code=row['atc_code'] or None,
                reference_source=row['source'], atc_reference_source=row['source'])
            target = self.exact_brand if row.get('brand') else self.generic
            target[row['alias'].casefold()].append(drug)
            self.local[key(row['alias'])].append(drug)
            self.safe[key(row['alias']).replace(' ', '')].append(drug)
        # Curated aliases form part of the versioned local dictionary, not model guesses.
        for alias, row in curated.items():
            self.local[alias] = [row]
            self.safe[alias.replace(' ', '')] = [row]
            for ingredient in row['active_ingredients']:
                if len(row['active_ingredients']) == 1 and key(ingredient) == alias:
                    self.generic[alias] = [row]
        self.curated = curated
        self.source_hash = digest([sha(self.dictionary_path), sha(self.alias_path)])

    def compound_generic(self, name):
        parts = re.split(r'\s*[/+]\s*', name)
        if len(parts) < 2:
            return None
        drugs = []
        for part in parts:
            rows = self.generic.get(part.casefold(), [])
            drug = self.unique(rows) if rows else None
            if not drug or len(drug['active_ingredients']) != 1:
                return None
            drugs.append(drug)
        classes = {d['research_class'] for d in drugs}
        return dict(active_ingredients=[i for d in drugs for i in d['active_ingredients']],
                    research_class=next(iter(classes)) if len(classes) == 1 else 'OTHER',
                    research_subclass='EXPLICIT_GENERIC_COMBINATION', therapeutic_class='COMBINATION',
                    atc_code=None, atc_reference_source=None,
                    reference_source='; '.join(sorted({d['reference_source'] for d in drugs})))

    @staticmethod
    def unique(rows):
        identities = {tuple(sorted(key(i) for i in row['active_ingredients'])) for row in rows}
        if len(identities) != 1:
            return None
        classes = {row['research_class'] for row in rows}
        codes = {row.get('atc_code') for row in rows}
        if len(classes) != 1 or len(codes) != 1:
            return None
        return rows[0]

    def resolve(self, raw):
        result = clean(raw)
        head = result['cleaned_product_name']
        # Generic (brand) notation can use the generic, but both names must agree
        # if the brand also resolves; a disagreement remains review-only.
        notation = re.fullmatch(r'(.+?)\s*\(([^)]+)\)', head)
        names = [notation[1].strip(), notation[2].strip()] if notation else [head]
        stages = [('GENERIC_ACTIVE_INGREDIENT', self.generic, lambda s: s.casefold()),
                  ('LOCAL_EXACT_TRADE_NAME', self.exact_brand, lambda s: s.casefold()),
                  ('LOCAL_NORMALIZED_ALIAS', self.local, key),
                  ('FUZZY_SAFE_SPACING_ALIAS', self.safe, lambda s: key(s).replace(' ', ''))]
        resolved = []
        for name in names:
            hit = self.compound_generic(name)
            if hit:
                resolved.append(('GENERIC_ACTIVE_INGREDIENT_COMBINATION', hit, name))
                continue
            for status, index, normalize_key in stages:
                rows = index.get(normalize_key(name), [])
                if rows:
                    hit = self.unique(rows)
                    if hit:
                        resolved.append((status, hit, name))
                    break  # ambiguous entries never fall through to a looser stage
            # Exact curated synonym stage, no arbitrary prefix or edit-distance search.
            if not hit and key(name) in self.curated:
                resolved.append(('CURATED_SYNONYM', self.curated[key(name)], name))
        unknown = dict(active_ingredients=[], active_ingredient_count=0, canonical_drug=None, drug_identity_id=None,
                       atc_code=None, therapeutic_class='UNKNOWN', research_class='UNKNOWN', research_subclass='UNKNOWN',
                       mapping_source=None, mapping_status='UNRESOLVED', review_required=True)
        if not resolved:
            return dict(result, **unknown, lookup_key=key(head), rule_version=VERSION)
        ids = {tuple(sorted(key(i) for i in row['active_ingredients'])) for _, row, _ in resolved}
        if len(ids) > 1:
            return dict(result, **dict(unknown, mapping_status='AMBIGUOUS_GENERIC_BRAND'), lookup_key=key(head), rule_version=VERSION)
        status, drug, matched = resolved[0]
        ingredients = drug['active_ingredients']
        canonical = '+'.join(sorted(re.sub(r'[^A-Z0-9]+', '_', i.upper()).strip('_') for i in ingredients))
        result.update(drug, canonical_drug=canonical, active_ingredient_count=len(ingredients),
                      mapping_source=drug['reference_source'], mapping_status=status, review_required=False,
                      lookup_key=key(matched), rule_version=VERSION,
                      atc_status='LOCAL_REFERENCE_VERIFIED' if drug.get('atc_code') else 'UNKNOWN_NOT_INFERRED')
        return result


def assemble(patient_id, source_rows, resolver, encounters):
    mentions, exposures = [], []
    groups = defaultdict(list)
    for n, row in enumerate(source_rows):
        evidence = row['evidence_id']
        item = resolver.resolve(row['raw_medication'])
        mention_id = f'{patient_id}:MEDM:{digest([evidence, n])[:14]}'
        drug_id = f'{patient_id}:DRUG:{digest(item["canonical_drug"])[:14]}' if item['canonical_drug'] else None
        doc = str(row['source_document_id'])
        item.update(mention_id=mention_id, patient_id=patient_id, document_id=row['source_document_id'],
                    page=row.get('page'), section=row.get('section_id'), evidence_id=evidence,
                    medication_context=row.get('medication_context'), available_date=row.get('available_date') or None,
                    drug_identity_id=drug_id, original_mapping=row)
        mentions.append(item)
        raw = row['raw_medication']
        start = re.search(r'\bseit\s+(\d{1,2})\.(\d{1,2})\.(\d{4})', raw, re.I)
        start_date = f'{start[3]}-{int(start[2]):02}-{int(start[1]):02}' if start else None
        stop = bool(re.search(r'abgesetzt|beendet|gestoppt', raw, re.I))
        encounter = encounters.get(doc, {})
        exposure = dict(exposure_id=f'{patient_id}:EXPOSURE:{digest([evidence, n])[:14]}', patient_id=patient_id,
            mention_id=mention_id, drug_identity_id=drug_id, canonical_drug=item['canonical_drug'],
            evidence_id=evidence, document_id=row['source_document_id'], page=row.get('page'), section=row.get('section_id'),
            encounter_id=f'{patient_id}:{doc}', encounter_admission_date=encounter.get('admission_date'),
            encounter_discharge_date=encounter.get('discharge_date'), source_available_date=item['available_date'],
            start_date=start_date, start_date_basis='EXPLICIT_SEIT_DATE' if start else 'UNKNOWN',
            active_date=None, stop_date=None, stop_status='STOPPED_DATE_UNKNOWN' if stop else 'UNKNOWN',
            exposure_status='SOURCE_DOCUMENTED_STOP' if stop else 'SOURCE_MEDICATION_LIST_OBSERVATION',
            strength=item['strength'], dose=item['dose'], dose_status=item['dose_status'], route=item['route'],
            schedule=item['schedule'], formulation=item['formulation'], prn=item['prn'], duration=item['duration'],
            source_instructions=item['separated_source_instructions'], review_required=True)
        exposures.append(exposure)
        if drug_id:
            groups[drug_id].append(item)
    canonical = []
    for identity, members in sorted(groups.items()):
        first = members[0]
        canonical.append(dict(drug_identity_id=identity, patient_id=patient_id,
            **{k: first[k] for k in ('canonical_drug', 'active_ingredients', 'active_ingredient_count', 'research_class', 'research_subclass', 'therapeutic_class')},
            atc_codes=sorted({m['atc_code'] for m in members if m.get('atc_code')}),
            mention_count=len(members), exposure_count=len(members), source_document_count=len({m['document_id'] for m in members}),
            normalized_trade_names=sorted({m['normalized_trade_name'] for m in members}),
            evidence_ids=[m['evidence_id'] for m in members], mention_ids=[m['mention_id'] for m in members],
            review_required=True, rule_version=VERSION))
    return mentions, canonical, exposures
