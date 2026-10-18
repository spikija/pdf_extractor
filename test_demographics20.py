"""Source-census demographic recall and paired safety controls, without live writes."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from caa_pdf_extractor.extraction import case_rules as rules
from caa_pdf_extractor.extraction import case
from test_case import bundle, facts


@pytest.mark.parametrize('text,value',[
 ('geboren am 03.08.1935','1935-08-03'),('DOB: 1935-08-03','1935-08-03'),
 ('Geburtsdatum: 03.08.1935','1935-08-03'),('date of birth: 03.08.1935','1935-08-03')])
def test_full_explicit_birth_forms(text,value):
 assert facts(text)['update_fields']=={'date_of_birth':value}


@pytest.mark.parametrize('text',[
 '74-jähriger Patient','Jahrgang 1949','geboren 1949','geboren am 02.1949',
 'Mutter geboren am 03.08.1935','Frau Anna','Herr Peter','sie berichtet',
 'MRI am 03.08.1935','Geburtsdatum: unbekannt'])
def test_no_derived_birth_or_inferred_sex(text):
 o=facts(text)['update_fields'];assert 'date_of_birth' not in o and 'gender' not in o


@pytest.mark.parametrize('text,value',[
 ('Nikotin: Nieraucher','never'),('Nieraucher','never'),('nie geraucht','never'),
 ('Nikotin: nicht bekannt','unknown'),('Nikotin: Nicht bekannt.','unknown'),
 ('Nikotinabusus, faktisch negativ (zuletzt vor etwa 50 Jahren).','former'),
 ('Nikotinabusus, faktisch negativ (zuletzt vor 50 Jahren).','former')])
def test_census_smoking_states(text,value):
 o=facts(text);assert o['update_fields']=={'smoking':value}
 assert all(e['evidence'] in text for e in o['provenance']['smoking'])


@pytest.mark.parametrize('text',[
 'Nichtraucher','aktuell Nichtraucher','Nikotin: negiert.','Nikotin: negativ.',
 'Nikotin: Nein','Nikotin 0','Nikotinkarenz empfohlen','Nikotinabusus, faktisch negativ.',
 'Mutter: Nieraucher','Nikotin: nicht bekannt?','falls Nieraucher','vermutlich Nieraucher',
 'Nieraucher empfohlen','Nikotinabusus geplant, faktisch negativ (zuletzt vor etwa 50 Jahren).'])
def test_no_lifetime_or_former_state_from_nonassertions(text):
 assert 'smoking' not in facts(text)['update_fields']


def test_current_non_smoking_does_not_override_explicit_former_history():
 assert facts('Ex-Raucher\naktuell Nichtraucher')['update_fields']=={'smoking':'former'}


@pytest.mark.parametrize('text',[
 '40 - 50 p/y','40–50 PY','pack-years: 40-50','40 - 50 pack-years',
 '40 - 50 p/y ex 2013.','20 Zigaretten/Tag seit 30 Jahren'])
def test_no_range_endpoint_or_calculated_pack_years(text):
 assert 'pack_years' not in facts(text)['update_fields']


def test_exact_pack_years_retains_approximation_and_agreeing_range():
 o=facts('Nikotin ex vor einigen Jahren, insgesamt ca. 40 p/y.')
 assert o['update_fields']=={'smoking':'former','pack_years':40}
 assert o['provenance']['pack_years'][0]['approximate']
 o=facts('Nikotin ex 2013 (50 p/y)\nNikotin: 40 - 50 p/y ex 2013.')
 assert o['update_fields']['pack_years']==50 and len(o['provenance']['pack_years'])==1
 assert o['demographic_assessments'][0]['range']==[40,50]


@pytest.mark.parametrize('text',['40 p/y\n50 p/y','50 p/y\n60 - 80 p/y'])
def test_material_pack_year_conflicts_block(text):
 o=facts(text);assert 'pack_years' not in o['update_fields'] and o['source_conflicts']


@pytest.mark.parametrize('text,value',[
 ('Alkohol: nein','never'),('Alkohol: Nein.','never'),('C2: selten.','active'),
 ('gelegentlich Alkohol','active'),('früher Alkohol, jetzt abstinent','former')])
def test_explicit_alcohol_states(text,value):
 assert facts(text)['update_fields']['alcohol']==value


@pytest.mark.parametrize('text',[
 'C2/C3: Stenose','früher C2: selten','Alkoholwert <0.08','falls Alkohol: nein',
 'Alkohol: nein?','Mutter: Alkohol: nein','vermutlich C2: selten'])
def test_alcohol_nonstatus_and_historical_controls(text):
 assert 'alcohol' not in facts(text)['update_fields']


def test_current_denial_and_rare_use_remain_unresolved():
 o=facts('Alkohol: aktuell garnicht\nNoxen: Nikotin: Nieraucher, C2: selten.')
 assert o['update_fields']=={'smoking':'never'}
 assert any(a.get('disposition')=='OMITTED_UNRESOLVED_OR_CONTRADICTORY' for a in o['alcohol_assessments'])


def test_direct_death_cause_is_not_a_death_date_or_inferred_diagnosis():
 text='1. Exitus letalis bei zentralem Regulationsversagen bei Diagnose 2'
 o=facts(text);assert o['update_fields']=={'death_cause':'zentralem Regulationsversagen'}
 assert o['provenance']['death_cause'][0]['evidence'] in text


@pytest.mark.parametrize('text',[
 'Exitus letalis bei Diagnose 2','zentralem Regulationsversagen','Todesursache vermutlich Pneumonie',
 'Fraglich Exitus letalis bei zentralem Regulationsversagen',
 'droht Exitus letalis bei zentralem Regulationsversagen','Mutter: Exitus letalis bei zentralem Regulationsversagen',
 'Obduktion erforderlich','Zuweisung für eine Obduktion','vermutlich verstorben am 03.08.2025',
 'Exitus letalis bei zentralem Regulationsversagen?'])
def test_no_inferred_or_conditional_death_or_autopsy(text):
 o=facts(text)['update_fields'];assert not any(k in o for k in ('death_cause','death_date','autopsy'))


@pytest.mark.parametrize('text,value',[
 ('Exitus letalis am 20.05.2025','2025-05-20'),
 ('Am 22.05.2022 um 07:19 Uhr verstarb Herr Pohl überraschend.','2022-05-22')])
def test_known_death_fixtures(text,value):
 assert facts(text)['update_fields']['death_date']==value


def test_known_CAA_and_lifestyle_fixtures():
 assert facts('CAA nach Boston-Kriterien 2.0')['update_fields']['caa_criteria']=='Boston Criteria 2.0'
 o=facts('Nach Boston Criteria 2.0 ist eine Amyloidangiopathie zumindest\n möglich, je nach Klinik gegebenenfalls auch wahrscheinlich.')
 assert o['update_fields']=={'caa_criteria':'Boston Criteria 2.0'}
 assert 'caa_diagnosis_date' not in facts('MRI 20.05.2025: probable CAA')['update_fields']
 assert facts('Nikotin ex 2013 (50 p/y)')['update_fields']=={'smoking':'former','pack_years':50}
 assert facts('regel- und leicht übermäßiger Alkoholkonsum')['update_fields']['alcohol']=='active'


def test_curated_values_never_overwritten():
 updates,unchanged,conflicts=rules.conflict_check({'pack_years':40,'smoking':None},{'pack_years':50,'smoking':'former'})
 assert updates=={'smoking':'former'} and conflicts[0]['current']==40


def test_rule_changes_stale_old_fingerprint_and_no_inference():
 b=bundle('geboren am 03.08.1935');inputs=rules.select_inputs(b)
 with patch('caa_pdf_extractor.extraction.case_rules.Path.read_text',return_value='old rules'):
  old=rules.fingerprint(b,inputs)
 assert old!=rules.fingerprint(b,inputs)
 # case_v1's public client is unused in both dry-run and commit paths;
 # existing artifact/commit tests exercise persistence and idempotency.
 assert 'client.' not in __import__('inspect').getsource(case.extract_patient)
