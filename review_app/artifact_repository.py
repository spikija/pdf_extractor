"""Read-only manifest discovery, integrity checks and lazy artifact loading."""
import hashlib
import json
from pathlib import Path
from functools import lru_cache

from review_app.config import ARTIFACT_ROOTS, STABLE_ROOT


@lru_cache(maxsize=4096)
def _sha(path, mtime, size, ctime):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha(path):
    p = Path(path)
    s = p.stat()
    return _sha(str(p), s.st_mtime_ns, s.st_size, s.st_ctime_ns)


@lru_cache(maxsize=128)
def _read(path, mtime, size):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_json(path):
    p = Path(path)
    s = p.stat()
    return _read(str(p), s.st_mtime_ns, s.st_size)


def discover(roots=ARTIFACT_ROOTS):
    patients = {}
    for root in roots:
        if root.exists():
            for path in root.rglob('module_manifest.json'):
                patients.setdefault(path.parent.name, []).append(path)
    return patients


def select_modules(paths):
    stable = []
    debug = []
    for path in paths:
        try:
            value = read_json(path)
            if value.get('storage') == 'STABLE':
                stable.append((path, value))
            else:
                debug.append(path)
        except (OSError, ValueError, AttributeError):
            if Path(path).resolve().is_relative_to(STABLE_ROOT.resolve()):
                return {}, {}, ['Stable manifest is unreadable; debug fallback blocked']
            debug.append(path)
    if stable:
        from caa_pdf_extractor.workbench_store import active_entries
        # Stable pointers are authoritative, even for missing/corrupt/stale modules.
        # Never resurrect debug data behind a physician's active-version decision.
        path, manifest = sorted(stable, key=lambda p: str(p[0]))[-1]
        selected, latest, errors = active_entries(manifest)
        for module, entry in latest.items():
            reason = manifest['modules'].get(module,{}).get('candidate_qa_holds',{}).get(entry['version'])
            if reason:entry['qa_hold']=reason
        return selected, latest, errors
    paths = debug
    snapshots, errors = [], []
    for path in paths:
        try:
            manifest = read_json(path)
            snapshots.append((max((e.get('created_at', '') for e in manifest.values()), default=''), str(path), manifest))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            errors.append(f'Manifest unavailable: {path.name}: {type(exc).__name__}')
    candidates = {}
    for _, _, manifest in sorted(snapshots):
        for module, entry in manifest.items():
            candidates.setdefault(module, {})[entry.get('artifact', '')] = entry
    selected, latest = {}, {}
    for module, entries in candidates.items():
        ordered = sorted(entries.values(), key=lambda e: e.get('created_at', ''), reverse=True)
        latest[module] = ordered[0]
        for entry in ordered:
            if entry.get('status') != 'VALIDATED':
                continue
            try:
                files = entry.get('artifacts', [])
                if not files or not all(sha(f['path']) == f['sha256'] for f in files):
                    raise ValueError('Checksum mismatch')
                selected[module] = entry
                break
            except (OSError, ValueError, KeyError):
                errors.append(f'{module} {entry.get("version")}: missing or changed artifact')
    return selected, latest, errors


def module_hash(entry):
    # Actual bytes, across all module files, rather than an upstream source hash.
    files = sorted(entry['artifacts'], key=lambda f: f['path'])
    value = ''.join(sha(f['path']) for f in files)
    if entry.get('assertion_safety'):
        value += json.dumps(entry['assertion_safety'], sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


def layer(entry, prefix):
    for f in entry.get('artifacts', []):
        p = Path(f['path'])
        if p.name.startswith(prefix) and p.suffix == '.json':
            return read_json(p)
    return []
