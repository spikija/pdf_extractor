"""Explicit run/compare/review/promote controls for one selected patient/module."""
from collections import Counter
from pathlib import Path
import json

import streamlit as st

from caa_pdf_extractor.workbench_store import WorkbenchStore, integrity, fingerprint, read, candidate_entities
from caa_pdf_extractor.workbench_runner import run_module
from caa_pdf_extractor.workbench_comparison import compare
from review_app.review_repository import ReviewRepository
from review_app.views import diagnoses, medications


def render(module, selected, hashes, repo, patient, docs):
    store = WorkbenchStore()
    current = selected.get(module)
    version = current['version'] if current else None
    title = f'Run {module} for this patient'
    key = f'{patient}:{module}:workbench'
    try:
        ready = store.is_enabled(patient) and bool(store.manifest(patient).get('source_context'))
    except (OSError, ValueError, KeyError):
        st.error('Stable module manifest is unreadable. Restore or inspect it before running this module.')
        return
    if current:
        st.caption(f'CURRENT VALIDATED · {module} v{version}')
    else:
        st.caption(f'CURRENT VALIDATED · {module}: none; candidates require physician approval')
    if not ready:
        st.info('Register this validation patient and prepare stable local sources before running modules.')
    if st.button(title, key=key + ':request', disabled=not ready):
        st.session_state[key + ':pending'] = dict(patient=patient, module=module, active=version)
    pending = st.session_state.get(key + ':pending')
    if pending:
        st.warning(f'Patient: {patient} · Module: {module} · Current validated version: {pending["active"] or "None"} · Source PDFs: {len(docs)}')
        st.caption('Runs the existing local assembler on saved, hash-checked source inputs. The result is a candidate; the active version stays current.')
        if module=='diagnoses':st.caption('Diagnosis runs also infer probable admission reasons locally from complete discharge summaries. These inferences require physician review.')
        if st.button('CONFIRM RUN', key=key + ':confirm'):
            st.session_state.pop(key + ':pending', None)
            try:
                with st.spinner(f'Running {module} for {patient} locally…'):
                    entry = run_module(patient, module, store=store, expected_active=pending['active'])
                st.session_state[key + ':candidate'] = entry['version']
                st.session_state[key + ':dataset'] = 'CANDIDATE'
                st.cache_data.clear()
                st.rerun()
            except (OSError, ValueError, RuntimeError) as exc:
                st.error(f'Run aborted: {exc}')
        if st.button('Cancel run', key=key + ':cancel'):
            st.session_state.pop(key + ':pending', None)
            st.rerun()
    versions = store.versions(patient, module) if ready else {}
    if versions:
        with st.expander('Module version history'):
            st.dataframe([dict(Revision=v, Status=e['status'], Active=v == version,
                               Created=e.get('created_at'), Algorithm=e.get('module_version', e['version']))
                          for v, e in versions.items()], hide_index=True, width='stretch')
    candidates = list(reversed([v for v, entry in versions.items() if entry['status'] in ('CANDIDATE', 'REJECTED')]))
    if not candidates:
        (diagnoses if module == 'diagnoses' else medications).render(selected, hashes, repo, patient, docs)
        return
    candidate_version = st.selectbox('Candidate revision', candidates, format_func=lambda v: f'{versions[v]["status"]} v{v}', key=key + ':candidate')
    entry = versions[candidate_version]
    metadata = read(entry['metadata_path'])
    candidate_repo = ReviewRepository(repo.root / 'candidates' / candidate_version)
    valid = integrity(entry)
    gates = metadata['validation_test_results'].get('hard_gates', {})
    passed = valid and bool(gates) and all(v is True for v in gates.values()) and not metadata.get('fatal_errors') and not entry.get('assertion_safety', {}).get('promotion_blocked') and not entry.get('qa_hold')
    if entry.get('qa_hold'):
        st.error('INDEPENDENT SOURCE QA HOLD: ' + entry['qa_hold'])
    st.caption(f'{entry["status"]} v{candidate_version} · ' + ('Hard gates passed' if passed else 'FAILED — promotion blocked'))
    with st.expander('Candidate validation and run provenance'):
        st.json(metadata)
    if not valid:
        st.error('Candidate artifact hashes are invalid. Review and promotion are blocked.')
        (diagnoses if module == 'diagnoses' else medications).render(selected, hashes, repo, patient, docs)
        return
    differences = compare(current, entry, module)
    counts = Counter(row['change'] for row in differences)
    st.markdown('**CURRENT VALIDATED versus NEW CANDIDATE**')
    st.caption(' · '.join(f'{label}: {counts[label]}' for label in ('ADDED', 'REMOVED', 'CHANGED', 'UNCHANGED')))
    with st.expander('Canonical comparison', expanded=True):
        st.dataframe([dict(Change=r['change'], Kind=r['kind'], Entity=r['entity'],
                           Changes=json.dumps(r['changes'], ensure_ascii=False)) for r in differences], hide_index=True, width='stretch')
        with st.expander('Comparison details'):
            st.json(differences)
    if current is None and key + ':dataset' not in st.session_state:
        st.session_state[key + ':dataset'] = 'CANDIDATE'
    dataset = st.radio('Dataset to review', ['CURRENT VALIDATED', 'CANDIDATE'], key=key + ':dataset', horizontal=True)
    if dataset == 'CANDIDATE':
        st.warning(f'CANDIDATE DATASET — {module} v{candidate_version}. This is not the active extraction.')
        if entry['status'] == 'REJECTED':
            st.info('This candidate was rejected. Its source data and review audit remain available.')
        adapted = dict(entry, review_scope=candidate_version)
        view_selected, view_hashes = dict(selected), dict(hashes)
        view_selected[module] = adapted
        view_hashes[module] = fingerprint(entry)
        if module == 'diagnoses':
            view_selected['events'] = dict(adapted, module='events')
            view_hashes['events'] = view_hashes[module]
        (diagnoses if module == 'diagnoses' else medications).render(view_selected, view_hashes, candidate_repo, patient, docs)
    else:
        (diagnoses if module == 'diagnoses' else medications).render(selected, hashes, repo, patient, docs)
    records = candidate_repo.load(patient, module)
    required = [e for e in candidate_entities(entry, module) if e.get('review_required')]
    candidate_hash = fingerprint(entry)
    unresolved = sum(not (records.get(e['entity_id'], {}).get('action') in ('ACCEPT', 'EDIT', 'REJECT')
                         and records[e['entity_id']].get('source_artifact_hash') == candidate_hash) for e in required)
    st.caption(f'Review-required candidate entities awaiting adjudication: {unresolved}')
    confirmation = st.checkbox('I confirm physician approval of this candidate and activation in place of the current version', key=key + f':approve:{candidate_version}')
    if st.button('Approve candidate as validated version', key=key + ':promote', disabled=not (passed and not unresolved and confirmation and entry['status'] == 'CANDIDATE')):
        try:
            store.promote(patient, module, candidate_version, confirmed=confirmation, reviews=records, expected_active=version)
            st.cache_data.clear()
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f'Promotion blocked: {exc}')
    if st.button('Reject candidate version', key=key + ':reject', disabled=entry['status'] != 'CANDIDATE'):
        try:
            store.reject(patient, module, candidate_version)
            st.cache_data.clear()
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(f'Rejection failed: {exc}')
