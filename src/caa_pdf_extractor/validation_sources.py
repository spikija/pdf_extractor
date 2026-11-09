"""Prepare selected local PDF inputs using existing preparation/history/medication selectors.

This adapter performs no diagnosis normalization, medication resolution, clinical
scales, CAA classification, model calls or database operations.
"""
import csv
import hashlib
import json
import re
from pathlib import Path

import pymupdf
from . import utils
from .preparation.patient_bundle import prepare_document
from .preparation.models import PatientDocumentBundle
from .extraction.history_v2 import extract_bundle
from .extraction.medication_inputs import source_rows
from .extraction.encounter_mrs import encounter_identity
from .workbench_store import ROOT, atomic, sha, read, WorkbenchStore
from .workbench_runner import configure_sources

CACHE_ROOT = ROOT / 'data/debug/review_app/parsed_pdf_cache'


def parser_key():
    return pymupdf.__version__ + '-' + sha(Path(utils.__file__))[:16]


def prepare_patient(patient, output, *, store=None, pdf_root=ROOT / 'pdf_db', cache_root=CACHE_ROOT):
    store = store or WorkbenchStore()
    store.require_enabled(patient)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    prepared, cache_hits, page_count = [], 0, 0
    files = sorted((pdf_root / patient).glob('*.pdf'))
    if not files:
        raise ValueError('No source PDFs')
    for path in files:
        digest = sha(path)
        cache = cache_root / parser_key() / (digest + '.json')
        if cache.exists():
            payload = read(cache)
            if payload['document']['sha256'] != digest or payload['document']['parser_version'] != pymupdf.__version__:
                raise ValueError('Parsed PDF cache identity mismatch')
            # A byte-identical PDF can have a different local filename/patient owner.
            payload['document'].update(patient_id=patient, relative_path=path.relative_to(pdf_root).as_posix(), filename=path.name)
            cache_hits += 1
        else:
            payload = utils.parse_pdf(path, path.relative_to(pdf_root).as_posix(), patient)
            atomic(cache, payload)
        docid = int(hashlib.sha256((patient + '/' + path.name).encode()).hexdigest()[:12], 16)
        prepared.append(prepare_document(patient, docid, path.name, digest, payload))
        page_count += payload['document']['page_count']
    bundle = PatientDocumentBundle(patient, prepared)
    history = extract_bundle(bundle)
    encounters = []
    for doc in prepared:
        identity = encounter_identity(doc)
        encounters.append(dict(identity, patient_id=patient, source_document_id=doc.source_document_id))
    encounter_map = {e['source_document_id']: e for e in encounters}
    rows = source_rows(bundle,encounter_map)
    atomic(output / 'source_bundle.json', bundle.to_dict())
    atomic(output / 'source_mentions.json', history)
    atomic(output / 'encounters.json', [e for e in encounters if e['status']=='VERIFIED_SOURCE_ENCOUNTER'])
    atomic(output / 'source_encounter_identities.json', encounters)
    atomic(output / 'patient_summary.json', {'patient_id': patient, 'demographics': {'update_fields': {}}})
    fields=list(rows[0]) if rows else ['patient_id','raw_medication','source_document_id','page','section_id','evidence_id','medication_context','available_date']
    with (output / 'medications.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields);writer.writeheader();writer.writerows(rows)
    context = configure_sources(store, patient, output, pdf_root)
    info = dict(patient_id=patient, pdfs=len(files), pages=page_count, cache_hits=cache_hits,
        parser_version=pymupdf.__version__, parser_code_hash=sha(Path(utils.__file__)),
        preparation_version=bundle.preparation_version, explicit_encounters=sum(e['status']=='VERIFIED_SOURCE_ENCOUNTER' for e in encounters),
        source_medication_rows=len(rows), input_only=True, structured_scale_runs=0, clinical_database_writes=0, cloud_calls=0)
    atomic(output / 'input_preparation.json', info)
    return info
