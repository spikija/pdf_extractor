"""Verified local artifacts anchored to original, successful inference audits."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from ..config import load_environment
from ..database.models import ExtractionRun
from ..llm.radiology_prompts import PROMPT_VERSION, radiology_prompt
from .debug_export import DEBUG_ROOT
from .radiology_inputs import procedure_context
from .radiology_models import RadiologyExtraction
from .radiology_validators import ACUITY_RULE_VERSION, validate_radiology, validate_independent_radiology


class VerifiedExtractionError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def source_fingerprint(procedure):
    return digest({'context': procedure_context(procedure), 'result_text': procedure.result_text,
                   'source_text': procedure.source_text, 'prompt_version': PROMPT_VERSION,
                   'acuity_rule_version': ACUITY_RULE_VERSION,
                   'actual_prompt': radiology_prompt(procedure, RadiologyExtraction.model_json_schema()),
                   'validation_policy': Path(__file__).with_name('radiology_validators.py').read_text(encoding='utf-8')})


def artifact_directory(patient_id, debug_root=None):
    if not isinstance(patient_id, str) or patient_id in ('', '.', '..') or any(c in patient_id for c in '/\\:<>"|?*'):
        raise ValueError('Invalid patient identifier for verified artifact')
    return Path(debug_root or DEBUG_ROOT) / patient_id / 'verified'


def create_artifact(patient_id, case_id, procedures, records, *, model_name, model_version,
                    runtime, debug_root=None):
    by_id = {r['procedure']['procedure_id']: r for r in records}
    entries = []
    for p in procedures:
        r = by_id[p.procedure_id]
        expected = 'SUCCESS' if p.modality == 'MRI' else 'SKIPPED_NON_MRI'
        if r['status'] not in (('SUCCESS','PARTIAL_VALID','FAILED_VALIDATION') if p.modality=='MRI' else (expected,)):
            raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
        if p.modality=='MRI' and r['normalized_output'] is None:
            raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
        entries.append({'procedure': procedure_context(p), 'source_fingerprint': source_fingerprint(p),
                        'source_run_id': r['run_id'], 'model_name': r['model_name'],
                        'model_version': r['model_version'], 'normalized_output': r['normalized_output']})
    created = datetime.now(timezone.utc)
    accepted = [{'procedure_id':e['procedure']['procedure_id'], **f} for e in entries if e['normalized_output'] for f in e['normalized_output']['findings']]
    rejected = [{'procedure_id':e['procedure']['procedure_id'], **r} for e in entries if e['normalized_output'] for r in e['normalized_output'].get('rejected_records',[])]
    validation_status = 'PARTIAL_VALID' if rejected else 'VERIFIED' if accepted else 'VERIFIED_EMPTY'
    artifact = {'artifact_version': 1, 'patient_id': patient_id, 'resolved_case_id': case_id,
                'validation_status': validation_status, 'created_at': created.isoformat(),
                'accepted_records':accepted, 'rejected_records':rejected,
                'prompt_version': PROMPT_VERSION, 'acuity_rule_version': ACUITY_RULE_VERSION,
                'model_name': model_name, 'model_version': model_version, 'runtime': runtime,
                'procedures': entries}
    artifact['artifact_sha256'] = digest(artifact)
    folder = artifact_directory(patient_id, debug_root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"radiology_{created.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json"
    with path.open('x', encoding='utf-8') as f:
        f.write(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
    return path


def load_latest_artifact(patient_id, procedures, *, client=None, debug_root=None):
    files = sorted(artifact_directory(patient_id, debug_root).glob('radiology_*.json'))
    if not files:
        raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        artifact = json.loads(path.read_text(encoding='utf-8'))
        checksum = artifact.pop('artifact_sha256')
        if checksum != digest(artifact):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        artifact['artifact_sha256'] = checksum
        if (artifact['validation_status'] not in ('VERIFIED','VERIFIED_EMPTY','PARTIAL_VALID') or artifact['artifact_version'] != 1
                or artifact['patient_id'] != patient_id
                or type(artifact['resolved_case_id']) is not int
                or artifact['resolved_case_id'] <= 0):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        created = datetime.fromisoformat(artifact['created_at'])
        if created.tzinfo is None:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if artifact['prompt_version'] != PROMPT_VERSION or artifact['acuity_rule_version'] != ACUITY_RULE_VERSION:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        if client is not None:
            expected_model = client.model_name
            expected_version = client.model_version
        else:
            load_environment()
            backend = os.getenv('LLM_BACKEND', 'granite').strip().lower()
            if backend not in ('granite', 'mock'):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
            expected_model = os.getenv('GRANITE_MODEL', 'granite4.2:latest') if backend == 'granite' else 'mock-empty'
            expected_version = None  # No runtime/network call to discover a digest.
        if (artifact['model_name'] != expected_model
                or (expected_version is not None and artifact['model_version'] != expected_version)):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        entries = artifact['procedures']
        by_id = {e['procedure']['procedure_id']: e for e in entries}
        if len(by_id) != len(entries) or set(by_id) != {p.procedure_id for p in procedures}:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        for p in procedures:
            e = by_id[p.procedure_id]
            if e['procedure'] != procedure_context(p) or e['source_fingerprint'] != source_fingerprint(p):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        return artifact, path
    except VerifiedExtractionError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def verify_audit_anchor(artifact, procedures, session, case_id):
    """Revalidate original responses; never repair evidence or trust edited JSON."""
    if artifact['resolved_case_id'] != case_id:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    by_id = {p.procedure_id: p for p in procedures}
    normalized, sources = [], {}
    try:
        for entry in artifact['procedures']:
            p = by_id[entry['procedure']['procedure_id']]
            run = session.get(ExtractionRun, entry['source_run_id'])
            expected = 'SUCCESS' if p.modality == 'MRI' else 'SKIPPED_NON_MRI'
            if (run is None or run.status not in (('SUCCESS','PARTIAL_VALID','FAILED_VALIDATION') if p.modality=='MRI' else (expected,)) or run.prompt_version != artifact['prompt_version']
                    or run.source_document_id != p.source_document_id
                    or run.model_name != entry['model_name'] or run.model_version != entry['model_version']
                    or run.model_name != artifact['model_name']):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            raw = run.raw_output
            if (raw['dry_run'] is not True or raw['patient_id'] != artifact['patient_id']
                    or raw['procedure'] != entry['procedure']
                    or raw['resolved_case_id'] != case_id or raw['patient_validation_status'] not in ('SUCCESS','PARTIAL_VALID')
                    or raw['insertion_status'] not in ('SUCCESS','PARTIAL_VALID')
                    or raw['normalized_output'] != entry['normalized_output']):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            if p.modality == 'MRI':
                if entry['model_version'] != artifact['model_version']:
                    raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
                event = {**validate_independent_radiology(raw['response'], artifact['patient_id'], p), 'case_id': case_id}
                if run.status != event['validation_status']: raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
                if event != entry['normalized_output']:
                    raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
                normalized.append(entry['normalized_output'])
            elif entry['normalized_output'] is not None:
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            sources[p.procedure_id] = run
        accepted = [{'procedure_id':e['procedure_id'],**f} for e in normalized for f in e['findings']]
        rejected = [{'procedure_id':e['procedure_id'],**r} for e in normalized for r in e.get('rejected_records',[])]
        if 'accepted_records' in artifact:
            status = 'PARTIAL_VALID' if rejected else 'VERIFIED' if accepted else 'VERIFIED_EMPTY'
            if (artifact['accepted_records'] != accepted or artifact['rejected_records'] != rejected or artifact['validation_status']!=status):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        return normalized, sources
    except VerifiedExtractionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def verify_debug_artifact(patient_id, debug_path, *, session_factory=None, debug_root=None):
    """Promote a previously reviewed successful snapshot without new inference."""
    from ..database import SessionLocal
    from ..preparation import build_patient_bundle
    from .case_resolver import resolve_case_id
    factory = session_factory or SessionLocal
    debug = json.loads(Path(debug_path).read_text(encoding='utf-8'))
    if (debug.get('patient_id') != patient_id or debug.get('dry_run') is not True
            or debug.get('status') != 'SUCCESS' or debug.get('validation_status') != 'SUCCESS'
            or debug.get('prompt_version') != PROMPT_VERSION):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    bundle = build_patient_bundle(patient_id, factory)
    procedures = [p for d in bundle.documents for p in d.diagnostic_procedures]
    records = debug['runs']
    # Match the snapshot to its original audit rows before adding fingerprints.
    pseudo = {'patient_id': patient_id, 'resolved_case_id': debug['resolved_case_id'],
              'prompt_version': debug['prompt_version'], 'model_name': debug['model'],
              'model_version': next((r['model_version'] for r in reversed(records) if r['procedure']['modality'] == 'MRI'), None),
              'procedures': [{'procedure': r['procedure'], 'source_run_id': r['run_id'],
                              'model_name': r['model_name'], 'model_version': r['model_version'],
                              'normalized_output': r['normalized_output']} for r in records]}
    if {r['procedure']['procedure_id'] for r in records} != {p.procedure_id for p in procedures}:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    with factory.begin() as session:
        case_id = resolve_case_id(patient_id, session=session)
        normalized, sources = verify_audit_anchor(pseudo, procedures, session, case_id)
        if normalized != debug['normalized_output']:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        for r in records:
            source = sources[r['procedure']['procedure_id']]
            if r['granite_response'] != (json.loads(source.raw_output['response']) if source.raw_output['response'] else None):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    return create_artifact(patient_id, case_id, procedures, records,
                           model_name=pseudo['model_name'], model_version=pseudo['model_version'],
                           runtime=debug['runtime'], debug_root=debug_root)
