"""Identity validation primitives and the local multi-source audit entrypoint.

Checksum authority: https://www.sozialversicherung.gv.at/cdscontent/?contentid=10007.820902
The audit additionally requires a real DDMMYY date, as requested. Official SVNRs
can contain artificial dates; those fail this audit policy and require review.

HMAC configuration: --key-file or PERSON_ID_HMAC_KEY_FILE; otherwise reuse the
existing caa_private_linkage/mri_exam_key_PRIVATE.key (raw 32 bytes). Never create
or print a key. Person IDs use a separate versioned HMAC domain. Multivalue
SVNR/DOB fields exist only in the private audit CSV and use ';' separators.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pymupdf
from . import utils

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = (3, 7, 9, 0, 5, 8, 4, 2, 1, 6)
DOMAIN = b'pdf-extractor.person-identity.svnr.v1\x00'
MATCH = 'SVNR_VALID_DOB_MATCH'
UNAVAILABLE = 'SVNR_VALID_DOB_UNAVAILABLE'
DOB_CONFLICT = 'SVNR_VALID_DOB_CONFLICT'
INVALID = 'SVNR_INVALID'
NOT_FOUND = 'SVNR_NOT_FOUND'
MULTIPLE = 'MULTIPLE_VALID_SVNR_CONFLICT'
ELIGIBLE = {MATCH, UNAVAILABLE}
LABEL = re.compile(
    r'\b(?:SVNR|VSNR|VNR|SV[\s.-]*(?:NR|NUMMER|ZAHL)|'
    r'Sozialversicherungs[\s-]*(?:nummer|nr)|Versicherungs[\s-]*(?:nummer|nr)|'
    r'Vers\.?[\s-]*Nr)\b', re.I)
EXACT_NUMBER = re.compile(r'(?<![0-9])([0-9](?:[ \t\r\n\u00a0/\-]*[0-9]){9})(?![0-9])')
FORMATTED = re.compile(r'(?<![0-9])([0-9]{4}[ \t\u00a0/-]+[0-9]{6})(?![0-9])')
BIRTH = re.compile(
    r'\b(?:geboren|geb(?:urtsdatum)?\.?|Geb\.?\s*(?:datum|dat\.?)|DOB|date\s+of\s+birth)'
    r'\s*(?:am\s*)?[:*]?\s*(?P<date>[0-9]{1,2}\s*\.\s*[0-9]{1,2}\s*\.\s*[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})', re.I)


def normalize_svnr(value):
    return ''.join(re.findall(r'[0-9]', str(value)))


def encoded_date_valid(value):
    if not re.fullmatch(r'[0-9]{10}', value):
        return False
    tail = value[-6:]
    try:
        # A two-digit year does not encode a century. 2000 admits all possible
        # calendar-valid DDMMYY combinations, including leap-year century 00.
        date(2000 + int(tail[4:6]), int(tail[2:4]), int(tail[:2]))
        return True
    except ValueError:
        return False


def valid_svnr(value):
    if not re.fullmatch(r'[1-9][0-9]{9}', value):
        return False
    remainder = sum(int(digit) * weight for digit, weight in zip(value, WEIGHTS)) % 11
    return remainder < 10 and remainder == int(value[3]) and encoded_date_valid(value)


def canonical_dob(value):
    if isinstance(value, date):
        return value.isoformat()[:10]
    value = re.sub(r'\s+', '', str(value or ''))
    try:
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            return date.fromisoformat(value).isoformat()
        if re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}', value):
            day, month, year = map(int, value.split('.'))
            return date(year, month, day).isoformat()
    except ValueError:
        pass
    return None


def dob_suffix(value):
    parsed = date.fromisoformat(value)
    return parsed.strftime('%d%m%y')


def document_dobs(payload):
    found = set()
    for obj in (payload, payload.get('document', {}), payload.get('demographics', {})):
        for key in ('date_of_birth', 'birth_date', 'dob'):
            parsed = canonical_dob(obj.get(key))
            if parsed:
                found.add(parsed)
    pages = payload.get('pages') or []
    if pages:
        header = '\n'.join(pages[0].get('text', '').splitlines()[:45])
        for match in BIRTH.finditer(header):
            parsed = canonical_dob(match['date'])
            if parsed:
                found.add(parsed)
                break  # one patient birth header per document, no body dates
    return found


def candidates(text, header_dobs=(), *, header=False):
    result = set()
    for label in LABEL.finditer(text):
        after = text[label.end():label.end()+90]
        after = re.sub(r'^[\s.:#=()\-–]*', '', after)
        number = EXACT_NUMBER.match(after)
        if number:
            result.add(normalize_svnr(number[1]))
        else:
            # Retain malformed, explicitly labelled numeric values for review.
            malformed = re.match(r'[0-9][0-9 \t\u00a0/\-]{0,40}', after)
            if malformed:
                result.add(normalize_svnr(malformed[0]))
    if header and len(header_dobs) == 1:
        # An unlabelled number needs both SVNR-specific formatting and an
        # independently extracted matching birth date; checksum alone is insufficient.
        expected = dob_suffix(next(iter(header_dobs)))
        for match in FORMATTED.finditer(text):
            value = normalize_svnr(match[1])
            if valid_svnr(value) and value[-6:] == expected:
                result.add(value)
    return result


def resolve(valid, all_candidates, dobs):
    if len(valid) > 1:
        return MULTIPLE
    if not valid:
        return INVALID if all_candidates else NOT_FOUND
    if not dobs:
        return UNAVAILABLE
    if len(dobs) != 1 or any(dob_suffix(value) != next(iter(valid))[-6:] for value in dobs):
        return DOB_CONFLICT
    return MATCH


def load_secret(path):
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) == 32:
        return raw  # preserve every byte, including any leading/trailing whitespace
    encoded = raw.strip()
    if re.fullmatch(b'[0-9a-fA-F]{64}', encoded):
        return bytes.fromhex(encoded.decode('ascii'))
    raise ValueError('UNSUPPORTED_LOCAL_KEY_FORMAT')


def person_id(secret, value):
    if not valid_svnr(value):
        raise ValueError('INVALID_PERSON_ID_INPUT')
    return 'P_' + hmac.new(secret, DOMAIN + value.encode('ascii'), hashlib.sha256).hexdigest()[:16]


def protect(path):
    """Use the project's existing owner/SYSTEM-only private-artifact convention."""
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    proc = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, check=True)
    sid = next(csv.reader([proc.stdout.strip()]))[1]
    suffix = '(OI)(CI)F' if path.is_dir() else 'F'
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                    f'*{sid}:{suffix}', f'*S-1-5-18:{suffix}'], capture_output=True, check=True)
    # Existing explicit grants must not silently expose a reused private path.
    escaped = str(path).replace("'", "''")
    command = (f"$a=Get-Acl -LiteralPath '{escaped}'; "
               "$s=@($a.Access | Where-Object {$_.AccessControlType -eq 'Allow'} | "
               "ForEach-Object {$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value}); "
               f"if (@($s | Where-Object {{$_ -notin @('{sid}','S-1-5-18')}}).Count -gt 0) {{exit 9}}")
    checked = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True)
    if checked.returncode:
        raise PermissionError('PRIVATE_IDENTITY_ACCESS_TOO_BROAD')


def write_csv(path, fields, rows, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        protect(path.parent)
    temporary = path.with_suffix('.csv.tmp')
    if temporary.exists() and private:
        protect(temporary)
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if private:
        protect(temporary)
    temporary.replace(path)
    if private:
        protect(path)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields or list(reader) != rows:
            raise ValueError('CROSSWALK_SERIALIZATION_FAILED')


def read_payload(path, digest, *, allow_empty=False):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if value.get('document', {}).get('sha256') != digest:
            return None
        pages = value.get('pages')
        if isinstance(pages, list) and pages and all(isinstance(page.get('text'), str) for page in pages):
            if allow_empty or any(page['text'].strip() for page in pages):
                return value
    except (ValueError, OSError, AttributeError):
        pass
    return None


def inventory():
    root = ROOT / 'pdf_db'
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    rows = []
    for folder in folders:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix.lower() == '.pdf':
                rows.append(dict(fallnr=folder.name, path=path,
                                 relative_path=path.relative_to(root).as_posix(), digest=utils.get_sha256(path)))
    return [folder.name for folder in folders], rows


def payloads(rows):
    key = pymupdf.__version__ + '-' + utils.get_sha256(Path(utils.__file__))[:16]
    cache_root = ROOT / 'data/debug/review_app/parsed_pdf_cache'
    result = {}
    missing = []
    for row in rows:
        digest = row['digest']
        if digest in result:
            continue
        alternatives = [cache_root / key / (digest + '.json')]
        alternatives.extend(cache_root.glob('*/' + digest + '.json'))
        alternatives.append(ROOT / 'data/parsed' / row['fallnr'] / (row['path'].stem + '__' + digest[:8] + '.json'))
        for path in dict.fromkeys(alternatives):
            value = read_payload(path, digest, allow_empty=(path.parent.name == key))
            if value is not None:
                result[digest] = value
                break
        else:
            missing.append(row)
    # Only missing local parsed text can trigger the local, read-only DB fallback.
    if missing:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        from .config import database_url
        url = make_url(database_url())
        if url.host not in {'localhost', '127.0.0.1', '::1'}:
            raise RuntimeError('LOCAL_PARSED_DATABASE_REQUIRED')
        engine = create_engine(url, connect_args={'options': '-c default_transaction_read_only=on', 'connect_timeout': 10})
        try:
            with engine.connect() as connection:
                assert connection.execute(text('SHOW transaction_read_only')).scalar() == 'on'
                documents = connection.execute(text('SELECT sha256,document_json FROM source_documents '
                    'WHERE sha256=ANY(:hashes) AND document_json IS NOT NULL'),
                    {'hashes': list({row['digest'] for row in missing})}).mappings()
                for document in documents:
                    value = document['document_json']
                    if any(page.get('text', '').strip() for page in value.get('pages', [])):
                        result[document['sha256']] = value
        finally:
            engine.dispose()
    parsed_count = 0
    unavailable = set()
    for row in missing:
        if row['digest'] in result:
            continue
        # The existing parser API returns in-memory data; never save/overwrite it.
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                value = utils.parse_pdf(row['path'], row['relative_path'], row['fallnr'])
            if value['document']['sha256'] != row['digest']:
                raise ValueError('SOURCE_CHANGED_DURING_READ')
            result[row['digest']] = value
            parsed_count += 1
        except Exception:
            unavailable.add(row['digest'])
    # A current native parser's validated empty output cannot improve by rerunning
    # that same parser. Reuse it and explicitly require source review, not OCR/LLM.
    unavailable.update(digest for digest, payload in result.items()
                       if not any(page.get('text', '').strip() for page in payload.get('pages', [])))
    return result, parsed_count, unavailable




def run(key_file=None):
    """The public audit entrypoint now supports official and demographic IDs."""
    from .identity_resolution import run as resolve_all_identity_types
    return resolve_all_identity_types(key_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, help='Existing local HMAC secret file; never generated automatically')
    args = parser.parse_args()
    try:
        run(args.key_file)
    except Exception as error:
        # Exception representations can contain direct identifiers; never emit them.
        print('PERSON_IDENTITY_AUDIT_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
