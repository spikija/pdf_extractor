"""Native-source verification of exam reuse/identity aliases; no EEG extraction."""
import hashlib
import re
from pathlib import Path
import pymupdf
from .workbench_store import ROOT,read,atomic
from .eeg_holdout_inventory import HEAD


def verify(directory):
    directory=Path(directory);cohort=read(directory/'frozen_cohort.json');identities={};reports={};coverage={}
    for group,ids in [('development',cohort['excluded_patient_ids']),('holdout',cohort['patient_ids'])]:
        identities[group]={};reports[group]={};coverage[group]={}
        for patient in ids:
            if group=='holdout':
                docs=read(directory/'source_inputs'/patient/'source_bundle.json')['documents']
                texts=[(d['filename'],'\n'.join(s['text'] for s in d['sections'])) for d in docs]
            else:
                texts=[]
                for path in sorted((ROOT/'pdf_db'/patient).glob('*.pdf')):
                    with pymupdf.open(path) as pdf:texts.append((path.name,'\n'.join(page.get_text('text',sort=True) for page in pdf)))
            own=set()
            for filename,text in texts:
                for m in re.finditer(r'(?m)^[ \t]*([^\n,]{4,75}?),\s*geb\.?\s*:\s*(\d{2}\.\d{2}\.\d{4})',text,re.I):
                    key=hashlib.sha256((''.join(m[1].casefold().split())+'|'+m[2]).encode()).hexdigest()
                    identities[group].setdefault(key,set()).add(patient);own.add(key)
                heads=list(HEAD.finditer(text))
                for i,m in enumerate(heads):
                    body=text[m.end():heads[i+1].start() if i+1<len(heads) else min(len(text),m.end()+1000)]
                    norm=re.sub(r'\W','',body.casefold())[:400]
                    dates=re.findall(r'\d{1,2}\.\s*\d{1,2}\.\s*(?:19|20)\d{2}',m[0])
                    if len(norm)>=350 and dates:
                        key=hashlib.sha256((re.sub(r'\s','',dates[0])+'|'+norm).encode()).hexdigest()
                        reports[group].setdefault(key,[]).append(dict(patient_id=patient,filename=filename))
            coverage[group][patient]=len(own)
    common=identities['development'].keys()&identities['holdout'].keys();same=reports['development'].keys()&reports['holdout'].keys()
    value=dict(method='Native PDF name/DOB linkage hashes and long report-prefix/date fingerprints; no extraction invoked',
        identity_fingerprint_coverage=coverage,shared_name_DOB_fingerprints=len(common),shared_long_report_and_date_fingerprints=len(same),
        possible_exam_overlap=[dict(development=reports['development'][k],holdout=reports['holdout'][k]) for k in same],
        exact_development_PDF_reuse=0,verification_after_run_did_not_change_selection=True)
    atomic(directory/'development_exam_overlap_validation.json',value)
    if common or same or any(not n for patients in coverage.values() for n in patients.values()):raise ValueError('Source identity overlap or incomplete verification; physician review required')
    return value


if __name__=='__main__':
    import sys
    print(verify(Path(sys.argv[1])))
