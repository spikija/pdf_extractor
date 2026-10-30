from pathlib import Path
from review_app.artifact_repository import read_json


def legacy_folder(latest):
    for entry in latest.values():
        if entry.get('source_context_dir'):
            return Path(entry['source_context_dir'])
        p = Path(entry.get('artifact', '')).parent
        if (p / 'source_bundle.json').is_file():
            return p
    return None


def patient_context(latest):
    folder = legacy_folder(latest)
    def load(name, default):
        p = folder / name if folder else None
        return read_json(p) if p and p.is_file() else default
    summary = load('patient_summary.json', {})
    return {'documents': load('source_bundle.json', {}).get('documents', []),
            'encounters': load('encounters.json', []),
            'demographics': summary.get('demographics', {}).get('update_fields', {})}
