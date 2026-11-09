"""Stable, revisioned local module storage. Active selection is a manifest pointer.

No extraction is triggered by reads. Clinical bytes are immutable after publication.
One atomically replaced manifest commits status/pointer/audit changes together.
"""
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STABLE_ROOT = ROOT / 'data' / 'patient_artifacts'
PILOTS = ('2435608415', '2459345815', '2552817617', '2684469520', '2826947422')
EDITABLE = ('diagnoses', 'medications')
DOWNSTREAM = {
    'eeg': set(),
    'diagnoses': {'events', 'AF', 'CHA2DS2_VA', 'ORBIT', 'CAA', 'CAA_chronology', 'OAC_decision_analysis'},
    'medications': {'ORBIT', 'OAC_decision_analysis', 'antithrombotic_exposure_analysis'},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.commit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def integrity(entry):
    files = entry.get('artifacts', [])
    return bool(files) and all(Path(f['path']).is_file() and sha(f['path']) == f['sha256'] for f in files)


def fingerprint(entry):
    # Preserve existing physician review hashes across byte-identical migration.
    value = ''.join(sha(f['path']) for f in sorted(entry['artifacts'], key=lambda f: f['path']))
    if entry.get('assertion_safety'):
        value += json.dumps(entry['assertion_safety'], sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def active_entries(manifest):
    selected, latest, errors = {}, {}, []
    for module, group in manifest['modules'].items():
        version = group.get('active_validated_version')
        if version:
            e = copy.deepcopy(group['versions'][version])
            e['candidate_versions'] = [v for v, c in group['versions'].items() if c['status'] == 'CANDIDATE']
            if manifest.get('source_context'):
                e['source_context_dir'] = manifest['source_context']['directory']
            latest[module] = e
            if e['status'] == 'VALIDATED' and integrity(e):
                selected[module] = e
            elif e['status'] == 'VALIDATED':
                errors.append(f'{module}: active stable artifact failed integrity; debug fallback blocked')
        elif group.get('versions'):
            latest[module] = copy.deepcopy(list(group['versions'].values())[-1])
            if manifest.get('source_context'):
                latest[module]['source_context_dir'] = manifest['source_context']['directory']
    for entry in list(selected.values())+list(latest.values()):
        if entry.get('module')=='eeg' and manifest.get('eeg_source_context'):
            entry['source_context_dir']=manifest['eeg_source_context']['directory']
    return selected, latest, errors


class WorkbenchStore:
    def __init__(self, root=STABLE_ROOT):
        self.root = Path(root).resolve()

    def patient_dir(self, patient):
        if not re.fullmatch(r'[0-9]{1,32}', str(patient)):
            raise ValueError('A numeric patient ID is required')
        path = self.root / str(patient)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Patient path escapes stable storage')
        return path

    def enabled_patients(self):
        path = self.root / 'validation_cohort.json'
        if not path.exists():
            return set(PILOTS)
        registry = read(path)
        ids = registry.get('patient_ids', [])
        if len(ids) > 20 or len(ids) != len(set(ids)) or not all(isinstance(p, str) and re.fullmatch(r'[0-9]{1,32}', p) for p in ids):
            raise ValueError('Invalid bounded validation cohort registry')
        return set(ids)

    def is_enabled(self, patient):
        return str(patient) in self.enabled_patients()

    def require_enabled(self, patient):
        if not self.is_enabled(patient):
            raise ValueError('Only one patient in the registered validation cohort may run; full-cohort processing is disabled')

    def manifest(self, patient):
        path = self.patient_dir(patient) / 'module_manifest.json'
        return read(path) if path.exists() else dict(schema_version=1, storage='STABLE', patient_id=str(patient), modules={}, audit=[])

    def _commit(self, patient, manifest, action, module, previous, entry):
        metadata = read(entry['metadata_path'])
        manifest['audit'].append(dict(audit_id=str(uuid.uuid4()), timestamp=now(), patient_id=str(patient), module=module,
            previous_version=previous, new_version=entry['version'], action=action,
            code_hash=metadata.get('code_hash'), source_hashes=metadata.get('source_pdf_hashes', {})))
        # Audit and active pointer are committed in the same atomic document.
        atomic(self.patient_dir(patient) / 'module_manifest.json', manifest)
        self.sync_audit()

    def sync_audit(self):
        """Recoverable JSONL projection; per-patient manifest commits are authoritative."""
        with file_lock(self.root / '.audit.lock'):
            events = []
            for path in sorted(self.root.glob('*/module_manifest.json')):
                if path.parent.name.isdigit():
                    events.extend(read(path).get('audit', []))
            events.sort(key=lambda e: (e['timestamp'], e.get('audit_id', '')))
            atomic_text(self.root / 'module_run_audit.jsonl', ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))

    def publish(self, patient, module, version, artifacts, metadata, *, validated=False, legacy=None):
        if module not in EDITABLE and not (module=='eeg' and getattr(self,'eeg_only',False)):
            raise ValueError('Unsupported module publisher')
        self.require_enabled(patient)
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+', version) or version in ('.', '..'):
            raise ValueError('Unsafe module revision')
        if not artifacts:
            raise ValueError('Cannot publish an empty artifact set')
        folder = self.patient_dir(patient)
        with file_lock(folder / '.manifest.lock'):
            manifest = self.manifest(patient)
            group = manifest['modules'].setdefault(module, dict(active_validated_version=None, available_versions=[], versions={}))
            if version in group['versions']:
                raise ValueError('Revision already exists; artifacts are never overwritten')
            if validated and group['active_validated_version']:
                raise ValueError('Use explicit promotion to replace an existing active artifact')
            target = folder / module / ('v' + version)
            target.mkdir(parents=True, exist_ok=False)
            files = []
            for name, value in artifacts.items():
                if Path(name).name != name:
                    raise ValueError('Artifact names must be basenames')
                path = target / name
                with path.open('xb') as out:
                    out.write(value if isinstance(value, bytes) else value.encode('utf-8'))
                    out.flush()
                    os.fsync(out.fileno())
                files.append(dict(path=str(path), sha256=sha(path)))
            status = 'VALIDATED' if validated else 'CANDIDATE'
            meta = dict(metadata, module_name=module, revision=version, status=status,
                        created_at=now(), artifact_hashes={Path(f['path']).name: f['sha256'] for f in files})
            atomic(target / 'module_metadata.json', meta)
            primary = next((f['path'] for f in files if Path(f['path']).name.startswith('canonical_')), files[0]['path'])
            entry = dict(module=module, version=version, module_version=meta['module_version'], status=status,
                         artifact=primary, artifacts=files, created_at=meta['created_at'],
                         metadata_path=str(target / 'module_metadata.json'), validation=meta['validation_test_results'])
            group['versions'][version] = entry
            group['available_versions'].append(version)
            previous = group['active_validated_version']
            if validated:
                group['active_validated_version'] = version
            # Preserve read-only downstream metadata from debug once, never promote it.
            for name, old in (legacy or {}).items():
                if name not in ('diagnoses', 'events', 'medications') and name not in manifest['modules']:
                    manifest['modules'][name] = dict(active_validated_version=old['version'], available_versions=[old['version']], versions={old['version']: old})
            if module == 'diagnoses' and validated:
                self._event_pointer(manifest, entry)
            self._commit(patient, manifest, 'MIGRATE' if validated else 'RUN', module, previous, entry)
            return copy.deepcopy(entry)

    @staticmethod
    def _event_pointer(manifest, entry):
        event_file = next((f['path'] for f in entry['artifacts'] if Path(f['path']).name.startswith('canonical_clinical_events') and f['path'].endswith('.json')), None)
        if event_file:
            group = manifest['modules'].setdefault('events', dict(active_validated_version=None, available_versions=[], versions={}))
            event = dict(entry, module='events', artifact=event_file)
            group['versions'][entry['version']] = event
            if entry['version'] not in group['available_versions']:
                group['available_versions'].append(entry['version'])
            group['active_validated_version'] = entry['version']

    def versions(self, patient, module):
        group = self.manifest(patient)['modules'].get(module, {})
        versions = copy.deepcopy(group.get('versions', {}))
        for version, reason in group.get('candidate_qa_holds', {}).items():
            if version in versions:
                versions[version]['qa_hold'] = reason
        return versions

    def promote(self, patient, module, version, *, confirmed=False, reviews=None, expected_active=None):
        if not confirmed:
            raise ValueError('Explicit physician promotion confirmation is required')
        with file_lock(self.patient_dir(patient) / '.manifest.lock'):
            manifest = self.manifest(patient)
            group = manifest['modules'][module]
            if version in group.get('candidate_qa_holds', {}):
                raise ValueError('Candidate failed independent source QA; inspect its replacement revision')
            if group['active_validated_version'] != expected_active:
                raise ValueError('Active version changed; compare and confirm again')
            entry = group['versions'][version]
            metadata = read(entry['metadata_path'])
            if module == 'diagnoses':
                from .extraction.assertion_review import unsafe_af_identity
                if entry.get('assertion_safety', {}).get('promotion_blocked') or unsafe_af_identity(entry):
                    raise ValueError('Assertion safety failure: canonical AF lacks positive source evidence; rerun required')
            gates = metadata['validation_test_results'].get('hard_gates', {})
            if entry['status'] != 'CANDIDATE' or not integrity(entry) or not gates or not all(v is True for v in gates.values()) or metadata.get('fatal_errors'):
                raise ValueError('Candidate has failed gates, invalid hashes, fatal errors, or is no longer pending')
            # Source PDFs must still match the snapshot that generated this candidate.
            for path, expected in metadata.get('source_pdf_hashes', {}).items():
                if not Path(path).is_file() or sha(path) != expected:
                    raise ValueError('Source PDF changed after extraction')
            for path, expected in {**metadata.get('source_input_hashes', {}), **metadata.get('reference_hashes', {})}.items():
                if not Path(path).is_file() or sha(path) != expected:
                    raise ValueError('Candidate source inputs or medication reference changed; rerun required')
            entities = candidate_entities(entry, module)
            if len({e['entity_id'] for e in entities}) != len(entities):
                raise ValueError('Duplicate canonical entity IDs; extraction repair is required before promotion')
            reviews = reviews or {}
            current_hash = fingerprint(entry)
            for entity in entities:
                if entity.get('review_required'):
                    record = reviews.get(entity['entity_id'])
                    if not record or record.get('action') not in ('ACCEPT', 'EDIT', 'REJECT') or record.get('source_artifact_hash') != current_hash:
                        raise ValueError('Every review-required candidate entity needs explicit current physician adjudication')
            previous = group['active_validated_version']
            # Old validated versions remain historical VALIDATED; only the pointer changes.
            entry['status'] = 'VALIDATED'
            entry['physician_approved_at'] = now()
            group['active_validated_version'] = version
            for name in DOWNSTREAM[module]:
                if name in manifest['modules']:
                    dependent = manifest['modules'][name]
                    active = dependent.get('active_validated_version')
                    if active:
                        d = dependent['versions'][active]
                        d['previous_status'] = d['status']
                        d['status'] = 'STALE_DEPENDENCY'
                        d['stale_dependencies'] = sorted(set(d.get('stale_dependencies', [])) | {module})
            if module == 'diagnoses':
                self._event_pointer(manifest, entry)
            self._commit(patient, manifest, 'PROMOTE', module, previous, entry)
            # Metadata status is a projection; manifest is the commit authority after a crash.
            atomic(Path(entry['metadata_path']), dict(metadata, status='VALIDATED', physician_approved_at=entry['physician_approved_at']))
            return copy.deepcopy(entry)

    def reject(self, patient, module, version):
        with file_lock(self.patient_dir(patient) / '.manifest.lock'):
            manifest = self.manifest(patient)
            group = manifest['modules'][module]
            entry = group['versions'][version]
            if entry['status'] != 'CANDIDATE':
                raise ValueError('Only pending candidates may be rejected')
            entry['status'] = 'REJECTED'
            self._commit(patient, manifest, 'REJECT', module, group['active_validated_version'], entry)
            atomic(Path(entry['metadata_path']), dict(read(entry['metadata_path']), status='REJECTED'))


def candidate_entities(entry, module):
    """Extraction-independent canonical review identities used to gate promotion."""
    result = []
    by_name={Path(f['path']).name:f['path'] for f in entry['artifacts']}
    if module=='eeg':
        for name,identity in [('eeg_exams','eeg_exam_id'),('eeg_findings','finding_id')]:
            result.extend(dict(row,entity_id=row[identity]) for row in read(by_name[name+'.json']))
        return result
    if module=='diagnoses' and 'patient_diagnoses.json' in by_name:
        for name,identity in [('patient_diagnoses','patient_diagnosis_id'),('canonical_clinical_events','event_id'),
            ('clinical_event_candidates','candidate_id'),('unmapped_diagnosis_candidates','candidate_id'),('admission_clinical_summary','admission_id')]:
            if name+'.json' in by_name:
                result.extend(dict(row,entity_id=row[identity]) for row in read(by_name[name+'.json']))
        return result
    for f in entry['artifacts']:
        name = Path(f['path']).name
        if not name.endswith('.json'):
            continue
        if module == 'diagnoses' and name.startswith(('canonical_disease_identities', 'canonical_diagnoses', 'canonical_clinical_events')):
            for row in read(f['path']):
                result.append(dict(row, entity_id=row.get('diagnosis_identity_id', row.get('event_id'))))
        elif module == 'diagnoses' and name.startswith('unmapped_diagnosis_candidates'):
            result.extend(dict(row,entity_id=row['candidate_id']) for row in read(f['path']))
        elif module == 'medications' and name.startswith('canonical_medications'):
            result.extend(dict(row, entity_id=row['drug_identity_id']) for row in read(f['path']))
        elif module == 'medications' and name.startswith('medication_exposures'):
            result.extend(dict(row, entity_id=row['exposure_id']) for row in read(f['path']) if not row.get('drug_identity_id'))
    return result
