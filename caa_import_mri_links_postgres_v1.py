#!/usr/bin/env python3
"""Import high-confidence CAA MRI/ORBIS linkage candidates into PostgreSQL.

Only status EXACT_NAME_DOB_CASE_DOB_CANDIDATE is eligible. No modifications
are made to public.case or PDF tables. Dry-run is default and rolls back.

Run in the authorized local clinical environment; private input contains PHI.
Requires psycopg2-binary. Connection: DATABASE_URL or PG* libpq environment.
"""
import argparse
import csv
import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, unquote

import psycopg2
from psycopg2 import sql

ROOT = Path(r'C:\app\pdf_extractor\caa_private_linkage')
REPORT = ROOT / 'mri_orbis_fallnr_exact_candidates_v3_PRIVATE.csv'
INVENTORY = ROOT / 'mri_identity_series_PRIVATE.csv'
GOOD = 'EXACT_NAME_DOB_CASE_DOB_CANDIDATE'
VERSION = '2026-10-28-v1'


def load_csv(path, required):
    if not path.is_file():
        raise ValueError(f'Missing private input file: {path}')
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'Unexpected columns in {path.name}; missing: {sorted(required - set(reader.fieldnames or []))}')
        rows = list(reader)
    return rows


def norm_dob(x):
    s = (x or '').strip()
    if re.fullmatch(r'\d{8}', s):
        return s
    raise ValueError('Invalid DOB in private identity inventory/candidate report')


def key(dicom_id, name, dob):
    # Using DICOM name only in memory, not in PostgreSQL; a hash is still pseudonymous.
    value = '\x1f'.join((dicom_id.strip(), name.strip(), norm_dob(dob)))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def connect():
    u = os.environ.get('DATABASE_URL')
    if not u:
        if not os.environ.get('PGDATABASE'):
            raise ValueError('Set DATABASE_URL or PGDATABASE/PGUSER and other PG* environment variables')
        return psycopg2.connect('')
    p = urlsplit(u)
    if p.scheme not in ('postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'):
        raise ValueError('Unsupported PostgreSQL URL scheme')
    if not p.hostname or not p.path.lstrip('/'):
        raise ValueError('PostgreSQL URL missing host or database')
    kwargs = {'host': p.hostname, 'dbname': unquote(p.path.lstrip('/'))}
    if p.port: kwargs['port'] = p.port
    if p.username: kwargs['user'] = unquote(p.username)
    if p.password: kwargs['password'] = unquote(p.password)
    return psycopg2.connect(**kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report', type=Path, default=REPORT)
    ap.add_argument('--inventory', type=Path, default=INVENTORY)
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()
    print('Script version:', VERSION)
    candidates = load_csv(args.report, {'dicom_patient_id','patient_name_dicom','dicom_dob','candidate_fallnr','status'})
    items = load_csv(args.inventory, {'dicom_patient_id','patient_name_dicom','birth_date_yyyymmdd',
                                     'study_uid','study_date_yyyymmdd','series_uid','group_id',
                                     'nifti_category','nifti_paths','series_description','identity_qc'})
    status_counts = Counter(r['status'] for r in candidates)
    good = [r for r in candidates if r['status'] == GOOD]
    if not good:
        raise ValueError('No eligible exact candidate rows')

    by_key = {}
    by_fall = {}
    by_pid = defaultdict(set)
    for r in good:
        h = key(r['dicom_patient_id'], r['patient_name_dicom'], r['dicom_dob'])
        fall = r['candidate_fallnr'].strip()
        if not fall or h in by_key or fall in by_fall:
            raise ValueError('Duplicate eligible MRI identity or fallnr; refusing import')
        by_key[h] = (fall, r['dicom_patient_id'].strip())
        by_fall[fall] = h
        by_pid[r['dicom_patient_id'].strip()].add(h)
    if any(len(v) > 1 for v in by_pid.values()):
        raise ValueError('Same DICOM PatientID associated with multiple eligible identities')

    excluded_identities = set()
    for r in candidates:
        if r['status'] != GOOD:
            excluded_identities.add(key(r['dicom_patient_id'], r['patient_name_dicom'], r['dicom_dob']))
    if set(by_key) & excluded_identities:
        raise ValueError('Identity appears in both accepted and rejected candidate groups')

    # Only actually converted and located image files are assigned to linked MRI studies.
    records = {}
    study_owners = {}
    for r in items:
        try:
            h = key(r['dicom_patient_id'], r['patient_name_dicom'], r['birth_date_yyyymmdd'])
        except ValueError:
            continue
        if h not in by_key:
            continue
        if r['identity_qc'].strip():
            # Avoid automatically linking DICOM groups with UID or identity QC warnings.
            continue
        study_uid = r['study_uid'].strip()
        gid = r['group_id'].strip()
        if not study_uid or not gid or not r['series_uid'].strip():
            continue
        if study_uid in study_owners and study_owners[study_uid] != h:
            raise ValueError('StudyInstanceUID occurs for multiple linked patients')
        study_owners[study_uid] = h
        category = r['nifti_category'].strip()
        paths = [x.strip() for x in r['nifti_paths'].split(' | ') if x.strip()]
        if category == 'NOT_CONVERTED' or not paths:
            continue
        for path in paths:
            record_key = (h, study_uid, gid, category, path)
            records[record_key] = (h, study_uid, r['study_date_yyyymmdd'].strip(),
                                   gid, r['series_uid'].strip(), category, path,
                                   r['series_description'].strip())
    print('Candidate status counts:', ', '.join(f'{k}={v}' for k,v in sorted(status_counts.items())))
    print('Eligible distinct MRI identities:', len(by_key))
    print('Studies with converted images and clean identity QC:', len({(r[0],r[1]) for r in records.values()}))
    print('Image path records:', len(records))
    if not records:
        raise ValueError('No converted image paths for eligible candidates. No schema changes attempted.')

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, fallnr FROM public."case" WHERE fallnr IS NOT NULL')
                case_by_fall = {str(fall).strip(): cid for cid, fall in cur.fetchall()}
                absent = set(by_fall) - set(case_by_fall)
                if absent:
                    raise ValueError(f'{len(absent)} eligible fallnr values are absent from public.case; stop')

                # Schema isolated from public and PDF tables; do not expose to raters.
                cur.execute('CREATE SCHEMA IF NOT EXISTS caa_mri_private')
                cur.execute('''CREATE TABLE IF NOT EXISTS caa_mri_private.identity_links (
                    identity_key text PRIMARY KEY,
                    case_id bigint NOT NULL REFERENCES public."case"(id),
                    fallnr text NOT NULL,
                    dicom_patient_id text NOT NULL,
                    match_method text NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE(case_id), UNIQUE(dicom_patient_id)
                )''')
                cur.execute('''CREATE TABLE IF NOT EXISTS caa_mri_private.studies (
                    study_uid text PRIMARY KEY,
                    identity_key text NOT NULL REFERENCES caa_mri_private.identity_links(identity_key),
                    study_date text,
                    UNIQUE(study_uid, identity_key)
                )''')
                cur.execute('''CREATE TABLE IF NOT EXISTS caa_mri_private.image_files (
                    identity_key text NOT NULL REFERENCES caa_mri_private.identity_links(identity_key),
                    study_uid text NOT NULL REFERENCES caa_mri_private.studies(study_uid),
                    group_id text NOT NULL,
                    series_uid text NOT NULL,
                    category text NOT NULL,
                    nifti_path text NOT NULL,
                    series_description text,
                    PRIMARY KEY(group_id, category, nifti_path)
                )''')
                # Do not overwrite prior verified assignments; conflicts cause rollback.
                new_links = 0
                for h,(fall,dicom_id) in by_key.items():
                    case_id = case_by_fall[fall]
                    cur.execute('SELECT case_id, fallnr, dicom_patient_id FROM caa_mri_private.identity_links WHERE identity_key=%s', (h,))
                    prev = cur.fetchone()
                    if prev:
                        if (str(prev[0]),prev[1],prev[2]) != (str(case_id),fall,dicom_id):
                            raise ValueError('Existing MRI identity linkage conflicts with proposed match')
                    else:
                        cur.execute('INSERT INTO caa_mri_private.identity_links (identity_key,case_id,fallnr,dicom_patient_id,match_method) VALUES (%s,%s,%s,%s,%s)',
                                    (h,case_id,fall,dicom_id,'exact_orbis_name_dob_plus_case_dob_v3'))
                        new_links += 1
                new_studies = 0
                for h,study_uid,study_date,*_ in sorted(set(records.values())):
                    cur.execute('SELECT identity_key,study_date FROM caa_mri_private.studies WHERE study_uid=%s',(study_uid,))
                    prev = cur.fetchone()
                    if prev:
                        if prev[0] != h or (prev[1] and study_date and prev[1] != study_date):
                            raise ValueError('Existing MRI study linkage or date conflicts')
                    else:
                        cur.execute('INSERT INTO caa_mri_private.studies(study_uid,identity_key,study_date) VALUES(%s,%s,%s)',
                                    (study_uid,h,study_date or None))
                        new_studies += 1
                new_files = 0
                for h,study_uid,study_date,gid,series_uid,category,path,description in records.values():
                    cur.execute('SELECT identity_key,study_uid,series_uid FROM caa_mri_private.image_files WHERE group_id=%s AND category=%s AND nifti_path=%s',
                                (gid,category,path))
                    prev = cur.fetchone()
                    if prev:
                        if prev != (h,study_uid,series_uid):
                            raise ValueError('Existing image path assigned to a different patient/study/series')
                    else:
                        cur.execute('INSERT INTO caa_mri_private.image_files(identity_key,study_uid,group_id,series_uid,category,nifti_path,series_description) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                                    (h,study_uid,gid,series_uid,category,path,description))
                        new_files += 1
                print('Would add: links=',new_links,' studies=',new_studies,' image_files=',new_files)
                if not args.commit:
                    conn.rollback()
                    print('DRY RUN: transaction rolled back; nothing written.')
                else:
                    print('COMMITTED: only caa_mri_private schema modified; public.case and PDFs unchanged.')
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # Never print driver exceptions with passwords / patient data.
        if isinstance(e, (psycopg2.Error,)):
            print('STOPPED safely: PostgreSQL operation failed. Review PostgreSQL server logs locally; no commit completed.', file=sys.stderr)
        else:
            print('STOPPED safely:', str(e), file=sys.stderr)
        sys.exit(1)
