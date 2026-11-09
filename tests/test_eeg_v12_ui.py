"""Real Streamlit EEG panel with synthetic 1.2 artifacts and isolated review storage."""
import copy
import json
import pytest
from streamlit.testing.v1 import AppTest

from caa_pdf_extractor.extraction.eeg_pipeline_v12 import assemble
from caa_pdf_extractor.workbench_eeg import EEGWorkbenchStore,reviewed_exam,validate_edits
from review_app.artifact_repository import sha
from caa_pdf_extractor.workbench_store import ROOT,read,atomic

EVIDENCE=ROOT/'data/debug/eeg_v1_2_20261101T113547_290839Z'


def test_v12_ncse_segments_clean_source_and_explicit_review(tmp_path,monkeypatch):
    from review_app.review_repository import ReviewRepository
    raw='EEG (19.05.2025)\nAbnormes EEG. Interpretation: NCSE links temporal. Klinisch Aphasie.\n'
    raw+='MOBILE Video-EEG-Ableitung vom 19.5.25\n'
    raw+='cEEG Kurzbefund von 19.05.2025 14:08 - 20.05.2025 08:19\nAbnormes EEG. NCSE links temporal.\n'
    raw+='cEEG Kurzbefund von 20.05.2025 08:20 - 20.05.2025 11:23\nAbnormes EEG. Kein Status epilepticus-Muster.'
    bundle=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=raw,raw_pdf_text='Institutional raw context\n'+raw)])])
    result=assemble(bundle);before=copy.deepcopy(result);files=[]
    for name,rows in result.items():
        path=tmp_path/(name+'.json');path.write_text(json.dumps(rows),encoding='utf-8');files.append(dict(path=str(path),sha256=sha(path)))
    entry=dict(version='1.2+synthetic',module_version='1.2',status='CANDIDATE',artifacts=files)
    monkeypatch.setattr(EEGWorkbenchStore,'versions',lambda *a:{})
    app=AppTest.from_string('from review_app.views.eeg import render\nfrom review_app.review_repository import ReviewRepository\n'+f'render({entry!r},ReviewRepository({str(tmp_path/"reviews")!r}),"123",[])').run()
    assert not app.exception,[e.message for e in app.exception]
    video=next(i for i,e in enumerate(result['eeg_exams']) if e['recording_type']=='VIDEO_EEG')
    next(s for s in app.selectbox if s.label=='EEG examination').select(video).run()
    assert not app.exception,[e.message for e in app.exception]
    assert any('NON-CONVULSIVE STATUS EPILEPTICUS' in e.value for e in app.error)
    assert any('Recording type: VIDEO EEG' in m.value for m in app.markdown)
    assert any('2025-05-19T14:08' in c.value for c in app.caption)
    assert len([e for e in app.expander if e.label.startswith('SEGMENT ')])==2
    assert any(e.label=='Raw source context' for e in app.expander)
    assert not any('Institutional raw context' in e.value for e in app.text if 'NCSE' not in e.value)
    assert all('Recording end' not in c.value for c in app.caption)
    assert not (tmp_path/'reviews/candidates/1.2+synthetic/123/eeg_review.json').exists()
    next(s for s in app.selectbox if s.label=='EEG review action').select('ACCEPT')
    next(b for b in app.button if b.label=='Save EEG review').click().run()
    assert not app.exception,[e.message for e in app.exception]
    assert any('Review status: ACCEPT' in c.value for c in app.caption)
    assert result==before and all(sha(f['path'])==f['sha256'] for f in files)


def test_v12_new_fields_review_edits_validate_and_do_not_mutate_raw():
    bundle=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text='EEG (19.05.2025)\nAbnormes EEG. NCSE links temporal.')])])
    result=assemble(bundle);exam=result['eeg_exams'][0];before=copy.deepcopy(exam)
    edits=validate_edits(exam,dict(NCSE=None,electrographic_status_epilepticus=None,NCSE_OCCURRED_DURING_RECORDING=None,status_type='UNKNOWN'))
    record=dict(entity_id=exam['eeg_exam_id'],action='EDIT',source_artifact_hash='hash',updated_at='2026-11-01',physician_value=edits)
    projected=reviewed_exam(exam,result['eeg_findings'],{exam['eeg_exam_id']:record},'hash')
    assert projected['NCSE'] is None and 'STATUS_EPILEPTICUS' not in projected['clinical_phenotype']
    assert exam==before
    with pytest.raises(ValueError):validate_edits(exam,{'NCSE':'yes'})


def test_accepting_later_negative_segment_does_not_erase_earlier_ncse():
    text='MOBILE Video-EEG-Ableitung (19.05.2025)\n'
    text+='cEEG Kurzbefund von 19.05.2025 14:08 - 20.05.2025 08:19\nAbnormes EEG. NCSE links temporal.\n'
    text+='cEEG Kurzbefund von 20.05.2025 08:20 - 20.05.2025 11:23\nAbnormes EEG. Kein Status epilepticus-Muster.'
    b=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=text)])])
    r=assemble(b);exam=r['eeg_exams'][0]
    negative=next(f for f in r['eeg_findings'] if f['field']=='NCSE' and f['value'] is False)
    record=dict(entity_id=negative['finding_id'],action='ACCEPT',source_artifact_hash='hash',updated_at='2026-11-01',physician_value={})
    projected=reviewed_exam(exam,r['eeg_findings'],{record['entity_id']:record},'hash')
    assert projected['NCSE'] is True and projected['NCSE_OCCURRED_DURING_RECORDING'] is True
    assert projected['status_at_final_segment']=='ABSENT' and projected['video'] is True and projected['mobile'] is True
    assert [s['segment_status'] for s in projected['CEEG_SEGMENTS']]==['PRESENT','ABSENT']


def test_historical_video_interval_remains_visible_from_source_heading(tmp_path,monkeypatch):
    from review_app.artifact_repository import sha
    header='VIDEO-EEG-MONITORING (29.09. - 04.10.2024):'
    b=dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=header+'\nNormales EEG. Keine epileptiformen Potenziale.')])])
    r=assemble(b);files=[]
    for name,value in r.items():
        path=tmp_path/(name+'.json');path.write_text(json.dumps(value),encoding='utf-8');files.append(dict(path=str(path),sha256=sha(path)))
    entry=dict(version='1.2+synthetic',module_version='1.2',status='CANDIDATE',artifacts=files)
    monkeypatch.setattr(EEGWorkbenchStore,'versions',lambda *a:{})
    app=AppTest.from_string('from review_app.views.eeg import render\nfrom review_app.review_repository import ReviewRepository\n'+f'render({entry!r},ReviewRepository({str(tmp_path/"reviews")!r}),"123",[])').run()
    assert not app.exception,[e.message for e in app.exception]
    assert any(header in c.value for c in app.caption)


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Local EEG 1.2 candidates unavailable')
def test_v12_all_published_patients_and_preserved_holdout_navigation(tmp_path,monkeypatch,request):
    import streamlit as st
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    published=read(EVIDENCE/'published_candidates.json');index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    original=ReviewRepository.__init__
    monkeypatch.setattr(ReviewRepository,'__init__',lambda self,root=None:original(self,root or tmp_path/'reviews'))
    st.cache_data.clear();request.addfinalizer(st.cache_data.clear)
    hashes={f['path']:sha(f['path']) for entry in published.values() for f in entry['artifacts']}
    app=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run();verified=[]
    for patient,entry in published.items():
        next(s for s in app.selectbox if s.label=='Patient ID').select(patient).run()
        assert not app.exception,[e.message for e in app.exception]
        assert next(s for s in app.selectbox if s.label=='EEG revision').value==entry['version']
        next(b for b in app.button if ':eeg:report:' in (b.key or '')).click().run()
        assert not app.exception and 'source_error' not in app.session_state
        verified.append(dict(patient_id=patient,candidate_version=entry['version'],source_navigation=True))
    next(s for s in app.selectbox if s.label=='Patient ID').select('2687726420').run()
    exams=artifact_repository.layer(published['2687726420'],'eeg_exams')
    critical=next(i for i,e in enumerate(exams) if e['eeg_date']=='2026-07-29')
    next(s for s in app.selectbox if s.label=='EEG examination').select(critical).run()
    assert any('NON-CONVULSIVE STATUS EPILEPTICUS' in e.value for e in app.error)
    next(s for s in app.selectbox if s.label=='Patient ID').select('2957537425').run()
    video=next(i for i,e in enumerate(artifact_repository.layer(published['2957537425'],'eeg_exams')) if e['recording_type']=='VIDEO_EEG')
    next(s for s in app.selectbox if s.label=='EEG examination').select(video).run()
    assert len([e for e in app.expander if e.label.startswith('SEGMENT ')])==2
    next(b for b in app.button if ':eeg:segment:' in (b.key or '')).click().run()
    assert not app.exception and 'source_error' not in app.session_state
    next(s for s in app.selectbox if s.label=='Review queue').select('EEG V1.1 HOLDOUT').run()
    next(s for s in app.selectbox if s.label=='Patient ID').select('2687726420').run()
    assert next(s for s in app.selectbox if s.label=='EEG revision').value.startswith('1.1+holdout.')
    assert any('EEG V1.1 HOLDOUT' in e.value for e in app.info)
    assert all(sha(p)==h for p,h in hashes.items())
    atomic(EVIDENCE/'ui_smoke.json',dict(patients=verified,prominent_NCSE=True,segment_display_and_source_links=True,legacy_holdout_version_pinned=True,
        real_physician_reviews_written=False,raw_artifacts_unchanged=True))
