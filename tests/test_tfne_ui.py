"""TFNE episode review actions, structural corrections and immutable candidate bytes."""
import copy
import json
import pytest
from streamlit.testing.v1 import AppTest

from caa_pdf_extractor.tfne_schema import defaults
from caa_pdf_extractor.tfne_screen import derive
from review_app.tfne_repository import TFNEReviewRepository,project
from caa_pdf_extractor.workbench_store import ROOT,read,atomic,sha

EVIDENCE=ROOT/'data/debug/tfne_pilot_v0_1_20261101T130202_586729Z'


def artifact():
    source=dict(document_id=10,source_document_id=10,page=1,section_id='narrative',parent_section_id='p1',section_type='ANAMNESE',
        evidence_text='15 Minuten Aphasie, vollständig rückläufig, historisch als Epilepsie beurteilt.',evidence_start=0,evidence_end=81,evidence_id='ev')
    e=defaults();e.update(episode_id='one',symptom_description='Aphasie',temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',focality='FOCAL',symptom_domains=['LANGUAGE'],historical_diagnosis='EPILEPSY',
        evidence=[source],field_evidence={'symptom_description':[source]},physician_review_status='UNREVIEWED');e.update(derive(e))
    return dict(patient_id='123',module_version='0.1',episodes=[e],validation_flags=[],granite_prompt_name='tfne_neurological_deficit_v0_1',granite_model_name='granite4.2:latest')


@pytest.mark.parametrize('action',['ACCEPT','EDIT','REJECT','UNCERTAIN'])
def test_tfne_actions_are_separate_hash_scoped_overlays(tmp_path,action):
    a=artifact();before=copy.deepcopy(a);repo=TFNEReviewRepository(tmp_path)
    repo.save(a,'a'*64,['one'],action,{'temporal_course':'PERSISTENT','fully_resolved':'FALSE'} if action=='EDIT' else {})
    result=project(a,repo.load('123','a'*64))
    assert result[0]['review_status']==action and a==before
    assert repo.load('123','b'*64)==[]
    if action=='EDIT':assert result[0]['TFNE_clinical_screen']=='TFNE_SCREEN_NEGATIVE'


def test_split_merge_and_reload_preserve_original_history(tmp_path):
    a=artifact();before=copy.deepcopy(a);repo=TFNEReviewRepository(tmp_path)
    repo.save(a,'a'*64,['one'],'SPLIT',[dict(symptom_description='Episode A'),dict(symptom_description='Episode B')])
    split=project(a,repo.load('123','a'*64));assert len(split)==2
    assert all(e['historical_diagnosis']=='EPILEPSY' for e in split)
    repo.save(a,'a'*64,[e['episode_id'] for e in split],'MERGE',dict(symptom_description='One recurrent series'))
    merged=project(a,TFNEReviewRepository(tmp_path).load('123','a'*64))
    assert len(merged)==1 and merged[0]['temporal_course']=='UNKNOWN'
    assert merged[0]['merged_historical_diagnoses']==['EPILEPSY','EPILEPSY'] and a==before
    assert merged[0]['historical_diagnosis']=='EPILEPSY'
    with pytest.raises(ValueError):repo.save(a,'a'*64,[merged[0]['episode_id']],'EDIT',dict(TFNE_clinical_screen='TFNE_SCREEN_STRONG'))


def test_tfne_cards_show_axes_and_do_not_save_without_action(tmp_path,monkeypatch):
    from review_app.views import tfne
    a=artifact();monkeypatch.setattr(tfne,'load_artifact',lambda *args:(a,'a'*64))
    app=AppTest.from_string('from review_app.views.tfne import render\n'+f'render("123",[],review_root={str(tmp_path)!r})').run()
    assert not app.exception,[e.message for e in app.exception]
    assert any('TEMPORAL COURSE: TRANSIENT' in m.value for m in app.markdown)
    assert any('Historical diagnosis: EPILEPSY' in m.value for m in app.markdown)
    assert any('Retrospective clinical phenotype: TFNE_POSSIBLE' in m.value for m in app.markdown)
    assert any('SPREADING: UNKNOWN' in m.value for m in app.markdown)
    assert not list(tmp_path.rglob('events.json'))
    next(s for s in app.selectbox if s.label=='TFNE review action').select('ACCEPT')
    next(b for b in app.button if b.label=='Save TFNE review').click().run()
    assert not app.exception and any('Review status: ACCEPT' in c.value for c in app.caption)


@pytest.mark.skipif(not (EVIDENCE/'published_artifacts.json').exists(),reason='Local TFNE pilot unavailable')
def test_all_ten_tfne_patients_source_links_and_isolated_review_reload(tmp_path,monkeypatch,request):
    import streamlit as st
    from review_app import artifact_repository
    published=read(EVIDENCE/'published_artifacts.json');index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    original=TFNEReviewRepository.__init__
    monkeypatch.setattr(TFNEReviewRepository,'__init__',lambda self,root=None:original(self,root or tmp_path/'reviews'))
    st.cache_data.clear();request.addfinalizer(st.cache_data.clear)
    before={e['path']:sha(e['path']) for e in published.values()}
    app=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    next(s for s in app.selectbox if s.label=='Review queue').select('TFNE V0.1 PILOT').run()
    options=next(s for s in app.selectbox if s.label=='Patient ID').options
    assert len(options)==10 and {s.split(' ')[0] for s in options}==set(published)
    verified=[]
    for patient in published:
        next(s for s in app.selectbox if s.label=='Patient ID').select(patient).run()
        assert not app.exception,[e.message for e in app.exception]
        assert any(t.label=='TFNE / Neurological Deficits' for t in app.tabs)
        assert any('TEMPORAL COURSE:' in m.value for m in app.markdown)
        next(b for b in app.button if (b.key or '').startswith('tfne:') and ':source:' in b.key).click().run()
        assert not app.exception and 'source_error' not in app.session_state
        next(b for b in app.button if b.label=='Open source narrative').click().run()
        assert not app.exception and 'source_error' not in app.session_state
        verified.append(dict(patient_id=patient,episode_card=True,episode_and_inventory_source_navigation=True))
    for action in ('ACCEPT','EDIT','REJECT','UNCERTAIN'):
        next(s for s in app.selectbox if s.label=='TFNE review action').select(action)
        if action=='EDIT':next(t for t in app.text_area if t.label.startswith('TFNE corrections')).set_value('{"onset_type":"GRADUAL"}')
        next(b for b in app.button if b.label=='Save TFNE review').click().run()
        assert not app.exception,[e.message for e in app.exception]
    loaded=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    next(s for s in loaded.selectbox if s.label=='Review queue').select('TFNE V0.1 PILOT').run()
    next(s for s in loaded.selectbox if s.label=='Patient ID').select(patient).run()
    assert not loaded.exception and any('Review status: UNCERTAIN' in c.value for c in loaded.caption)
    assert all(sha(path)==h for path,h in before.items())
    atomic(EVIDENCE/'ui_smoke.json',dict(patients=verified,TFNE_filter=True,explicit_review_actions=['ACCEPT','EDIT','REJECT','UNCERTAIN'],
        isolated_review_directory=str(tmp_path),reload_persistence=True,raw_artifacts_unchanged=True,real_physician_judgments_saved=False))
