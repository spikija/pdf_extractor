"""Append-only authoritative audit, with atomic JSON projections. No DB imports."""
import copy
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from review_app.config import REVIEW_ROOT


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.review-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ReviewRepository:
    def __init__(self, root=REVIEW_ROOT):
        self.root = Path(root).resolve()

    def _file(self, patient, module):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', str(patient)):
            raise ValueError('Invalid patient ID')
        if module not in ('diagnoses', 'medications'):
            raise ValueError('Unsupported review module')
        p = self.root / str(patient) / f'{module}_review.json'
        if not p.resolve().is_relative_to(self.root):
            raise ValueError('Review path escapes review directory')
        return p

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.review.lock').open('a+b') as lock:
            lock.seek(0)
            if lock.read(1) == b'':
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def load(self, patient, module):
        path = self._file(patient, module)
        records = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        # Replay committed actions if a crash interrupted projection updates.
        audit = self.root / 'review_audit.jsonl'
        if audit.exists():
            with audit.open(encoding='utf-8') as stream:
                for line in stream:
                    event = json.loads(line)
                    if event['patient_id'] == str(patient) and event['module'] == module:
                        records[event['entity_id']] = event['new_value']
        return records

    def save(self, patient, module, entity_id, action, raw, physician, version, source_hash, *, suggest=False):
        path = self._file(patient, module)
        allowed = {'ACCEPT', 'EDIT', 'REJECT', 'RESET', 'UNRESOLVED' if module == 'medications' else 'UNCERTAIN'}
        if action not in allowed:
            raise ValueError('Unsupported review action')
        if suggest and (module != 'medications' or action != 'EDIT'):
            raise ValueError('Dictionary proposals require a medication edit')
        with self._lock():
            records = self.load(patient, module)
            old = records.get(entity_id)
            now = datetime.now(timezone.utc).isoformat()
            record = dict(review_id=str(uuid.uuid4()), patient_id=str(patient), module=module,
                          entity_id=entity_id, action=action, raw_value=copy.deepcopy(raw),
                          physician_value=copy.deepcopy(physician), reviewer='PHYSICIAN',
                          created_at=old['created_at'] if old else now, updated_at=now,
                          source_artifact_version=version, source_artifact_hash=source_hash,
                          dictionary_suggestion=suggest)
            event = dict(timestamp=now, patient_id=str(patient), module=module,
                         entity_id=entity_id, action=action, previous_value=old, new_value=record)
            # Atomically replace the complete log so refresh/crash cannot leave a partial line.
            audit = self.root / 'review_audit.jsonl'
            previous = audit.read_text(encoding='utf-8') if audit.exists() else ''
            self._atomic_text(audit, previous + json.dumps(event, ensure_ascii=False) + '\n')
            records[entity_id] = record
            atomic_json(path, records)
            status = path.parent / 'patient_review_status.json'
            atomic_json(status, dict(patient_id=str(patient), status='IN_REVIEW', updated_at=now,
                                     note='Live status recomputed against current artifact hashes'))
            if suggest:
                proposals = self.root / 'medication_dictionary_suggestions.jsonl'
                previous = proposals.read_text(encoding='utf-8') if proposals.exists() else ''
                self._atomic_text(proposals, previous + json.dumps(record, ensure_ascii=False) + '\n')
            return record

    @staticmethod
    def _atomic_text(path, value):
        fd, name = tempfile.mkstemp(prefix='.review-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write(value)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def overlay(raw, record, current_hash):
    result = copy.deepcopy(raw)
    status = 'NOT_REVIEWED'
    if record and record['action'] != 'RESET':
        status = record['action'] if record['source_artifact_hash'] == current_hash else 'STALE_REVIEW'
        if status == 'EDIT':
            result.update(record['physician_value'])
    result['physician_status'] = status
    return result
