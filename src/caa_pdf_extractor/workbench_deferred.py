"""Persistent workbench queue for explicitly deferred medication extraction issues."""
from pathlib import Path
from .workbench_store import ROOT,read,atomic,file_lock


def medication_queue(root=ROOT/'data/review'):
    path=Path(root)/'deferred_medication_regressions.json'
    return read(path) if path.exists() else []


def enqueue_medication(record,root=ROOT/'data/review'):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/'deferred_medication_regressions.json'
    with file_lock(root/'.deferred_regressions.lock'):
        items=medication_queue(root)
        existing=next((r for r in items if (r['patient_id'],r['drug'])==(record['patient_id'],record['drug'])),None)
        if existing is None:
            items.append(dict(record,status='OPEN',review_required=True));atomic(path,items)
    return existing or items[-1]
