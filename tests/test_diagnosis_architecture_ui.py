"""Read-only previews for the nine new candidates; no model, run, save or promotion action."""
import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z'


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Local vNext candidates unavailable')
def test_nine_candidate_registries_events_admissions_and_sources(monkeypatch,tmp_path):
    pytest.importorskip('streamlit')
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    from review_app.display_models import admission_entities
    from caa_pdf_extractor.workbench_store import candidate_entities,read
    candidates=read(EVIDENCE/'published_candidates.json')
    complete=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:complete[p] for p in candidates})
    original_init=ReviewRepository.__init__
    def isolate(self,root=None):original_init(self,tmp_path/'reviews'/hashlib.sha256(str(root).encode()).hexdigest()[:12])
    monkeypatch.setattr(ReviewRepository,'__init__',isolate)
    st.cache_data.clear()
    at=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    verified=[]
    for pid,e in candidates.items():
        next(s for s in at.selectbox if s.label=='Patient ID').select(pid).run()
        dataset=next(r for r in at.radio if r.label=='Dataset to review' and ':diagnoses:' in (r.key or ''))
        if dataset.value!='CANDIDATE':dataset.set_value('CANDIDATE').run()
        assert not at.exception,[x.message for x in at.exception]
        assert e['version'] in [s.value for s in at.selectbox if s.label=='Candidate revision']
        headings={s.value for s in at.subheader}
        assert {'Patient diagnoses','Clinical events','Admissions · probable diagnosis'}<=headings
        assert 'Chronic diseases' not in headings
        button=next(b for b in at.button if ':admissions:' in (b.key or '') and ':source:' in (b.key or '')) if any(a['supporting_evidence_ids'] for a in admission_entities(e)) else None
        if button:
            button.click().run();assert not at.exception
            assert 'source_error' not in at.session_state
            assert at.session_state[f'{pid}:active']
        rows=admission_entities(e);ids={x['entity_id'] for x in candidate_entities(e,'diagnoses')}
        assert all(a['entity_id'] in ids for a in rows)
        verified.append(dict(patient_id=pid,version=e['version'],unified_diagnoses_visible=True,events_visible=True,
            admissions_visible=True,admission_count=len(rows),source_navigation_verified=bool(button),
            model_abstention_without_fabricated_evidence=not bool(button)))
    (EVIDENCE/'ui_smoke.json').write_text(json.dumps(dict(patients=verified,review_saves=0,extraction_runs=0,promotions=0),indent=2),encoding='utf8')
