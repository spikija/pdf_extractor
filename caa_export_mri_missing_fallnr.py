"""Export privacy-minimized fallnr lists for retrieving missing MRI examinations.

NO database writes. Does not establish identity from DOB alone.
Sources: local original Retool case.csv, complete MRI identity inventory.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from caa_prepare_mri_case_linkage_v1 import birth

DEFAULT_CASE = Path(r'C:\app\pdf_extractor\data\old_retool_db\case.csv')
DEFAULT_MRI = Path(r'C:\app\pdf_extractor\caa_private_linkage\mri_identity_series_PRIVATE.csv')
DEFAULT_DIR = Path(r'C:\app\pdf_extractor\caa_private_linkage')

def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, rows, fieldnames):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fieldnames)
        writer.writeheader()
        for row in rows: writer.writerow(row)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',type=Path,default=DEFAULT_CASE)
    p.add_argument('--mri',type=Path,default=DEFAULT_MRI)
    p.add_argument('--private-dir',type=Path,default=DEFAULT_DIR)
    args=p.parse_args()
    for path in (args.cases,args.mri):
        if not path.is_file(): p.error(f'Input not found: {path}')
    if not args.private_dir.is_dir(): p.error('Private output directory must already exist')
    cases=read_csv(args.cases)
    mri=read_csv(args.mri)
    if not cases or not mri: p.error('One input file is empty')
    if not {'fallnr','date_of_birth'}.issubset(cases[0]): p.error('Missing fallnr/date_of_birth in case.csv')
    if not {'dicom_patient_id','patient_name_dicom','birth_date_yyyymmdd'}.issubset(mri[0]): p.error('Missing MRI identity columns')
    case_by_id={}
    for r in cases:
        key=(r.get('fallnr') or '').strip()
        if not key: p.error('CSV has blank fallnr')
        if key in case_by_id: p.error('CSV contains duplicate fallnr')
        dob=birth(r.get('date_of_birth'))
        if not dob: p.error(f'Unparseable case DOB for fallnr: {key}')
        case_by_id[key]=dob
    # Identity count is intentionally deduplicated across MRI series and studies.
    mri_identities=set()
    for r in mri:
        mri_identities.add(((r.get('dicom_patient_id') or '').strip(),
                            (r.get('patient_name_dicom') or '').strip(),
                            birth(r.get('birth_date_yyyymmdd'))))
    mri_dob_counts=Counter(dob for _id,_name,dob in mri_identities if dob)
    if len(mri_identities) < 10:
        p.error('Only a few MRI identities found; full inventory may not have been generated')
    no_dob_match=[]; candidate=[]
    for fallnr,dob in sorted(case_by_id.items()):
        count=mri_dob_counts.get(dob,0)
        if not count:
            no_dob_match.append({'fallnr':fallnr})
        else:
            candidate.append({'fallnr':fallnr,'mri_identity_count_with_same_dob':count,
                              'status':'DOB_MATCH_UNVERIFIED'})
    a=args.private_dir/'fallnr_no_matching_MRI_DOB_RETRIEVAL_LIST.csv'
    b=args.private_dir/'fallnr_MRI_DOB_candidates_VERIFY_FIRST.csv'
    write_csv(a,no_dob_match,['fallnr'])
    write_csv(b,candidate,['fallnr','mri_identity_count_with_same_dob','status'])
    print('Retool cases:',len(case_by_id))
    print('Distinct MRI identity tuples:',len(mri_identities))
    print('Fallnr WITHOUT MRI DOB match (MRI retrieval list):',len(no_dob_match))
    print('Fallnr WITH MRI DOB candidate (identity NOT verified):',len(candidate))
    print('Retrieval list (fallnr only):',a)
    print('Separate review list:',b)
    print('IMPORTANT: No MRI DOB match means none was found in the LOCAL MRI inventory;')
    print('it does not prove an MRI has never been performed. DOB matches are not verified identities.')
    print('No PostgreSQL data or original files modified.')

if __name__=='__main__': main()
