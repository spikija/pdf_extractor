"""EEG 1.1 assembly. Immutable 1.0 source occurrence groups preserve study identities."""
import re
from collections import defaultdict
from ..module_artifacts import digest
from .eeg_sources_v11 import discover
from .eeg_extraction_v11 import extract,clinical_summary
from .eeg_extraction_v1 import FURNITURE


def report_key(report):
    text='\n'.join(line for line in report['eeg_report_text'].splitlines() if not FURNITURE.search(line))
    text=re.sub(r'Interpretation\s*:','',text,flags=re.I)
    text=re.sub(r'[^\w]+','',text.casefold())
    return (report['patient_id'],report['eeg_date'],text) if report['eeg_date'] else (report['patient_id'],None,report['document_id'],report['report_start'])


def assemble(bundle,encounters=None,source_reconciliation=None,baseline_exams=None):
    performed,decisions=discover(bundle);groups=defaultdict(list)
    baselines={occ:e['eeg_exam_id'] for e in baseline_exams or [] for occ in e['source_occurrence_ids']}
    for report in performed:
        reviewed=(source_reconciliation or {}).get(report['report_occurrence_id'])
        groups[('BASELINE_EXAM_ID',baselines[report['report_occurrence_id']]) if report['report_occurrence_id'] in baselines else ('INDEPENDENT_SOURCE_RECONCILIATION',bundle['patient_id'],reviewed) if reviewed else report_key(report)].append(report)
    exams=[];findings=[];summaries=[];provenance=[]
    for key,reports in groups.items():
        reports.sort(key=lambda r:-len(r['eeg_report_text']))
        primary=reports[0];exam_id=key[1] if key[0]=='BASELINE_EXAM_ID' else f"{bundle['patient_id']}:EEG:{digest(key)[:18]}"
        values,source_findings=extract(primary)
        typ='VIDEO_EEG' if re.search(r'video|vEEG',primary['header'],re.I) else 'LONG_TERM_EEG' if re.search(r'Langzeit',primary['header'],re.I) else 'SLEEP_EEG' if re.search(r'Schlaf',primary['header'],re.I) else 'ROUTINE_EEG' if re.search(r'Routine',primary['header'],re.I) else 'AMBULATORY_EEG' if re.search(r'ambulant',primary['header'],re.I) else 'ICU_EEG' if re.search(r'ICU|Intensiv',primary['header'],re.I) else 'UNKNOWN'
        values['recording_type']=typ
        encounter=None
        for e in encounters or []:
            if str(e['source_document_id'])==str(primary['document_id']) and primary['eeg_date'] and len(primary['eeg_date'])==10 and e.get('admission_date') and e.get('discharge_date') and e['admission_date']<=primary['eeg_date']<=e['discharge_date']:
                encounter=f"{bundle['patient_id']}:{primary['document_id']}";break
        exam=dict(eeg_exam_id=exam_id,patient_id=bundle['patient_id'],document_id=primary['document_id'],encounter_id=encounter,
            eeg_date=primary['eeg_date'],eeg_date_precision=primary['eeg_date_precision'],
            source_page_start=primary['source_page_start'],source_page_end=primary['source_page_end'],
            section_ids=sorted({s for r in reports for s in r['section_ids']}),
            source_document_ids=sorted({r['document_id'] for r in reports}),eeg_report_text=primary['eeg_report_text'],
            source_header=primary['header'],source_occurrence_ids=[r['report_occurrence_id'] for r in reports],
            source_report_copies=[dict(document_id=r['document_id'],header=r['header'],text=r['eeg_report_text']) for r in reports],
            performed_status='EEG_PERFORMED',review_required=True,**values)
        for f in source_findings:f['eeg_exam_id']=exam_id
        evidence=[dict(patient_id=bundle['patient_id'],eeg_exam_id=exam_id,document_id=r['document_id'],
            page=r['source_page_start'],section_id=r['section_ids'][0],evidence_id=r['report_occurrence_id'],
            source_text_short=(r['header']+'\n'+r['eeg_report_text'])[:450],source_type='EXPLICIT_SOURCE',
            source_start=r['header_start'],report_start=r['report_start'],report_end=r['report_end'],
            eeg_date=r['eeg_date'],source_document=r['source_document'],report_occurrence_id=r['report_occurrence_id']) for r in reports]
        exam['evidence_ids']=[e['evidence_id'] for e in evidence]+[f['evidence_id'] for f in source_findings]
        exam['evidence']=evidence
        summary=clinical_summary(exam);exam['eeg_primary_finding']=summary['eeg_primary_finding'];exam['clinical_phenotype']=summary['clinical_phenotype']
        exams.append(exam);findings+=source_findings;summaries.append(summary);provenance+=evidence
    exams.sort(key=lambda e:(e['eeg_date'] or '9999',e['eeg_exam_id']))
    for exam in exams:
        if sum(e['eeg_date']==exam['eeg_date'] for e in exams)>1:
            exam['source_conflict']='Different report texts share this date. Retained separately; study identity/date requires physician confirmation.'
    return dict(eeg_exams=exams,eeg_findings=findings,eeg_clinical_summary=summaries,eeg_provenance=provenance,
        eeg_discovery_decisions=[{k:v for k,v in r.items() if k!='ranges'} for r in decisions])
