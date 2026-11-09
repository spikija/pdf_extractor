"""One recording identity, independently interpreted cEEG segments, immutable sources."""
import copy
import re
from difflib import SequenceMatcher
from collections import defaultdict

from .eeg_sources_v12 import discover
from .eeg_extraction_v12 import extract,clinical_summary
from .eeg_schema_v12 import BOOL_FIELDS,defaults
from ..module_artifacts import digest


def normalized(text):return re.sub(r'\W+','',text.casefold())


def same_recording(a,b):
    if not a['eeg_date'] or a['eeg_date']!=b['eeg_date'] or a['recording_type']!=b['recording_type']:return False
    left=normalized(a['eeg_report_text']);right=normalized(b['eeg_report_text'])
    if a['segments'] or b['segments']:
        # A dated abbreviated video summary can corroborate its full session.
        return a['recording_type']=='VIDEO_EEG' and (not left or not right or SequenceMatcher(None,left,right).ratio()>.85)
    if min(len(left),len(right))<40:return left==right
    shorter,longer=sorted((left,right),key=len)
    return shorter in longer or SequenceMatcher(None,left,right,autojunk=False).ratio()>=.88


def evidence(report,exam_id):
    return dict(patient_id=report['patient_id'],eeg_exam_id=exam_id,document_id=report['document_id'],page=report['source_page_start'],
        section_id=report['section_ids'][0],evidence_id=report['report_occurrence_id'],source_text_short=(report['header']+'\n'+report['eeg_report_text'])[:450],
        source_type='EXPLICIT_SOURCE',source_layer='EEG_BOUNDED_SOURCE_TEXT',source_document=report['source_document'],source_start=report['header_start'],
        report_start=report['report_start'],report_end=report['report_end'],eeg_date=report['eeg_date'],report_occurrence_id=report['report_occurrence_id'])


def assemble(bundle,encounters=None):
    reports,decisions=discover(bundle);groups=[]
    for report in reports:
        same=next((group for group in groups if any(same_recording(r,report) for r in group)),None)
        if same is None:groups.append([report])
        else:same.append(report)
    exams=[];findings=[];summaries=[];provenance=[]
    for group in groups:
        group.sort(key=lambda r:(-len(r['segments']),-len(r['eeg_report_text'])))
        primary=group[0];exam_id=f'{bundle["patient_id"]}:EEG12:{digest([primary["report_occurrence_id"]])[:18]}'
        source_reports=list({r['report_occurrence_id']:r for parent in group for r in [parent]+parent['segments']}.values())
        ev=[evidence(r,exam_id) for r in source_reports];segments=[];own_findings=[]
        if primary['segments']:
            segment_values=[]
            for i,report in enumerate(primary['segments'],1):
                values,fs=extract(report)
                values.update({k:primary[k] for k in ('recording_type','continuous','video','mobile','ICU_recording')})
                segment_values.append(values)
                for f in fs:f['ceeg_segment_id']=f'{exam_id}:SEGMENT:{i}'
                own_findings+=fs
                segments.append(dict(segment_id=f'{exam_id}:SEGMENT:{i}',segment_start=report['segment_start'],segment_end=report['segment_end'],
                    segment_findings=values,segment_interpretation=values['original_interpretation_text'],
                    segment_status='PRESENT' if values['NCSE'] is True else 'ABSENT' if values['electrographic_status_epilepticus'] is False else 'UNKNOWN',
                    segment_evidence=[evidence(report,exam_id)],EEG_BOUNDED_SOURCE_TEXT=report['eeg_report_text'],source_heading=report['header']))
            values=copy.deepcopy(segment_values[0])
            # Summary describes occurrence at any time; final status stays separate.
            for field in BOOL_FIELDS:
                observations=[s.get(field) for s in segment_values]
                values[field]=True if True in observations else False if False in observations and all(v is False for v in observations) else None
            for field in ('focal_abnormalities','slow_patterns','rhythmic_patterns','reactivity_findings','assessability_periods'):
                values[field]=[copy.deepcopy(row) for s in segment_values for row in s.get(field,[])]
            values['NCSE_OCCURRED_DURING_RECORDING']=True if any(s['NCSE'] is True for s in segment_values) else None
            values['status_at_final_segment']=segments[-1]['segment_status']
            values['assessability']='LIMITED' if any(s['assessability'] in ('LIMITED','NOT_ASSESSABLE') or s['assessability_periods'] for s in segment_values) else values['assessability']
            positive=next((s for s in segment_values if s['NCSE'] is True),None)
            if positive:
                for field in ('NCSE_assertion','status_type','status_side','status_region','clinical_correlate','response_to_ASM'):values[field]=positive[field]
            bounded='\n\n'.join(r['header']+'\n'+r['eeg_report_text'] for r in primary['segments'])
        else:
            values,own_findings=extract(primary);bounded=primary['eeg_report_text']
        for f in own_findings:f['eeg_exam_id']=exam_id
        values.update({k:primary[k] for k in ('recording_type','continuous','video','mobile','ICU_recording')})
        # For ordinary continuous reports without timed subreports retain one
        # explicit segment rather than lose the continuous structure.
        if primary['continuous'] and not segments:
            for f in own_findings:f['ceeg_segment_id']=exam_id+':SEGMENT:1'
            segments=[dict(segment_id=exam_id+':SEGMENT:1',segment_start=primary['segment_start'],segment_end=primary['segment_end'],
                segment_findings=copy.deepcopy(values),segment_interpretation=values['original_interpretation_text'],
                segment_status=values['status_at_final_segment'],segment_evidence=[ev[0]],EEG_BOUNDED_SOURCE_TEXT=bounded,source_heading=primary['header'])]
        exam=dict(**values,eeg_exam_id=exam_id,patient_id=bundle['patient_id'],document_id=primary['document_id'],encounter_id=None,
            eeg_date=primary['eeg_date'],eeg_date_precision=primary['eeg_date_precision'],source_page_start=primary['source_page_start'],source_page_end=primary['source_page_end'],
            section_ids=sorted({s for r in source_reports for s in r['section_ids']}),source_document_ids=sorted({r['document_id'] for r in source_reports}),
            source_occurrence_ids=[r['report_occurrence_id'] for r in source_reports],source_header=primary['header'],
            source_report_copies=[dict(document_id=r['document_id'],header=r['header'],text=r['eeg_report_text']) for r in source_reports],
            eeg_report_text=bounded,EEG_BOUNDED_SOURCE_TEXT=bounded,source_layer='EEG_BOUNDED_SOURCE_TEXT',CEEG_SEGMENTS=segments,
            performed_status='EEG_PERFORMED',review_required=True,evidence=ev,evidence_ids=[e['evidence_id'] for e in ev]+[f['evidence_id'] for f in own_findings])
        # Segment intervals are deliberately not an exam recording-end field.
        if segments:exam['recording_interval']=dict(start=segments[0]['segment_start'],end=segments[-1]['segment_end'])
        summary=clinical_summary(exam)
        if segments:
            phenotype=set(summary['clinical_phenotype'])
            for segment in segments:phenotype.update(clinical_summary(dict(segment['segment_findings'],eeg_exam_id=exam_id,patient_id=bundle['patient_id']))['clinical_phenotype'])
            summary['clinical_phenotype']=sorted(phenotype)
        exam.update(eeg_primary_finding=summary['eeg_primary_finding'],clinical_phenotype=summary['clinical_phenotype'])
        # All three source layers remain available; raw text is never rewritten.
        doc=next(d for d in bundle['documents'] if d['source_document_id']==primary['document_id'])
        context=[s for s in doc['sections'] if s['section_id'] in exam['section_ids']]
        exam['source_context']=[dict(page=s['page_start'],RAW_PDF_TEXT=s.get('raw_pdf_text',s['text']),CLEANED_PAGE_TEXT=s['text']) for s in context]
        exams.append(exam);findings+=own_findings;summaries.append(summary);provenance+=ev
    exams.sort(key=lambda e:(e['eeg_date'] or '9999',e['eeg_exam_id']))
    return dict(eeg_exams=exams,eeg_findings=findings,eeg_clinical_summary=summaries,eeg_provenance=provenance,
        eeg_discovery_decisions=[{k:v for k,v in r.items() if k not in ('ranges','segments')} for r in decisions])
