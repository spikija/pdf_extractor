"""Morphology and modifiers have independent, source-exact assertion owners."""
import pytest
from test_radiology_v2_pet import findings

@pytest.mark.parametrize('text',[
 'Nach intravenöser Kontrastmittelapplikation zeigt sich keine Aufladung im Randbereich der Blutung.',
 'Keine raumfordernde Blutung.',
 'Keine größere intrakranielle Blutung.',
 'Keine frische raumfordernde Blutung intrakraniell.',
 'Kein Nachweis einer rezenten flächigen zerebralen Blutung.',
 'Kein Anhaltspunkt für eine neoplastisch assoziierte Blutung.',
 'Atypische Parenchymblutung rechts frontal. Keine relevante raumfordernde Wirkung.',
 'Keine suffiziente Aussage möglich - nach Resorption der Blutungsareale wird ein MRI empfohlen.',
 'Keine Zeichen solider Kontrastmittel aufnehmender Veränderungen innerhalb des Blutungsareales als Ausdruck einer Tumorblutung.',
 'Kein eindeutig pathologisches Kontrastmittelenhancement.',
])
def test_modifier_or_qualified_absence_never_becomes_no_hemorrhage(text):
    e=findings(text)
    assert not any(n['finding_type'] in ('NO_ICH','NO_ACUTE_HEMORRHAGE') for n in e['negative_findings'])
    assert e['other_assertions']

@pytest.mark.parametrize('text',[
 'Lobäre Blutung rechts frontal ohne pathologische KM-Aufnahme.',
 'Kein Hinweis auf vaskulitische Genese der lobären Blutung rechts frontal.',
 'Lobäre Blutung rechts frontal möglicherweise tumorbedingt.',
 'Lobäre Blutung rechts frontal, keine Raumforderung.',
 'Blutung rechts frontal wird sich vermutlich resorbieren.',
 'Links frontal zeigt sich eine Blutung im subakuten Stadium, weiters im Bereich der Sulci Hämorrhagien.',
 'Atypische Blutung frontal rechts und primär rechtshemisphärische superfizielle Hämosiderose ohne Nachweis einer Blutungsquelle.',
 'Das Blutungsareal rechts hochfrontolateral zeigt sich am Übergang vom subakuten ins chron. Stadium. Hämosiderose - posttraumatisch?',
])
def test_definite_morphology_survives_neighboring_modifiers(text):
    e=findings(text)
    assert any(f['finding_type']=='ICH' for f in e['findings'])
    assert not any(n['finding_type'] in ('NO_ICH','NO_ACUTE_HEMORRHAGE') for n in e['negative_findings'])

@pytest.mark.parametrize('text',[
 'Keine sichere Blutung.','Blutung nicht sicher abgrenzbar.',
 'Kein eindeutig pathologisches Kontrastmittelenhancement.',
])
def test_uncertainty_not_absent(text):
    e=findings(text);assert not e['findings'] and not e['negative_findings'] and e['uncertain_findings']

def test_enhancement_absence_and_ich_are_separate_concepts():
    e=findings('Lobäre Blutung rechts frontal ohne pathologische KM-Aufnahme.')
    assert [x['finding_type'] for x in e['negative_findings']]==['NO_ENHANCEMENT']
    assert any(x['concept']=='ENHANCEMENT' and x['assertion_state']=='ABSENT' for x in e['other_assertions'])

def test_shared_noun_new_and_old_cmb_components():
    fs=findings('Neue und alte Mikroblutungen rechts frontal.')['findings']
    assert {(f['temporal_status'],f['acute_lesion'],f['interval_new']) for f in fs}=={('INTERVAL_NEW',None,True),('CHRONIC',False,False)}

def test_mixed_ich_age_components_do_not_collapse():
    text='Geringe Blutung im rechten Mediastromgebiet fast resorbiert und etwa unverändert die links suprainsuläre frontale akute Blutung.'
    fs=[f for f in findings(text)['findings'] if f['finding_type']=='ICH']
    assert {f['temporal_status'] for f in fs}=={'CHRONIC','ACUTE'}
    assert all(f['evidence_text'] in text for f in fs)

@pytest.mark.parametrize('text,expected',[
 ('Keine Blutung.','NO_ACUTE_HEMORRHAGE'),
 ('Keine frische Ischämie.','NO_ACUTE_ISCHEMIA'),
 ('Kein Enhancement.','NO_ENHANCEMENT'),
])
def test_direct_negation_still_generates_exact_negative(text,expected):
    e=findings(text)
    assert expected in {f['finding_type'] for f in e['negative_findings']} and not e['findings']

@pytest.mark.parametrize('text',[
 'Kein aktiver Blutungsnachweis der atypischen Parenchymblutung links frontal.',
 'Kein Nachweis einer neu aufgetretenen eindeutig demarkierten Ischämie bzw. Blutung.',
 'Kein Nachweis einer demarkierten Ischämie-oder Blutung.',
 'Keine wesentliche Raumforderung!',
])
def test_qualified_visibility_activity_and_mass_effect_remain_audit_only(text):
    e=findings(text)
    assert not e['negative_findings']
    assert any(c['qualified'] for c in e['other_assertions'])

def test_alternative_differential_does_not_become_definite_infarct():
    e=findings('Das hämorrhagische Areal -Differenzialdiagnose v.a. hypertensive Blutung, hämorrhagische Infarkt- größenmäßig nicht wesentlich verändert.')
    assert not e['findings'] and e['uncertain_findings']

def test_category_label_governs_age_without_creating_second_cmb():
    e=findings('Hämosiderin/alte Mikroblutungen: Fokale Mikroblutungen temporal rechts, parietal links.')
    fs=[f for f in e['findings'] if f['finding_type']=='CMB']
    assert len(fs)==1 and fs[0]['temporal_status']=='CHRONIC'
    assert any(r['reason']=='CATEGORY_LABEL_CONTEXT_ONLY' for r in e['rejected_candidates'])
