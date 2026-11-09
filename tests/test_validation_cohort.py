import copy
import json
from pathlib import Path

import pymupdf
import pytest

from caa_pdf_extractor.workbench_store import WorkbenchStore, PILOTS, atomic, sha
from caa_pdf_extractor.validation_cohort import choose, FEATURES
from caa_pdf_extractor.validation_sources import prepare_patient
from caa_pdf_extractor.workbench_runner import run_module
from review_app.cohort_dashboard import patient_summary, matches, pick_entry
from review_app.review_repository import ReviewRepository
from review_app.display_models import diagnosis_entities


def test_selection_deterministic_heterogeneous_and_exactly_fifteen_new():
    rows=[]
    for i,pid in enumerate(list(PILOTS)+[str(1000000000+i) for i in range(28)]):
        rows.append(dict(patient_id=pid,features={f:(i % len(FEATURES)==j or i % 7==j) for j,f in enumerate(FEATURES)},screening_errors=[]))
    first,new=choose(rows)
    second,again=choose(list(reversed(rows)))
    assert [r['patient_id'] for r in first]==[r['patient_id'] for r in second]
    assert new==again and len(new)==15 and len(first)==20
    assert not set(new)&set(PILOTS)
    assert set(new)!=set(sorted(r['patient_id'] for r in rows if r['patient_id'] not in PILOTS)[:15])
    with pytest.raises(ValueError):choose(rows,new_count=16)


def test_registry_bounded_and_nonregistered_execution_rejected(tmp_path):
    store=WorkbenchStore(tmp_path/'stable')
    pid='1234567890'
    atomic(store.root/'validation_cohort.json',dict(patient_ids=list(PILOTS)+[pid]))
    assert store.is_enabled(pid) and not store.is_enabled('9999999999')
    with pytest.raises(ValueError,match='registered'):
        run_module('9999999999','diagnoses',store=store)
    with pytest.raises(ValueError):store.patient_dir('../escape')
    atomic(store.root/'validation_cohort.json',dict(patient_ids=[str(i) for i in range(21)]))
    with pytest.raises(ValueError,match='bounded'):store.enabled_patients()


def test_selected_source_preparation_cache_and_real_registered_workers(tmp_path):
    pid='1234567890';store=WorkbenchStore(tmp_path/'stable')
    atomic(store.root/'validation_cohort.json',dict(patient_ids=[pid]))
    pdf_root=tmp_path/'pdfs';folder=pdf_root/pid;folder.mkdir(parents=True)
    path=folder/'1.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page();page.insert_text((40,50),'Diagnosen',fontsize=15)
        page.insert_text((40,80),'Bekanntes Vorhofflimmern.\nArterielle Hypertonie.',fontsize=11)
        doc.save(path)
    digest=sha(path)
    first=prepare_patient(pid,tmp_path/'first',store=store,pdf_root=pdf_root,cache_root=tmp_path/'cache')
    second=prepare_patient(pid,tmp_path/'second',store=store,pdf_root=pdf_root,cache_root=tmp_path/'cache')
    assert first['cache_hits']==0 and second['cache_hits']==1 and sha(path)==digest
    for module in ('diagnoses','medications'):
        entry=run_module(pid,module,store=store)
        assert entry['status']=='CANDIDATE'
        assert store.manifest(pid)['modules'][module]['active_validated_version'] is None
    assert set(p.name for p in store.root.iterdir() if p.is_dir())=={pid}


def test_af_filter_uses_resolved_state_only_and_status_filters():
    row=dict(patient_id='p',status='NOT_REVIEWED',AF_state='SUSPECTED_AF',review_required=5)
    assert matches(row,'SUSPECTED AF') and not matches(row,'CONFIRMED AF')
    assert not matches(row,'NO CONFIRMED AF')
    assert matches(row,'ALL') and matches(row,'UNREVIEWED') and matches(row,'REVIEW REQUIRED')
    assert not matches(row,'APPROVED')


def test_candidate_only_dashboard_and_reference_approval_do_not_write_reviews(tmp_path):
    store=WorkbenchStore(tmp_path/'stable');pid=PILOTS[0]
    artifacts={'canonical_disease_identities.json':json.dumps([dict(diagnosis_identity_id='d',canonical_name='ARTERIAL_HYPERTENSION',review_required=True)]),
               'canonical_clinical_events.json':'[]','af_state.json':json.dumps({'AF_state':'NO_CONFIRMED_AF'})}
    meta=dict(module_version='4.1',validation_test_results={'hard_gates':{'ok':True}},source_pdf_hashes={})
    dx=store.publish(pid,'diagnoses','4.1+c',artifacts,meta)
    med=store.publish(pid,'medications','3.0+c',{'canonical_medications.json':'[]','medication_exposures.json':'[]'},dict(meta,module_version='3.0'))
    repo=ReviewRepository(tmp_path/'review');row=patient_summary(pid,store.manifest(pid),repo)
    assert row['status']=='NOT_REVIEWED' and row['AF_state']=='NO_CONFIRMED_AF' and row['review_required']==1
    assert not repo.root.exists()
    from review_app.artifact_repository import module_hash
    refs={module:dict(version=e['version'],hash=module_hash(e),physician_review_passed=True) for module,e in [('diagnoses',dx),('medications',med)]}
    assert patient_summary(pid,store.manifest(pid),repo,refs)['status']=='APPROVED'
    assert not repo.root.exists()
    manifest=store.manifest(pid);manifest['modules']['diagnoses']['candidate_qa_holds']={dx['version']:'duplicate event ID'}
    assert pick_entry(manifest,'diagnoses')['qa_hold']
    assert patient_summary(pid,manifest,repo)['status']=='ERROR'


def test_duplicate_event_ids_never_hide_source_rows_or_share_review_keys(tmp_path):
    path=tmp_path/'canonical_clinical_events.json'
    rows=[dict(event_id='collision',event_type='ISCHEMIC_STROKE',event_date='2021',source_evidence_ids=[f'e{i}']) for i in range(3)]
    path.write_text(json.dumps(rows),encoding='utf-8')
    entry=dict(artifacts=[dict(path=str(path),sha256=sha(path))])
    view=diagnosis_entities(entry,True)
    assert len(view)==3 and len({r['entity_id'] for r in view})==3
    assert all(r['review_blocked'] and r['canonical_event_id']=='collision' for r in view)
    assert {e for row in view for e in row['source_evidence_ids']}=={'e0','e1','e2'}


def test_duplicate_ids_block_promotion_even_without_a_previous_qa_hold(tmp_path):
    store=WorkbenchStore(tmp_path/'stable');pid=PILOTS[0]
    events=[dict(event_id='duplicate',event_type='TIA',event_date='2021',review_required=False) for _ in range(2)]
    meta=dict(module_version='4.1',validation_test_results={'hard_gates':{'historical_gate':True}},source_pdf_hashes={})
    entry=store.publish(pid,'diagnoses','4.1+bad',{'canonical_clinical_events.json':json.dumps(events)},meta)
    with pytest.raises(ValueError,match='Duplicate canonical entity IDs'):
        store.promote(pid,'diagnoses',entry['version'],confirmed=True)
    assert store.manifest(pid)['modules']['diagnoses']['active_validated_version'] is None
