"""Scope, immutable holdout evidence, source recall and physician-status safeguards."""
from pathlib import Path
import pytest
from caa_pdf_extractor.workbench_store import ROOT,read,atomic,sha
from caa_pdf_extractor.eeg_holdout_run import EEGHoldoutStore

EVIDENCE=ROOT/'data/debug/eeg_v1_1_holdout_20261029T101453_435311Z'


def test_holdout_store_refuses_development_overlap_and_promotion(tmp_path):
    ids=[str(100+i) for i in range(12)]
    atomic(tmp_path/'eeg_validation_cohort.json',dict(patient_ids=['999']))
    registry=tmp_path/'eeg_v1_1_holdout_cohort.json'
    cohort=dict(patient_ids=ids,excluded_patient_ids=['999'],module_version='1.1',selection_before_extraction=True)
    atomic(registry,cohort);store=EEGHoldoutStore(tmp_path)
    assert store.enabled_patients()==set(ids)
    with pytest.raises(ValueError):store.require_enabled('999')
    with pytest.raises(ValueError,match='promotion'):store.promote()
    cohort['patient_ids'][0]='999';atomic(registry,cohort)
    with pytest.raises(ValueError,match='holdout'):store.enabled_patients()


def test_existing_run_is_refused_before_launching_a_worker(monkeypatch,tmp_path):
    from caa_pdf_extractor import eeg_holdout_run
    (tmp_path/'workers').mkdir()
    monkeypatch.setattr(eeg_holdout_run,'check_design',lambda p:({},{}))
    launched=[]
    monkeypatch.setattr(eeg_holdout_run.subprocess,'run',lambda *a,**kw:launched.append(a))
    with pytest.raises(ValueError,match='already started'):eeg_holdout_run.run(tmp_path)
    assert not launched


@pytest.mark.skipif(not (EVIDENCE/'validation_metrics.json').exists(),reason='Local holdout unavailable')
def test_frozen_holdout_has_source_denominator_and_discloses_misses():
    # Historical evidence stays verifiable after later source/schema changes;
    # the execution guard separately requires the original live code hashes.
    cohort=read(EVIDENCE/'frozen_cohort.json');metrics=read(EVIDENCE/'validation_metrics.json')
    assert cohort==read(ROOT/'data/patient_artifacts/eeg_v1_1_holdout_cohort.json')
    assert sha(EVIDENCE/'eeg_v1_1_holdout_selection.csv')==cohort['selection_sha256']
    assert sha(EVIDENCE/'eeg_v1_1_holdout_exclusions.csv')==cohort['exclusions_sha256']
    freeze=read(EVIDENCE/'source_inventory_freeze.json')
    assert freeze['before_extraction'] and all(sha(p)==h for p,h in freeze['files'].items())
    inventory=read(EVIDENCE/'source_inventory_studies.json');issues=read(EVIDENCE/'review_issue_details.json')
    assert set(cohort['patient_ids']).isdisjoint(cohort['excluded_patient_ids'])
    assert len(inventory)==43 and metrics['source_EEG_exams']==43
    assert metrics['matched_source_EEG_exams']==38 and metrics['EEG_exam_recall']==38/43
    misses=[r for r in issues if r['issue_category']=='EXAM_DETECTION']
    assert len(misses)==5 and len({r['eeg_exam_id'] for r in misses})==5
    assert len([r for r in inventory if r['EEG_date_precision']=='UNKNOWN'])==2
    assert metrics['existing_v11_gate_failures']==0 and metrics['hard_gate_failures']>0
    assert metrics['production_ready'] is False
    for patient,entry in read(EVIDENCE/'published_candidates.json').items():
        manifest=read(ROOT/'data/patient_artifacts'/patient/'module_manifest.json')
        assert entry['status']=='CANDIDATE' and not manifest['modules']['eeg'].get('active_validated_version')


def test_extraction_freeze_still_refuses_later_source_changes(tmp_path):
    from caa_pdf_extractor.eeg_holdout_design import verify_freeze
    from caa_pdf_extractor.module_artifacts import digest
    source=tmp_path/'pipeline.py';source.write_text('original',encoding='utf-8')
    files={str(source):sha(source)}
    atomic(tmp_path/'eeg_v1_1_freeze.json',dict(files=files,code_hash=digest(files)))
    verify_freeze(tmp_path)
    source.write_text('later schema cleanup',encoding='utf-8')
    with pytest.raises(ValueError,match='Frozen EEG v1.1 code changed'):
        verify_freeze(tmp_path)


@pytest.mark.parametrize('actions,expected',[
    ({},'UNREVIEWED'),({'a':'ACCEPT'},'IN_REVIEW'),({'a':'UNCERTAIN'},'REVIEW_REQUIRED'),
    ({'a':'ACCEPT','b':'REJECT'},'PHYSICIAN_APPROVED'),({'a':'RESET'},'UNREVIEWED'),({'orphan':'ACCEPT'},'STALE')])
def test_holdout_status_requires_explicit_current_reviews(monkeypatch,tmp_path,actions,expected):
    from review_app import eeg_holdout
    from review_app.review_repository import ReviewRepository
    from caa_pdf_extractor import workbench_store
    monkeypatch.setattr(workbench_store,'integrity',lambda e:True)
    monkeypatch.setattr(eeg_holdout,'module_hash',lambda e:'hash')
    monkeypatch.setattr(eeg_holdout,'candidate_entities',lambda e,m:[dict(entity_id='a'),dict(entity_id='b')])
    monkeypatch.setattr(ReviewRepository,'load',lambda *a:{eid:dict(action=act,source_artifact_hash='hash') for eid,act in actions.items()})
    assert eeg_holdout.review_status(dict(version='candidate'),ReviewRepository(tmp_path),'patient')==expected


def test_completed_candidate_reviews_do_not_approve_a_missing_source_exam(monkeypatch,tmp_path):
    from review_app import eeg_holdout
    from review_app.review_repository import ReviewRepository
    from caa_pdf_extractor import workbench_store
    monkeypatch.setattr(workbench_store,'integrity',lambda e:True)
    monkeypatch.setattr(eeg_holdout,'module_hash',lambda e:'current')
    monkeypatch.setattr(eeg_holdout,'candidate_entities',lambda e,m:[dict(entity_id='exam')])
    record=dict(action='ACCEPT',source_artifact_hash='current')
    monkeypatch.setattr(ReviewRepository,'load',lambda *a:dict(exam=record))
    atomic(tmp_path/'metadata.json',dict(source_inventory=dict(path=str(tmp_path/'inventory.csv'))))
    atomic(tmp_path/'review_issue_details.json',[dict(patient_id='patient',issue_category='EXAM_DETECTION')])
    entry=dict(version='candidate',metadata_path=str(tmp_path/'metadata.json'))
    assert eeg_holdout.review_status(entry,ReviewRepository(tmp_path),'patient')=='REVIEW_REQUIRED'
    record['source_artifact_hash']='old'
    assert eeg_holdout.review_status(entry,ReviewRepository(tmp_path),'patient')=='STALE'


@pytest.mark.skipif(not (EVIDENCE/'published_candidates.json').exists(),reason='Local holdout unavailable')
def test_holdout_ui_all_patients_source_navigation_and_isolated_actions(monkeypatch,request):
    import uuid
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from review_app import artifact_repository
    from review_app.review_repository import ReviewRepository
    published=read(EVIDENCE/'published_candidates.json');index=artifact_repository.discover()
    monkeypatch.setattr(artifact_repository,'discover',lambda:{p:index[p] for p in published})
    output=EVIDENCE/'ui_smoke'/uuid.uuid4().hex;original=ReviewRepository.__init__
    monkeypatch.setattr(ReviewRepository,'__init__',lambda self,root=None:original(self,root or output/'reviews'))
    request.addfinalizer(st.cache_data.clear);st.cache_data.clear()
    before={f['path']:sha(f['path']) for entry in published.values() for f in entry['artifacts']}
    at=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    assert not at.exception,[e.message for e in at.exception]
    next(s for s in at.selectbox if s.label=='Review queue').select('EEG V1.1 HOLDOUT').run()
    assert set(next(s for s in at.selectbox if s.label=='Patient ID').options)=={p+' · NOT_REVIEWED' for p in published}
    checks=[]
    for patient,entry in published.items():
        next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
        assert not at.exception,[e.message for e in at.exception]
        assert next(s for s in at.selectbox if s.label=='EEG revision').value==entry['version']
        assert any('UNREVIEWED' in c.value for c in at.info)
        assert any('multi-label phenotype' in m.value for m in at.markdown)
        next(b for b in at.button if (b.key or '').startswith('holdout-source:')).click().run()
        assert not at.exception and 'source_error' not in at.session_state
        checks.append(dict(patient_id=patient,candidate_version=entry['version'],source_navigation=True,initial_status='UNREVIEWED'))
    # Open a source-only missed recording as well as ordinary extracted evidence.
    patient='2730816321';next(s for s in at.selectbox if s.label=='Patient ID').select(patient).run()
    missing=next(r for r in read(EVIDENCE/'source_inventory_studies.json') if r['patient_id']==patient and r['EEG_date'] is None)
    next(b for b in at.button if b.key==f"holdout-source:{patient}:{missing['inventory_id']}").click().run()
    assert at.session_state[f'{patient}:page']==5 and 'source_error' not in at.session_state
    for action in ('ACCEPT','EDIT','REJECT','UNCERTAIN'):
        next(s for s in at.selectbox if s.label=='EEG review action').select(action)
        if action=='EDIT':next(t for t in at.text_area if t.label.startswith('EEG corrections')).set_value('{"assessability":"FULL"}')
        next(b for b in at.button if b.label=='Save EEG review').click().run()
        assert not at.exception,[e.message for e in at.exception]
    loaded=AppTest.from_file(str(ROOT/'review_app/app.py'),default_timeout=90).run()
    next(s for s in loaded.selectbox if s.label=='Review queue').select('EEG V1.1 HOLDOUT').run()
    next(s for s in loaded.selectbox if s.label=='Patient ID').select(patient).run()
    assert any('Review status: UNCERTAIN' in c.value for c in loaded.caption)
    assert all(sha(path)==h for path,h in before.items())
    atomic(EVIDENCE/'ui_smoke.json',dict(patients=checks,isolated_review_directory=str(output),holdout_filter=True,missing_source_navigation=True,
        explicit_actions=['ACCEPT','EDIT','REJECT','UNCERTAIN'],reload_persistence=True,raw_artifacts_unchanged=True,real_physician_reviews_written=False))
