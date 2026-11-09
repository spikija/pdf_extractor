"""Document-local, auditable margin suppression over native PDF text blocks."""
import copy
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

from ..workbench_store import ROOT, atomic
from .eeg_sources import snapshot as raw_snapshot

from ..page_boilerplate import clean_pages, signature
from ..section_dictionary import build_section_tree


def snapshot(patient,output,pdf_root=ROOT/'pdf_db'):
    output=Path(output)
    raw=raw_snapshot(patient,output,pdf_root)
    atomic(output/'raw_source_bundle.json',raw)
    bundle=copy.deepcopy(raw);all_exclusions=[];geometry=[]
    for doc in bundle['documents']:
        pages=[]
        with pymupdf.open(Path(pdf_root)/patient/doc['filename']) as pdf:
            for n,page in enumerate(pdf,1):
                blocks=[dict(block_id=f'{doc["source_document_id"]}:p{n}:b{i}',bbox=list(b[:4]),text=b[4])
                    for i,b in enumerate(page.get_text('blocks',sort=True)) if b[6]==0]
                pages.append(dict(page=n,width=page.rect.width,height=page.rect.height,blocks=blocks))
        cleaned,excluded=clean_pages(pages)
        geometry.append(dict(document_id=doc['source_document_id'],filename=doc['filename'],pages=pages))
        for section,page in zip(doc['sections'],cleaned):
            section['raw_pdf_text']=section['text'];section['text']=page['text'];section['block_spans']=page['block_spans']
            section['source_layer']='CLEANED_PAGE_TEXT'
        all_exclusions.extend(dict(patient_id=patient,document_id=doc['source_document_id'],filename=doc['filename'],**e) for e in excluded)
    for doc in bundle['documents']:
        doc['section_tree']=build_section_tree(doc['sections'],doc['source_document_id'])
    bundle['section_dictionary_version']=1
    bundle['preparation_version']='EEG_GEOMETRY_1.2'
    atomic(output/'source_bundle.json',bundle);atomic(output/'page_geometry.json',geometry)
    atomic(output/'page_boilerplate_exclusions.json',all_exclusions)
    return bundle


def reclean(output):
    """Rebuild only this development snapshot from preserved raw blocks."""
    from ..workbench_store import read
    output=Path(output);bundle=read(output/'raw_source_bundle.json');geometry=read(output/'page_geometry.json');excluded=[]
    for name in ('source_bundle','page_boilerplate_exclusions'):
        backup=output/(name+'_initial.json')
        if not backup.exists():backup.write_bytes((output/(name+'.json')).read_bytes())
    for doc,raw_geometry in zip(bundle['documents'],geometry):
        pages,rows=clean_pages(raw_geometry['pages'])
        for section,page in zip(doc['sections'],pages):
            section['raw_pdf_text']=section['text'];section['text']=page['text'];section['block_spans']=page['block_spans'];section['source_layer']='CLEANED_PAGE_TEXT'
        excluded.extend(dict(patient_id=bundle['patient_id'],document_id=doc['source_document_id'],filename=doc['filename'],**r) for r in rows)
    for doc in bundle['documents']:
        doc['section_tree']=build_section_tree(doc['sections'],doc['source_document_id'])
    bundle['section_dictionary_version']=1
    bundle['preparation_version']='EEG_GEOMETRY_1.2'
    atomic(output/'source_bundle.json',bundle);atomic(output/'page_boilerplate_exclusions.json',excluded)
