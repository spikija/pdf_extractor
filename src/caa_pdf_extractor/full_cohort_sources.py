"""Freeze and prepare the dynamically discovered local PDF cohort; no SQL."""
import argparse
from collections import Counter
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pymupdf
from .utils import parse_pdf
from .preparation.patient_bundle import prepare_document
from .preparation.models import PatientDocumentBundle
from .diagnosis_caa_v21_review import read,write,sha,protect,csvfile

BASELINE=Path('data/debug/caa_panorama_frozen_20261021T220651+0200/snapshot.json')


def discover(pdfroot):
    rows=[];documents=[]
    for folder in sorted(p for p in pdfroot.iterdir() if p.is_dir()):
        files=sorted(f for f in folder.rglob('*') if f.is_file() and f.suffix.lower()=='.pdf')
        pages=0;errors=[]
        for file in files:
            try:
                with pymupdf.open(file) as pdf:count=len(pdf)
                documents.append(dict(patient_id=folder.name,path=str(file.resolve()),relative_path=file.relative_to(pdfroot).as_posix(),
                    filename=file.name,sha256=sha(file),pages=count))
                pages+=count
            except Exception as exc:errors.append(file.name+':'+type(exc).__name__)
        rows.append(dict(patient_id=folder.name,number_of_pdfs=len(files),pdf_filenames=[f.relative_to(folder).as_posix() for f in files],
            total_pages=pages,processing_status='DISCOVERED' if files and not errors else 'SOURCE_ERROR',error_status=errors or (['NO_PDFS'] if not files else [])))
    return rows,documents


def prepare(root,pdfroot):
    root.mkdir(exist_ok=True);protect(root)
    manifest,documents=discover(pdfroot)
    frozen=root/'source_manifest.json'
    if frozen.exists() and read(frozen)!=documents:raise ValueError('Cohort source set or bytes changed since freeze')
    write(frozen,documents);csvfile(root,'cohort_manifest',manifest)
    old=read(BASELINE);old_by_key={(d['document_json']['document']['patient_id'],d['document_json']['document']['relative_path'],d['sha256']):d for d in old['tables']['source_documents']}
    parsed=root/'parsed';parsed.mkdir(exist_ok=True)
    bundles=[];source_docs=[]
    for row in manifest:
        pid=row['patient_id'];prepared=[]
        for item in (d for d in documents if d['patient_id']==pid):
            index=documents.index(item);prior=old_by_key.get((pid,item['relative_path'],item['sha256']))
            docid=prior['id'] if prior else 100001+index
            cache=parsed/(str(docid)+'.json')
            if cache.exists():payload=read(cache)
            elif prior:payload=prior['document_json'];write(cache,payload)
            else:
                payload=parse_pdf(Path(item['path']),item['relative_path'],pid);write(cache,payload)
            if payload['document']['sha256']!=item['sha256']:raise ValueError('Parsed/source hash mismatch')
            doc=prepare_document(pid,docid,item['filename'],item['sha256'],payload);prepared.append(doc)
            source_docs.append(dict(id=docid,sha256=item['sha256'],filename=item['filename'],document_json=payload))
        bundles.append(PatientDocumentBundle(pid,prepared).to_dict())
        print(f'PREPARED {pid} documents={len(prepared)} pages={row["total_pages"]}',flush=True)
    write(root/'bundles.json',bundles)
    write(root/'sources.json',dict(patient_ids=[r['patient_id'] for r in manifest],tables=dict(source_documents=source_docs)))
    write(root/'cohort_freeze.json',dict(patients=len(manifest),pdfs=len(documents),pages=sum(d['pages'] for d in documents),
        source_manifest_sha256=sha(frozen),source_database_connections=0,clinical_writes=0))
    return manifest,documents


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);p.add_argument('--pdf-root',type=Path,default=Path('pdf_db'));a=p.parse_args()
    root=a.output or Path('data/debug')/('full_cohort_'+datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S%z'))
    Path('data/debug/full_cohort_latest.txt').write_text(str(root),encoding='utf-8')
    prepare(root,a.pdf_root)
