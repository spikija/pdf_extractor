#!/usr/bin/env python3
"""Import missing Retool case CSV rows into PostgreSQL public.case, keyed by fallnr.

Default: read-only preview. --commit performs INSERTs, never UPDATE/DELETE.
Connection via DATABASE_URL or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (libpq).
Dependency: pip install psycopg2-binary
"""
import argparse
import csv
import os
import re
import sys
import traceback
from urllib.parse import urlsplit, unquote
from collections import Counter
from pathlib import Path

try:
    import psycopg2
    from psycopg2 import sql
except ImportError:
    sys.exit('Missing PostgreSQL driver. Run: py -m pip install psycopg2-binary')

SCRIPT_VERSION = '2026-10-28-v2'
DEFAULT_CSV = r'C:\app\pdf_extractor\data\old_retool_db\case.csv'


def read_csv(path):
    raw = path.read_bytes()
    text = None
    for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            pass
    if text is None:
        raise ValueError('CSV encoding is not supported')
    first = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(first, delimiters=',;\t')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ','
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise ValueError('CSV is empty or has no data rows')
    headers = [h.strip().lstrip('\ufeff') if h else '' for h in rows[0].keys()]
    if any(not h for h in headers) or len({h.casefold() for h in headers}) != len(headers):
        raise ValueError('CSV contains empty or duplicate header names')
    # Strip BOM/whitespace from header names while keeping case-insensitive matching.
    normalized = []
    for raw_row in rows:
        if None in raw_row:
            raise ValueError('Row with more fields than CSV header encountered')
        normalized.append({h: (raw_row.get(orig) or '').strip() for h, orig in zip(headers, raw_row.keys())})
    return normalized, headers, delimiter


def norm_key(value, db_type):
    value = str(value or '').strip()
    if not value:
        return ''
    if db_type in ('smallint', 'integer', 'bigint', 'numeric'):
        if re.fullmatch(r'[0-9]+\.0+', value):
            value = value.split('.')[0]
        if not re.fullmatch(r'[0-9]+', value):
            raise ValueError('fallnr must be a whole-number string for this database column')
        return str(int(value))
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', default=DEFAULT_CSV)
    p.add_argument('--schema', default='public')
    p.add_argument('--commit', action='store_true', help='INSERT missing case rows (default dry run)')
    args = p.parse_args()
    print('Script version:', SCRIPT_VERSION)
    path = Path(args.csv)
    if not path.is_file():
        sys.exit(f'CSV not found: {path}')
    rows, headers, delimiter = read_csv(path)
    header_lookup = {h.casefold(): h for h in headers}
    if 'fallnr' not in header_lookup:
        sys.exit(f'CSV lacks fallnr column. Available headers: {", ".join(headers)}')

    db_url = os.environ.get('DATABASE_URL')
    if not db_url and not os.environ.get('PGDATABASE'):
        sys.exit('Set PGDATABASE=caa_database and PGUSER/PGHOST/PGPORT (and secure password), or DATABASE_URL')
    if db_url:
        print('Connection configuration: DATABASE_URL (URL parsed into psycopg2 keyword arguments)')
        # SQLAlchemy's driver-qualified URLs are not accepted as psycopg2 DSNs.
        # Extract credentials into keyword parameters; this also handles special
        # characters in passwords without printing them.
        parsed = urlsplit(db_url)
        scheme = parsed.scheme.lower()
        if scheme not in ('postgresql', 'postgres', 'postgresql+psycopg',
                          'postgresql+psycopg2'):
            raise ValueError('Unsupported DATABASE_URL scheme; expected PostgreSQL URL')
        if not parsed.hostname or not parsed.path.lstrip('/'):
            raise ValueError('DATABASE_URL lacks host or database name')
        conn_args = {'host': parsed.hostname,
                     'dbname': unquote(parsed.path.lstrip('/'))}
        if parsed.port is not None:
            conn_args['port'] = parsed.port
        if parsed.username is not None:
            conn_args['user'] = unquote(parsed.username)
        if parsed.password is not None:
            conn_args['password'] = unquote(parsed.password)
        conn = psycopg2.connect(**conn_args)
    else:
        print('Connection configuration: PG* environment variables')
        # psycopg2 / libpq reads the standard PGHOST, PGUSER, etc.
        conn = psycopg2.connect('')
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute('''
                SELECT column_name, data_type, is_nullable, column_default, is_identity, is_generated
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name='case'
                ORDER BY ordinal_position
            ''', (args.schema,))
            colinfo = cur.fetchall()
            if not colinfo:
                raise ValueError(f'No accessible table {args.schema}.case')
            db_cols = {name.casefold(): (name, typ, nullable, default, identity, generated)
                       for name, typ, nullable, default, identity, generated in colinfo}
            if 'fallnr' not in db_cols:
                raise ValueError('PostgreSQL case table lacks fallnr column')
            fallnr_col, fallnr_type, *_ = db_cols['fallnr']
            keys = [norm_key(row[header_lookup['fallnr']], fallnr_type) for row in rows]
            missing_key_rows = sum(not key for key in keys)
            dups = [key for key, count in Counter(k for k in keys if k).items() if count > 1]
            if missing_key_rows or dups:
                raise ValueError(f'CSV keys invalid: missing fallnr rows={missing_key_rows}, duplicate fallnr values={len(dups)}. No import performed.')

            # Never ingest old Retool IDs; PostgreSQL id is a distinct generated PK.
            common = [k for k in db_cols if k in header_lookup and k != 'id'
                      and db_cols[k][4] == 'NO' and db_cols[k][5] == 'NEVER']
            if 'fallnr' not in common:
                raise ValueError('fallnr is not importable')
            ignored_csv = [h for h in headers if h.casefold() not in db_cols or h.casefold() == 'id']
            required_missing = [info[0] for k, info in db_cols.items()
                                if info[2] == 'NO' and info[3] is None
                                and info[4] == 'NO' and info[5] == 'NEVER'
                                and k not in common]
            if required_missing:
                raise ValueError('Required PostgreSQL fields not available in CSV: ' + ', '.join(required_missing))

            table = sql.Identifier(args.schema, 'case')
            cur.execute(sql.SQL('SELECT {} FROM {}').format(sql.Identifier(fallnr_col), table))
            existing = {norm_key(x[0], fallnr_type) for x in cur.fetchall() if x[0] is not None}
            to_add = [(row, key) for row, key in zip(rows, keys) if key not in existing]
            already = len(rows) - len(to_add)
            print('RETOOL CASE-ONLY IMPORT PREVIEW')
            print(f'CSV rows: {len(rows)} | delimiter: {repr(delimiter)}')
            print(f'PostgreSQL existing case rows with fallnr: {len(existing)}')
            print(f'CSV already present: {already} | missing and eligible for insert: {len(to_add)}')
            print(f'Matching fields ({len(common)}): {", ".join(db_cols[k][0] for k in common)}')
            print(f'Ignored CSV fields ({len(ignored_csv)}): {", ".join(ignored_csv) if ignored_csv else "none"}')
            if len(common) == 1:
                print('WARNING: only fallnr matches DB column names; other Retool case data will NOT be imported.')
            # Detect critical data failures before writing any rows.
            for k in common:
                info = db_cols[k]
                if info[2] == 'NO' and info[3] is None:
                    blanks = sum(not row[header_lookup[k]] for row, _ in to_add)
                    if blanks:
                        raise ValueError(f'Required column {info[0]} blank in {blanks} insert rows')

            if not args.commit:
                conn.rollback()
                print('DRY RUN: no data changed. Review field mapping; then rerun with --commit.')
                return
            if len(common) == 1:
                raise ValueError('Refusing commit: no Retool fields other than fallnr matched; review migration mapping')
            insert_cols = [db_cols[k][0] for k in common]
            statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                table, sql.SQL(', ').join(map(sql.Identifier, insert_cols)),
                sql.SQL(', ').join(sql.Placeholder() for _ in insert_cols))
            for row, key in to_add:
                vals = [key if k == 'fallnr' else (row[header_lookup[k]] or None) for k in common]
                cur.execute(statement, vals)
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(table))
            print(f'Rows inserted: {len(to_add)} | final case count: {cur.fetchone()[0]}')
            conn.commit()
            print('COMMITTED: existing rows unchanged; no other tables written.')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        message = str(e)
        # Connection errors sometimes include complete URLs with passwords.
        message = re.sub(r'(?i)(postgres(?:ql)?(?:\+psycopg2?)?://)[^\s]+', r'\1[REDACTED]', message)
        print('STOPPED safely (transaction rolled back):', message, file=sys.stderr)
        sys.exit(1)
