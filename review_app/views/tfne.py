"""History-only TFNE pilot episode cards and explicit physician overlays."""
import json
from pathlib import Path
import streamlit as st
from review_app.components import open_source
from review_app.tfne_repository import load_artifact,available_versions,TFNEReviewRepository,project


def render(patient,docs,review_root=None,preferred_version=None):
    st.subheader('TFNE / Neurological Deficits')
    versions=available_versions(patient) or ['0.1']
    default=versions.index(preferred_version) if preferred_version in versions else 0
    version_key=f'tfne:{patient}:version'+(f':queue:{preferred_version}' if preferred_version else '')
    version=st.selectbox('TFNE extraction version',versions,index=default,format_func=lambda v:'v'+v,key=version_key)
    try:artifact,fingerprint=load_artifact(patient) if version=='0.1' else load_artifact(patient,version=version)
    except (ValueError,OSError,KeyError) as exc:st.error(str(exc));return
    if artifact is None:st.info('This patient is outside the frozen ten-patient TFNE pilot.');return
    if version=='0.1':repo=TFNEReviewRepository() if review_root is None else TFNEReviewRepository(review_root)
    else:repo=TFNEReviewRepository(None if review_root is None else Path(review_root)/'v0_2',version=version)
    events=repo.load(patient,fingerprint);episodes=project(artifact,events)
    st.warning(f'PILOT {version} · History-only research phenotype · Physician validation pending. Screening is not a diagnosis.')
    st.caption(f"Granite prompt: {artifact['granite_prompt_name']} · Model: {artifact['granite_model_name']} · {len(episodes)} episode cards")
    if artifact.get('source_inputs'):
        from review_app.artifact_repository import read_json
        sections=read_json(artifact['source_inputs'])['sections']
        with st.expander('All permitted source narratives — check episode recall'):
            chosen=st.selectbox('TFNE source narrative',range(len(sections)),format_func=lambda i:f"{sections[i]['filename']} · page {sections[i]['page']} · {sections[i]['section_type']}")
            source=sections[chosen]
            st.text('\n\n'.join(row['text'] for row in source['spans']))
            st.button('Open source narrative',key=f'tfne:{patient}:all-sources',on_click=open_source,args=(source,docs,patient,source['section_id']))
    if artifact['validation_flags']:
        with st.expander('Source-grounding flags for physician review'):
            st.dataframe([dict(row,raw_value=json.dumps(row['raw_value'],ensure_ascii=False)) for row in artifact['validation_flags']],hide_index=True,width='stretch')
    if not episodes:st.info('No grounded episodes extracted. Review the allowed source narratives for possible omissions.')
    def yn(value):return {'TRUE':'YES','FALSE':'NO'}.get(value,'UNKNOWN')
    old=None
    if version=='0.2':
        try:old,_=load_artifact(patient)
        except (OSError,ValueError,KeyError):pass
    for n,e in enumerate(episodes,1):
        eid=e['episode_id'];key=f'tfne:{patient}:{eid}:{fingerprint[:12]}'
        with st.container(border=True):
            st.markdown(f"**EPISODE {n} · {e.get('episode_date_text') or 'Date not stated'}**")
            st.markdown('**TEMPORAL COURSE: '+e['temporal_course'].replace('_',' — ')+'**')
            st.write(e['symptom_description'])
            st.caption('Source assertion: '+e['assertion'])
            if version=='0.2':
                from caa_pdf_extractor.tfne_v02_audit import matching
                from caa_pdf_extractor.tfne_v02_schema import FIELDS
                paired=[old['episodes'][i] for i,j in matching(old['episodes'],[e])] if old else []
                changed=[field for field in FIELDS if not paired or any(p.get(field)!=e.get(field) for p in paired)]
                st.warning('Changed from v0.1: '+(', '.join(changed) if changed else 'No clinical field changes'))
                st.caption('Comparison uses shared source passages. Episode splits, merges and field changes require physician review.')
                rows=[]
                for field in FIELDS:
                    quotes=list(dict.fromkeys(r['evidence_text'] for r in e.get('field_evidence',{}).get(field,[])))
                    rows.append({'Field':field,'Value':json.dumps(e.get(field),ensure_ascii=False),
                        'Evidence span':' | '.join(quotes) or 'No supporting evidence; value left unknown/empty',
                        'Validation status':e.get('field_validation_status',{}).get(field,'REVIEW_REQUIRED'),
                        'Changed from v0.1':field in changed})
                with st.expander('v0.2 values, evidence spans and validation status',expanded=True):
                    st.dataframe(rows,hide_index=True,width='stretch')
            st.write('Historical diagnosis: '+e['historical_diagnosis'])
            if e.get('merged_historical_diagnoses'):st.caption('Historical labels in merged sources: '+', '.join(dict.fromkeys(e['merged_historical_diagnoses'])))
            st.write('Retrospective clinical phenotype: '+e['retrospective_clinical_phenotype'])
            st.write('TFNE clinical screen: '+e['TFNE_clinical_screen'])
            st.caption(f"Duration: {e.get('duration_text') or 'UNKNOWN'} · Focality: {e['focality']} · Side: {e['side']}")
            st.write('Symptoms: '+(', '.join(e['symptom_domains']) or 'UNKNOWN')+' · Positive/negative: '+e['positive_negative'])
            if e['positive_symptoms']:st.caption('Positive symptoms: '+', '.join(e['positive_symptoms']))
            if e['negative_symptoms']:st.caption('Negative symptoms: '+', '.join(e['negative_symptoms']))
            st.write(f"SPREADING: {yn(e['spreading_progression'])} · RECURRENT: {yn(e['recurrent'])} · STEREOTYPED: {yn(e['stereotyped'])}")
            st.write('Complete resolution: '+yn(e['fully_resolved']))
            if e['spread_sequence']:st.write('Spread: '+' → '.join(e['spread_sequence']))
            if e['body_regions']:st.caption('Body distribution: '+', '.join(e['body_regions']))
            if e.get('episode_count') or e.get('episode_frequency_text'):st.caption(f"Episode count: {e.get('episode_count') or 'Not stated'} · Frequency: {e.get('episode_frequency_text') or 'Not stated'}")
            with st.expander('Epilepsy-differential clinical features'):
                st.json({field:e.get(field,'UNKNOWN') for field in ('impaired_awareness','loss_of_consciousness','automatisms','tonic_posturing','clonic_jerking','forced_head_eye_deviation','speech_arrest','post_event_confusion','post_event_aphasia','postictal_confusion','postictal_aphasia','todd_paresis')})
            if e.get('symptom_components'):st.dataframe(e['symptom_components'],hide_index=True,width='stretch')
            st.caption('Review status: '+e['review_status'])
            if e.get('review_source'):st.caption('Physician overlay; original extraction is preserved.')
            with st.expander('Exact source text and field evidence',expanded=True):
                unique={row['evidence_id']:row for row in e['evidence']}
                for i,row in enumerate(unique.values()):
                    st.caption(f"Document {row['document_id']} · page {row['page']} · {row['section_type']}")
                    st.text(row['evidence_text'])
                    st.button('Open source',key=key+f':source:{i}',on_click=open_source,args=(row,docs,patient,eid))
                with st.expander('Field provenance'):st.json(e.get('field_evidence',{}))
            with st.form(key+':review'):
                action=st.selectbox('TFNE review action',['ACCEPT','EDIT','REJECT','UNCERTAIN'],key=key+':action')
                correction=st.text_area('TFNE corrections (JSON; EDIT only)',value='{}',key=key+':changes')
                save=st.form_submit_button('Save TFNE review')
            if save:
                try:repo.save(artifact,fingerprint,[eid],action,json.loads(correction) if action=='EDIT' else {});st.rerun()
                except (ValueError,TypeError) as exc:st.error(str(exc))
    if episodes:
        with st.expander('Split or merge episodes'):
            with st.form(f'tfne:{patient}:structure:{fingerprint[:12]}'):
                action=st.selectbox('Episode structure action',['SPLIT','MERGE'])
                ids=st.multiselect('Episodes to split / merge',[e['episode_id'] for e in episodes],format_func=lambda eid:next(e['symptom_description'] for e in episodes if e['episode_id']==eid))
                changes=st.text_area('Structure corrections (JSON)',value='{}',help='SPLIT: select one episode and enter an array of two or more clinical correction objects. MERGE: select two or more episodes and enter one correction object; unspecified temporal facts remain unknown.')
                save=st.form_submit_button('Save episode structure review')
            if save:
                try:repo.save(artifact,fingerprint,ids,action,json.loads(changes));st.rerun()
                except (ValueError,TypeError) as exc:st.error(str(exc))
