"""Prepare PRIVATE MRI -> Retool case.fallnr candidate review. NO database writes.

Uses exact DOB to produce *candidates*, never a confirmed identity match.
Run only on a protected clinical workstation; output contains direct identifiers.
"""
import argparse
import csv
import re
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime

DEFAULT_MRI = Path(r'C:\app\pdf_extractor\caa_private_linkage\mri_identity_series_PRIVATE.csv')
DEFAULT_CASE = Path(r'C:\app\pdf_extractor\data\old_retool_db\case.csv')
DEFAULT_OUT = Path(r'C:\app\pdf_extractor\caa_private_linkage')

def csv_rows(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def birth(value):
    s = str(value or '').strip()
    if not s or s.lower() in ('null','none','nan','nat'): return ''
    if re.fullmatch(r'\d{8}', s):
        for fmt in ('%Y%m%d','%d%m%Y'):
            try: return datetime.strptime(s, fmt).strftime('%Y%m%d')
            except ValueError: pass
        return ''
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', s):
        s = s[:10]
        try: return datetime.strptime(s,'%Y-%m-%d').strftime('%Y%m%d')
        except ValueError: return ''
    for fmt in ('%d.%m.%Y','%d/%m/%Y','%m/%d/%Y'):
        try: return datetime.strptime(s,fmt).strftime('%Y%m%d')
        except ValueError: pass
    return ''

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mri', type=Path, default=DEFAULT_MRI)
    ap.add_argument('--cases',type=Path,default=DEFAULT_CASE)
    ap.add_argument('--private-dir',type=Path,default=DEFAULT_OUT)
    args=ap.parse_args()
    for p in (args.mri,args.cases):
        if not p.is_file(): ap.error(f'Missing input: {p}')
    if not args.private_dir.is_dir(): ap.error('Existing protected --private-dir required')
    mri=csv_rows(args.mri); cases=csv_rows(args.cases)
    if not mri: ap.error('MRI inventory is empty; rerun identity inventory without --max-groups')
    if not cases: ap.error('Case CSV is empty')
    for key in ('dicom_patient_id','patient_name_dicom','birth_date_yyyymmdd','study_uid','study_date_yyyymmdd','nifti_category'):
        if key not in mri[0]: ap.error(f'Missing MRI column {key}')
    for key in ('fallnr','date_of_birth'):
        if key not in cases[0]: ap.error(f'Missing case column {key}')
    by_dob=defaultdict(set); case_by_fallnr={}; invalid_case=0
    for row in cases:
        fallnr=str(row['fallnr'] or '').strip()
        if not fallnr: continue
        if fallnr in case_by_fallnr:
            ap.error('Duplicate fallnr in case CSV; resolve before linking')
        case_by_fallnr[fallnr]=row
        bd=birth(row['date_of_birth'])
        if bd: by_dob[bd].add(fallnr)
        else: invalid_case+=1
    identities={}
    for row in mri:
        # Multiple series/studies should collapse to one candidate per DICOM identity.
        pid=str(row['dicom_patient_id'] or '').strip()
        name=str(row['patient_name_dicom'] or '').strip()
        dob=birth(row['birth_date_yyyymmdd'])
        identity=(pid,name,dob)
        entry=identities.setdefault(identity,{'studies':set(),'converted':set(),'groups':set(),'dates':set()})
        entry['studies'].add(row['study_uid'])
        if row['study_date_yyyymmdd']: entry['dates'].add(row['study_date_yyyymmdd'])
        if row.get('group_id'): entry['groups'].add(row['group_id'])
        if row['nifti_category']!='NOT_CONVERTED': entry['converted'].add(row['nifti_category'])
    report=args.private_dir/'mri_fallnr_candidates_PRIVATE.csv'
    fields=['dicom_patient_id','patient_name_dicom','birth_date_yyyymmdd',
            'mri_study_count','mri_group_count','mri_first_date','mri_last_date',
            'converted_categories','matching_dob_case_count','candidate_fallnr',
            'review_status','verified_by','verified_date','review_note']
    counts=Counter()
    with report.open('w',newline='',encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f,fieldnames=fields); wr.writeheader()
        for (pid,name,dob),v in sorted(identities.items(),key=lambda x:(x[0][2],x[0][0],x[0][1])):
            matched=sorted(by_dob.get(dob,())) if dob else []
            status=('MISSING_DOB' if not dob else 'NO_DOB_CANDIDATE' if not matched
                    else 'DOB_ONLY_ONE_CANDIDATE_NEEDS_IDENTITY_VERIFICATION' if len(matched)==1
                    else 'MULTIPLE_DOB_CANDIDATES_NEEDS_REVIEW')
            counts[status]+=1
            base=dict(dicom_patient_id=pid,patient_name_dicom=name,birth_date_yyyymmdd=dob,
                mri_study_count=len(v['studies']),mri_group_count=len(v['groups']),
                mri_first_date=min(v['dates']) if v['dates'] else '',
                mri_last_date=max(v['dates']) if v['dates'] else '',
                converted_categories=';'.join(sorted(v['converted'])),
                matching_dob_case_count=len(matched),review_status=status,
                verified_by='',verified_date='',review_note='')
            if not matched: wr.writerow(dict(base,candidate_fallnr=''))
            else:
                for fallnr in matched: wr.writerow(dict(base,candidate_fallnr=fallnr))
    print('MRI distinct identity tuples:',len(identities))
    print('Retool case rows:',len(cases),'| unique fallnr:',len(case_by_fallnr))
    print('Cases with unparseable DOB:',invalid_case)
    for key,val in sorted(counts.items()): print(f'{key}: {val}')
    print('Private candidate review file created (do NOT share):',report)
    print('IMPORTANT: DOB match is NOT proof of identity. No patient-to-fallnr linkage created.')
    print('No PostgreSQL or PDF files modified.')

if __name__=='__main__': main()
