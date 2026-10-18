"""Shared immutable artifacts for document-scoped, strictly validated extraction."""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, text, null
from ..config import load_environment
from ..database import SessionLocal
from ..database.models import ExtractionRun, ClinicalHistory, Medication
from .case_resolver import resolve_case_id, CaseResolutionError
from .radiology_artifacts import digest, artifact_directory, VerifiedExtractionError

VERSION = 'document_artifact_v2_independent'


def specification(domain):
    if domain == 'clinical_history':
        from .clinical_history import select_sections
        from .validators import validate_output, deduplicate, normalize_disease
        from .models import ClinicalHistoryExtraction
        from ..llm.prompts import PROMPT_VERSION, clinical_history_prompt
        return select_sections, validate_output, deduplicate, lambda x: (normalize_disease(x.disease or ''), x.date_disease), ClinicalHistoryExtraction, PROMPT_VERSION, clinical_history_prompt, ClinicalHistory
    from .medication_inputs import select_medication_inputs
    from .medication_validators import validate_medications, deduplicate_medications, medication_key
    from .medication_models import MedicationExtraction
    from ..llm.medication_prompts import PROMPT_VERSION, medication_prompt
    return select_medication_inputs, validate_medications, deduplicate_medications, medication_key, MedicationExtraction, PROMPT_VERSION, medication_prompt, Medication


def fingerprint(domain, bundle):
    select_inputs, _, _, _, schema, version, prompt, _ = specification(domain)
    inputs = select_inputs(bundle)
    paths = ('validators.py', 'models.py', 'clinical_history.py', 'diagnosis_items.py', 'date_attribution.py', 'history_target_policy.py') if domain == 'clinical_history' else ('medication_validators.py', 'medication_models.py', 'medication_inputs.py', 'medications.py')
    from .history_target_policy import VERSION as target_version
    return digest({'version': VERSION, 'domain': domain, 'bundle': bundle.to_dict(),
        'prompts': [prompt(bundle.patient_id, [i for i in inputs if i.source_document_id == d.source_document_id], schema.model_json_schema()) for d in bundle.documents],
        'implementation': {p: Path(__file__).with_name(p).read_text(encoding='utf-8') for p in paths},
        'prompt_implementation': (Path(__file__).parents[1] / 'llm' / ('prompts.py' if domain == 'clinical_history' else 'medication_prompts.py')).read_text(encoding='utf-8'),
        'artifact_implementation': Path(__file__).read_text(encoding='utf-8'),
        **({'target_policy_version':target_version} if domain=='clinical_history' else {})})


def create(domain, bundle, case_id, runs, client, root):
    version = specification(domain)[5]
    allowed = ('SUCCESS','PARTIAL_VALID','FAILED_VALIDATION')
    if not runs or any(run.status not in allowed or (run.status=='FAILED_VALIDATION' and not run.raw_output.get('independent_validation')) for run in runs):
        raise VerifiedExtractionError("NO_VERIFIED_EXTRACTION")
    normalized = [item for run in runs for item in run.raw_output['normalized_output']]
    if domain == 'clinical_history':
        from .date_attribution import require_date_ownership
        from .history_target_policy import require_target_eligibility
        require_date_ownership(normalized, [m for run in runs for m in run.raw_output.get('evidence_metadata',[])])
        require_target_eligibility(normalized, [m for run in runs for m in run.raw_output.get('evidence_metadata',[])])
    rejected = [item for run in runs for item in run.raw_output.get('rejected_records',[])]
    status = 'PARTIAL_VALID' if normalized and rejected else 'VERIFIED' if normalized else 'VERIFIED_EMPTY'
    if rejected and not normalized: raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    original_fingerprint = fingerprint(domain, bundle)
    for run in runs:
        run.raw_output = {**run.raw_output, 'source_fingerprint': original_fingerprint,
                          'extraction_type': domain, 'policy_version': VERSION}
        if domain=='clinical_history':
            from .history_target_policy import VERSION as target_version
            run.raw_output={**run.raw_output,'target_policy_version':target_version}
    artifact = {'artifact_version': 1, 'extraction_type': domain, 'patient_id': bundle.patient_id,
        'resolved_case_id': case_id, 'validation_status': status,
        'created_at': datetime.now(timezone.utc).isoformat(), 'policy_version': VERSION,
        'prompt_version': version, 'model_name': client.model_name, 'model_version': client.model_version,
        'runtime': client.runtime, 'source_fingerprint': original_fingerprint,
        'source_run_ids': [r.id for r in runs], 'normalized_output': normalized,
        'accepted_records':normalized, 'rejected_records':rejected,
        'evidence_metadata':[item for run in runs for item in run.raw_output.get('evidence_metadata',[])]}
    if domain=='clinical_history':
        artifact['target_policy_version']=target_version
        artifact['audit_only_records']=[m for m in artifact['evidence_metadata'] if m.get('audit_only') is True]
    artifact['artifact_sha256'] = digest(artifact)
    folder = artifact_directory(bundle.patient_id, root); folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{domain}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}_{uuid4().hex}.json'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(artifact, ensure_ascii=False, indent=2)+'\n')
    return path


def load(domain, bundle, case_id, client, session, root):
    files = sorted(artifact_directory(bundle.patient_id, root).glob(domain+'_*.json'))
    if not files: raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    path = files[-1]
    try:
        artifact = json.loads(path.read_text(encoding='utf-8'))
        checksum = artifact.pop('artifact_sha256')
        if digest(artifact) != checksum: raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        artifact['artifact_sha256'] = checksum
        selector, validator, dedup, key, _, version, _, _ = specification(domain)
        if domain=='clinical_history':
            from .history_target_policy import VERSION as target_version
            if artifact.get('target_policy_version')!=target_version:
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        load_environment()
        backend = os.getenv('LLM_BACKEND', 'granite').strip().lower()
        model = client.model_name if client else ('mock-empty' if backend == 'mock' else os.getenv('GRANITE_MODEL', 'granite4.2:latest'))
        if (artifact['patient_id'] != bundle.patient_id or artifact['resolved_case_id'] != case_id
                or artifact['policy_version'] != VERSION or artifact['prompt_version'] != version
                or artifact['source_fingerprint'] != fingerprint(domain, bundle) or artifact['model_name'] != model
                or (client and (artifact['model_version'] != client.model_version or artifact['runtime'] != client.runtime))
                or (client is None and backend not in ('mock','granite'))):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
        if artifact['artifact_version'] != 1 or artifact['validation_status'] not in ('VERIFIED','VERIFIED_EMPTY','PARTIAL_VALID') or artifact['extraction_type'] != domain:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if domain == 'clinical_history':
            from .date_attribution import require_date_ownership
            from .history_target_policy import require_target_eligibility
            require_date_ownership(artifact['accepted_records'],artifact.get('evidence_metadata',[]))
            require_target_eligibility(artifact['accepted_records'],artifact.get('evidence_metadata',[]))
        inputs = selector(bundle)
        expected_docs = {i.source_document_id for i in inputs}
        validated, saved, seen_docs = [], [], set()
        rejections, evidence_metadata = [], []
        if len(set(artifact['source_run_ids'])) != len(artifact['source_run_ids']): raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        for run_id in artifact['source_run_ids']:
            run = session.get(ExtractionRun, run_id)
            if (run is None or run.status not in ('SUCCESS','PARTIAL_VALID','FAILED_VALIDATION') or run.prompt_version != version
                    or run.model_name != artifact['model_name'] or run.model_version != artifact['model_version']
                    or run.source_document_id in seen_docs or run.source_document_id not in expected_docs):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            seen_docs.add(run.source_document_id)
            raw = run.raw_output
            if domain=='clinical_history' and raw.get('target_policy_version')!=target_version:
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
            chosen = [i for i in inputs if i.source_document_id == run.source_document_id]
            if (raw['patient_id'] != bundle.patient_id or raw['dry_run'] is not True
                    or raw['patient_validation_status'] not in ('SUCCESS','PARTIAL_VALID')
                    or raw['section_ids'] != [i.section_id for i in chosen]
                    or raw.get('case_id') != case_id
                    or raw.get('extraction_type') != domain or raw.get('policy_version') != VERSION
                    or raw.get('source_fingerprint') != artifact['source_fingerprint']):
                raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
            if domain in ('clinical_history','medications'):
                if domain=='clinical_history':
                    from .diagnosis_items import validate_independent
                else:
                    from .medication_validators import validate_independent_medications as validate_independent
                if domain == 'clinical_history':
                    from .diagnosis_items import validate_recall_responses
                    result, rejected, metadata = validate_recall_responses(raw['response'], raw.get('focused_retries',[]), bundle.patient_id, chosen)
                else:
                    result, rejected, metadata = validate_independent(raw['response'], bundle.patient_id, chosen)
                expected = 'PARTIAL_VALID' if getattr(result,domain) and rejected else 'FAILED_VALIDATION' if rejected else 'SUCCESS'
                if (run.status != expected or rejected != raw.get('rejected_records',[]) or metadata != raw.get('evidence_metadata',[])):
                    raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
                rejections.extend(rejected); evidence_metadata.extend(metadata)
            else:
                result = validator(raw['response'], bundle.patient_id, chosen)
            validated.extend(getattr(result, domain))
            saved.extend(raw['normalized_output'])
        if seen_docs != expected_docs: raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if domain == 'clinical_history':
            priority = {i.section_id: n for n,i in enumerate(inputs)}
            validated.sort(key=lambda i: priority[i.section_id])
        values = [v.model_dump(mode='json') for v in dedup(validated)]
        # Audits group globally deduplicated output by source document.
        order = {doc.source_document_id:n for n,doc in enumerate(bundle.documents)}
        values.sort(key=lambda i: order[i['source_document_id']])
        expected_status = 'PARTIAL_VALID' if saved and rejections else 'VERIFIED' if saved else 'VERIFIED_EMPTY'
        if domain=='clinical_history' and artifact.get('audit_only_records')!=[m for m in evidence_metadata if m.get('audit_only') is True]:
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        if (values != saved or saved != artifact['normalized_output'] or saved != artifact['accepted_records']
                or rejections != artifact['rejected_records'] or evidence_metadata != artifact['evidence_metadata']
                or artifact['validation_status'] != expected_status):
            raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
        return artifact, path, dedup(validated)
    except VerifiedExtractionError: raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID') from None


def commit(domain, patient_id, bundle, *, client=None, session_factory=None, debug_root=None):
    factory = session_factory or SessionLocal
    summary = {'patient_id':patient_id, 'dry_run':False, 'granite_called':False,
               'model_calls':0, 'new_rows_inserted':0, 'inserted':0, 'duplicates_skipped':0,
               'case_id':None, 'matched_fallnr':None, 'case_exists':False, 'diagnoses':[]}
    try:
        with factory.begin() as session:
            case_id = resolve_case_id(patient_id,session=session)
            summary.update(case_id=case_id, resolved_case_id=case_id, matched_fallnr=patient_id, case_exists=True)
            session.execute(text('SELECT pg_advisory_xact_lock(:case_id)'), {'case_id':case_id})
            artifact,path,items=load(domain,bundle,case_id,client,session,debug_root)
            if domain=='clinical_history':
                from .history_target_policy import VERSION as target_version, require_target_eligibility
                if artifact.get('target_policy_version')!=target_version:
                    raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
                require_target_eligibility([item.model_dump(mode='json') for item in items],artifact.get('evidence_metadata',[]))
            _,_,_,key,_,version,_,model=specification(domain)
            existing = {key(r) for r in session.scalars(select(model).where(model.case_id==case_id))}
            inserted = skipped = 0
            for item in items:
                identity = key(item)
                if identity in existing: skipped += 1; continue
                if domain=='clinical_history':
                    row=model(case_id=case_id,disease=item.disease,date_disease=item.date_disease or null(),
                        disease_category=null(),date_admissioned=null(),date_discharged=null())
                else:
                    row=model(case_id=case_id,medication=item.medication,date_noted=item.date_noted or null(),
                        date_stopped=item.date_stopped or null(),medication_category=item.medication_category or null(),deleted=False)
                session.add(row); existing.add(identity); inserted += 1
            if bundle.documents:
                session.add(ExtractionRun(source_document_id=bundle.documents[0].source_document_id,
                    model_name=artifact['model_name'],model_version=artifact['model_version'],prompt_version=version,
                    status='SUCCESS',completed_at=datetime.now(timezone.utc),raw_output={
                        'patient_id':patient_id,'case_id':case_id,'dry_run':False,'extraction_type':domain,
                        'operation':'COMMIT_VERIFIED_EXTRACTION','inference_performed':False,
                        'source_run_ids':artifact['source_run_ids'],'artifact_sha256':artifact['artifact_sha256'],
                        'verified_artifact':str(path),'normalized_output':artifact['normalized_output'],
                        'inserted':inserted,'duplicates_skipped':skipped}))
        summary.update(status='SUCCESS',validation_status=artifact['validation_status'],verified_artifact_used=True,
            verified_artifact=str(path),new_rows_inserted=inserted,inserted=inserted,duplicates_skipped=skipped,
            diagnosis_count=len(items) if domain=='clinical_history' else 0,
            validated_diagnoses=len(items) if domain=='clinical_history' else 0,
            validated_medications=len(items) if domain=='medications' else 0)
    except (CaseResolutionError,VerifiedExtractionError) as exc:
        summary.update(status=exc.status,validation_status=exc.status,case_exists=exc.status!='CASE_NOT_FOUND' and summary['case_exists'])
    return summary
