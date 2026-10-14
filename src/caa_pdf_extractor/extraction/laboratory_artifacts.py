"""Immutable, source-fingerprinted laboratory artifacts anchored to dry runs."""
from datetime import datetime, timezone
import json
import os
from uuid import uuid4
from ..config import load_environment
from ..database.models import ExtractionRun
from ..llm.laboratory_prompts import PROMPT_VERSION, laboratory_prompt
from .laboratory_models import RULE_VERSION, validate_laboratory
from .radiology_artifacts import artifact_directory, digest, VerifiedExtractionError


def fingerprint(patient_id, inputs, manifest):
    return digest({'source_manifest': manifest, 'prompt': laboratory_prompt(patient_id, inputs),
                   'rule_version': RULE_VERSION})


def save_artifact(patient_id, case_id, inputs, manifest, run, client, root):
    created = datetime.now(timezone.utc)
    data = {'artifact_version': 1, 'extraction_type': 'laboratory', 'patient_id': patient_id,
            'resolved_case_id': case_id, 'validation_status': 'VERIFIED', 'created_at': created.isoformat(),
            'prompt_version': PROMPT_VERSION, 'rule_version': RULE_VERSION,
            'model_name': client.model_name, 'model_version': client.model_version, 'runtime': client.runtime,
            'source_fingerprint': fingerprint(patient_id, inputs, manifest),
            'source_run_id': run.id, 'normalized_output': run.raw_output['normalized_output']}
    data['artifact_sha256'] = digest(data)
    folder = artifact_directory(patient_id, root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"laboratory_{created.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json"
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return path


def load_artifact(patient_id, inputs, manifest, client, root):
    files = sorted(artifact_directory(patient_id, root).glob('laboratory_*.json'))
    if not files:
        raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        checksum = data.pop('artifact_sha256')
        if checksum != digest(data):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        data['artifact_sha256'] = checksum
        if (data['artifact_version'] != 1 or data['extraction_type'] != 'laboratory'
                or data['patient_id'] != patient_id or data['validation_status'] != 'VERIFIED'
                or type(data['resolved_case_id']) is not int or data['resolved_case_id'] <= 0
                or datetime.fromisoformat(data['created_at']).tzinfo is None):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        load_environment()
        backend = os.getenv('LLM_BACKEND', 'granite').strip().lower()
        if client is None and backend not in ('mock', 'granite'):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        model = client.model_name if client else ('mock-empty' if backend == 'mock' else os.getenv('GRANITE_MODEL', 'granite4.2:latest'))
        if (data['model_name'] != model or (client and data['model_version'] != client.model_version)
                or data['prompt_version'] != PROMPT_VERSION or data['rule_version'] != RULE_VERSION
                or data['source_fingerprint'] != fingerprint(patient_id, inputs, manifest)):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        return data, path
    except VerifiedExtractionError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def verify_sources(data, patient_id, case_id, inputs, manifest, session):
    if data['resolved_case_id'] != case_id:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    try:
        run = session.get(ExtractionRun, data['source_run_id'])
        if (run is None or run.status != 'SUCCESS' or run.prompt_version != PROMPT_VERSION
                or run.model_name != data['model_name'] or run.model_version != data['model_version']):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        raw = run.raw_output
        expected_source = inputs[0].source_document_id if inputs else raw['empty_source_document_id']
        if (run.source_document_id != expected_source or raw['dry_run'] is not True
                or raw['patient_id'] != patient_id or raw['resolved_case_id'] != case_id
                or raw['patient_validation_status'] != 'SUCCESS' or raw['extraction_type'] != 'laboratory'
                or raw['source_manifest'] != manifest or raw['source_fingerprint'] != data['source_fingerprint']
                or raw['selected_passages'] != [i.context() for i in inputs]
                or raw['normalized_output'] != data['normalized_output']):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        normalized = validate_laboratory(raw['response'], patient_id, inputs)
        if normalized != data['normalized_output']:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        return normalized, run
    except VerifiedExtractionError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None
