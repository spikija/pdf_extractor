import json
from pathlib import Path

import pytest

from caa_pdf_extractor import diagnosis_medication_v3 as runner
from caa_pdf_extractor.extraction import diagnosis_identity_v3 as dx
from caa_pdf_extractor.extraction.medication_identity_v3 import Resolver, COMMON_BRANDS, assemble
from caa_pdf_extractor.module_artifacts import ModuleStore, invalidate, select_latest_validated, sha


@pytest.fixture(scope='module')
def resolver():
    return Resolver()


@pytest.mark.parametrize('brand', COMMON_BRANDS)
def test_required_trade_names_with_instructions(resolver, brand):
    result = resolver.resolve(brand + ' 10 mg s.c. 1 0 1 bei Bedarf')
    expected = next(e for e in resolver.reference['entries'] if brand in e['aliases'])
    assert result['active_ingredients'] == expected['ingredients']
    assert result['research_class'] == expected['class']
    assert result['research_subclass'] == expected['subclass']
    assert result['cleaned_product_name'] == brand.upper()


@pytest.mark.parametrize('raw,product', [
    ('Thyrex 100 µg 1 0 0', 'THYREX'),
    ('Depakine Chronosphere Retardgranulat in Beuteln 500 mg 1 0 1', 'DEPAKINE CHRONOSPHERE'),
    ('Lovenox 40 mg s.c. 0 0 0 1', 'LOVENOX'),
    ('Oleovit D3 20 Tropfen 1 x pro Woche', 'OLEOVIT D3'),
    ('Colecalciferol (Oleovit D3) Tropfen 1x wöchentlich 50 Tropfen', 'COLECALCIFEROL (OLEOVIT D3)'),
])
def test_product_cleaning(resolver, raw, product):
    result = resolver.resolve(raw)
    assert result['cleaned_product_name'] == product
    assert result['canonical_drug']


def test_no_unsafe_drug_guess(resolver):
    assert resolver.resolve('Concor Plus 5 mg')['mapping_status'] == 'UNRESOLVED'
    assert resolver.resolve('Thyrex (Lovenox) 5 mg')['mapping_status'] == 'AMBIGUOUS_GENERIC_BRAND'
    assert resolver.resolve('Thyrez 100 µg')['mapping_status'] == 'UNRESOLVED'


def test_combination_and_temporal_history(resolver):
    rows = [dict(raw_medication=raw, evidence_id=str(i), source_document_id=i, available_date=f'2020-01-0{i+1}')
            for i, raw in enumerate(['Crestor 10 mg 1 0 0', 'Rosuvalan 20 mg 1 0 0', 'Rosuvastatin 20 mg abgesetzt', 'Sevikar 20/5 mg 1 0 0'])]
    mentions, canonical, exposures = assemble('p', rows, resolver, {})
    assert len(mentions) == len(exposures) == 4
    assert len(canonical) == 2
    assert mentions[0]['drug_identity_id'] == mentions[2]['drug_identity_id']
    assert exposures[2]['stop_status'] == 'STOPPED_DATE_UNKNOWN'
    assert mentions[3]['active_ingredient_count'] == 2


@pytest.mark.parametrize('labels,name', [
    (['Amyloidangiopathie', 'Cerebrale Amyloidangiopathie', 'Zerebrale Amyloidangiopathie', 'Rahmen einer Amyloidangiopathie'], 'CEREBRAL_AMYLOID_ANGIOPATHY'),
    (['Vorhofflimmern', 'Vorhofflimmerarrhythmie', 'VHF', 'paroxysmales Vorhofflimmern'], 'ATRIAL_FIBRILLATION'),
    (['Diabetes mellitus Typ 2', 'DM Typ II', 'Cortison-induzierter Diabetes mellitus Typ 2'], 'DIABETES_MELLITUS_TYPE_2'),
    (['Arterielle Hypertonie', 'arterielleHypertonie', 'Hypertonie'], 'ARTERIAL_HYPERTENSION'),
])
def test_disease_identity_and_mentions(labels, name):
    sources = [dict(item_id=str(i), source_document_id=i, disease=label, evidence_text=label,
                    temporal={'date_disease_year': 2006} if i == 0 else {}) for i, label in enumerate(labels)]
    rows = [dict(candidate_type='DIAGNOSIS_DISEASE', raw_candidate=s['disease'], source_mentions=[s]) for s in sources]
    # Two distinct acute events remain source records and never enter the chronic grouping.
    for i, year in enumerate([2006, 2015], 100):
        s = dict(item_id=str(i), source_document_id=i, disease='Ischemic stroke', evidence_text='Ischemic stroke', temporal={'date_disease_year': year})
        sources.append(s)
        rows.append(dict(candidate_type='ACUTE_CLINICAL_EVENT', raw_candidate=s['disease'], source_mentions=[s]))
    mentions, canonical = dx.assemble('p', rows, {'mentions': sources}, {str(i): '2020-01-01' for i in range(len(labels))})
    assert len(canonical) == 1
    assert canonical[0]['canonical_name'] == name
    assert canonical[0]['first_documented_date'] == '2006'
    assert canonical[0]['first_documented_date_precision'] == 'YEAR'
    assert canonical[0]['mention_count'] == len(labels)
    assert len([m for m in mentions if m['candidate_type'] == 'ACUTE_CLINICAL_EVENT']) == 2
    if name == 'DIABETES_MELLITUS_TYPE_2':
        assert canonical[0]['modifiers']['STEROID_INDUCED']


def test_no_incompatible_taxonomy_merges():
    assert dx.identities('Pulmonale Hypertonie')[0]['canonical_name'] == 'PULMONARY_HYPERTENSION'
    assert len(dx.identities('Pulmonale Hypertonie')) == 1
    assert dx.identities('CAA-ri')[0]['canonical_name'] == 'CAA_RELATED_INFLAMMATION'
    assert dx.identities('Osteoporose')[0]['domain'] == 'MUSCULOSKELETAL'
    assert dx.identities('Stenose der Arteria carotis interna')[0]['canonical_name'] == 'CAROTID_ARTERY_STENOSIS'


def test_document_header_date_only():
    document = {'sections': [{'page_start': 1, 'text': 'geboren am 01.02.1950\nSalzburg, am 24.07.2017\nInfarkt 2006'}]}
    assert runner.document_date(document) == '2017-07-24'
    document['sections'][0]['text'] = 'geboren am 01.02.1950\nInfarkt 2006'
    assert runner.document_date(document) is None


def test_immutable_selection_and_transitive_staleness(tmp_path):
    store = ModuleStore(tmp_path / 'first')
    dx_entry = store.publish('p', 'diagnoses', '3.0', {'dx.json': []}, 'source')
    af = store.publish('p', 'AF', '2.0', {'af.json': []}, 'source')
    score = store.publish('p', 'CHA2DS2_VA', '2.0', {'score.json': []}, 'source')
    imaging = store.publish('p', 'imaging', '2.0', {'imaging.json': []}, 'source')
    previous = dict(diagnoses=dx_entry, AF=af, CHA2DS2_VA=score, imaging=imaging)
    current = invalidate(previous, ['diagnoses'])
    assert current['AF']['status'] == current['CHA2DS2_VA']['status'] == 'STALE_DEPENDENCY'
    assert current['imaging'] == previous['imaging']
    assert previous['AF']['status'] == 'VALIDATED'
    assert select_latest_validated([previous, current], 'CHA2DS2_VA') is None
    assert select_latest_validated([previous, current], 'imaging') == imaging
    with pytest.raises(FileExistsError):
        store.publish('p', 'diagnoses', '3.0', {'dx.json': [1]}, 'source')
    Path(imaging['artifact']).write_text('tampered')
    assert select_latest_validated([previous], 'imaging') is None


def test_secondary_disease_does_not_inherit_primary_onset():
    source = dict(item_id="s", source_document_id=1,
                  disease="Symptomatische Epilepsie bei Amyloidangiopathie",
                  evidence_text="Symptomatische Epilepsie bei Amyloidangiopathie",
                  temporal={"date_disease_year": 2006})
    rows = [dict(candidate_type="DIAGNOSIS_DISEASE", raw_candidate=source["disease"], source_mentions=[source])]
    _, canonical = dx.assemble("p", rows, {"mentions": [source]}, {"1": "2020-01-01"})
    by_name = {r["canonical_name"]: r for r in canonical}
    assert by_name["SYMPTOMATIC_EPILEPSY"]["first_documented_date"] == "2006"
    assert by_name["CEREBRAL_AMYLOID_ANGIOPATHY"]["first_documented_date"] == "2020-01-01"
