"""Physician review of immutable EEG candidates and source-linked findings."""
import json
import streamlit as st
from review_app.artifact_repository import layer,module_hash,sha
from review_app.review_repository import ReviewRepository,overlay
from review_app.components import open_source
from caa_pdf_extractor.workbench_eeg import reviewed_exam,validate_edits,groups_for,EEGWorkbenchStore


def clinical_details(exam,findings,patient,docs,version):
    st.write(f"Assessability: {exam['assessability']} · Source abnormality grade: {exam['source_reported_abnormality_grade']}")
    st.write(f"Artifact types: {', '.join(exam['artifact_type']) or 'UNKNOWN'} · Severity: {exam['artifact_severity']}")
    st.write(f"Dominant focus: {exam['dominant_focus_side']} · {', '.join(exam['dominant_focus_regions']) or 'regions unknown'}")
    st.write(f"Asymmetry: {exam['focal_slowing_asymmetry']} · Extent: {exam['anterior_posterior_extent']}")
    st.write(f"Spread: {exam['spread_side']} · {', '.join(exam['spread_regions']) or 'not documented'}")
    st.write(f"Rhythmic pattern type: {exam['rhythmic_delta_type']} · Asymmetry: {exam['pattern_asymmetry']}")
    st.write(f"Epileptiform evidence: {exam['epileptiform_evidence_status']} · Morphology: {exam['epileptiform_discharge_type']} · {exam['morphology_modifier']} · Frequency: {exam['epileptiform_frequency']}")
    st.write(f"Ictal activity evidence: {exam['ictal_activity_status']} · Clinical signs of NCSE: {exam['clinical_signs_of_NCSE']}")
    st.caption(f"Focal slowing continuity: {exam['focal_slowing_continuity']} · Background slowing continuity: {exam['background_slowing_continuity']} · Global background continuity: {exam['background_continuity']}")
    for reactivity in exam['reactivity_findings']:st.write(f"Reactivity — {reactivity['stimulus']}: {reactivity['response']}")
    evidence={f['evidence_id']:f for f in findings}
    if exam['focal_abnormalities']:
        st.write('Focal abnormalities and source relationships')
        st.dataframe([{'Relationship':f['relationship'].replace('_',' '),'Finding':f['finding_type'],'Side':f['side'],'Regions':', '.join(f['regions']) or 'UNKNOWN',
            'Extent':f['anterior_posterior_extent'],'Continuity':f['continuity'],'Frequency':f['frequency'],'Asymmetry':f['asymmetry'],'Epileptiform component':f['epileptiform_component']} for f in exam['focal_abnormalities']],hide_index=True,width='stretch')
        st.caption('Repeated source statements may corroborate the same focus; source relationships are retained rather than inferring a new independent focus.')
        for f in exam['focal_abnormalities']:
            st.caption(f"{f['relationship'].replace('_',' ')} · {f['side']} · {', '.join(f['regions']) or 'regions unknown'}: {f['source_text_short']}")
            for i,eid in enumerate(f['evidence_ids']):
                if eid in evidence:
                    st.button('Open source',key=f"{patient}:eeg:focus:{version}:{f['focal_abnormality_id']}:{i}",on_click=open_source,args=(evidence[eid],docs,patient,f['focal_abnormality_id']))
    if exam['slow_patterns']:
        st.write('Slow and rhythmic patterns')
        st.dataframe([{'Pattern':p['kind'],'Source term':p['source_term'] or '—','Distribution':p['distribution'],'Maximum':p['maximum'],'Asymmetry':p['asymmetry'],
            'Regions':', '.join(p['regions']),'Frequency (Hz)':str(p['frequency_hz']) if p['frequency_hz'] is not None else 'UNKNOWN','Continuity':p['continuity']} for p in exam['slow_patterns']],hide_index=True,width='stretch')


def render(entry,repo,patient,docs,preferred_version=None):
    st.subheader('EEG examinations')
    if not entry:
        st.info('No EEG candidate available.');return
    versions=EEGWorkbenchStore().versions(patient,'eeg')
    choices=[v for v,e in reversed(list(versions.items())) if not e.get('qa_hold')]
    if choices:
        if preferred_version in choices:choices=[preferred_version]+[v for v in choices if v!=preferred_version]
        revision_key=f'{patient}:eeg:revision'+(f':{preferred_version}' if preferred_version else '')
        revision=st.selectbox('EEG revision',choices,key=revision_key)
        entry=versions[revision]
    if not all(sha(f['path'])==f['sha256'] for f in entry['artifacts']):
        st.error('EEG artifact checksum changed; review is blocked.');return
    fingerprint=module_hash(entry)
    scope=ReviewRepository(repo.root/'candidates'/entry['version']) if entry['status']=='CANDIDATE' else repo
    records=scope.load(patient,'eeg');exams=layer(entry,'eeg_exams');findings=layer(entry,'eeg_findings')
    from review_app.eeg_holdout import guidance
    from review_app.config import STABLE_ROOT
    guidance(patient,entry,repo,docs,STABLE_ROOT)
    st.warning(f"EEG {entry['module_version']} · {entry['status']} · Validation preview; physician adjudication pending")
    if not exams:st.info('No performed EEGs extracted.');return
    chosen=st.selectbox('EEG examination',range(len(exams)),format_func=lambda i:f"{exams[i]['eeg_date'] or 'Date unknown'} · {exams[i]['recording_type']} · {exams[i]['eeg_primary_finding']}",key=f'{patient}:eeg:exam:{entry["version"]}')
    raw=exams[chosen];fs=[f for f in findings if f['eeg_exam_id']==raw['eeg_exam_id']]
    exam=reviewed_exam(raw,fs,records,fingerprint)
    recording_type=exam.get('recording_type')
    suffix=f' · {recording_type}' if recording_type and recording_type!='UNKNOWN' else ''
    st.write(f"EEG {exam['eeg_date'] or 'Date unknown'}{suffix}")
    st.caption(f"Date precision: {exam['eeg_date_precision']}")
    if exam.get('eeg_schema_version')=='1.2':
        st.write('Recording type: '+exam['recording_type'].replace('_',' '))
        if exam.get('NCSE') is True or exam.get('NCSE_OCCURRED_DURING_RECORDING') is True:
            st.error('STATUS: NON-CONVULSIVE STATUS EPILEPTICUS')
            st.write(f"Side: {exam['status_side']} · Region: {exam['status_region']} · Pattern: {exam['periodic_discharge_type']}")
            st.write(f"Clinical correlate: {exam['clinical_correlate']} · Treatment response: {exam['response_to_ASM']}")
        span=exam.get('recording_interval',{})
        if span.get('start') or span.get('end'):
            st.caption(f"Recording interval: {span.get('start') or 'Start undocumented'} → {span.get('end') or 'End undocumented'}")
        elif exam['recording_type'] in ('VIDEO_EEG','CONTINUOUS_EEG'):
            st.caption('Recording interval / date (source heading): '+raw['source_header'])
        for n,segment in enumerate(exam.get('CEEG_SEGMENTS',[]),1):
            with st.expander(f"SEGMENT {n} · {segment.get('segment_start') or 'Interval undocumented'} → {segment.get('segment_end') or ''}",expanded=n==1):
                st.write('Segment status: '+segment['segment_status'])
                if segment['segment_findings'].get('NCSE') is True:st.error('STATUS: NON-CONVULSIVE STATUS EPILEPTICUS')
                st.write(segment.get('segment_interpretation') or 'Interpretation not separately labelled')
                st.text(segment['EEG_BOUNDED_SOURCE_TEXT'])
                st.json(segment['segment_findings'])
                for i,e in enumerate(segment['segment_evidence']):
                    st.button('Open source',key=f'{patient}:eeg:segment:{segment["segment_id"]}:{i}',on_click=open_source,args=(e,docs,patient,segment['segment_id']))
        if exam.get('CEEG_SEGMENTS'):st.caption('Episode phenotype flags summarize findings reported at any time. Review localization, status and assessability within each segment.')
    st.write('Primary finding: '+exam['eeg_primary_finding'])
    st.write('Clinical phenotype: '+(', '.join(exam['clinical_phenotype']) or 'UNKNOWN'))
    st.write(f"Laterality: {exam['focal_slowing_side']} {exam['focal_slowing_region']}")
    def yn(value):return 'UNKNOWN' if value is None else 'YES' if value else 'NO'
    st.caption(f"Seizure recorded: {yn(exam['seizure_recorded'])} · Status: {yn(exam['electrographic_status_epilepticus'])} · Event captured: {yn(exam['eeg_clinical_event_captured'])}")
    if raw.get('source_conflict'):st.warning(raw['source_conflict'])
    if exam.get('eeg_schema_version') in ('1.1','1.2'):clinical_details(exam,fs,patient,docs,entry['version'])
    for label,fields in groups_for(exam).items():
        with st.expander(label):st.json({field:exam.get(field) for field in fields})
    with st.expander('Source-reported assessment / quality'):
        st.json({f:exam.get(f) for f in ('source_reported_abnormality_grade','assessability','artifact_limited')})
    if raw.get('eeg_schema_version')=='1.2':
        if not raw.get('CEEG_SEGMENTS'):
            with st.expander('EEG_BOUNDED_SOURCE_TEXT',expanded=True):st.text(raw['EEG_BOUNDED_SOURCE_TEXT'])
    else:
        with st.expander('Original interpretation / complete EEG report'):
            st.text(raw.get('original_interpretation_text') or 'No separately labelled interpretation')
            st.text(raw['eeg_report_text'])
    if raw.get('source_context'):
        with st.expander('Raw source context',expanded=False):
            for context in raw['source_context']:
                st.caption(f"Page {context['page']} · RAW_PDF_TEXT")
                st.text(context['RAW_PDF_TEXT'])
                with st.expander(f"Page {context['page']} · CLEANED_PAGE_TEXT"):
                    st.text(context['CLEANED_PAGE_TEXT'])
    with st.expander('Report source copies'):
        for n,e in enumerate(raw['evidence']):
            st.caption(f"{e['source_document']} · page {e['page']}")
            st.button('Open source',key=f'{patient}:eeg:report:{e["evidence_id"]}',on_click=open_source,args=(e,docs,patient,raw['eeg_exam_id']))
        st.json(raw.get('source_report_copies',[]))
    options=[raw]+fs
    def target_label(i):
        if i==0:return 'Complete examination'
        item=options[i];segment=item.get('ceeg_segment_id')
        prefix=f"Segment {segment.rsplit(':',1)[-1]} · " if segment else ''
        value=item['value'] if item['field'] not in ('focal_abnormalities','slow_patterns','reactivity_findings') else 'structured finding'
        return f"{prefix}{item['field']} · {item['assertion']} · {value}"
    selected=st.selectbox('EEG review target',range(len(options)),format_func=target_label,key=f'{patient}:eeg:target:{entry["version"]}:{raw["eeg_exam_id"]}')
    target=options[selected];eid=target.get('finding_id',target.get('eeg_exam_id'))
    state=overlay(target,records.get(eid),fingerprint)['physician_status'];st.caption('Review status: '+state)
    if state=='STALE_REVIEW':st.warning('Extraction changed since this review. Old corrections are not applied.')
    if selected:
        st.text(target['source_text_short'])
        st.button('Open source',key=f'{patient}:eeg:finding:{eid}',on_click=open_source,args=(target,docs,patient,eid))
    with st.form(f'{patient}:eeg:review:{eid}:{fingerprint[:12]}'):
        action=st.selectbox('EEG review action',['ACCEPT','EDIT','REJECT','UNCERTAIN','RESET'])
        edits=st.text_area('EEG corrections (JSON; used only for EDIT)',value=json.dumps({'value':target['value']} if selected else {},ensure_ascii=False),help='Exam: schema field/value object. Finding: {"value": ...}. Use null for unknown Boolean values.')
        save=st.form_submit_button('Save EEG review')
    if save:
        try:
            correction=validate_edits(target,json.loads(edits)) if action=='EDIT' else {}
            scope.save(patient,'eeg',eid,action,target,correction,entry['version'],fingerprint)
            st.success('EEG review saved.');st.rerun()
        except (ValueError,TypeError) as exc:st.error(str(exc))
