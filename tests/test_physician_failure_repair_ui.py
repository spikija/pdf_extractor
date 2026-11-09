"""Read-only AppTest of the seven new candidates; no extraction or review actions."""
import hashlib
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'data/debug/physician_failure_repair_20261026T190001_368626Z'


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Seven-patient repair candidates unavailable')
def test_seven_candidate_previews_and_source_navigation(monkeypatch,tmp_path):
    pytest.importorskip('streamlit')
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    from review_app.display_models import diagnosis_entities
    candidates=json.loads((EVIDENCE/'published_candidates.json').read_text(encoding='utf8'))
    all_index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:all_index[p] for p in candidates})
    original_init=ReviewRepository.__init__
    def isolate(self,root=None):
        original_init(self,tmp_path/'reviews'/hashlib.sha256(str(root).encode()).hexdigest()[:12])
    monkeypatch.setattr(ReviewRepository,'__init__',isolate)
    st.cache_data.clear()
    at=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    evidence=[]
    for pid,entries in candidates.items():
        next(s for s in at.selectbox if s.label=='Patient ID').select(pid).run()
        assert not at.exception,[x.message for x in at.exception]
        revisions=[s.value for s in at.selectbox if s.label=='Candidate revision']
        assert entries['diagnoses']['version'] in revisions and entries['medications']['version'] in revisions
        unknown=[r for r in diagnosis_entities(entries['diagnoses']) if r.get('candidate_type')=='UNMAPPED_DIAGNOSIS_CANDIDATE']
        assert unknown and any('UNMAPPED_DIAGNOSIS_CANDIDATE' in str(s.options) for s in at.selectbox)
        if pid in ('2788388022','2620249118'):
            assert any(e.label=='CAA source assertions' for e in at.expander)
            assert not any(r.get('canonical_name')=='CEREBRAL_AMYLOID_ANGIOPATHY' for r in diagnosis_entities(entries['diagnoses']))
            button=next(b for b in at.button if ':caa-source:' in (b.key or ''))
            button.click().run()
            assert not at.exception
            assert 'source_error' not in at.session_state
            assert at.session_state[f'{pid}:active']
        evidence.append(dict(patient_id=pid,diagnoses_candidate=entries['diagnoses']['version'],medications_candidate=entries['medications']['version'],
            candidate_versions_visible=True,unmapped_review_items_visible=True,exceptions=0))
    (EVIDENCE/'ui_smoke.json').write_text(json.dumps(dict(patients=evidence,review_saves=0,extraction_runs=0,
        promotion_actions=0,source_navigation_verified_for_CAA_patients=True),indent=2),encoding='utf8')
