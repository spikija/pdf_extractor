"""EEG 1.1 clinical display, navigation and isolated review persistence."""
import copy
import json
import uuid
import pytest
from pathlib import Path
from caa_pdf_extractor.workbench_store import ROOT,read,atomic,sha

EVIDENCE=ROOT/'data/debug/eeg_v1_1_20261029T075226_750876Z'


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Local EEG 1.1 validation candidates unavailable')
def test_v11_cohort_clinical_display_source_and_review(monkeypatch,request):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    published=read(EVIDENCE/'published_candidates.json');index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    request.addfinalizer(st.cache_data.clear)
    output=EVIDENCE/'ui_smoke'/uuid.uuid4().hex;original=ReviewRepository.__init__
    def isolated(self,root=None):original(self,root or output/'reviews')
    monkeypatch.setattr(ReviewRepository,'__init__',isolated)
    before={f['path']:sha(f['path']) for e in published.values() for f in e['artifacts']}
    st.cache_data.clear();at=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    verified=[]
    for patient,entry in published.items():
        next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
        next(s for s in at.selectbox if s.label=='EEG revision').select(entry['version']).run()
        assert not at.exception,[e.message for e in at.exception]
        assert next(s for s in at.selectbox if s.label=='EEG revision').value==entry['version']
        assert any('Assessability:' in m.value for m in at.markdown)
        assert any('Epileptiform evidence:' in m.value for m in at.markdown)
        assert any('Rhythmic pattern type:' in m.value for m in at.markdown)
        next(b for b in at.button if ':eeg:report:' in (b.key or '')).click().run()
        assert not at.exception and 'source_error' not in at.session_state
        verified.append(dict(patient_id=patient,version=entry['version'],clinical_fields_visible=True,source_navigation=True))
    patient='2345733313';entry=published[patient]
    exams=next(read(f['path']) for f in entry['artifacts'] if Path(f['path']).name=='eeg_exams.json')
    findings=next(read(f['path']) for f in entry['artifacts'] if Path(f['path']).name=='eeg_findings.json')
    selected=next(i for i,e in enumerate(exams) if e['eeg_date']=='2023-03-02');raw=exams[selected]
    next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
    next(s for s in at.selectbox if s.label=='EEG revision').select(entry['version']).run()
    next(s for s in at.selectbox if s.label=='EEG examination').select(selected).run()
    assert any('Reactivity — VERBAL: ABSENT' in m.value for m in at.markdown)
    assert any('Assessability: LIMITED' in m.value for m in at.markdown)
    assert any('Epileptiform evidence: NO_DEFINITE_EVIDENCE' in m.value for m in at.markdown)
    focus_table=next(df.value for df in at.dataframe if 'Relationship' in df.value.columns)
    assert 'FRONTAL, CENTRAL, TEMPORAL' in focus_table['Regions'].tolist()
    next(b for b in at.button if ':eeg:focus:' in (b.key or '')).click().run()
    assert not at.exception and 'source_error' not in at.session_state
    assert at.session_state[f'{patient}:page'] in (6,7)
    next(s for s in at.selectbox if s.label=='EEG review action').select('EDIT')
    next(t for t in at.text_area if t.label.startswith('EEG corrections')).set_value('{"assessability":"FULL"}')
    next(b for b in at.button if b.label=='Save EEG review').click().run()
    assert not at.exception
    own=[f for f in findings if f['eeg_exam_id']==raw['eeg_exam_id']]
    target=next(i for i,f in enumerate(own) if f['field']=='focal_abnormalities')
    next(s for s in at.selectbox if s.label=='EEG review target').select(target+1).run()
    edited=copy.deepcopy(own[target]['value']);edited[0]['side']='RIGHT'
    next(s for s in at.selectbox if s.label=='EEG review action').select('EDIT')
    next(t for t in at.text_area if t.label.startswith('EEG corrections')).set_value(json.dumps({'value':edited}))
    next(b for b in at.button if b.label=='Save EEG review').click().run()
    assert not at.exception,[e.message for e in at.exception]
    assert any('Dominant focus: RIGHT' in m.value for m in at.markdown)
    loaded=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    next(s for s in loaded.selectbox if s.label=='Patient ID').select(patient).run()
    next(s for s in loaded.selectbox if s.label=='EEG revision').select(entry['version']).run()
    next(s for s in loaded.selectbox if s.label=='EEG examination').select(selected).run()
    assert not loaded.exception
    assert any('Assessability: FULL' in m.value for m in loaded.markdown)
    assert any('Dominant focus: RIGHT' in m.value for m in loaded.markdown)
    next(s for s in loaded.selectbox if s.label=='EEG revision').select('1.0+20261028T103249_606975Z-r2').run()
    assert not loaded.exception
    assert not any('Dominant focus: RIGHT' in m.value for m in loaded.markdown)
    assert all(sha(path)==value for path,value in before.items())
    atomic(EVIDENCE/'ui_smoke.json',dict(patients=verified,clinical_detail_display=True,source_navigation=True,exam_and_focus_edits_saved=True,
        reload_persistence=True,version_isolation=True,raw_artifacts_unchanged=True,isolated_review_directory=str(output)))
