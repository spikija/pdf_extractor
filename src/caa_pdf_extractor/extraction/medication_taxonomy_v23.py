"""Exact local ontology lookup. Unknowns never become inferred ingredients."""
import csv,re,unicodedata
from pathlib import Path

DEFAULT_DICTIONARY=Path('data/reference/medication_dictionary.csv')

def key(text):
    return re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKC',text).casefold()).strip()

def load_dictionary(path=DEFAULT_DICTIONARY):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def normalize(raw,dictionary):
    cleaned=key(raw);hits=[]
    for entry in dictionary:
        alias=key(entry['alias'])
        if cleaned==alias or cleaned.startswith(alias+' '):
            remainder=cleaned[len(alias):]
            if re.match(r'\s+(?:plus|comp|hct|hctz)\b',remainder):continue
            # An uncurated combination must not become a single ingredient.
            prefix=raw[:next((i for i,c in enumerate(raw) if c.isdigit()),len(raw))]
            if '/' in prefix and '/' not in entry['alias']:continue
            hits.append(entry)
    if not hits:return dict(raw_medication=raw,normalized_brand=None,active_ingredient=None,canonical_drug=None,atc_code=None,
        atc_level_2=None,atc_level_3=None,atc_level_4=None,therapeutic_class='UNKNOWN',research_class='UNKNOWN',mapping_status='UNRESOLVED',review_required=True,rule_version='MEDICATION_V2.3')
    longest=max(len(key(e['alias'])) for e in hits);hits=[e for e in hits if len(key(e['alias']))==longest]
    if len({e['atc_code'] for e in hits})!=1:return dict(raw_medication=raw,mapping_status='AMBIGUOUS',review_required=True,therapeutic_class='UNKNOWN',research_class='UNKNOWN')
    e=hits[0];code=e['atc_code']
    result=dict(raw_medication=raw,normalized_brand=e.get('brand') or None,active_ingredient=e['active_ingredient'],canonical_drug=e['canonical_drug'],
        atc_code=code,atc_level_2=code[:3],atc_level_3=code[:4],atc_level_4=code[:5],therapeutic_class=e['therapeutic_class'],research_class=e['research_class'],
        mapping_status='CURATED_EXACT',reference_source=e['source'],reference_version=e['reference_version'],review_required=False,rule_version='MEDICATION_V2.3')
    subclasses={'C09A':'ACE_INHIBITOR','C09C':'ARB','C07':'BETA_BLOCKER','C08':'CALCIUM_CHANNEL_BLOCKER','C03A':'THIAZIDE_OR_THIAZIDE_LIKE','C03B':'THIAZIDE_OR_THIAZIDE_LIKE','C03C':'LOOP_DIURETIC','C02CA':'ALPHA_BLOCKER','C02A':'CENTRAL_ANTIHYPERTENSIVE',
        'B01AA':'VKA','B01AE':'DOAC_DIRECT_THROMBIN','B01AF':'DOAC_FACTOR_XA','B01AB04':'LMWH','B01AB05':'LMWH','B01AB01':'UFH','B01AC06':'ASPIRIN','B01AC04':'CLOPIDOGREL','B01AC22':'PRASUGREL','B01AC24':'TICAGRELOR','B01AC07':'DIPYRIDAMOLE'}
    result['research_subclass']=next((value for prefix,value in sorted(subclasses.items(),key=lambda x:-len(x[0])) if code.startswith(prefix)),'OTHER')
    result['anticoagulant_role']='UNKNOWN' if code.startswith('B01A') and not code.startswith('B01AC') else 'NOT_APPLICABLE'
    return result
