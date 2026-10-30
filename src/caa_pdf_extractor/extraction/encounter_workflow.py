"""Encounter/mRS dry-run artifacts only. No clinical commit endpoint."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from ..database import get_engine
from ..preparation import build_patient_bundle
from .encounter_mrs import create_artifact, verify_artifact


def extract_patient(patient_id, *, case_id, debug_root='data/debug/encounter_mrs'):
    with get_engine().connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        ids = connection.execute(text('SELECT id FROM public."case" WHERE fallnr=:pid AND deleted IS NOT TRUE'), {'pid': patient_id}).scalars().all()
        if ids != [case_id]:
            raise ValueError('PATIENT_CASE_IDENTITY_MISMATCH')
    bundle = build_patient_bundle(patient_id)
    artifact = create_artifact(bundle, case_id)
    verify_artifact(artifact, bundle, case_id)
    folder = Path(debug_root) / patient_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('encounter_mrs_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(artifact, stream, ensure_ascii=False, indent=2)
    return {'artifact': str(path), 'encounters': len(artifact['output']['encounters']), 'Granite_calls': 0, 'clinical_writes': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patient', required=True)
    parser.add_argument('--case-id', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--debug-root', default='data/debug/encounter_mrs')
    args = parser.parse_args()
    print(json.dumps(extract_patient(args.patient, case_id=args.case_id, debug_root=args.debug_root)))


if __name__ == '__main__':
    main()
