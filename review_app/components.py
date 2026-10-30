from collections import Counter
import json
import streamlit as st
from review_app.display_models import evidence_for
from review_app.pdf_utils import resolve_source
from review_app.review_repository import overlay


def counters(rows):
    counts = Counter(r['physician_status'] for r in rows)
    st.caption(' · '.join([f'Total entities: {len(rows)}', f'Accepted: {counts["ACCEPT"]}',
                          f'Edited: {counts["EDIT"]}', f'Rejected: {counts["REJECT"]}',
                          f'Unreviewed: {counts["NOT_REVIEWED"]}',
                          f'Review required: {sum(r["physician_status"] not in ("ACCEPT", "EDIT", "REJECT") for r in rows)}']))


def open_source(evidence, docs, patient, entity):
    try:
        path, page = resolve_source(docs, evidence.get('document_id', evidence.get('source_document_id')), evidence.get('page'))
        st.session_state[f'{patient}:document'] = path
        st.session_state[f'{patient}:page'] = page
        st.session_state[f'{patient}:active'] = entity
        st.session_state.pop('source_error', None)
    except ValueError as exc:
        st.session_state['source_error'] = str(exc)


def review_entities(title, raw_rows, entry, fingerprint, repo, patient, module, docs, fields, columns, key):
    st.subheader(title)
    records = repo.load(patient, module)
    rows = [overlay(r, records.get(r['entity_id']), fingerprint) for r in raw_rows]
    counters(rows)
    # Reviews for disappeared identities must remain visible and prevent approval.
    known = {r['entity_id'] for r in raw_rows}
    orphaned = [r for eid, r in records.items() if eid not in known and r['action'] != 'RESET']
    if key != 'events' and orphaned:
        st.caption('Stored reviews for identities outside this table: see Overview for stale/orphan review counts.')
    if not rows:
        st.info('No canonical entities available in the selected validated artifact.')
        return
    table = [{c: format_cell(r.get(c)) for c in columns} for r in rows]
    st.dataframe(table, hide_index=True, width='stretch')
    scope = ':' + entry['review_scope'] if entry.get('review_scope') else ''
    identity_key = f'{patient}:{key}{scope}:selection'
    selected = st.selectbox('Select item to review', range(len(rows)),
                            format_func=lambda i: f'{rows[i].get("canonical_name", rows[i].get("event_type", rows[i].get("canonical_drug", "UNRESOLVED")))} · {rows[i]["physician_status"]} · {rows[i]["entity_id"]}',
                            key=identity_key)
    raw, row = raw_rows[selected], rows[selected]
    eid = raw['entity_id']
    widget = f'{patient}:{key}{scope}:{eid}:{fingerprint[:12]}'
    if st.session_state.get(f'{patient}:active') == eid:
        st.info('Currently reviewing this item in the source viewer.')
    if row['physician_status'] == 'STALE_REVIEW':
        st.warning('Extraction changed since this review. The old correction is not applied.')
    st.caption(f'Source assertion: {raw.get("status", raw.get("assertion_status", "See linked evidence"))} · First documented: {raw.get("first_documented_date", raw.get("event_date", "Unknown"))}')
    if module == 'medications':
        with st.expander('Individual medication exposures', expanded=True):
            exposure_cols = ['raw_medication', 'normalized_trade_name', 'active_ingredients', 'research_class',
                             'research_subclass', 'dose', 'strength', 'schedule', 'source_available_date',
                             'encounter_id', 'mapping_status', 'document_id', 'page']
            st.dataframe([{c: format_cell(e.get(c)) for c in exposure_cols} for e in raw.get('exposures', [])], hide_index=True)
    with st.expander('Evidence', expanded=True):
        evidence = evidence_for(raw, entry)
        if not evidence:
            st.info('No resolvable evidence records in this artifact.')
        for i, e in enumerate(evidence):
            st.caption(f'Document {e.get("document_id", e.get("source_document_id", "Unknown"))} · page {e.get("page", "Unknown")} · section {e.get("section_id", e.get("section", "Unknown"))} · evidence {e.get("evidence_id", "Unknown")} · assertion {e.get("assertion_status", "Unknown")}')
            snippet = e.get('raw_text', e.get('raw_medication', e.get('evidence_text', '')))
            if snippet:
                st.text(str(snippet)[:450])
            st.button('Open source', key=f'{widget}:source:{i}', on_click=open_source, args=(e, docs, patient, eid))
    with st.form(f'{widget}:form'):
        actions = ['ACCEPT', 'EDIT', 'REJECT', 'UNRESOLVED' if module == 'medications' else 'UNCERTAIN', 'RESET']
        action = st.selectbox('Physician action', actions, key=f'{widget}:action')
        st.caption('Edit fields are saved only when EDIT is selected. Dates may retain year/month precision.')
        physician = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            physician[field] = st.text_input(field.replace('_', ' ').capitalize(), value='' if value is None else str(value), key=f'{widget}:edit:{field}')
        suggest = st.checkbox('Suggest this mapping for local medication dictionary', key=f'{widget}:suggest') if module == 'medications' else False
        submitted = st.form_submit_button('Save review')
    if submitted:
        if suggest and action != 'EDIT':
            st.error('Select EDIT to suggest a dictionary mapping.')
        else:
            try:
                edits = {}
                if action == 'EDIT':
                    for field, value in physician.items():
                        if isinstance(raw.get(field), (list, dict)):
                            edits[field] = json.loads(value)
                            if not isinstance(edits[field], type(raw[field])):
                                raise ValueError(f'{field} must retain its JSON type')
                        else:
                            edits[field] = value or None
                repo.save(patient, module, eid, action, raw, edits if action == 'EDIT' else None,
                          entry['version'], fingerprint, suggest=suggest)
                st.success('Review saved locally.')
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(f'Review could not be saved: {exc}')
    with st.expander('Raw artifact / technical metadata'):
        st.json(raw)
        if records.get(eid):
            st.json(records[eid])


def format_cell(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
