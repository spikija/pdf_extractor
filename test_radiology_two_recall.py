"""Permanent source fixtures: pinhead CMB and compound acute-SAH CT."""
import json
from pathlib import Path
import pytest
from test_radiology_v2_pet import bundle
from caa_pdf_extractor.extraction.radiology_v2 import normalize

FIXTURES=json.loads((Path(__file__).parent/'tests/fixtures/radiology_two_recall.json').read_text(encoding='utf-8'))

def exam(text,header='MRT: Schädel (28.08.2017)'):
    result=normalize(bundle(text,header),42)
    assert len(result)==1
    return result[0]

def test_patient_2561410517_pinhead_bihemispheric_hemosiderin_fixture():
    fixture=FIXTURES['mri_2561410517'];r=exam(fixture['report'],fixture['header'])
    assert len(r['findings'])==1
    f=r['findings'][0]
    for key in ('finding_type','temporal_status','acute_lesion','location'):assert f[key]==fixture[key]
    assert f['assertion_state']=='PRESENT' and f['laterality']=='bilateral'
    assert f['evidence_text']==fixture['report']
    assert not any(f.get(k) for k in ('lobar','cortical','distribution','caa_etiology'))
    assert not f['attributes']

@pytest.mark.parametrize('morphology',[
 'punktförmige Hämosiderinablagerungen','punktförmige Hämosiderinauflagerungen',
 'stecknadelkopfgroße Hämosiderinablagerungen','stecknadelkopfgroße Hämosiderinauflagerungen',
 'multiple kleine Hämosiderinherde','zahlreiche kleine Hämosiderinherde','Mikro-Hämosiderinresiduen',
])
def test_specific_cmb_morphology_is_present_chronic(morphology):
    r=exam(morphology+'.');f=next(f for f in r['findings'] if f['finding_type']=='CMB')
    assert (f['assertion_state'],f['temporal_status'],f['acute_lesion'])==('PRESENT','CHRONIC',False)

@pytest.mark.parametrize('text',[
 'Hämosiderin am Rand einer alten ICB.', 'Kortikale oberflächliche Hämosiderinauflagerungen.',
 'Unspezifische Suszeptibilitätsartefakte.','Hämosiderinablagerungen.',
 'Keine punktförmigen Hämosiderinauflagerungen.', 'Fragliche punktförmige Hämosiderinablagerungen.',
])
def test_generic_superficial_negated_and_uncertain_hemosiderin_are_not_cmb(text):
    assert not any(f['finding_type']=='CMB' for f in exam(text)['findings'])

def test_superficial_hemosiderin_deposits_evaluate_as_css():
    assert [f['finding_type'] for f in exam('Kortikale oberflächliche Hämosiderinauflagerungen.')['findings']]==['CSS']

def test_patient_2586499918_compound_ct_fixture_keeps_all_atomic_findings():
    fixture=FIXTURES['ct_2586499918'];r=exam(fixture['report'],fixture['header'])
    e=r['exam'];assert (e['modality'],e['exam_date'],e['exam_date_source'],e['anatomic_scope'],e['performed'])==('CT','2018-02-20','REPORT_EXPLICIT','BRAIN',True)
    assert len(r['findings'])==3
    for expected in fixture['findings']:
        f=next(f for f in r['findings'] if f['finding_type']==expected['finding_type'])
        assert f['assertion_state']=='PRESENT'
        for key,value in expected.items():
            if key=='evidence':assert value in f['evidence_text']
            else:assert f[key]==value
    assert not any(f['finding_type'] in ('ICH','OTHER') for f in r['findings'])
    assert not any('Plaques' in f['evidence_text'] or 'Sinusitis' in f['evidence_text'] for f in r['findings'])

@pytest.mark.parametrize('morphology',[
 'Subarachnoidalblutung','Subarachnoidalblutungen','SAB','cSAH','konvexitäre Subarachnoidalblutung',
 'kortikale Subarachnoidalblutung','Sulcusblutung','sulcale Blutung',
])
def test_explicit_sah_compartment_never_becomes_ich(morphology):
    r=exam('Frische '+morphology+' rechts frontal.','CT Schädel (20.02.2018)')
    assert {f['finding_type'] for f in r['findings']}=={'SAH'}
    f=r['findings'][0];assert (f['temporal_status'],f['acute_lesion'],f['compartment'])==('ACUTE',True,'subarachnoid')

@pytest.mark.parametrize('prefix,status,acute',[
 ('Akute','ACUTE',True),('Rezente','ACUTE',True),('Neu aufgetretene','ACUTE',True),
 ('Alte','CHRONIC',False),('Chronische','CHRONIC',False),('Residuelle','CHRONIC',False),('Z.n.','CHRONIC',False),
])
def test_sah_owned_temporal_adjectives(prefix,status,acute):
    f=next(f for f in exam(prefix+' Subarachnoidalblutung.')['findings'] if f['finding_type']=='SAH')
    assert (f['temporal_status'],f['acute_lesion'])==(status,acute)

@pytest.mark.parametrize('text',[
 'V.a. SAB.','Fragliche SAB.','SAB nicht auszuschließen.','V.a. Subarachnoidalblutung.',
 'Blutung frontal.','Subarachnoidalraum unauffällig.',
])
def test_uncertain_or_unstated_compartment_is_not_positive_sah(text):
    r=exam(text)
    assert not any(f['finding_type']=='SAH' for f in r['findings'])
    if 'V.a.' in text or 'Fragliche' in text or 'auszuschließen' in text:assert r['uncertain_findings']

@pytest.mark.parametrize('morphology',['Subarachnoidalblutung','sulcale Blutung','Sulcusblutung'])
def test_negative_sah_is_specific_and_not_positive(morphology):
    r=exam('Keine '+morphology+'.')
    assert not r['findings']
    assert {f['finding_type'] for f in r['negative_findings']}=={'NO_SAH'}

@pytest.mark.parametrize('header',[
 'Computertomografie: Schädel nativ (20.02.2018)','CCT (20.02.2018)',
 'CT Schädel (20.02.2018)','CT Cerebrum (20.02.2018)','CT Neurokranium (20.02.2018)',
])
def test_cranial_ct_alias_retains_empty_parent_and_owned_date(header):
    r=exam('Voruntersuchung vom 01.11.2017. Unauffälliger Befund.',header)
    e=r['exam'];assert (e['modality'],e['exam_date'],e['exam_date_source'],e['anatomic_scope'],e['performed'])==('CT','2018-02-20','REPORT_EXPLICIT','BRAIN',True)
    assert not r['findings']
