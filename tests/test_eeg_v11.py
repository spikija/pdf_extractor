"""Physician EEG 1.1 semantics plus independent negative/scope regressions."""
import pytest
from caa_pdf_extractor.extraction.eeg_pipeline_v11 import assemble
from caa_pdf_extractor.extraction.eeg_schema_v11 import defaults,validate_value


def result(text):
    bundle=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text='EEG Befund (01.02.2020)\n'+text)])])
    return assemble(bundle)


def exam(text):return result('Abnormes EEG. '+text)['eeg_exams'][0]


def test_three_regions_extent_and_modifier_scope():
    e=exam('Nahezu kontinuierliche Theta-Delta-Aktivität links fronto-zentral bis temporal vorne. Kontinuierlich verlangsamter Basisrhythmus.')
    assert e['dominant_focus_regions']==['FRONTAL','CENTRAL','TEMPORAL']
    assert e['focal_slowing_region']=='OTHER'
    assert e['anterior_posterior_extent']=='ANTERIOR'
    assert e['focal_slowing_continuity']=='NEAR_CONTINUOUS'
    assert e['background_slowing_continuity']=='CONTINUOUS' and e['background_continuity']=='UNKNOWN'


def test_spread_is_not_bilateral_primary_and_frequency_belongs_to_spread():
    e=exam('Kontinuierliche Theta-Delta-Aktivität über rechts fronto-temporal, häufig auch mit Ausbreitung nach links fronto-temporal.')
    assert e['focal_slowing_side']==e['dominant_focus_side']=='RIGHT'
    assert e['spread_side']=='LEFT'
    assert {f['relationship'] for f in e['focal_abnormalities']}=={'PRIMARY','SPREAD'}
    assert next(f for f in e['focal_abnormalities'] if f['relationship']=='PRIMARY')['frequency']=='UNKNOWN'
    assert next(f for f in e['focal_abnormalities'] if f['relationship']=='SPREAD')['frequency']=='FREQUENT'


def test_secondary_focus_differs_from_spread():
    e=exam('Nahezu kontinuierliche Theta-Delta-Aktivität rechts fronto-zentral. Seltener zeigt sich unabhängig davon eine Theta-Delta-Aktivität links fronto-temporal mit superponierten sharp-waves. Seltener breiten sich die sharp-waves beidseits fronto-zentral aus.')
    assert e['dominant_focus_side']=='RIGHT'
    secondary=next(f for f in e['focal_abnormalities'] if f['relationship']=='SECONDARY_INDEPENDENT')
    assert secondary['side']=='LEFT' and secondary['epileptiform_component'] is True and secondary['frequency']=='OCCASIONAL'
    assert any(f['relationship']=='SPREAD' and f['side']=='BILATERAL' for f in e['focal_abnormalities'])


@pytest.mark.parametrize('reference',['davon','hiervon'])
def test_elliptical_independent_focus_keeps_antecedent(reference):
    e=exam(f'Intermittierende Theta-Delta-Aktivität rechts frontotemporal sowie unabhängig {reference} über links fronto-zentro-temporal.')
    assert e['dominant_focus_side']=='RIGHT'
    assert any(f['relationship']=='SECONDARY_INDEPENDENT' and f['side']=='LEFT' and f['regions']==['FRONTAL','CENTRAL','TEMPORAL'] for f in e['focal_abnormalities'])


def test_bilateral_asymmetry_and_multiple_patterns():
    e=exam('Intermittierende z. T. rhythmische Delta-Theta-Gruppen temporal beidseits, links deutlich mehr als rechts, sowie intermittierende generalisierte Delta-Gruppen mit frontalem Maximum.')
    assert e['focal_slowing_side']=='BILATERAL' and e['focal_slowing_asymmetry']=='LEFT_PREDOMINANT'
    assert e['dominant_focus_regions']==['TEMPORAL']
    assert any(p['kind']=='RHYTHMIC_THETA_DELTA' and p['distribution']=='BILATERAL' for p in e['slow_patterns'])
    assert any(p['kind']=='GENERALIZED_SLOWING' and p['maximum']=='FRONTAL' for p in e['slow_patterns'])


def test_firda_asymmetry_does_not_make_focal_slowing():
    e=exam('Häufig intermittierende generalisierte rhythmische Delta-Gruppen mit frontalem Maximum, deutlich links betont (FIRDA).')
    assert e['rhythmic_delta_type']=='FIRDA'
    assert e['pattern_asymmetry']=='LEFT_PREDOMINANT'
    assert e['focal_slowing_present'] is None and not e['focal_abnormalities']
    assert e['rhythmic_patterns'][0]['modern_pattern_family']=='GRDA'


def test_firda_and_true_independent_focal_slowing_coexist():
    e=exam('Bifrontale Delta-Wellen in Form von FIRDA. Intermittierende Delta-Aktivität über links temporal Mitte bis vorne.')
    assert e['rhythmic_delta_type']=='FIRDA' and e['focal_slowing_present'] is True
    assert e['dominant_focus_side']=='LEFT' and e['anterior_posterior_extent']=='MID_TO_ANTERIOR'
    assert {'RHYTHMIC_DELTA_ACTIVITY','FOCAL_SLOWING'}<=set(e['clinical_phenotype'])


@pytest.mark.parametrize('source,stimulus,response',[
 ('Fehlende Reagibilität auf Ansprache.','VERBAL','ABSENT'),
 ('Erhaltene Reaktion auf taktilen Reiz.','TACTILE','PRESENT'),
 ('Erhaltene Reaktion auf exogene Reize.','EXTERNAL','PRESENT'),
])
def test_stimulus_specific_reactivity_is_not_global(source,stimulus,response):
    e=exam(source)
    assert e['reactivity_stimulus']==stimulus and e['reactivity_to_external_stimuli']==response
    assert e['background_reactivity']=='UNKNOWN'


def test_mixed_stimulus_responses_remain_separate():
    e=exam('Fehlende Reagibilität auf Ansprache. Erhaltene Reaktion auf taktilen Reiz.')
    assert e['reactivity_stimulus']=='MULTIPLE' and e['reactivity_to_external_stimuli']=='UNKNOWN'
    assert len(e['reactivity_findings'])==2


@pytest.mark.parametrize('source,expected',[
 ('Soweit bei Artefaktüberlagerung beurteilbar mäßig abnormes EEG.','LIMITED'),
 ('Soweit beurteilbar mäßig abnormes EEG.','LIMITED'),
 ('Bei starker Muskel- und Bewegungsartefaktüberlagerung mäßig abnormes EEG.','LIMITED'),
 ('EEG aufgrund massiver Artefakte nicht beurteilbar.','NOT_ASSESSABLE'),
 ('Grundaktivität vollständig beurteilbar.','FULL'),
 ('Normales EEG.','UNKNOWN'),
])
def test_assessability(source,expected):assert exam(source)['assessability']==expected


@pytest.mark.parametrize('source,present,confirmed,status',[
 ('Keine eindeutigen epileptiformen Potenziale.',None,False,'NO_DEFINITE_EVIDENCE'),
 ('Kein sicherer Hinweis auf epilepsietypische Potentiale.',None,False,'NO_DEFINITE_EVIDENCE'),
 ('Keine epileptiformen Potenziale.',False,False,'EXPLICITLY_ABSENT'),
 ('Fragliche epileptiforme Potenziale.',None,None,'UNCERTAIN'),
 ('Epileptiforme Potenziale links frontal.',True,True,'CONFIRMED'),
 ('Normales EEG.',None,None,'UNKNOWN'),
])
def test_evidence_states(source,present,confirmed,status):
    e=exam(source)
    assert e['epileptiform_discharge_present'] is present
    assert e['epileptiform_confirmed'] is confirmed and e['epileptiform_evidence_status']==status
    assert e['seizure_recorded'] is None and e['electrographic_status_epilepticus'] is None


def test_ictal_no_definite_evidence_and_clinical_vs_electrographic_status():
    e=exam('Keine eindeutige iktale Aktivität. Keine klinischen Zeichen eines nicht-konvulsiven Status epilepticus.')
    assert e['ictal_activity_status']=='NO_DEFINITE_EVIDENCE' and e['seizure_recorded'] is None
    assert e['clinical_signs_of_NCSE']=='ABSENT' and e['electrographic_status_epilepticus'] is None
    assert exam('Kein Status epilepticus-Muster.')['electrographic_status_epilepticus'] is False


def test_broad_sharp_waves_have_explicit_morphology_and_focal_distribution():
    e=exam('Links fronto-zentral bis temporal vorne intermittierende Theta-Delta-Aktivität mit vereinzelt eingelagerten broad sharp-waves.')
    assert e['epileptiform_discharge_type']=='SHARP_WAVE' and e['morphology_modifier']=='BROAD'
    assert e['epileptiform_frequency']=='OCCASIONAL' and e['epileptiform_distribution']=='FOCAL'
    assert e['seizure_recorded'] is None and e['electrographic_status_epilepticus'] is None


def test_generalized_theta_and_source_grade():
    e=result('Leicht pathologisches EEG mit generalisierter Theta-Aktivität um 4-5/sec. mit bifrontalem Maximum.')['eeg_exams'][0]
    assert e['eeg_primary_finding']=='GENERALIZED_SLOWING' and e['focal_slowing_present'] is None
    assert e['generalized_slowing_maximum']=='BIFRONTAL'
    assert e['source_reported_abnormality_grade']=='MILDLY_ABNORMAL'


def test_hard_ecg_boundary_and_internal_channel():
    r=result('Mäßig abnormes EEG. Im EKG tachykarder Rhythmus. Keine epileptiformen Potenziale.\n24-STUNDEN-EKG (16.01.2012):\nSinusrhythmus. 200 ventrikuläre Extrasystolen. Vorhofflimmern.')
    e=r['eeg_exams'][0]
    assert '24-STUNDEN-EKG' not in e['eeg_report_text'] and 'Vorhofflimmern' not in e['eeg_report_text']
    assert e['heart_rate_category']=='TACHYCARDIC' and e['ecg_rhythm']=='UNKNOWN'
    assert e['ventricular_extrasystoles'] is None


def test_secondary_bilateral_synchrony_remains_focal():
    e=exam('Kontinuierliche Theta-Delta-Aktivität links temporal vorne, gelegentlich mit Ausbreitung auf die gesamte linke Hemisphäre und sekundärer bilateraler Synchronie. Keine epileptiformen Potenziale.')
    assert e['secondary_bilateral_synchrony'] is True
    assert e['dominant_focus_side']=='LEFT' and e['epileptiform_discharge_present'] is False
    assert 'GENERALIZED_EPILEPTIFORM_ACTIVITY' not in e['clinical_phenotype']
    assert any(f['relationship']=='PRIMARY' and f['secondary_bilateral_synchrony'] for f in e['focal_abnormalities'])


def test_negated_spread_and_rhythmic_pattern_not_promoted():
    e=exam('Kontinuierliche Theta-Delta-Aktivität rechts temporal. Keine Ausbreitung nach links temporal. Keine FIRDA.')
    assert not any(f['relationship']=='SPREAD' for f in e['focal_abnormalities'])
    assert e['spread_present'] is False and e['rhythmic_delta_activity_present'] is False


def test_indication_does_not_create_finding_and_schema_validates_every_field():
    r=result('Indikation: V.a. Epilepsie und fokale Verlangsamung links temporal.\nBefund: Normales EEG.')
    e=r['eeg_exams'][0]
    assert e['focal_slowing_present'] is None and not e['focal_abnormalities'] and e['epileptiform_confirmed'] is None
    for field in defaults():validate_value(field,e[field])
    assert all(f['document_id']==10 and f['page']==1 and f['section_id']=='p1' and f['evidence_id'] for f in r['eeg_findings'])


@pytest.mark.parametrize('source,expected',[
 ('Epileptiform: [ ] ja [ ] nein',None),
 ('Epileptiform: [x] ja [ ] nein',True),
 ('Epileptiform: [ ] ja [x] nein',False),
])
def test_form_assertions_and_unknown_are_preserved(source,expected):
    assert exam(source)['epileptiform_discharge_present'] is expected


def test_review_of_one_focus_preserves_other_foci_and_recomputes_summaries(tmp_path):
    import copy
    from caa_pdf_extractor.workbench_eeg import reviewed_exam,validate_edits
    from review_app.review_repository import ReviewRepository
    r=result('Abnormes EEG. Kontinuierliche Theta-Delta-Aktivität rechts frontal. Seltener unabhängig davon Theta-Delta-Aktivität links temporal.')
    e=r['eeg_exams'][0];fs=r['eeg_findings'];raw=copy.deepcopy(e)
    secondary=next(f for f in fs if f['field']=='focal_abnormalities' and f['value'][0]['relationship']=='SECONDARY_INDEPENDENT')
    repo=ReviewRepository(tmp_path)
    repo.save('123','eeg',secondary['finding_id'],'REJECT',secondary,{},'1.1','hash')
    reviewed=reviewed_exam(e,fs,repo.load('123','eeg'),'hash')
    assert reviewed['dominant_focus_side']=='RIGHT' and reviewed['focal_slowing_side']=='RIGHT'
    assert len(reviewed['focal_abnormalities'])==1 and e==raw
    assert len(reviewed_exam(e,fs,repo.load('123','eeg'),'changed')['focal_abnormalities'])==2
    edited=copy.deepcopy(secondary['value']);edited[0]['regions']=['FRONTAL','CENTRAL','TEMPORAL']
    repo.save('123','eeg',secondary['finding_id'],'EDIT',secondary,validate_edits(secondary,{'value':edited}),'1.1','hash')
    updated=reviewed_exam(e,fs,repo.load('123','eeg'),'hash')
    assert len(updated['focal_abnormalities'])==2 and updated['focal_slowing_regions']==['FRONTAL','CENTRAL','TEMPORAL']
    assert updated['dominant_focus_side']=='RIGHT'
    with pytest.raises(ValueError):validate_edits(e,{'dominant_focus_side':'LEFT'})
    repo.save('123','eeg',e['eeg_exam_id'],'REJECT',e,{},'1.1','hash')
    rejected=reviewed_exam(e,fs,repo.load('123','eeg'),'hash')
    assert rejected['focal_slowing_present'] is None and rejected['epileptiform_discharge_present'] is None


def test_source_span_on_second_page_is_exact():
    b=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[
        dict(section_id='p1',page_start=1,page_end=1,text='EEG Befund (01.02.2020)\nAbnormes EEG.\n'),
        dict(section_id='p2',page_start=2,page_end=2,text='Seltener unabhängig davon intermittierende Theta-Aktivität links temporal Mitte bis vorne.')])])
    r=assemble(b);focus=next(f for f in r['eeg_findings'] if f['field']=='focal_abnormalities')
    assert focus['page']==2 and focus['section_id']=='p2'
    assert r['eeg_exams'][0]['source_page_end']==2
