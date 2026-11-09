"""Read-only cohort review projection and resolved-AF filtering."""
from collections import Counter
from pathlib import Path
import streamlit as st

from review_app.artifact_repository import read_json, layer, module_hash, sha
from review_app.review_repository import ReviewRepository
from caa_pdf_extractor.workbench_store import candidate_entities


@st.cache_data(show_spinner=False, max_entries=128)
def cached_entities(entry, signatures):
    return candidate_entities(entry, entry['module'])

FILTERS = ['ALL', 'EEG V1.1 HOLDOUT', 'UNREVIEWED', 'IN REVIEW', 'REVIEW REQUIRED', 'APPROVED', 'STALE', 'ERROR',
           'CONFIRMED AF', 'SUSPECTED AF', 'NO CONFIRMED AF']
AF_FILTERS = {'CONFIRMED AF':'CONFIRMED_AF', 'SUSPECTED AF':'SUSPECTED_AF', 'NO CONFIRMED AF':'NO_CONFIRMED_AF'}


def pick_entry(manifest, module, reference=None):
    group = manifest.get('modules', {}).get(module, {})
    versions = group.get('versions', {})
    requested = reference.get('version') if reference else group.get('active_validated_version')
    if requested in versions:
        return versions[requested]
    candidates = [e for e in versions.values() if e.get('status') == 'CANDIDATE']
    if not candidates:return None
    entry = max(candidates, key=lambda e:e.get('created_at',''))
    if entry['version'] in group.get('candidate_qa_holds',{}):
        entry = dict(entry,qa_hold=group['candidate_qa_holds'][entry['version']])
    return entry


def module_state(patient, module, entry, repo, reference=None):
    if entry is None:
        return dict(status='ERROR', source_status='NOT_RUN', pending=0, total=0, reviewed=0)
    try:
        if entry.get('qa_hold'):
            raise ValueError('Independent QA hold')
        files = entry.get('artifacts',[])
        if not files or not all(sha(f['path'])==f['sha256'] for f in files):
            raise ValueError('Artifact integrity failure')
        gates = entry.get('validation', {}).get('hard_gates', {})
        metadata = read_json(entry['metadata_path']) if entry.get('metadata_path') else {}
        if metadata.get('fatal_errors') or (gates and not all(v is True for v in gates.values())):
            raise ValueError('Pipeline validation failed')
        digest = module_hash(entry)
        signatures = tuple((Path(f['path']).stat().st_mtime_ns,Path(f['path']).stat().st_size) for f in files)
        entities = cached_entities(entry,signatures)
        if reference and reference.get('physician_review_passed'):
            return dict(status='APPROVED' if digest == reference['hash'] else 'STALE',
                        source_status=entry['status'], pending=0, total=len(entities), reviewed=len(entities))
        scope = ReviewRepository(repo.root / 'candidates' / entry['version']) if entry['status']=='CANDIDATE' else repo
        records = scope.load(patient, module)
        ids = {e['entity_id'] for e in entities}
        stale = bool(entry.get('assertion_safety')) or any(r['action']!='RESET' and (eid not in ids or r.get('source_artifact_hash')!=digest) for eid,r in records.items())
        reviewed = sum(records.get(e['entity_id'],{}).get('action') in ('ACCEPT','EDIT','REJECT') and records[e['entity_id']].get('source_artifact_hash')==digest for e in entities)
        pending = len(entities)-reviewed
        status = 'STALE' if stale else 'APPROVED' if entities and not pending else 'IN_REVIEW' if records else 'NOT_REVIEWED'
        return dict(status=status, source_status=entry['status'], pending=pending, total=len(entities), reviewed=reviewed)
    except (OSError, ValueError, KeyError, TypeError):
        return dict(status='ERROR', source_status=entry.get('status','FAILED'), pending=0, total=0, reviewed=0)


def patient_summary(patient, manifest, repo, references=None):
    references = references or {}
    modules, entries = {}, {}
    module_names=['diagnoses','medications'] if any(m in manifest.get('modules',{}) for m in ('diagnoses','medications')) else []
    if 'eeg' in manifest.get('modules',{}):module_names.append('eeg')
    for module in module_names:
        ref = references.get(module)
        entries[module] = pick_entry(manifest,module,ref)
        modules[module] = module_state(patient,module,entries[module],repo,ref)
    statuses = [m['status'] for m in modules.values()]
    status = 'ERROR' if 'ERROR' in statuses else 'STALE' if 'STALE' in statuses else 'APPROVED' if all(s=='APPROVED' for s in statuses) else 'IN_REVIEW' if any(m['reviewed'] for m in modules.values()) else 'NOT_REVIEWED'
    af = layer(entries.get('diagnoses') or {}, 'af_state')
    af_state = af.get('AF_state','UNKNOWN') if isinstance(af,dict) else 'UNKNOWN'
    if af_state == 'UNKNOWN':
        # Resolve only stored diagnosis-layer state or a checked assertion audit, never strings.
        audit = (entries.get('diagnoses') or {}).get('assertion_audit', {})
        af_state = audit.get('AF_state','UNKNOWN')
    return dict(patient_id=patient,status=status,AF_state=af_state,modules=modules,
                review_required=sum(m['pending'] for m in modules.values()))


def matches(row, choice):
    if choice == 'ALL':return True
    if choice == 'EEG V1.1 HOLDOUT':return row.get('eeg_holdout',False)
    if choice in AF_FILTERS:return row['AF_state']==AF_FILTERS[choice]
    if choice == 'REVIEW REQUIRED':return row['review_required']>0 or row['status'] in ('STALE','ERROR')
    return row['status']=={'UNREVIEWED':'NOT_REVIEWED','IN REVIEW':'IN_REVIEW'}.get(choice,choice)


def render(rows):
    holdout=[r for r in rows if r.get('eeg_holdout')]
    if holdout:
        with st.expander('EEG V1.1 HOLDOUT',expanded=False):
            st.caption('Candidate records are extraction outputs, not a validated exam denominator. All candidates require physician review.')
            st.dataframe([dict(Patient=r['patient_id'],EEG_exams=r['eeg_exam_count'],Review_status=r['eeg_review_status'],Candidate_version=r['eeg_candidate_version']) for r in holdout],hide_index=True,width='stretch')
    with st.expander('Validation cohort dashboard', expanded=True):
        counts = Counter(r['status'] for r in rows)
        st.caption(f'Total patients: {len(rows)} · Unreviewed: {counts["NOT_REVIEWED"]} · In review: {counts["IN_REVIEW"]} · Review required: {sum(r["review_required"]>0 for r in rows)} · Physician approved: {counts["APPROVED"]} · Stale: {counts["STALE"]} · Pipeline error: {counts["ERROR"]}')
        grouped = Counter((module, data['source_status'], data['status']) for r in rows for module,data in r['modules'].items())
        st.dataframe([dict(Module=module.title(),Artifact_status=source,Review_status=status,Patients=count)
                      for (module,source,status),count in sorted(grouped.items())], hide_index=True, width='stretch')
        st.caption('Review-required counts include unreviewed entities. Original-five reference approval is the physician review reported for the frozen reference versions; patient tabs preserve actual active/candidate pointers.')
