#!/usr/bin/env python3
"""Reconcile ONLY existing public.case rows from original Retool case.csv.

Default dry run. --commit fills PostgreSQL NULL fields (and blank text fields)
from nonempty CSV fields for matching fallnr, leaving all nonempty database
values untouched. No inserts/deletes, no other tables written. Summaries do
not print identifiers or patient values. Conflicting values are counted only.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, unquote

import psycopg2
from psycopg2 import sql

VERSION = '2026-10-28-reconcile-v1'
DEFAULT_CSV = r'C:\app\pdf_extractor\data\old_retool_db\case.csv'

def get_connection():
    u = os.environ.get('DATABASE_URL')
    if not u:
        return psycopg2.connect('')
    p = urlsplit(u)
    if p.scheme.lower() not in ('postgresql', 'postgres', 'postgresql+psycopg', 'postgresql+psycopg2'):
        raise ValueError('Unsupported database URL scheme')
    if not p.hostname or not p.path.lstrip('/'):
        raise ValueError('Database URL has no hostname or database name')
    args = {'host': p.hostname, 'dbname': unquote(p.path.lstrip('/'))}
    if p.port: args['port'] = p.port
    if p.username: args['user'] = unquote(p.username)
    if p.password: args['password'] = unquote(p.password)
    return psycopg2.connect(**args)

def load_csv(path):
    b = path.read_bytes()
    s = None
    for enc in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            s = b.decode(enc)
            break
        except UnicodeError:
            pass
    if s is None: raise ValueError('Unknown CSV encoding')
    try: delim = csv.Sniffer().sniff(s[:8192], delimiters=',;\t').delimiter
    except csv.Error: delim = ','
    reader = csv.DictReader(s.splitlines(), delimiter=delim)
    headers = reader.fieldnames or []
    headers = [h.strip().lstrip('\ufeff') for h in headers]
    if not headers or len(headers) != len(set(h.casefold() for h in headers)):
        raise ValueError('Empty/duplicate CSV headers')
    rows = []
    for row in reader:
        if None in row: raise ValueError('Malformed CSV row')
        rows.append({h.casefold(): (row.get(original) or '').strip()
                     for h, original in zip(headers, row.keys())})
    return rows, [h.casefold() for h in headers]

def norm_key(v, typ):
    v = str(v or '').strip()
    if not v: return ''
    if typ in ('integer', 'smallint', 'bigint', 'numeric'):
        if re.fullmatch(r'\d+\.0+', v): v = v.split('.')[0]
        if not re.fullmatch(r'\d+', v): raise ValueError('Invalid fallnr format')
        return str(int(v))
    return v

def norm_value(v, typ):
    if v is None: return None
    s = str(v).strip()
    if not s: return None
    if typ in ('boolean',):
        x = s.lower()
        if x in ('true', 't', '1', 'yes', 'y'): return 'true'
        if x in ('false', 'f', '0', 'no', 'n'): return 'false'
    if typ in ('smallint', 'integer', 'bigint', 'numeric', 'real', 'double precision'):
        try: return str(Decimal(s).normalize())
        except InvalidOperation: pass
    if typ in ('date', 'timestamp without time zone', 'timestamp with time zone'):
        if isinstance(v, (date, datetime)): return v.isoformat()
        # ISO text forms and common Retool export time suffix; only cosmetic normalization
        if len(s) >= 10 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', s[:10]):
            if typ == 'date' or re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T00:00:00(?:\.0+)?(?:Z|[+]00:00)?)?', s):
                return s[:10]
    return s

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--csv', default=DEFAULT_CSV)
    ap.add_argument('--schema', default='public')
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()
    print('Script version:', VERSION)
    path = Path(args.csv)
    if not path.is_file(): raise ValueError('CSV file not found')
    rows, headers = load_csv(path)
    if 'fallnr' not in headers: raise ValueError('CSV has no fallnr column')
    conn = get_connection()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute('''SELECT column_name, data_type, is_identity, is_generated
                           FROM information_schema.columns
                           WHERE table_schema=%s AND table_name='case'
                           ORDER BY ordinal_position''', (args.schema,))
            info = cur.fetchall()
            if not info: raise ValueError('case table missing')
            cols = {n.lower(): (n,t) for n,t,identity,generated in info
                    if identity == 'NO' and generated == 'NEVER'}
            if 'fallnr' not in cols: raise ValueError('case.fallnr missing')
            fall_name, fall_type = cols['fallnr']
            keys = [norm_key(r['fallnr'], fall_type) for r in rows]
            counts = Counter(keys)
            if not all(keys) or any(v > 1 for v in counts.values()):
                raise ValueError('CSV fallnr values contain blanks or duplicates; abort')
            by_key = dict(zip(keys, rows))
            common = [k for k in headers if k in cols and k not in ('id', 'fallnr')]
            if not common: raise ValueError('No matching editable columns')
            tbl = sql.Identifier(args.schema, 'case')
            select_cols = [fall_name] + [cols[k][0] for k in common]
            cur.execute(sql.SQL('SELECT {} FROM {}').format(
                sql.SQL(', ').join(map(sql.Identifier, select_cols)), tbl))
            records = cur.fetchall()
            dbkeys = [norm_key(r[0], fall_type) for r in records]
            if len(dbkeys) != len(set(dbkeys)):
                raise ValueError('Duplicate fallnr in PostgreSQL case; abort')
            matched = [(key, values) for key, values in zip(dbkeys, records)
                       if key in by_key]
            changes = []
            conflicts = Counter()
            already_equal = Counter()
            blanks_in_csv = Counter()
            fillings = Counter()
            for key, values in matched:
                csvrow = by_key[key]
                updates = {}
                for idx, k in enumerate(common, start=1):
                    proposed = csvrow.get(k, '')
                    actual = values[idx]
                    if not proposed:
                        blanks_in_csv[k] += 1
                        continue
                    # Empty DB text or NULL is treated as missing; existing 0/False is not missing.
                    missing = actual is None or (isinstance(actual, str) and not actual.strip())
                    if missing:
                        updates[k] = proposed
                        fillings[k] += 1
                    elif norm_value(actual, cols[k][1]) == norm_value(proposed, cols[k][1]):
                        already_equal[k] += 1
                    else:
                        conflicts[k] += 1
                if updates: changes.append((key, updates))
            print('RETOOL EXISTING-CASE RECONCILIATION')
            print(f'CSV cases: {len(rows)} | PostgreSQL cases: {len(records)} | matched existing cases: {len(matched)}')
            print(f'Existing cases with at least one field to fill: {len(changes)}')
            print(f'NULL/blank field values to fill: {sum(fillings.values())}')
            print(f'Nonempty differing field values held unchanged: {sum(conflicts.values())}')
            print('FIELD SUMMARY (field | fill | different-preserved | equal)')
            for k in common:
                print(f'  {k} | {fillings[k]} | {conflicts[k]} | {already_equal[k]}')
            if not args.commit:
                conn.rollback()
                print('DRY RUN: no database writes. Use --commit only after reviewing counts.')
                return
            # Row-level updates restricted to NULL or empty text; never overwrite a populated value.
            updated = 0
            for key, updates in changes:
                sets = []
                vals = []
                for k, proposed in updates.items():
                    colname, dtype = cols[k]
                    ident = sql.Identifier(colname)
                    if dtype in ('character varying', 'text', 'character'):
                        condition = sql.SQL('({} IS NULL OR btrim({}) = \'\')').format(ident, ident)
                    else:
                        condition = sql.SQL('{} IS NULL').format(ident)
                    sets.append(sql.SQL('{} = CASE WHEN {} THEN %s ELSE {} END').format(ident, condition, ident))
                    vals.append(proposed)
                vals.append(key)
                query = sql.SQL('UPDATE {} SET {} WHERE {} = %s').format(
                    tbl, sql.SQL(', ').join(sets), sql.Identifier(fall_name))
                cur.execute(query, vals)
                updated += cur.rowcount
            conn.commit()
            print(f'COMMITTED: updated {updated} existing case rows; no inserts/deletes; no other tables written.')
            print('Nonempty differences preserved for manual reconciliation.')
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    try: main()
    except Exception as e:
        msg = re.sub(r'(?i)(postgres(?:ql)?(?:\+psycopg2?)?://)[^\s]+', r'\1[REDACTED]', str(e))
        print('STOPPED safely (transaction rolled back):', msg, file=sys.stderr)
        sys.exit(1)
