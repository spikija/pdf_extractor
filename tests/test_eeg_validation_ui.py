"""Local EEG AppTest; review writes are isolated under debug evidence."""
from pathlib import Path
import uuid
import pytest
from caa_pdf_extractor.workbench_store import ROOT,read,atomic,sha

EVIDENCE=ROOT/'data/debug/eeg_validation_20261028T103249_606975Z'


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Local EEG validation candidates unavailable')
def test_twenty_eeg_candidates_navigation_review_and_reload(monkeypatch,request):
    import streamlit as st
    request.addfinalizer(st.cache_data.clear)
    from streamlit.testing.v1 import AppTest
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    published=read(EVIDENCE/'published_candidates.json');index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    output=EVIDENCE/'ui_smoke'/uuid.uuid4().hex;original=ReviewRepository.__init__
    def isolated(self,root=None):original(self,root or output/'reviews')
    monkeypatch.setattr(ReviewRepository,'__init__',isolated)
    before={f['path']:sha(f['path']) for e in published.values() for f in e['artifacts']}
    st.cache_data.clear();at=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    checks=[]
    for patient,entry in published.items():
        next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
        next(s for s in at.selectbox if s.label=='EEG revision').select(entry['version']).run()
        assert not at.exception,[e.message for e in at.exception]
        assert any(s.label=='EEG examination' for s in at.selectbox)
        assert any(e.label=='Original interpretation / complete EEG report' for e in at.expander)
        button=next(b for b in at.button if ':eeg:report:' in (b.key or ''))
        button.click().run();assert not at.exception
        assert 'source_error' not in at.session_state
        assert at.session_state[f'{patient}:page']>=1
        assert ':EEG:' in at.session_state[f'{patient}:active']
        checks.append(dict(patient_id=patient,EEG_tab=True,source_navigation=True,exceptions=0))
    patient=list(published)[0];next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
    next(s for s in at.selectbox if s.label=='EEG revision').select(published[patient]['version']).run()
    for action in ['ACCEPT','UNCERTAIN','REJECT','EDIT','RESET','ACCEPT']:
        next(s for s in at.selectbox if s.label=='EEG review action').select(action)
        if action=='EDIT':next(s for s in at.text_area if s.label.startswith('EEG corrections')).set_value('{"seizure_recorded": null}')
        next(b for b in at.button if b.label=='Save EEG review').click().run()
        assert not at.exception,[e.message for e in at.exception]
    next(s for s in at.selectbox if s.label=='EEG review target').select(1).run()
    next(b for b in at.button if ':eeg:finding:' in (b.key or '')).click().run()
    assert not at.exception and 'source_error' not in at.session_state
    next(b for b in at.button if b.label=='Save EEG review').click().run()
    assert not at.exception
    loaded=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    next(s for s in loaded.selectbox if s.label=='Patient ID').select(patient).run()
    next(s for s in loaded.selectbox if s.label=='EEG revision').select(published[patient]['version']).run()
    assert not loaded.exception
    assert any('Review status: ACCEPT' in c.value for c in loaded.caption)
    records=ReviewRepository(output/'reviews'/'candidates'/published[patient]['version']).load(patient,'eeg')
    assert len(records)>=2 and all(r['action']=='ACCEPT' for r in records.values())
    assert all(sha(path)==value for path,value in before.items())
    atomic(output/'ui_smoke.json',dict(patients=checks,isolated_review_directory=str(output),exam_and_finding_save=True,actions=['ACCEPT','EDIT','REJECT','UNCERTAIN','RESET'],reload_persistence=True,raw_artifacts_unchanged=True))
