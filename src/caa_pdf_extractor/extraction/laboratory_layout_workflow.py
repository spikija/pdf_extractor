"""Approved layout artifacts and observation-to-row audit reconciliation."""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, text, null
from ..database import SessionLocal
from ..database.models import Laboratory, ExtractionRun
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id, CaseResolutionError
from .debug_export import DEBUG_ROOT
from .laboratory_layout import inspect_patient, METHOD_VERSION
from .laboratory_inputs import source_manifest
from .laboratory_models import FIELDS
from .radiology_artifacts import artifact_directory, digest, VerifiedExtractionError

POLICY_VERSION = 'crp_hba1c_null_date_v1'


def observation_fingerprint(patient_id, candidate):
    return digest({'patient_id': patient_id, **{name: candidate[name] for name in (
        'source_document_id', 'page_number', 'biomarker', 'raw_numeric_text',
        'numeric_value_bbox', 'biomarker_label_bbox', 'column_role', 'column_header_text', 'column_header_bbox')}})


def observations(patient_id, case_id, candidates):
    result = []
    for candidate in candidates:
        if candidate['validation_status'] != 'VALID' or candidate['issues']:
            raise VerifiedExtractionError('LAYOUT_VALIDATION_FAILED')
        if (candidate['audit_only'] or candidate['biomarker'] not in ('crp', 'hba1c')):
            continue
        unit = candidate['unit_text'].casefold()
        if unit != ('mg/dl' if candidate['biomarker'] == 'crp' else '%'):
            raise VerifiedExtractionError('UNIT_MISMATCH')
        result.append({'patient_id': patient_id, 'resolved_case_id': case_id, **candidate,
            'observation_fingerprint': observation_fingerprint(patient_id, candidate),
            'clinical_persistence_authorized': True})
    return result


def snapshot(patient_id, factory, root):
    summary = inspect_patient(patient_id, session_factory=factory, debug_root=root)
    if summary['status'] != 'SUCCESS':
        raise VerifiedExtractionError(summary['status'])
    data = json.loads(Path(summary['layout_debug_artifact']).read_text(encoding='utf-8'))
    return {'patient_id': patient_id, 'resolved_case_id': data['resolved_case_id'],
            'method_version': METHOD_VERSION, 'candidates': data['candidates'], 'source_pages': data['source_pages'],
            'prepared_source_manifest': source_manifest(build_patient_bundle(patient_id, factory))}


def create_verified(patient_id, factory, root):
    current = snapshot(patient_id, factory, root)
    normalized = observations(patient_id, current['resolved_case_id'], current['candidates'])
    source_id = current['source_pages'][0]['source_document_id'] if current['source_pages'] else None
    with factory.begin() as session:
        case_id = resolve_case_id(patient_id, session=session)
        if case_id != current['resolved_case_id']:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        run = ExtractionRun(source_document_id=source_id, model_name='deterministic-layout',
            model_version=METHOD_VERSION, prompt_version=METHOD_VERSION, status='SUCCESS',
            completed_at=datetime.now(timezone.utc), raw_output={
                'patient_id': patient_id, 'resolved_case_id': case_id, 'extraction_type': 'laboratory',
                'operation': 'VERIFY_LAYOUT', 'dry_run': True, 'inference_performed': False,
                'policy_version': POLICY_VERSION, 'source_snapshot': current,
                'source_fingerprint': digest(current), 'observations': normalized})
        session.add(run)
        session.flush()
        run_id = run.id
    created = datetime.now(timezone.utc)
    artifact = {'artifact_version': 1, 'extraction_type': 'laboratory_layout', 'patient_id': patient_id,
        'resolved_case_id': case_id, 'validation_status': 'VERIFIED', 'created_at': created.isoformat(),
        'method_version': METHOD_VERSION, 'policy_version': POLICY_VERSION, 'source_run_id': run_id,
        'source_document_id': source_id, 'source_snapshot': current, 'source_fingerprint': digest(current),
        'observations': normalized}
    artifact['artifact_sha256'] = digest(artifact)
    folder = artifact_directory(patient_id, root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"laboratory_layout_{created.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json"
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
    return artifact, path


def load_verified(patient_id, root):
    files = sorted(artifact_directory(patient_id, root).glob('laboratory_layout_*.json'))
    if not files:
        raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        artifact = json.loads(path.read_text(encoding='utf-8'))
        checksum = artifact.pop('artifact_sha256')
        if checksum != digest(artifact):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        artifact['artifact_sha256'] = checksum
        if (artifact['artifact_version'] != 1 or artifact['extraction_type'] != 'laboratory_layout'
                or artifact['patient_id'] != patient_id or artifact['validation_status'] != 'VERIFIED'
                or datetime.fromisoformat(artifact['created_at']).tzinfo is None):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if artifact['method_version'] != METHOD_VERSION or artifact['policy_version'] != POLICY_VERSION:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        return artifact, path
    except VerifiedExtractionError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def row_identity(row, biomarker):
    value = getattr(row, biomarker)
    return (row.date_of_exam.isoformat() if row.date_of_exam else None, biomarker,
            Decimal(value) if value is not None else None)


def identity(observation):
    return observation['date_of_exam'], observation['biomarker'], Decimal(observation['decimal_value'])


def reconcile(observations, rows, runs):
    links = defaultdict(set)
    for run in runs:
        raw = run.raw_output or {}
        if run.status == 'SUCCESS' and raw.get('operation') == 'COMMIT_LAYOUT' and raw.get('policy_version') == POLICY_VERSION:
            for link in raw.get('observation_links', []):
                links[link['observation_fingerprint']].add(link['laboratory_id'])
    multiplicity = Counter(identity(o) for o in observations)
    plan = []
    for observation in observations:
        fingerprint = observation['observation_fingerprint']
        matches = [r for r in rows if row_identity(r, observation['biomarker']) == identity(observation)]
        exact = [r for r in matches if r.id in links[fingerprint]]
        inconsistent_links = [r for r in rows if r.id in links[fingerprint] and r not in matches]
        if len(exact) == 1:
            plan.append((observation, 'skip', exact[0]))
        elif matches or inconsistent_links or multiplicity[identity(observation)] > 1:
            plan.append((observation, 'ambiguous', None))
        else:
            plan.append((observation, 'insert', None))
    return plan


def commit_verified(patient_id, factory, root):
    artifact, path = load_verified(patient_id, root)
    current = snapshot(patient_id, factory, root)
    if current != artifact['source_snapshot'] or digest(current) != artifact['source_fingerprint']:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    normalized = observations(patient_id, current['resolved_case_id'], current['candidates'])
    if normalized != artifact['observations']:
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    with factory.begin() as session:
        case_id = resolve_case_id(patient_id, session=session)
        if case_id != artifact['resolved_case_id']:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        session.execute(text('SELECT pg_advisory_xact_lock(:case_id)'), {'case_id': case_id})
        source = session.get(ExtractionRun, artifact['source_run_id'])
        if (source is None or source.status != 'SUCCESS' or source.source_document_id != artifact['source_document_id']
                or source.model_name != 'deterministic-layout' or source.model_version != METHOD_VERSION
                or source.prompt_version != METHOD_VERSION):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        expected = {'patient_id': patient_id, 'resolved_case_id': case_id, 'extraction_type': 'laboratory',
            'operation': 'VERIFY_LAYOUT', 'dry_run': True, 'inference_performed': False,
            'policy_version': POLICY_VERSION, 'source_snapshot': current,
            'source_fingerprint': digest(current), 'observations': normalized}
        if source.raw_output != expected:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        rows = list(session.scalars(select(Laboratory).where(Laboratory.case_id == case_id, Laboratory.deleted.is_(False))))
        audits = list(session.scalars(select(ExtractionRun).where(ExtractionRun.prompt_version == METHOD_VERSION,
            ExtractionRun.status == 'SUCCESS', ExtractionRun.raw_output['resolved_case_id'].astext == str(case_id))))
        plan = reconcile(normalized, rows, audits)
        inserted = skipped = 0
        blocked, links = [], []
        for observation, operation, existing in plan:
            if operation == 'ambiguous':
                blocked.append({'observation_fingerprint': observation['observation_fingerprint'],
                                'candidate_id': observation['candidate_id'], 'status': 'DUPLICATE_IDENTITY_AMBIGUOUS'})
                continue
            if operation == 'skip':
                skipped += 1
                row = existing
            else:
                fields = {name: null() for name in FIELDS}
                fields[observation['biomarker']] = Decimal(observation['decimal_value'])
                row = Laboratory(case_id=case_id, deleted=False,
                    date_of_exam=date.fromisoformat(observation['date_of_exam']) if observation['date_of_exam'] else null(), **fields)
                session.add(row)
                session.flush()
                inserted += 1
            links.append({'observation_fingerprint': observation['observation_fingerprint'],
                          'laboratory_id': row.id, 'operation': operation})
        session.add(ExtractionRun(source_document_id=artifact['source_document_id'], model_name='deterministic-layout',
            model_version=METHOD_VERSION, prompt_version=METHOD_VERSION, status='SUCCESS',
            completed_at=datetime.now(timezone.utc), raw_output={
                'patient_id': patient_id, 'resolved_case_id': case_id, 'extraction_type': 'laboratory',
                'operation': 'COMMIT_LAYOUT', 'dry_run': False, 'inference_performed': False,
                'policy_version': POLICY_VERSION, 'verified_artifact': str(path),
                'artifact_sha256': artifact['artifact_sha256'], 'source_run_id': source.id,
                'observations': normalized, 'observation_links': links, 'ambiguous_observations': blocked,
                'inserted': inserted, 'duplicates_skipped': skipped}))
    return {'status': 'DUPLICATE_IDENTITY_AMBIGUOUS' if blocked else 'SUCCESS', 'validation_status': 'VERIFIED',
            'resolved_case_id': case_id, 'verified_artifact': str(path), 'verified_artifact_used': True,
            'persistable_observations': len(normalized), 'new_rows_inserted': inserted,
            'duplicates_skipped': skipped, 'ambiguous_blocked': len(blocked), 'ambiguities': blocked,
            'observation_links': links}


def extract_layout_patient(patient_id, *, dry_run=True, session_factory=None, debug_root=None):
    artifact_directory(patient_id, debug_root)
    summary = {'patient_id': patient_id, 'dry_run': dry_run, 'granite_called': False,
               'new_rows_inserted': 0, 'duplicates_skipped': 0, 'ambiguous_blocked': 0}
    try:
        factory = session_factory or SessionLocal
        if dry_run:
            artifact, path = create_verified(patient_id, factory, debug_root)
            summary.update(status='SUCCESS', validation_status='VERIFIED', verified_artifact=str(path),
                verified_artifact_created=True, resolved_case_id=artifact['resolved_case_id'],
                persistable_observations=len(artifact['observations']),
                audit_only_observations=len(artifact['source_snapshot']['candidates']) - len(artifact['observations']))
        else:
            summary.update(commit_verified(patient_id, factory, debug_root))
    except (VerifiedExtractionError, CaseResolutionError) as exc:
        summary.update(status=exc.status, validation_status=exc.status)
    except Exception as exc:
        summary.update(status='FAILED_LAYOUT_WORKFLOW', validation_status='FAILED_LAYOUT_WORKFLOW', error_type=type(exc).__name__)
    path = Path(debug_root or DEBUG_ROOT) / patient_id / 'laboratory_layout_commit_summary.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary
