"""Immutable module snapshots, integrity-checked selection and dependency invalidation.

VALIDATED means the module's declared validation passed, never physician approval.
Legacy artifacts retain their original review status and are not promoted by import.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

DEPENDENCIES = {
    'AF': ['diagnoses'],
    'CHA2DS2_VA': ['diagnoses', 'AF', 'demographics', 'events'],
    'ORBIT': ['labs', 'medications', 'bleeding_history', 'AF', 'AF_index'],
    'OAC_decision_analysis': ['medications', 'AF', 'diagnoses', 'CAA', 'decision_extraction'],
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='') as f:
        f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def invalidate(manifest, changed):
    """Return a new manifest; never mutate previous manifests or artifact bytes."""
    result = copy.deepcopy(manifest)
    affected = set(changed)
    while True:
        expanded = affected | {m for m, deps in DEPENDENCIES.items() if affected.intersection(deps)}
        if expanded == affected:
            break
        affected = expanded
    for name, entry in result.items():
        if name in affected - set(changed):
            entry['previous_status'] = entry['status']
            entry['status'] = 'STALE_DEPENDENCY'
            entry['stale_dependencies'] = sorted(affected.intersection(DEPENDENCIES.get(name, [])))
    return result


class ModuleStore:
    """Each run gets a new root. Any module can publish without deleting siblings."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)

    def publish(self, patient_id, module, version, artifacts, source_hash, *,
                status='VALIDATED', dependencies=None, validation=None):
        folder = self.root / 'patient_artifacts' / str(patient_id)
        paths = []
        for name, payload in artifacts.items():
            if Path(name).name != name:
                raise ValueError('Artifact names must be basenames')
            path = folder / name
            write_new(path, payload)
            paths.append(dict(path=str(path), sha256=sha(path)))
        return dict(module=module, version=version, status=status,
                    artifact=paths[0]['path'], artifacts=paths, source_hash=source_hash,
                    created_at=now(), dependencies=dependencies or [], validation=validation or {})

    def save_manifest(self, patient_id, manifest):
        write_new(self.root / 'patient_artifacts' / str(patient_id) / 'module_manifest.json', manifest)

    def preserve(self, entry):
        """Reference an existing snapshot without copying or modifying its files."""
        result = copy.deepcopy(entry)
        for artifact in result.get('artifacts', []):
            if sha(artifact['path']) != artifact['sha256']:
                raise ValueError('Preserved artifact integrity failure')
        return result


def select_latest_validated(manifests, module):
    """Reject stale, failed, missing and modified artifacts before timestamp selection.

    A later snapshot supersedes the same artifact in an earlier manifest. Thus an
    invalidation cannot be bypassed by selecting its older VALIDATED occurrence.
    """
    by_artifact = {}
    for manifest in manifests:  # caller supplies chronological snapshots
        if module in manifest:
            entry = manifest[module]
            by_artifact[entry['artifact']] = entry
    good = []
    for entry in by_artifact.values():
        if entry.get('status') != 'VALIDATED':
            continue
        files = entry.get('artifacts', [])
        if files and all(Path(f['path']).is_file() and sha(f['path']) == f['sha256'] for f in files):
            good.append(entry)
    return max(good, key=lambda e: e['created_at']) if good else None
