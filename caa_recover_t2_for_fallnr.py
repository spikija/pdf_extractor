#!/usr/bin/env python3
r"""Audit/optionally recover missing conventional T2 conversions for ONE linked fallnr.

Defaults to read-only preview. With --convert, writes new NIfTI only under
E:\caa_nifti_t2_display\T2\<group_id>, preserving all source DICOM and existing images.
It does NOT update PostgreSQL or the coordinator manifest: reindex after conversion.

Run locally only in an approved clinical computing environment.
"""
import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote

try:
    import psycopg2
except ImportError:
    sys.exit('Requires psycopg2-binary in activated Python environment')

VERSION = '2026-10-29-v1'
EXCLUSION = re.compile(r'flair|dark.?fluid|tirm|stir|dti|dwi|diff|adc|trace|swi|swip|haem|hemo|t2\s*\*|t2star|\bffe\b|\bgre\b|\bt1\b|t1w|spine|cervical|lumbar|\blws\b|\bhws\b|thorax|abdomen|carotid|mobi|dixon|\bmip\b|projection|posdisp|scout|localiz|survey|\bpd_tse_fs\b|^pd_tse_fs|\bwarp\b|nerv|\bssh\b|spair|\bspir\b', re.I)
T2_RE = re.compile(r'\b(?:t2w?_?tse|t2[_ ]tse|t2[_ ]ax|t2[_ ]cor|t2[_ ]sag|t2[_ ]spc|t2[_ ]space|ax t2 frfse|cor t2 frfse|t2w_4mm|dual[_ /]?tse|dual/tse|tse2/sag|pd\+t2_tse|t2w_drive|t2w_mv_xd|e?t2w?_?tse|e?t2w_4mm|t2_tse_tra)(?:\b|_)', re.I)


def classify(description, subtype, modality, n):
    if modality != 'MR' or n < 12:
        return False
    desc = (description or '').strip().lower()
    st = (subtype or '').lower()
    if not desc or 'unknown' == st or 'derived' in st or 'secondary' in st:
        return False
    if EXCLUSION.search(desc):
        return False
    # Philips compressed sensing prefixes may be joined: csT2W_TSE tra 4mm.
    desc = re.sub(r'^cs\s*(?=t2w)', '', desc, count=1)
    return bool(T2_RE.search(desc))


def get_db_url():
    if os.environ.get('DATABASE_URL'):
        return os.environ['DATABASE_URL']
    dot_env = Path(r'C:\app\pdf_extractor\.env')
    if dot_env.is_file():
        for line in dot_env.read_text(encoding='utf-8-sig').splitlines():
            if line.lstrip().startswith('DATABASE_URL='):
                return line.split('=', 1)[1].strip().strip('"\'')
    return ''


def connect():
    url = get_db_url()
    if not url:
        if not os.getenv('PGDATABASE'):
            raise ValueError('Set DATABASE_URL or PGDATABASE/PGUSER')
        return psycopg2.connect('')
    parsed = urlsplit(url)
    if parsed.scheme not in ('postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'):
        raise ValueError('Unsupported DATABASE_URL scheme')
    args = {'host': parsed.hostname, 'dbname': unquote(parsed.path.lstrip('/'))}
    if parsed.port: args['port'] = parsed.port
    if parsed.username: args['user'] = unquote(parsed.username)
    if parsed.password: args['password'] = unquote(parsed.password)
    return psycopg2.connect(**args)


def group_id(row):
    study, series, subtype, echo, geometry = row[:5]
    if any(x is None for x in (study, series, subtype, echo, geometry)):
        return None
    return hashlib.sha256('\x1f'.join([study, series, subtype, echo, geometry]).encode()).hexdigest()[:20]


def run_conversion(db, row, gid, a):
    study, series, subtype, echo, geometry, n, mod, desc = row
    out = a.destination / 'T2' / gid
    if list(out.glob('*.nii.gz')) or list(out.glob('*.nii')):
        return 'SKIP_EXISTING'
    images = [Path(p) for (p,) in db.execute('''SELECT path FROM instances WHERE study=? AND series=? AND subtype=? AND echo=? AND geometry=? ORDER BY path''',
                                            (study, series, subtype, echo, geometry))]
    if len(images) != n or not all(p.is_file() for p in images):
        return 'REVIEW_SOURCE_DICOM_MISSING'
    if not a.dcm2niix.is_file():
        return 'MISSING_DCM2NIIX'
    required = sum(f.stat().st_size for f in images) + int(a.reserve_gb * 1024**3)
    if shutil.disk_usage(a.destination).free < required:
        return 'INSUFFICIENT_FREE_SPACE'
    stage = a.destination / '_staging_t2' / gid
    if stage.exists():
        return 'STALE_STAGE_NEEDS_MANUAL_REVIEW'
    stage.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    try:
        for idx, p in enumerate(images):
            shutil.copyfile(p, stage / f'I{idx:06}.dcm')
        cmd = [str(a.dcm2niix), '-d', '0', '-z', 'y', '-b', 'y', '-ba', 'y',
               '-p', 'n', '-m', 'n', '-w', '0', '-f', 'image_%s_%e',
               '-o', str(out), str(stage)]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
        log = (proc.stdout or '') + '\n' + (proc.stderr or '')
        (out / 'conversion.log').write_text(log, encoding='utf-8')
        files = list(out.glob('*.nii.gz')) + list(out.glob('*.nii'))
        if not files or proc.returncode:
            return 'REVIEW_CONVERSION_FAILURE'
        if any(t in log.lower() for t in ['error:', 'missing images', 'interslice distance varies', 'unable to rotate']):
            return 'REVIEW_GEOMETRY_WARNING'
        return f'CONVERTED_UNVALIDATED({len(files)} NIfTI)'
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fallnr', required=True)
    p.add_argument('--inventory', type=Path, default=Path(r'E:\caa_nifti_batch\inventory.sqlite'))
    p.add_argument('--destination', type=Path, default=Path(r'E:\caa_nifti_t2_display'))
    p.add_argument('--dcm2niix', type=Path, default=Path(r'C:\app\dcm2niix\dcm2niix.exe'))
    p.add_argument('--series-name', default='', help='Optional case-insensitive exact substring filter, e.g. csT2W_TSE tra 4mm')
    p.add_argument('--convert', action='store_true', help='Explicit opt-in to write NEW T2 NIfTI files only')
    p.add_argument('--limit', type=int, default=1, help='Maximum new conversions with --convert, default 1')
    p.add_argument('--reserve-gb', type=float, default=10)
    a = p.parse_args()
    if not a.inventory.is_file():
        raise ValueError('Inventory SQLite does not exist')
    if a.limit < 1:
        raise ValueError('--limit must be >= 1')
    print('CAA targeted T2 recovery', VERSION)
    conn = connect()
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.study_uid, s.study_date FROM caa_mri_private.identity_links l
                           JOIN caa_mri_private.studies s ON s.identity_key=l.identity_key
                           WHERE l.fallnr=%s ORDER BY s.study_date, s.study_uid''', (a.fallnr,))
            studies = cur.fetchall()
            cur.execute('''SELECT f.category, COUNT(*) FROM caa_mri_private.identity_links l
                           JOIN caa_mri_private.image_files f ON f.identity_key=l.identity_key
                           WHERE l.fallnr=%s GROUP BY f.category ORDER BY f.category''', (a.fallnr,))
            cats = cur.fetchall()
    finally:
        conn.close()
    if not studies:
        raise ValueError('No MRI identity/studies linked to that fallnr; no conversions attempted')
    print('Linked MRI examinations:', len(studies))
    print('Already indexed NIfTI records by category:', ', '.join(f'{cat}={n}' for cat, n in cats))
    with sqlite3.connect(f'{a.inventory.resolve().as_uri()}?mode=ro', uri=True) as inv:
        # Database is read-only; source archive remains unchanged.
        all_rows = []
        for idx, (uid, date) in enumerate(studies, 1):
            rows = inv.execute('''SELECT study,series,subtype,echo,geometry,COUNT(*),MIN(modality),MIN(description)
                                  FROM instances WHERE study=? GROUP BY study,series,subtype,echo,geometry''', (uid,)).fetchall()
            print(f'Study #{idx}: {len(rows)} DICOM acquisition groups in source inventory')
            for row in rows:
                if not classify(row[7], row[2], row[6], row[5]):
                    continue
                if a.series_name and a.series_name.casefold() not in (row[7] or '').casefold():
                    continue
                gid = group_id(row)
                if not gid:
                    continue
                dst = a.destination / 'T2' / gid
                existing = list(dst.glob('*.nii.gz')) + list(dst.glob('*.nii'))
                all_rows.append((idx, row, gid, existing))
        all_rows.sort(key=lambda x: (-x[1][5], x[0], x[2]))
        print('Conventional T2 candidate groups matching filter:', len(all_rows))
        converted = 0
        for idx, row, gid, existing in all_rows:
            tag = 'ALREADY_CONVERTED' if existing else 'MISSING_CONVERSION'
            print(f'  Study #{idx} | {tag} | slices={row[5]} | {row[7]} | group={gid} | NIfTI={len(existing)}')
            if a.convert and not existing and converted < a.limit:
                outcome = run_conversion(inv, row, gid, a)
                print('    CONVERSION RESULT:', outcome)
                if outcome.startswith('CONVERTED_'):
                    converted += 1
        if not a.convert:
            print('PREVIEW ONLY: no data changed. Run with --convert to recover matching T2 groups.')
        else:
            print('New T2 groups successfully converted:', converted)
            print('Next: rebuild private MRI identity inventory, rerun PostgreSQL MRI importer, re-export coordinator manifest.')


if __name__ == '__main__':
    try:
        main()
    except psycopg2.Error:
        sys.exit('STOPPED: PostgreSQL query failed (credentials are not printed).')
    except Exception as err:
        sys.exit('STOPPED: ' + str(err))
