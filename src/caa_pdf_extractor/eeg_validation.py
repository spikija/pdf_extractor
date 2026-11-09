"""Reproducible bounded EEG validation utilities; publishing is an explicit separate step."""
import csv
import io
import re
from pathlib import Path
import pymupdf
from .workbench_store import read,atomic
from .extraction.eeg_sources import discover
from .extraction.eeg_pipeline import assemble

# Source-reviewed study dates, transcribed separately from report assembly.
# Two incompatible 2013-11-22 report versions are deliberately separate source units.
SOURCE_DATES={
'2921697224':'2001 2002-01-29 2002-01-31 2004-01-26 2013-11-22 2013-11-22 2013-11-25 2013-11-26 2016-05-31 2016-08-22 2016-08-23 2019-09-10 2019-10-17 2020-12-14 2020-12-18 2021-01-08 2021-03-03 2021-04-19 2021-05-17 2021-08-02 2024-09-29 2024-09-30',
'2661437519':'2019-07-26 2021-02-01 2021-02-08 2021-02-10',
'2984303525':'2018-04-20 2025-10-27 2025-10-29',
'2964796925':'2022-03-03 2025-07-07',
'2878624323':'2023-12-07 2023-12-11',
'2788388022':'2021-12-13 2022-04-01 2022-05-19',
'2976764725':'2025-09-17 2025-09-19 2025-09-23',
'2897716624':'2024-06-04 2024-11-14 2026-03-11',
'2739464121':'2021-04-07 2021-04-08 2021-04-20',
'2693909420':'2020-03-10 2020-07-24 2020-12-29',
'2648066819':'2015-08-05 2015-08-31 2016-08-01',
'2529565017':'2017-01-17 2017-02-01 2017-02-02',
'2520723716':'2012-01-16 2013-05-06 2016-11-11 2017-02-03',
'2345733313':'2013-08-09 2023-02-17 2023-03-02',
'2956023325':'2024-01-08 2025-05-08 2025-05-12',
'2945890325':'2023-02-03 2025-03-20',
'2920768024':'2023-01-20 2023-09-25 2024-09-23',
'2918603524':'2024-09-09 2026-03-26',
'2831142723':'2023-01-20 2023-09-25 2024-09-23',
'2674159019':'2014-05-28 2019-12-04',
}


def csv_write(path,rows):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
    writer.writeheader();writer.writerows(rows);Path(path).write_text(stream.getvalue(),encoding='utf-8-sig')


def prepare(directory,pdf_root):
    """Direct PDF inspection inventory is independent of source snapshot/report discovery."""
    directory=Path(directory);rows=[];excerpts=[]
    for patient in read(directory/'selected_patients.json'):
        for path in sorted((Path(pdf_root)/patient).glob('*.pdf')):
            with pymupdf.open(path) as pdf:
                for page_number,page in enumerate(pdf,1):
                    lines=page.get_text('text',sort=True).splitlines()
                    for i,line in enumerate(lines):
                        if re.search(r'\bEEG\b|Elektroen[cz]ephalo|Epilepsiemonitoring',line,re.I):
                            rows.append(dict(patient_id=patient,document=path.name,page=page_number,line=i+1,source_line=line.strip(),following_text=' '.join(lines[i+1:i+5])[:700]))
        # Only independently reviewed same-study copies are reconciled. No date-only
        # merging is applied to new patients or conflicting historical interpretations.
    csv_write(directory/'independent_pdf_eeg_mentions.csv',rows)
    reconciliation={}
    for patient,date in [('2921697224','2021-05-17'),('2945890325','2025-03-20')]:
        performed,_=discover(read(directory/'source_inputs'/patient/'source_bundle.json'))
        for r in performed:
            if r['eeg_date']==date:reconciliation[r['report_occurrence_id']]=patient+':'+date+':SOURCE_REVIEWED_SAME_STUDY'
    atomic(directory/'source_reconciliation.json',reconciliation)
    atomic(directory/'independent_expected_dates.json',{p:dates.split() for p,dates in SOURCE_DATES.items()})
    return rows


def preflight(directory):
    from collections import Counter
    directory=Path(directory);outputs={};inventory=[]
    semantics=read(directory/'semantic_results.json');reconciliation=read(directory/'source_reconciliation.json')
    for patient in read(directory/'selected_patients.json'):
        source=directory/'source_inputs'/patient
        r=assemble(read(source/'source_bundle.json'),read(source/'encounters.json'),semantics,reconciliation)
        expected=Counter(SOURCE_DATES[patient].split());actual=Counter(e['eeg_date'] for e in r['eeg_exams'])
        if expected!=actual:raise ValueError(f'Independent study inventory mismatch {patient}: expected={expected},actual={actual}')
        for e in r['eeg_exams']:
            inventory.append(dict(patient_id=patient,source_study_date=e['eeg_date'],source_precision=e['eeg_date_precision'],source_documents=','.join(str(i) for i in e['source_document_ids']),source_occurrences=len(e['source_occurrence_ids']),eeg_exam_id=e['eeg_exam_id'],extracted='YES',note=e.get('source_conflict','Copied reports reconciled only where independently verified')))
        outputs[patient]=r
    atomic(directory/'preflight_outputs.json',outputs);csv_write(directory/'eeg_source_exam_inventory.csv',inventory)
    return outputs
