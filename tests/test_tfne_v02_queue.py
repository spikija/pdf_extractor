"""The named v0.2 queue opens the published cohort and the correct version."""
import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from caa_pdf_extractor.workbench_store import ROOT,sha


def test_named_v02_queue_opens_v02_and_all_ten_patients_without_review_writes(tmp_path,monkeypatch,request):
    from review_app import artifact_repository
    from review_app.tfne_repository import TFNEReviewRepository
    registry=ROOT/'data/debug/tfne_v0_2_20261101T174337_177561Z/release/published_artifacts.json'
    if not registry.exists():pytest.skip('Local v0.2 pilot unavailable')
    published=json.loads(registry.read_text(encoding='utf-8'))
    index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    original=TFNEReviewRepository.__init__
    monkeypatch.setattr(TFNEReviewRepository,'__init__',lambda self,root=None,version='0.1':original(self,root or tmp_path/version,version))
    st.cache_data.clear();request.addfinalizer(st.cache_data.clear)
    before={p:sha(item['path']) for p,item in published.items()}
    app=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    queue=next(s for s in app.selectbox if s.label=='Review queue')
    assert 'TFNE V0.2 PILOT' in queue.options
    queue.select('TFNE V0.2 PILOT').run()
    assert not app.exception,[e.message for e in app.exception]
    picker=next(s for s in app.selectbox if s.label=='Patient ID')
    assert len(picker.options)==10
    picker.select('2702072620').run()
    assert next(s for s in app.selectbox if s.label=='TFNE extraction version').value=='0.2'
    assert any('PILOT 0.2' in w.value for w in app.warning)
    assert any('Changed from v0.1:' in w.value for w in app.warning)
    next(s for s in app.selectbox if s.label=='Patient ID').select('2674159019').run()
    assert next(s for s in app.selectbox if s.label=='TFNE extraction version').value=='0.2'
    assert any('No grounded episodes' in info.value for info in app.info)
    next(s for s in app.selectbox if s.label=='Review queue').select('TFNE V0.1 PILOT').run()
    assert next(s for s in app.selectbox if s.label=='TFNE extraction version').value=='0.1'
    assert not app.exception and not list(tmp_path.rglob('events.json'))
    assert all(sha(item['path'])==before[p] for p,item in published.items())
