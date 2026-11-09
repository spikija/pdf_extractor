#!/usr/bin/env python3
"""Read-only exporter of a coordinator-only real CAA MRI manifest.

Requires psycopg2-binary, and optionally pydicom for header verification.
Outputs are SENSITIVE: contain fallnr and clinical image location/provenance.
No assignments are granted. No PostgreSQL writes. Existing exam IDs persist.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit, unquote

try:
    import psycopg2
except ImportError:
    sys.exit('Install into your active environment: python -m pip install psycopg2-binary')

VERSION = '2026-10-29-v1'
PRIVATE = Path(r'C:\app\pdf_extractor\caa_private_linkage')
INV = PRIVATE / 'mri_identity_series_PRIVATE.csv'
EXAM_MAP = PRIVATE / 'mri_exam_key_PRIVATE.csv'
FIELDS = ['fallnr','mri_exam_id','exam_order','study_date','category','series_description',
          'group_id','nifti_relative_path','dicom_relative_path','file_exists',
          'dicom_header_qc','match_method','sha256']


def connect():
    url = os.getenv('DATABASE_URL')
    if not url:
        if not os.getenv('PGDATABASE'):
            raise ValueError('Set DATABASE_URL or PostgreSQL PG* environment variables')
        return psycopg2.connect('')
    p = urlsplit(url)
    if p.scheme not in ('postgresql','postgres','postgresql+psycopg','postgresql+psycopg2'):
        raise ValueError('Unsupported DATABASE_URL protocol')
    kw = {'host':p.hostname, 'dbname':unquote(p.path.lstrip('/'))}
    if p.port: kw['port'] = p.port
    if p.username: kw['user'] = unquote(p.username)
    if p.password: kw['password'] = unquote(p.password)
    return psycopg2.connect(**kw)


def rel_win(path, root):
    """Convert exact original Windows drive-root layout to portable relative path."""
    if not path:
        return ''
    p = PureWindowsPath(path)
    r = PureWindowsPath(root)
    if not p.is_absolute() or not r.is_absolute():
        raise ValueError('Expected absolute Windows source paths')
    parts = p.parts
    rp = r.parts
    if len(parts) <= len(rp) or tuple(x.casefold() for x in parts[:len(rp)]) != tuple(x.casefold() for x in rp):
        raise ValueError('Source path is not beneath the configured root')
    tail = parts[len(rp):]
    if any(x in ('', '.', '..') for x in tail):
        raise ValueError('Unsafe path segment')
    return '/'.join(tail)


def read_inventory(path):
    if not path.exists():
        raise ValueError('Private MRI identity inventory missing; regenerate FULL inventory first')
    d = defaultdict(list)
    with path.open(encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        required = {'group_id','study_uid','series_uid','nifti_paths','nifti_category',
                    'dicom_representative_path','identity_qc','dicom_patient_id',
                    'patient_name_dicom','birth_date_yyyymmdd'}
        if not required.issubset(rd.fieldnames or []):
            raise ValueError('Private inventory columns differ from expected schema')
        for row in rd:
            for candidate in row['nifti_paths'].split(' | '):
                candidate = candidate.strip()
                if candidate:
                    d[(row['group_id'],row['study_uid'],row['series_uid'],row['nifti_category'],
                       candidate.casefold())].append(row)
    return d


def read_exam_map(path):
    if not path.exists():
        return {}
    with path.open(encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        if not {'study_uid','mri_exam_id'}.issubset(rd.fieldnames or []):
            raise ValueError('Existing exam key has unknown columns; do not overwrite')
        pairs = {}
        ids = set()
        for row in rd:
            uid, eid = row['study_uid'].strip(), row['mri_exam_id'].strip()
            if not uid or not eid or uid in pairs or eid in ids:
                raise ValueError('Existing persistent exam key has duplicates or blanks')
            pairs[uid] = eid
            ids.add(eid)
        return pairs


def atomic_csv(path, rows, fields):
    tmp = path.with_name(path.name + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--private-dir', type=Path, default=PRIVATE)
    ap.add_argument('--data-root', default='E:\\', help='Original root of the indexed Windows image paths (e.g. E:\\)')
    ap.add_argument('--verify-dicom', action='store_true', help='Read local representative DICOM headers and check linkage/UIDs')
    ap.add_argument('--hash-files', action='store_true', help='SHA256 of every NIfTI; may take substantial I/O')
    ap.add_argument('--replace-exam-key', action='store_true', help='Never used: existing keys are always preserved')
    a = ap.parse_args()
    if not a.private_dir.is_dir():
        raise ValueError('Private directory must exist')
    inventory = read_inventory(a.private_dir / INV.name)
    keypath = a.private_dir / EXAM_MAP.name
    oldkey = read_exam_map(keypath)
    try:
        import pydicom
    except ImportError:
        pydicom = None
    if a.verify_dicom and pydicom is None:
        raise ValueError('Install pydicom for --verify-dicom')
    conn = connect()
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute('''SELECT l.fallnr, l.identity_key, l.dicom_patient_id, l.match_method,
                                  s.study_uid, s.study_date, f.group_id, f.series_uid,
                                  f.category, f.nifti_path, COALESCE(f.series_description,'')
                           FROM caa_mri_private.identity_links l
                           JOIN caa_mri_private.studies s ON s.identity_key = l.identity_key
                           JOIN caa_mri_private.image_files f ON f.study_uid = s.study_uid
                             AND f.identity_key = l.identity_key
                           ORDER BY l.fallnr, s.study_date NULLS LAST, s.study_uid, f.category, f.group_id, f.nifti_path''')
            dbrows = cur.fetchall()
    finally:
        conn.close()
    if not dbrows:
        raise ValueError('PostgreSQL returned no linked image rows')
    subjects = defaultdict(set)
    uid_identity = {}
    for fall, identity, pid, method, uid, date, *_ in dbrows:
        subjects[fall].add(uid)
        prev = uid_identity.setdefault(uid,(fall,identity))
        if prev != (fall, identity):
            raise ValueError('A study UID is associated with more than one case/identity')
    generated = dict(oldkey)
    used_ids = set(generated.values())
    for uid in sorted(uid_identity):
        if uid not in generated:
            eid = 'MRI-' + uuid.uuid4().hex[:16].upper()
            while eid in used_ids:
                eid = 'MRI-' + uuid.uuid4().hex[:16].upper()
            generated[uid] = eid
            used_ids.add(eid)
    root = Path(a.data_root)
    order = {}
    date_by = {}
    for fall, identity, pid, method, uid, date, *_ in dbrows:
        date_by[(fall,uid)] = date or ''
    for fall, ids in subjects.items():
        for i, uid in enumerate(sorted(ids,key=lambda u:(date_by[(fall,u)] or '99999999', u)), start=1):
            order[(fall,uid)] = i

    counts = Counter()
    output = []
    failures = []
    for fall, identity, pid, method, uid, date, gid, series, cat, nifti, desc in dbrows:
        checks = []
        dicom = ''
        try:
            nrel = rel_win(nifti, a.data_root)
        except ValueError:
            nrel = ''
            checks.append('INVALID_NIFTI_ROOT')
        hits = inventory.get((gid,uid,series,cat,nifti.casefold()),[])
        if len(hits) != 1:
            checks.append('DICOM_SOURCE_MISSING_OR_AMBIGUOUS')
        else:
            info = hits[0]
            if info['identity_qc'].strip():
                checks.append('INVENTORY_IDENTITY_QC_FLAG')
            if info['dicom_patient_id'].strip() != pid:
                checks.append('DICOM_PATIENT_ID_CONFLICT')
            try:
                dicom = rel_win(info['dicom_representative_path'],a.data_root)
            except ValueError:
                checks.append('INVALID_DICOM_ROOT')
                dicom = ''
        if nrel:
            nfile = root.joinpath(*nrel.split('/'))
            if not nfile.is_file():
                checks.append('NIFTI_NOT_FOUND')
        else:
            nfile = None
        if dicom:
            dfile = root.joinpath(*dicom.split('/'))
            if not dfile.is_file():
                checks.append('DICOM_NOT_FOUND')
            elif a.verify_dicom and len(hits)==1:
                try:
                    ds = pydicom.dcmread(str(dfile), stop_before_pixels=True,
                         specific_tags=['PatientID','PatientName','PatientBirthDate','StudyInstanceUID','SeriesInstanceUID'])
                    if str(ds.get('PatientID','')).strip() != pid:
                        checks.append('HEADER_PATIENT_ID_CONFLICT')
                    if str(ds.get('StudyInstanceUID','')).strip() != uid:
                        checks.append('HEADER_STUDY_UID_CONFLICT')
                    if str(ds.get('SeriesInstanceUID','')).strip() != series:
                        checks.append('HEADER_SERIES_UID_CONFLICT')
                    if str(ds.get('PatientBirthDate','')).strip() != info['birth_date_yyyymmdd'].strip() or str(ds.get('PatientName','')).strip() != info['patient_name_dicom'].strip():
                        checks.append('HEADER_IDENTITY_MISMATCH_WITH_INVENTORY')
                except Exception:
                    checks.append('DICOM_READ_ERROR')
        else:
            dfile = None
        if not a.verify_dicom:
            checks.append('HEADER_NOT_RECHECKED')
        sha = digest(nfile) if a.hash_files and nfile is not None and nfile.is_file() else ''
        rec = dict(fallnr=str(fall),mri_exam_id=generated[uid], exam_order=order[(fall,uid)],
                   study_date=date or '',category=cat, series_description=desc, group_id=gid,
                   nifti_relative_path=nrel,dicom_relative_path=dicom,
                   file_exists='YES' if nfile is not None and nfile.is_file() else 'NO',
                   dicom_header_qc=';'.join(checks) if checks else 'OK',
                   match_method=method,sha256=sha)
        output.append(rec)
        counts.update(checks)
        if checks and checks != ['HEADER_NOT_RECHECKED']:
            failures.append(dict(mri_exam_id=generated[uid],group_id=gid,issues=';'.join(checks)))

    # Write exam mapping before manifest; never overwrite existing mappings with changed IDs.
    if generated != oldkey:
        atomic_csv(keypath, ({'study_uid':u,'mri_exam_id':generated[u]} for u in sorted(generated)),['study_uid','mri_exam_id'])
    outpath = a.private_dir / 'caa_coordinator_mri_manifest_PRIVATE.csv'
    atomic_csv(outpath,output,FIELDS)
    qcpath = a.private_dir / 'caa_coordinator_mri_manifest_QC_PRIVATE.csv'
    atomic_csv(qcpath,failures,['mri_exam_id','group_id','issues'])
    summary = {'version':VERSION, 'generated_utc':datetime.now(timezone.utc).isoformat(),
        'coordinator_only':True,'identity_confirmation':'automated_linkage_not_manually_confirmed',
        'assignments_approved':False,'unique_fallnr':len(subjects),'unique_examinations':len(uid_identity),
        'image_records':len(output),'existing_exam_keys_reused':len(set(oldkey)&set(uid_identity)),
        'new_exam_keys':len(set(uid_identity)-set(oldkey)), 'rows_with_qc_issues':len(failures),
        'qc_counts':dict(counts),'dicom_header_checked':a.verify_dicom,'nifti_sha256_computed':a.hash_files}
    with (a.private_dir / 'caa_coordinator_mri_manifest_summary.json').open('w',encoding='utf-8') as f:
        json.dump(summary,f,indent=2)
    print('CAA MRI coordinator manifest,',VERSION)
    print('Cases:',len(subjects),'| studies:',len(uid_identity),'| image files:',len(output))
    print('Reused exam IDs:',summary['existing_exam_keys_reused'],'| new exam IDs:',summary['new_exam_keys'])
    print('QC rows:',len(failures),'| counts:',dict(counts))
    print('Private manifest:',outpath)
    print('Persistent private exam key:',keypath)
    print('IMPORTANT: coordinator-only local preview, NOT an approved rater distribution manifest.')
    print('Check whether Codex has already generated exam IDs; if so, use that mapping before distribution.')

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        if isinstance(e,psycopg2.Error):
            print('Stopped: PostgreSQL read failed; no database writes.',file=sys.stderr)
        else:
            print('Stopped:',str(e),file=sys.stderr)
        sys.exit(1)
