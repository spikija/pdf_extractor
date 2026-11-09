"""Version switches, evidence/status display, and separate physician overlays."""
import copy
import hashlib

from streamlit.testing.v1 import AppTest

from caa_pdf_extractor.tfne_schema import defaults
from caa_pdf_extractor.tfne_screen import derive
from review_app.tfne_repository import TFNEReviewRepository,project


def sample(version):
    e=defaults();e.update(episode_id='episode-'+version,symptom_description='Aphasie.',symptom_domains=['LANGUAGE'],focality='FOCAL')
    source=dict(document_id=10,source_document_id=10,page=1,section_id='history',parent_section_id='page1',section_type='ANAMNESE',
        evidence_text='Aphasie.',evidence_start=0,evidence_end=8,evidence_id='ev')
    e.update(evidence=[source],field_evidence={f:[dict(source,field=f)] for f in ('symptom_description','symptom_domains','focality')},
        field_validation_status={'symptom_domains':'SUPPORTED'},validation_flags=[])
    e.update(derive(e))
    if version=='0.1':e['symptom_domains']=['LANGUAGE','COGNITIVE']
    return dict(patient_id='123',module_version=version,episodes=[e],validation_flags=[],granite_prompt_name='tfne_neurological_deficit_v'+version.replace('.','_'),granite_model_name='granite4.2:latest')


def test_version_switch_and_changed_fields_evidence_status_display(tmp_path,monkeypatch):
    from review_app.views import tfne
    artifacts={v:sample(v) for v in ('0.1','0.2')}
    monkeypatch.setattr(tfne,'available_versions',lambda *args:['0.1','0.2'])
    monkeypatch.setattr(tfne,'load_artifact',lambda patient,version='0.1':(artifacts[version],hashlib.sha256(version.encode()).hexdigest()))
    app=AppTest.from_string('from review_app.views.tfne import render\n'+f'render("123",[],review_root={str(tmp_path)!r})').run()
    assert not app.exception
    next(s for s in app.selectbox if s.label=='TFNE extraction version').select('0.2').run()
    assert not app.exception,[e.message for e in app.exception]
    assert any('Changed from v0.1: symptom_domains' in m.value for m in app.warning)
    frames=[d.value for d in app.dataframe]
    assert any({'Value','Evidence span','Validation status','Changed from v0.1'}<=set(d.columns) for d in frames)
    assert not list(tmp_path.rglob('events.json'))
    next(s for s in app.selectbox if s.label=='TFNE review action').select('ACCEPT')
    next(b for b in app.button if b.label=='Save TFNE review').click().run()
    assert not app.exception and len(list((tmp_path/'v0_2').rglob('events.json')))==1
    next(s for s in app.selectbox if s.label=='TFNE extraction version').select('0.1').run()
    assert not app.exception and any('Review status: UNREVIEWED' in c.value for c in app.caption)


def test_v02_review_namespace_and_module_version_are_separate(tmp_path):
    a=sample('0.2');before=copy.deepcopy(a);repo=TFNEReviewRepository(tmp_path,version='0.2')
    record=repo.save(a,'a'*64,['episode-0.2'],'EDIT',dict(focality='UNKNOWN'))
    assert record['module_version']=='0.2'
    e=project(a,repo.load('123','a'*64))[0]
    assert e['field_validation_status']['focality']=='PHYSICIAN_OVERLAY' and a==before
    assert repo.load('123','b'*64)==[]
