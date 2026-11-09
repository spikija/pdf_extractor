from review_app.components import review_entities
from review_app.display_models import diagnosis_entities,admission_entities
from review_app.components import open_source
from review_app.artifact_repository import read_json, sha


def render(selected, hashes, repo, patient, docs):
    import streamlit as st
    dx = selected.get('diagnoses', {})
    audit = dx.get('assertion_audit')
    from review_app.artifact_repository import layer
    af_state = layer(dx, 'af_state')
    if isinstance(af_state, dict) and af_state:
        st.info(f'Source-backed AF state: {af_state["AF_state"]}')
        from caa_pdf_extractor.extraction.assertion_review import unsafe_af_identity
        if af_state.get('confirmed_af') and unsafe_af_identity(dx):
            st.error('The stored confirmed AF state lacks independently positive evidence under the current assertion rules. This historical artifact is unchanged; source review and a corrected candidate are required.')
        canonical = layer(dx, 'canonical_disease_identities')
        present = any(d.get('canonical_name') == 'ATRIAL_FIBRILLATION' for d in canonical)
        st.caption('AF canonical disease: ' + ('PRESENT' if present else 'ABSENT') + ' in this extraction artifact')
    if dx.get('assertion_safety'):
        st.error('ASSERTION SAFETY HOLD: unsupported AF identity suppressed in this historical extraction. Original artifact retained; inspect a repaired candidate before promotion.')
    observations = []
    caa_assertions=[a for a in layer(dx,'diagnostic_assertion_timeline') if a.get('canonical_name')=='CEREBRAL_AMYLOID_ANGIOPATHY']
    if caa_assertions:
        with st.expander('CAA source assertions',expanded=False):
            st.caption('Source assertions are separate from computed Boston criteria. Historical is a temporal attribute; nondiagnostic exams do not establish disease.')
            for n,a in enumerate(caa_assertions):
                st.markdown(f"**{a['assertion_status']}** · {a.get('semantic_relation') or a.get('semantic_class','')}")
                st.text(a.get('raw_text','')[:450])
                st.caption(f"Document {a.get('document_id')} · page {a.get('page')} · {a.get('section_id')}")
                st.button('Open source',key=f'{patient}:caa-source:{dx.get("version")}:{n}',on_click=open_source,
                    args=(a,docs,patient,a.get('evidence_id','CAA assertion')))
    if audit:
        if sha(audit['path']) != audit['sha256']:
            st.error('Assertion audit failed integrity. Restore the audit before relying on this dataset.')
        else:
            observations = read_json(audit['path']).get(patient, [])
    if not observations:
        observations = layer(dx, 'af_observations')
    if observations:
        state = audit.get('AF_state') if audit else None
        if state:
            st.info(f'Source-backed AF state: {state}')
        with st.expander('AF observations — positive, negative and uncertain source evidence', expanded=bool(dx.get('assertion_safety'))):
            for i, observation in enumerate(observations):
                state = observation['assertion_status']
                st.markdown(f'**Assertion: {state}**')
                if state == 'NEGATED':
                    st.warning('NEGATIVE OBSERVATION — does not establish AF and does not erase independently confirmed historical AF.')
                st.caption(f'Document {observation.get("document_id")} · page {observation.get("page")} · section {observation.get("section_id")} · evidence {observation.get("evidence_id")} · date {observation.get("observation_date")} · scope {observation.get("scope")} · modality {observation.get("source_modality", "OTHER")}')
                st.text(observation.get('source_text_short', observation.get('raw_text', ''))[:450])
                st.button('Open source', key=f'{patient}:af-observation:{dx.get("version")}:{i}', on_click=open_source,
                          args=(observation, docs, patient, observation.get('evidence_id', 'AF observation')))
    for module, title, fields, cols in [
        ('diagnoses', 'Patient diagnoses',
         ['canonical_name', 'first_documented_date', 'first_documented_date_precision', 'attributes', 'status'],
         ['canonical_name', 'raw_text', 'first_documented_date','attributes', 'status', 'mention_count', 'document_count', 'review_required', 'physician_status']),
        ('events', 'Clinical events',
         ['event_type', 'event_date', 'event_date_precision', 'side', 'territory', 'location', 'etiology', 'acute_treatment', 'status'],
         ['event_type', 'event_date','event_date_precision', 'side', 'territory', 'location', 'etiology','admission_linked', 'status','mention_count', 'review_required', 'physician_status'])]:
        if module not in selected:
            st.warning(f'{title}: no validated artifact available. Legacy source status is in Overview.')
            continue
        entry = selected[module]
        review_entities(title, diagnosis_entities(entry, module == 'events'), entry, hashes[module], repo,
                        patient, 'diagnoses', docs, fields, cols, module)
    if dx and admission_entities(dx):
        st.caption('Probable admission diagnoses are local model inferences from the complete encounter summary. They require physician review and do not establish final diagnoses or new events.')
        review_entities('Admissions · probable diagnosis',admission_entities(dx),dx,hashes['diagnoses'],repo,
            patient,'diagnoses',docs,['probable_admission_diagnosis','probable_admission_event_type','confidence'],
            ['admission_date','probable_admission_diagnosis','probable_admission_event_type','confidence','admission_event_id','status','physician_status'],'admissions')
