from review_app.config import MODULES
from review_app.display_models import diagnosis_entities, medication_entities
from review_app.review_repository import overlay


def module_rows(selected, latest, hashes, repo, patient):
    result = []
    all_dx_ids = set()
    for module in ('diagnoses', 'events'):
        if module in selected:
            all_dx_ids.update(r['entity_id'] for r in diagnosis_entities(selected[module], module == 'events'))
    for module, label in MODULES.items():
        entry = selected.get(module, latest.get(module))
        status, pending, total = 'NOT_RUN', 0, 0
        if entry:
            source_status = entry.get('status', '')
            status = ('STALE' if 'STALE' in source_status else 'FAILED' if 'FAIL' in source_status else 'EXTRACTED')
            if module in selected and module in ('diagnoses', 'events', 'medications'):
                rows = medication_entities(entry) if module == 'medications' else diagnosis_entities(entry, module == 'events')
                records = repo.load(patient, 'medications' if module == 'medications' else 'diagnoses')
                statuses = [overlay(r, records.get(r['entity_id']), hashes[module])['physician_status'] for r in rows]
                ids = {r['entity_id'] for r in rows} if module == 'medications' else all_dx_ids
                orphans = sum(r['action'] != 'RESET' and eid not in ids for eid, r in records.items()) if module != 'events' else 0
                total = len(rows)
                pending = sum(s not in ('ACCEPT', 'EDIT', 'REJECT') for s in statuses) + orphans
                status = 'STALE' if 'STALE_REVIEW' in statuses or orphans else 'REVIEW_REQUIRED' if pending else 'PHYSICIAN_APPROVED' if rows else 'EXTRACTED'
            elif module in ('diagnoses', 'events', 'medications'):
                status = 'STALE' if 'STALE' in source_status else 'FAILED' if 'FAIL' in source_status or source_status == 'VALIDATED' else 'REVIEW_REQUIRED'
                pending = 1
        result.append(dict(Module=label, Version=entry.get('version', '—') if entry else '—', Status=status,
                           **{'Review items': pending, 'Total entities': total,
                              'Source status': entry.get('status', 'NOT_RUN') if entry else 'NOT_RUN'}))
        result[-1]['Candidate revisions'] = ', '.join(entry.get('candidate_versions', [])) if entry else ''
    return result


def patient_status(rows, has_reviews=False, errors=False):
    if errors or any(r['Status'] == 'FAILED' for r in rows):
        return 'ERROR'
    # Overall approval applies to implemented MVP modules; future dependency stale stays in Overview.
    active = [r for r in rows if r['Module'] in ('Diagnoses', 'Clinical events', 'Medications')]
    if any(r['Status'] == 'STALE' for r in active):
        return 'STALE'
    if all(r['Status'] == 'PHYSICIAN_APPROVED' or (r['Status'] == 'EXTRACTED' and r['Total entities'] == 0) for r in active) and any(r['Total entities'] for r in active):
        return 'APPROVED'
    if has_reviews:
        return 'IN_REVIEW'
    if any(r['Review items'] for r in active):
        return 'REVIEW_REQUIRED'
    return 'NOT_REVIEWED'


def render(rows):
    import streamlit as st
    st.dataframe(rows, hide_index=True, width='stretch')
    st.metric('Total unresolved review items', sum(r['Review items'] for r in rows))
    st.caption('Physician approval covers canonical entities in the implemented MVP panels. Future modules retain extraction/dependency status.')
