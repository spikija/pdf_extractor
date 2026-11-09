"""Independent source inventory for the frozen holdout; never imports the EEG parser.

The source study unit is one recording, not each interval summary or copied letter.
Date/identity reconciliation below was specified from native PDF pages before extraction.
"""
import re
from datetime import date,datetime,timezone
from pathlib import Path
from .workbench_store import read,atomic,sha
from .eeg_holdout_design import csv_file,verify_freeze

HEAD=re.compile(r'(?im)^[ \t]*(?:Elektroen\w*[^\n]{0,100}Befund[^\n]*|EEG\s+(?:Befund\s*)?\([^\n]*|cEEG\s*-\s*kontinuierliches[^\n]*|MOBILE\s+Video-EEG-Ableitung[^\n]*)')
DATE=re.compile(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4}|\d{2})(?!\d)')


def inventory(directory):
    directory=Path(directory).resolve();verify_freeze(directory)
    if (directory/'source_inventory_freeze.json').exists() or (directory/'workers').exists():raise ValueError('Inventory is already frozen or extraction started')
    studies={};occurrences=[];context=[]
    for patient in read(directory/'selected_patients.json'):
        bundle=read(directory/'source_inputs'/patient/'source_bundle.json')
        for doc in bundle['documents']:
            for section in doc['sections']:
                text=section['text']
                for match in HEAD.finditer(text):
                    heading=match[0].strip();dt=DATE.search(heading)
                    if not dt:continue
                    day,month,year=map(int,dt.groups());year=year+2000 if year<30 else year+1900 if year<100 else year
                    value=date(year,month,day).isoformat()
                    form='STRUCTURED_CONTINUOUS_VIDEO_EEG' if heading.startswith('MOBILE') else 'CONTINUOUS_EEG_REPORT' if heading.startswith('cEEG') else 'FREE_TEXT_DATED_REPORT'
                    modality='VIDEO' if heading.startswith('MOBILE') else 'CONTINUOUS' if heading.startswith('cEEG') else 'ROUTINE'
                    key=(patient,value,modality)
                    row=dict(patient_id=patient,document_id=doc['source_document_id'],page=section['page_start'],EEG_date=value,EEG_date_precision='DAY',performed_YN='Y',source_heading=heading,source_format=form,
                        notes='Actual report in native PDF; repeated reports of same recording are source copies, not additional exams.')
                    occurrence=dict(**row,filename=doc['filename'],page_offset=match.start(),study_key=list(key),source_excerpt=text[match.end():match.end()+1100])
                    occurrences.append(occurrence)
                    studies.setdefault(key,{**row,'study_key':list(key),'source_occurrences':[]})['source_occurrences'].append(occurrence)
                # Retain all source mentions independently of whether a report header was detected.
                for match in re.finditer(r'EEG|Elektroen\w*|Epilepsiemonitor\w*',text,re.I):
                    context.append(dict(patient_id=patient,document_id=doc['source_document_id'],filename=doc['filename'],page=section['page_start'],text=text[max(0,match.start()-150):match.end()+400]))
        # Performed examinations documented only in the encounter narrative. Dates
        # remain UNKNOWN: a document/admission date is not an EEG examination date.
        narrative={'2730816321':('1.pdf',5,'Im EEG zeigt sich ein unveränderter Befund','Current-encounter performed EEG in 31 March 2021 emergency letter; exam date not explicitly stated. Distinct encounter from February/March inpatient EEGs.'),
                   '2537649217':('1.pdf',2,'Im  interiktalen EEG','Performed intensive video-EEG monitoring during 28 January-1 February 2019 admission. Exact recording date not stated; one monitoring episode, not one exam per day.')}
        if patient in narrative:
            filename,page,heading,note=narrative[patient]
            doc=next(d for d in bundle['documents'] if d['filename']==filename)
            text=next(s['text'] for s in doc['sections'] if s['page_start']==page)
            if heading not in text:raise ValueError('Narrative evidence changed')
            key=(patient,None,'NARRATIVE_MONITORING' if patient=='2537649217' else 'NARRATIVE_ROUTINE')
            row=dict(patient_id=patient,document_id=doc['source_document_id'],page=page,EEG_date=None,EEG_date_precision='UNKNOWN',performed_YN='Y',source_heading=heading,source_format='PERFORMED_NARRATIVE',notes=note)
            occurrence=dict(**row,filename=filename,page_offset=text.index(heading),study_key=list(key),source_excerpt=text[text.index(heading):text.index(heading)+1000])
            occurrences.append(occurrence);studies[key]={**row,'study_key':list(key),'source_occurrences':[occurrence]}
    # Decisions based exclusively on the PDF source, frozen before worker execution.
    decisions=[
        '2687726420: 28.01.20 source headings in 4.pdf/7.pdf and 8.pdf narrative describe the same January 2020 recording; count once. Four 2019 recordings are copied across 4.pdf, 7.pdf and 10.pdf.',
        '2957537425: MOBILE Video-EEG 19-20 May 2025 is one continuous recording with two interval summaries. A separate routine EEG on 19 May is a distinct exam. 3.pdf contains abbreviated copies of the video recording and routine 19/22 May reports.',
        '2944202925: acute ZANE EEG without status is corroborating narrative for the admission-day 11 April recording; no positive evidence of a separate second same-day recording. Physician should confirm this linkage.',
        '2730816321: emergency encounter narrative is a performed EEG despite no formal dated EEG heading; do not discard it.',
        '2537649217: completed video monitoring narrative is a performed EEG despite no formal dated EEG heading.',
        'Appointments, recommended EEGs, interictal historical diagnosis-list references and summaries of inventoried recordings are not additional performed exams.',
        'Screening found no STRUCTURED_FORM_SIGNAL in the remaining corpus. Selected 2957537425 nevertheless contains a structured continuous/video EEG report. Older 2015 free-text report format retained; no older checkbox form was demonstrated.'
    ]
    rows=list(studies.values());rows.sort(key=lambda r:(r['patient_id'],r['EEG_date'] or '9999',r['source_format']))
    for n,row in enumerate(rows,1):row['inventory_id']=f'SOURCE_EEG_{n:03}'
    fields='patient_id document_id page EEG_date EEG_date_precision performed_YN source_heading source_format notes'.split()
    csv_file(directory/'eeg_holdout_source_inventory.csv',[{k:r[k] for k in fields} for r in rows],fields)
    atomic(directory/'source_inventory_studies.json',rows);atomic(directory/'source_occurrences.json',occurrences)
    atomic(directory/'source_mention_contexts.json',context);atomic(directory/'source_identity_decisions.json',decisions)
    paths=['eeg_holdout_source_inventory.csv','source_inventory_studies.json','source_occurrences.json','source_identity_decisions.json']
    atomic(directory/'source_inventory_freeze.json',dict(frozen_at=datetime.now(timezone.utc).isoformat(),before_extraction=True,source_exam_count=len(rows),
        method='Independent native PDF headings, full mention-context review and source-only study/copy reconciliation; no EEG extraction output inspected',files={str(directory/name):sha(directory/name) for name in paths}))
    return rows


if __name__=='__main__':
    import sys
    print('INDEPENDENT_SOURCE_EXAMS',len(inventory(Path(sys.argv[1]))))
