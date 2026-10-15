"""Examination artifacts use the established immutable, audit-anchored pattern."""
from datetime import datetime, timezone
import json
import os
from uuid import uuid4
from ..config import load_environment
from ..database.models import ExtractionRun
from ..llm.examination_prompts import PROMPT_VERSION, examination_prompt
from .radiology_artifacts import digest, artifact_directory, VerifiedExtractionError
from .examination_validators import validate_examinations


def fingerprint(item):
    return digest({'extraction_type': 'examinations', 'patient_id': item.patient_id,
                   'source_document_id': item.source_document_id, 'section_ids': [item.section_id],
                   'context': item.context(), 'source_text': item.text,
                   'prompt_version': PROMPT_VERSION, 'actual_prompt': examination_prompt(item),
                   'result_policy':'exact_source_subreport_v1'})


def save_artifact(patient_id, case_id, inputs, records, client, debug_root):
    by_id = {r['examination']['examination_id']: r for r in records}
    created = datetime.now(timezone.utc)
    data = {'artifact_version': 1, 'extraction_type': 'examinations', 'patient_id': patient_id,
            'resolved_case_id': case_id, 'validation_status': 'VERIFIED', 'created_at': created.isoformat(),
            'prompt_version': PROMPT_VERSION, 'model_name': client.model_name,
            'model_version': client.model_version, 'runtime': client.runtime,
            'inputs': [{'context': i.context(), 'source_fingerprint': fingerprint(i),
                        'source_run_id': by_id[i.examination_id]['run_id'],
                        'normalized_output': by_id[i.examination_id]['normalized_output']} for i in inputs]}
    data['artifact_sha256'] = digest(data)
    folder = artifact_directory(patient_id, debug_root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"examinations_{created.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json"
    with path.open('x', encoding='utf-8') as f: f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return path


def load_artifact(patient_id, inputs, client, debug_root):
    files = sorted(artifact_directory(patient_id, debug_root).glob('examinations_*.json'))
    if not files: raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        sha = data.pop('artifact_sha256')
        if sha != digest(data): raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        data['artifact_sha256'] = sha
        if (data['validation_status'] != 'VERIFIED' or data['extraction_type'] != 'examinations'
                or data['artifact_version'] != 1 or data['patient_id'] != patient_id
                or type(data['resolved_case_id']) is not int or data['resolved_case_id'] <= 0
                or datetime.fromisoformat(data['created_at']).tzinfo is None):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if data['prompt_version'] != PROMPT_VERSION: raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        if client:
            name, version = client.model_name, client.model_version
        else:
            load_environment()
            backend = os.getenv('LLM_BACKEND', 'granite').strip().lower()
            if backend not in ('granite','mock'): raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
            name = 'mock-empty' if backend == 'mock' else os.getenv('GRANITE_MODEL', 'granite4.2:latest')
            version = None
        if data['model_name'] != name or (version is not None and data['model_version'] != version):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        entries = {e['context']['examination_id']: e for e in data['inputs']}
        if len(entries) != len(data['inputs']) or set(entries) != {i.examination_id for i in inputs}:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        for item in inputs:
            e = entries[item.examination_id]
            if e['context'] != item.context() or e['source_fingerprint'] != fingerprint(item):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        return data, path
    except VerifiedExtractionError: raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def verify_sources(data, inputs, session, case_id):
    if data['resolved_case_id'] != case_id: raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    by_id = {i.examination_id: i for i in inputs}
    normalized, sources = [], {}
    try:
        for e in data['inputs']:
            item = by_id[e['context']['examination_id']]
            run = session.get(ExtractionRun, e['source_run_id'])
            if (run is None or run.status != 'SUCCESS' or run.prompt_version != data['prompt_version']
                    or run.model_name != data['model_name'] or run.model_version != data['model_version']
                    or run.source_document_id != item.source_document_id):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            raw = run.raw_output
            if (raw['dry_run'] is not True or raw['patient_id'] != data['patient_id']
                    or raw['resolved_case_id'] != case_id or raw['patient_validation_status'] != 'SUCCESS'
                    or raw['examination'] != e['context'] or raw['normalized_output'] != e['normalized_output']):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            event = {**validate_examinations(raw['response'], item), 'case_id': case_id}
            if event != e['normalized_output']: raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
            normalized.append(e['normalized_output'])
            sources[item.examination_id] = run
        return normalized, sources
    except VerifiedExtractionError: raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None
