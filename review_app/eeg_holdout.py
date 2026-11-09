"""Read-only holdout queue and source-inventory guidance; judgments remain overlays."""
from pathlib import Path
import streamlit as st
from review_app.artifact_repository import read_json,layer,module_hash
from review_app.review_repository import ReviewRepository
from review_app.components import open_source
from caa_pdf_extractor.workbench_store import candidate_entities


def registry(root):
    path=Path(root)/'eeg_v1_1_holdout_cohort.json'
    return read_json(path) if path.exists() else {}


def review_status(entry,repo,patient):
    if not entry:return 'REVIEW_REQUIRED'
    from caa_pdf_extractor.workbench_store import integrity
    if not integrity(entry):return 'STALE'
    records=ReviewRepository(repo.root/'candidates'/entry['version']).load(patient,'eeg')
    entities=candidate_entities(entry,'eeg');ids={e['entity_id'] for e in entities};fingerprint=module_hash(entry)
    actions={eid:r for eid,r in records.items() if r['action']!='RESET'}
    if any(eid not in ids or r.get('source_artifact_hash')!=fingerprint for eid,r in actions.items()):return 'STALE'
    if not actions:return 'UNREVIEWED'
    if any(r['action']=='UNCERTAIN' for r in actions.values()):return 'REVIEW_REQUIRED'
    if ids and ids<=set(actions):
        metadata=read_json(entry['metadata_path']) if entry.get('metadata_path') else {}
        source=metadata.get('source_inventory',{}).get('path')
        issue_path=Path(source).parent/'review_issue_details.json' if source else None
        if issue_path and issue_path.exists() and any(r['patient_id']==patient and r['issue_category']=='EXAM_DETECTION' for r in read_json(issue_path)):
            return 'REVIEW_REQUIRED'
        return 'PHYSICIAN_APPROVED'
    return 'IN_REVIEW'


def enrich(row,entry,repo,cohort):
    if row['patient_id'] not in cohort.get('patient_ids',[]):return row
    entry=archived_candidate(row['patient_id'],cohort) or entry
    return dict(row,eeg_holdout=True,eeg_exam_count=len(layer(entry,'eeg_exams')) if entry else 0,
        eeg_candidate_version=entry['version'] if entry else 'NOT_RUN',eeg_review_status=review_status(entry,repo,row['patient_id']))


def archived_candidate(patient,cohort):
    directory=cohort.get('output_directory')
    path=Path(directory)/'published_candidates.json' if directory else None
    return read_json(path).get(patient) if path and path.exists() else None


def guidance(patient,entry,repo,docs,root):
    if entry.get('module_version')!='1.1':return
    cohort=registry(root)
    if patient not in cohort.get('patient_ids',[]):return
    directory=Path(cohort['output_directory'])
    st.info(f"EEG V1.1 HOLDOUT · {len(layer(entry,'eeg_exams'))} candidate records · {review_status(entry,repo,patient)} · {entry['version']}")
    st.caption('Review statuses: UNREVIEWED → IN_REVIEW / REVIEW_REQUIRED → PHYSICIAN_APPROVED; changed extraction yields STALE. Approval describes explicit review overlays; candidate promotion is disabled.')
    st.warning('Frozen holdout: extraction discrepancies are retained for physician review. No extraction tuning or automatic promotion. Review actions are saved separately from extraction truth.')
    with st.expander('Holdout review priorities and independent source inventory',expanded=False):
        st.write('Confirm exam detection and multi-label phenotype first: NORMAL, FOCAL_SLOWING, GENERALIZED_SLOWING, FOCAL_EPILEPTIFORM_ACTIVITY, GENERALIZED_EPILEPTIFORM_ACTIVITY, RHYTHMIC_DELTA_ACTIVITY, PERIODIC_DISCHARGES, ELECTROGRAPHIC_SEIZURE, STATUS_EPILEPTICUS, OTHER.')
        st.write('Check focal side/location, dominant focus versus spread, independent secondary focus, asymmetric bilateral patterns, epileptiform polarity, seizure/status overcalling, FIRDA/GRDA, reactivity, assessability/artifacts, modality/section boundaries and source abnormality grade. Primary finding alone is not a quality target.')
        path=directory/'source_inventory_studies.json'
        if path.exists():
            rows=[r for r in read_json(path) if r['patient_id']==patient]
            st.caption(f'{len(rows)} independent source recordings; source copies and interval summaries count once. Unknown dates remain unknown.')
            st.dataframe([{k:r[k] for k in ('EEG_date','EEG_date_precision','document_id','page','source_heading','notes')} for r in rows],hide_index=True,width='stretch')
            for row in rows:
                evidence=dict(document_id=row['document_id'],page=row['page'])
                st.button(f"Open source · {row['inventory_id']} · {row['EEG_date'] or 'Date unknown'}",key=f"holdout-source:{patient}:{row['inventory_id']}",on_click=open_source,args=(evidence,docs,patient,row['inventory_id']))
        path=directory/'review_issue_details.json'
        if path.exists():
            issues=[r for r in read_json(path) if r['patient_id']==patient]
            st.caption('Automated/source-comparison flags are review prompts, not physician adjudications.')
            st.dataframe(issues,hide_index=True,width='stretch') if issues else st.write('No automated flags recorded; physician review remains required.')
