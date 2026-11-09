"""Launch from project root: streamlit run review_app/app.py."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.dont_write_bytecode = True
os.environ['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'

import streamlit as st
from review_app.artifact_repository import discover, select_modules, module_hash, read_json
from review_app.patient_repository import patient_context
from review_app.pdf_utils import documents, render_page
from review_app.review_repository import ReviewRepository
from review_app.views import patient_overview, future_modules, workbench, eeg, tfne
from review_app import cohort_dashboard
from review_app.tfne_repository import ARTIFACT_VERSIONS

st.set_page_config(page_title='Local physician review', layout='wide')
repo = ReviewRepository()


@st.cache_data(ttl=30, show_spinner=False)
def patient_index():
    return discover()


@st.cache_data(ttl=30, show_spinner=False)
def snapshot(paths, signatures):
    return select_modules([Path(p) for p in paths])


@st.cache_data(show_spinner=False, max_entries=32)
def pdf_index(patient, metadata, signatures):
    return documents(patient, metadata)


def load_snapshot(paths):
    return snapshot(tuple(map(str, paths)), tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths))


def main():
    st.title('Physician extraction workbench')
    st.caption('Local artifacts · Explicit single-patient runs · Physician-controlled candidate promotion')
    index = patient_index()
    if not index:
        st.info('No module manifests found under patient_artifacts/ or data/debug/.')
        return
    if st.sidebar.button('Refresh artifact index'):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.header('PATIENTS')
    tfne_queues = {f'TFNE V{version} PILOT':version for version in ARTIFACT_VERSIONS}
    queue = st.sidebar.selectbox('Review queue', cohort_dashboard.FILTERS+list(tfne_queues))
    # Canonical JSON reads are cached by file signature; PDFs stay lazy until selection.
    statuses, cohort_rows = {}, []
    from review_app.config import STABLE_ROOT
    registry_path = STABLE_ROOT / 'validation_cohort.json'
    registry = read_json(registry_path) if registry_path.exists() else {}
    from review_app import eeg_holdout
    holdout_cohort=eeg_holdout.registry(STABLE_ROOT)
    for pid in sorted(index):
        try:
            stable_manifest = next((path for path in index[pid] if read_json(path).get('storage')=='STABLE'), None)
            if stable_manifest:
                manifest = read_json(stable_manifest)
                reference = registry.get('reference_versions',{}).get(pid) if stable_manifest.parent.parent.resolve()==STABLE_ROOT.resolve() else None
                row = cohort_dashboard.patient_summary(pid,manifest,repo,reference)
                row = eeg_holdout.enrich(row,cohort_dashboard.pick_entry(manifest,'eeg'),repo,holdout_cohort)
                cohort_rows.append(row);statuses[pid]=row['status']
                continue
            ps, pl, pe = load_snapshot(index[pid])
            ph = {m: module_hash(e) for m, e in ps.items() if m in ('diagnoses', 'events', 'medications')}
            pr = patient_overview.module_rows(ps, pl, ph, repo, pid)
            has_reviews = any(repo.load(pid, m) for m in ('diagnoses', 'medications'))
            statuses[pid] = patient_overview.patient_status(pr, has_reviews, bool(pe))
        except (OSError, ValueError, KeyError, TypeError):
            statuses[pid] = 'ERROR'
    for pid in sorted(index):
        if pid not in {r['patient_id'] for r in cohort_rows}:
            cohort_rows.append(dict(patient_id=pid,status=statuses[pid],AF_state='UNKNOWN',modules={},review_required=0))
    cohort_dashboard.render(cohort_rows)
    tfne_version = tfne_queues.get(queue)
    options = [r['patient_id'] for r in cohort_rows if ((STABLE_ROOT/r['patient_id']/ARTIFACT_VERSIONS[tfne_version]).exists() if tfne_version else cohort_dashboard.matches(r,queue))]
    st.sidebar.caption(f'{len(index)} patients with module artifacts')
    if not options:
        st.info('No patients match this queue.')
        return
    patient = st.sidebar.selectbox('Patient ID', options, format_func=lambda p: f'{p} · {statuses[p]}', key='patient_picker')
    try:
        selected, latest, errors = load_snapshot(index[patient])
        if 'events' not in latest and latest.get('diagnoses',{}).get('status')=='CANDIDATE':
            latest['events'] = dict(latest['diagnoses'],module='events')
        # Recheck all current bytes on every interaction; cached JSON cannot hide a changed extraction.
        hashes = {}
        for module, entry in list(selected.items()):
            fingerprint = module_hash(entry)
            from review_app.artifact_repository import sha
            if any(sha(f['path']) != f['sha256'] for f in entry['artifacts']):
                errors.append(f'{module}: extraction checksum changed; refresh artifact index')
                del selected[module]
            else:
                hashes[module] = fingerprint
        context = patient_context(latest)
        pdf_dir = Path(__file__).resolve().parents[1] / 'pdf_db' / patient
        signatures = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in pdf_dir.glob('*.pdf'))
        docs = pdf_index(patient, context['documents'], signatures)
        rows = patient_overview.module_rows(selected, latest, hashes, repo, patient)
        reviewed = any(repo.load(patient, m) for m in ('diagnoses', 'medications', 'eeg'))
        status = patient_overview.patient_status(rows, reviewed, bool(errors))
        st.session_state[f'{patient}:status'] = status
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.session_state[f'{patient}:status'] = 'ERROR'
        st.error(f'Patient artifacts could not be loaded: {exc}')
        return
    demo = context['demographics']
    st.subheader(f'{patient} · {status}')
    st.caption(f'DOB: {demo.get("date_of_birth", "Unknown")} · Sex: {demo.get("sex", demo.get("gender", "Unknown"))} · PDFs: {len(docs)} · Encounters: {len(context["encounters"])}')
    st.caption(' · '.join(f'{m}: {e.get("version")}' for m, e in selected.items()))
    for error in errors:
        st.warning(error)
    if 'source_error' in st.session_state:
        st.warning(st.session_state['source_error'])
    left, right = st.columns([5, 6])
    with left:
        st.subheader('PDF / source')
        if docs:
            doc_key, page_key = f'{patient}:document', f'{patient}:page'
            paths = [d['path'] for d in docs]
            if st.session_state.get(doc_key) not in paths:
                st.session_state[doc_key] = paths[0]
            def reset_page():
                st.session_state[page_key] = 1
            path = st.selectbox('Document', paths, format_func=lambda p: next(f'{d["filename"]} — {d["date"] or "date unknown"}' for d in docs if d['path'] == p), key=doc_key, on_change=reset_page)
            doc = next(d for d in docs if d['path'] == path)
            st.caption(f'Document ID: {doc["document_id"]} · {doc["pages"]} pages')
            st.session_state[page_key] = max(1, min(doc['pages'], st.session_state.get(page_key, 1)))
            def move_page(delta):
                st.session_state[page_key] = max(1, min(doc['pages'], st.session_state[page_key] + delta))
            prev, number, nxt = st.columns([1, 2, 1])
            prev.button('Previous', on_click=move_page, args=(-1,), disabled=st.session_state[page_key] == 1)
            page = number.number_input('Page', min_value=1, max_value=doc['pages'], step=1, key=page_key)
            nxt.button('Next', on_click=move_page, args=(1,), disabled=page == doc['pages'])
            if st.session_state.get(f'{patient}:active'):
                st.caption(f'Source for: {st.session_state[f"{patient}:active"]}')
            st.image(render_page(path, page, Path(path).stat().st_mtime_ns), width='stretch')
        else:
            st.warning('No local PDFs available for this patient.')
    with right:
        tabs = st.tabs(['Overview', 'Diagnoses', 'Medications', 'EEG', 'CAA', 'Neurosonology', 'Clinical Scales', 'Risk Scores', 'OAC Decisions', 'TFNE / Neurological Deficits'])
        with tabs[0]:
            patient_overview.render(rows)
        with tabs[1]:
            workbench.render('diagnoses', selected, hashes, repo, patient, docs)
        with tabs[2]:
            workbench.render('medications', selected, hashes, repo, patient, docs)
        with tabs[3]:
            holdout_entry=eeg_holdout.archived_candidate(patient,holdout_cohort) if queue=='EEG V1.1 HOLDOUT' else None
            if holdout_entry:
                eeg.render(holdout_entry,repo,patient,docs,preferred_version=holdout_entry['version'])
            else:eeg.render(selected.get('eeg',latest.get('eeg')),repo,patient,docs)
        for tab, names in zip(tabs[4:], [['CAA'], ['neurosonology'], ['clinical_scales'], ['CHA2DS2_VA', 'ORBIT'], ['OAC_decision_analysis']]):
            with tab:
                future_modules.render(names, selected, latest)
        with tabs[-1]:tfne.render(patient,docs,preferred_version=tfne_version)


main()
