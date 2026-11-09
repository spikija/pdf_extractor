"""Private, deterministic name+DOB relinking; never write database records.

Names/DOB remain in memory. Public CSVs contain only approved identity keys,
Fallnummer and status/issue labels. Existing MRI and extraction CSVs are preserved.
"""
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from sqlalchemy import create_engine, text
from caa_pdf_extractor.config import database_url

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'caa_private_linkage'
OUT = ROOT / 'output'
TARGET = {'CAA_ESTABLISHED', 'CAA_SUSPECTED', 'CAA_REVIEW_CANDIDATE'}
DATE_TOKEN = r'(?:\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}|\d{4}-\d{2}-\d{2})'


def readcsv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def fallnr(value):
    value = str(value or '').strip()
    return value[:-2] if re.fullmatch(r'\d+\.0', value) else value


def dob(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (float, int)) and 10000 < value < 80000:
        return from_excel(value).date().isoformat()
    value = str(value or '').strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', value):
        value = value[:10]
    value = re.sub(r'\s+', '', value)
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%d%m%Y', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            parsed = datetime.strptime(value, fmt).date()
            if 1800 <= parsed.year <= date.today().year:
                return parsed.isoformat()
        except ValueError:
            pass
    return None


def name(value):
    value = unicodedata.normalize('NFKC', str(value or '')).upper().strip()
    value = re.sub(r"['’ʼ]", '', value)
    return ' '.join(''.join(c if c.isalpha() or c.isdigit() else ' ' for c in value).split())


def name_forms(value):
    value = name(value)
    expanded = value.replace('Ä', 'AE').replace('Ö', 'OE').replace('Ü', 'UE')
    deaccent = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return {v.replace(' ', '') for v in (value, expanded, deaccent) if v}


def token_forms(value):
    value = name(value)
    expanded = value.replace('Ä', 'AE').replace('Ö', 'OE').replace('Ü', 'UE')
    deaccent = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return {tuple(v.split()) for v in (value, expanded, deaccent) if v}


def identity(surname, given, birth):
    key = (name(surname), name(given), dob(birth))
    if not all(key) or not any(c.isalpha() for c in key[0]) or not any(c.isalpha() for c in key[1]):
        return None
    if key[0] in {'NN', 'UNBEKANNT', 'UNKNOWN'} or key[1] in {'NN', 'UNBEKANNT', 'UNKNOWN'}:
        return None
    return key


def match_method(left, right):
    if left[2] != right[2]:
        return None
    return name_match_method(left, right)


def name_match_method(left, right):
    """Name compatibility only; final identity linkage also requires exact DOB."""
    if left[:2] == right[:2]:
        return 'EXACT_NAME_DOB'
    if not name_forms(left[0]) & name_forms(right[0]):
        return None
    if name_forms(left[1]) & name_forms(right[1]):
        return 'SAFE_ORTHOGRAPHY_DOB'
    for first in token_forms(left[1]):
        for second in token_forms(right[1]):
            if first[:len(second)] == second or second[:len(first)] == first:
                return 'SAFE_ADDITIONAL_GIVEN_NAMES_DOB'
    return None


def dicom_identity(row):
    raw = str(row['patient_name_dicom']).split('=')[0]
    parts = raw.split('^') if '^' in raw else raw.split(',')
    if len(parts) < 2:
        return None
    given = ' '.join(parts[1:3]) if '^' in raw else parts[1]
    return identity(parts[0], given, row.get('dicom_dob') or row.get('birth_date_yyyymmdd'))


def private_key(row):
    birth = str(row.get('dicom_dob') or row.get('birth_date_yyyymmdd') or '').strip()
    return hashlib.sha256('\x1f'.join((row['dicom_patient_id'].strip(), row['patient_name_dicom'].strip(), birth)).encode()).hexdigest()


def clean_name_field(value):
    value = re.sub(r'^.*?\b(?:Patient(?:in|/in)?|Name|Nachname|Vorname)\s*:\s*', '', value, flags=re.I)
    value = re.sub(r'^\s*(?:Frau|Herrn?)\s+', '', value, flags=re.I)
    value = re.split(r'\b(?:geb\.?|geboren|Geburtsdatum|DOB|birth)\b', value, flags=re.I)[0]
    value = re.sub(r'\([MWFmwf]\)', '', value)
    return value.strip(' \t:;,*()')


def header_identities(text, references=()):
    """Only a bounded first-page patient header; never infer DOB from any date."""
    lines = text.splitlines()[:45]
    for index, line in enumerate(lines):
        # Own the date with an explicit birth label, never an admission/report date.
        birth_pattern = (r'\b(?:geb(?:oren)?\.?|Geburtsdatum|Geb\.?\s*(?:datum|dat\.?)|DOB|date\s+of\s+birth)'
                         r'\s*(?:am\s*)?[:*]?\s*(?P<date>' + DATE_TOKEN + ')')
        joined = line + ('\n' + lines[index+1] if index+1 < len(lines) else '')
        for found in re.finditer(birth_pattern, joined, re.I):
            if found.start() >= len(line):
                continue
            before = joined[:found.start()]
            birth = dob(found['date'])
            if not birth:
                continue
            # Patient name comma surname/given format, beside a birth label/date.
            candidates = [before] + list(reversed(lines[max(0, index-2):index]))
            for candidate in candidates:
                parts = [part.strip() for part in candidate.strip(' ,').split(',') if part.strip()]
                if len(parts) == 1:
                    # Existing letter headers use GIVEN SURNAME, geboren am DOB.
                    # This ordering was verified against 208 structured patients.
                    full = clean_name_field(parts[0])
                    if not full or any(c.isdigit() for c in full) or ':' in full:
                        continue
                    # References disambiguate the name fields only. Preserve the
                    # literal header DOB even when it conflicts with an MRI DOB.
                    ref_matches = {(person[0], person[1], birth) for person in references
                                   if name_forms(full) & name_forms(person[1] + ' ' + person[0])}
                    if ref_matches:
                        if coherent(ref_matches):
                            return {max(ref_matches, key=lambda p: len(p[1]))}
                        return set()
                    words = full.split()
                    if len(words) < 2:
                        continue
                    start = len(words)-1
                    particles = {'VON', 'VAN', 'DE', 'DEL', 'DER', 'DEN', 'DA', 'DI', 'DOS', 'DU',
                                 'LE', 'LA', 'EL', 'AL', 'ZU', 'ZUR', 'ZUM', 'TEN', 'TER'}
                    while start > 1 and name(words[start-1]) in particles:
                        start -= 1
                    # An exact name spelling can establish a compound surname.
                    for person in references:
                        for split in range(1, len(words)):
                            proposed = identity(' '.join(words[split:]), ' '.join(words[:split]), birth)
                            if proposed and name_match_method(person, proposed):
                                if split < start:
                                    start = split
                    parsed = identity(' '.join(words[start:]), ' '.join(words[:start]), birth)
                    if parsed:
                        return {parsed}
                    continue
                if len(parts) < 2:
                    continue
                surname, given = clean_name_field(parts[-2]), clean_name_field(parts[-1])
                if not surname or not given or len(surname) > 90 or len(given) > 90:
                    continue
                if any(c.isdigit() for c in surname + given) or ':' in surname + given:
                    continue
                parsed = identity(surname, given, birth)
                if parsed:
                    return {parsed}
    return set()


def cached_first_page(path):
    """Decode just the first page of an existing parser artifact."""
    decoder = json.JSONDecoder()
    prefix = ''
    with path.open(encoding='utf-8-sig') as stream:
        while len(prefix) < 4_000_000:
            chunk = stream.read(16384)
            if not chunk:
                break
            prefix += chunk
            match = re.search(r'"pages"\s*:\s*\[\s*', prefix)
            if match:
                try:
                    page, _ = decoder.raw_decode(prefix[match.end():])
                    return page.get('text', '')
                except json.JSONDecodeError:
                    pass
    return None


def coherent(records):
    records = sorted(records)
    return all(match_method(a, b) for i, a in enumerate(records) for b in records[i+1:])


def resolve(mri_identity, records):
    candidates = {}
    for pid, person in records.items():
        method = match_method(mri_identity, person)
        if method:
            candidates[pid] = method
    if not candidates:
        return {}, None
    if not coherent([records[pid] for pid in candidates]):
        return {}, 'MULTIPLE_POSSIBLE_PERSONS'
    return candidates, None


def writecsv(path, fields, rows):
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == fields and list(reader) == rows


def run():
    public_paths = [OUT / filename for filename in ('mri_identity_to_current_pdfdb.csv',
        'caa_mri_to_be_extracted_identity_linked.csv', 'mri_identity_relink_review.csv')]
    protected = {p: hashlib.sha256(p.read_bytes()).digest() for p in OUT.glob('*.csv') if p not in public_paths}
    folders = {p.name: p for p in (ROOT / 'pdf_db').iterdir() if p.is_dir()}
    classification = {row['fallnr']: row['CAA_status'] for row in readcsv(OUT / 'pdf_db_caa_classification.csv')}
    assert set(classification) == set(folders) and len(folders) == 390
    engine = create_engine(database_url(), isolation_level='REPEATABLE READ',
                           connect_args={'options': '-c default_transaction_read_only=on', 'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            assert connection.execute(text('SHOW transaction_read_only')).scalar() == 'on'
            links = [dict(row) for row in connection.execute(text(
                'SELECT identity_key,fallnr,dicom_patient_id FROM caa_mri_private.identity_links')).mappings()]
            db_dob = {str(row['fallnr']): dob(row['date_of_birth']) for row in connection.execute(text(
                'SELECT fallnr::text AS fallnr,date_of_birth FROM public."case" WHERE fallnr::text=ANY(:ids)'),
                {'ids': list(folders)}).mappings()}
            db_documents = [dict(row) for row in connection.execute(text(
                "SELECT sha256,document_json->'document' AS metadata,document_json->'pages'->0->>'text' AS first_page "
                "FROM source_documents WHERE document_json->'document'->>'patient_id'=ANY(:ids)"),
                {'ids': list(folders)}).mappings()]
    finally:
        engine.dispose()
    assert len(links) == len({row['identity_key'] for row in links}) == 91
    dicom = {}
    for source in ('mri_orbis_fallnr_exact_candidates_v3_PRIVATE.csv', 'mri_identity_series_PRIVATE.csv'):
        for row in readcsv(PRIVATE / source):
            key = private_key(row)
            if key in {link['identity_key'] for link in links}:
                person = dicom_identity(row)
                assert person is not None
                if key in dicom:
                    assert dicom[key] == person
                dicom[key] = person
    assert set(dicom) == {row['identity_key'] for row in links}
    observations = defaultdict(set)
    provenance = {}
    workbook = load_workbook(ROOT / 'data/old_retool_db/orbis_01012015_01062023_amyloidangiopat.xlsx',
                             read_only=True, data_only=True)
    try:
        for sheet_name in ('CAA', 'orbis_amyloidangiopat'):
            iterator = workbook[sheet_name].iter_rows(values_only=True)
            headers = next(iterator)
            indexes = {key: headers.index(key) for key in ('Fallnummer', 'Nachname', 'Vorname', 'Geburtsdatum')}
            collected = defaultdict(set)
            for row in iterator:
                pid = fallnr(row[indexes['Fallnummer']])
                if pid not in folders or (sheet_name != 'CAA' and observations[pid]):
                    continue
                person = identity(row[indexes['Nachname']], row[indexes['Vorname']], row[indexes['Geburtsdatum']])
                if person:
                    collected[pid].add(person)
            for pid, identities in collected.items():
                observations[pid].update(identities)
                provenance[pid] = 'STRUCTURED_ORBIS'
    finally:
        workbook.close()
    for document in db_documents:
        meta = document['metadata'] or {}
        pid = str(meta.get('patient_id', ''))
        if pid not in folders or observations[pid]:
            continue
        person = identity(meta.get('surname') or meta.get('last_name') or meta.get('nachname'),
                          meta.get('given_name') or meta.get('first_name') or meta.get('vorname'),
                          meta.get('date_of_birth') or meta.get('birth_date'))
        if person:
            observations[pid].add(person)
            provenance[pid] = 'SOURCE_DOCUMENT_METADATA'
    structured_count = sum(bool(observations[pid]) for pid in folders)
    # Reuse cached first pages. The current parser key is an existing convention.
    import pymupdf
    from caa_pdf_extractor import utils
    key = pymupdf.__version__ + '-' + utils.get_sha256(Path(utils.__file__))[:16]
    cache_root = ROOT / 'data/debug/review_app/parsed_pdf_cache' / key
    header_files = defaultdict(list)
    for pid, folder in folders.items():
        if observations[pid]:
            continue
        for pdf in sorted(folder.rglob('*')):
            if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
                continue
            header_files[pid].append(pdf)
            cached = cache_root / (utils.get_sha256(pdf) + '.json')
            if cached.exists():
                page = cached_first_page(cached)
                if page:
                    observations[pid].update(header_identities(page, dicom.values()))
        if observations[pid]:
            provenance[pid] = 'CACHED_FIRST_PAGE_HEADER'
    # First-page native extraction is allowed only where demographics remain absent.
    first_pages_read = 0
    for pid in folders:
        if observations[pid]:
            continue
        for pdf in header_files.get(pid, []):
            try:
                with pymupdf.open(pdf) as document:
                    if len(document):
                        first_pages_read += 1
                        observations[pid].update(header_identities(document[0].get_text('text', sort=False), dicom.values()))
            except Exception:
                continue
        if observations[pid]:
            provenance[pid] = 'PDF_FIRST_PAGE_FALLBACK'
    records, conflicts = {}, {}
    dob_disagreements, header_corroborated = 0, 0
    for pid in folders:
        people = observations[pid]
        if not people:
            conflicts[pid] = 'DEMOGRAPHICS_UNAVAILABLE'
        elif not coherent(people):
            conflicts[pid] = 'CONFLICTING_HEADER_IDENTITIES'
        else:
            person = max(people, key=lambda value: (len(value[1].split()), len(value[1])))
            if db_dob.get(pid) and db_dob[pid] != person[2]:
                dob_disagreements += 1
                # Resolve source disagreement only with literal birth-date evidence
                # in existing cached headers. Never swap an MRI DOB for matching.
                header_people = set()
                for pdf in sorted(folders[pid].rglob('*')):
                    if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
                        continue
                    cached = cache_root / (utils.get_sha256(pdf) + '.json')
                    if cached.exists():
                        for candidate in header_identities(cached_first_page(cached) or '', dicom.values()):
                            if name_match_method(person, candidate):
                                header_people.add(candidate)
                header_dates = {candidate[2] for candidate in header_people}
                if header_dates == {person[2]}:
                    records[pid] = person
                    header_corroborated += 1
                elif header_dates == {db_dob[pid]} and coherent(header_people):
                    records[pid] = max(header_people, key=lambda p: len(p[1]))
                    header_corroborated += 1
                else:
                    conflicts[pid] = 'DOB_CONFLICT'
            else:
                records[pid] = person
    print(f'DEMOGRAPHICS structured={structured_count} resolved={len(records)} unresolved={len(conflicts)} first_page_fallback_reads={first_pages_read}', flush=True)
    print(f'STRUCTURED_DOB_DISAGREEMENTS={dob_disagreements} HEADER_CORROBORATED={header_corroborated}', flush=True)
    print('UNRESOLVED_CURRENT_NAMES_COMPATIBLE_WITH_ANY_MRI =',
          sum(any(name_match_method(person, candidate) for person in dicom.values()
                  for candidate in observations[pid]) for pid in conflicts), flush=True)
    mappings, reviews, matched, same_old, different_old = [], [], set(), set(), set()
    available = set()
    for link in links:
        key = link['identity_key']
        person = dicom[key]
        candidates, issue = resolve(person, records)
        if candidates:
            matched.add(key)
            available.update(candidates)
            if fallnr(link['fallnr']) in candidates:
                same_old.add(key)
            else:
                different_old.add(key)
            for pid, method in candidates.items():
                mappings.append(dict(identity_key=key, current_fallnr=pid,
                    match_type='SAME_PERSON_MULTIPLE_FALLNR' if len(candidates) > 1 else method))
        else:
            if not issue:
                conflicting_candidates = [candidate for pid in conflicts for candidate in observations[pid]
                                          if name_match_method(person, candidate)]
                if conflicting_candidates:
                    issue = 'DOB_CONFLICT' if any(candidate[2] != person[2] for candidate in conflicting_candidates) else 'NAME_CONFLICT'
                elif any(name_match_method(person, other) and person[2] != other[2] for other in records.values()):
                    issue = 'DOB_CONFLICT'
                elif any(person[2] == other[2] and name_forms(person[0]) & name_forms(other[0]) for other in records.values()):
                    issue = 'NAME_CONFLICT'
                else:
                    issue = 'NO_CURRENT_PDFDB_MATCH'
            reviews.append(dict(identity_key=key, issue=issue))
    target = {pid: status for pid, status in classification.items() if status in TARGET}
    missing = [dict(fallnr=pid, CAA_status=status) for pid, status in sorted(target.items(), key=lambda item: (item[1], item[0]))
               if pid not in available]
    assert len(target) == 281
    assert matched == same_old | different_old and not same_old & different_old
    assert len(matched) + len(reviews) == len(links)
    assert len(mappings) == len({(r['identity_key'], r['current_fallnr']) for r in mappings})
    assert all(r['current_fallnr'] in folders and match_method(dicom[r['identity_key']], records[r['current_fallnr']]) for r in mappings)
    assert len(missing) == len({row['fallnr'] for row in missing}) == len(set(target) - available)
    assert len(target) == len(set(target) & available) + len(missing)
    writecsv(public_paths[0], ['identity_key', 'current_fallnr', 'match_type'],
             sorted(mappings, key=lambda row: (row['identity_key'], row['current_fallnr'])))
    writecsv(public_paths[1], ['fallnr', 'CAA_status'], missing)
    if reviews:
        writecsv(public_paths[2], ['identity_key', 'issue'], sorted(reviews, key=lambda row: row['identity_key']))
    elif public_paths[2].exists():
        public_paths[2].unlink()
    assert all(hashlib.sha256(path.read_bytes()).digest() == checksum for path, checksum in protected.items())
    print('REVIEW_ISSUE_COUNTS =', dict(Counter(row['issue'] for row in reviews)))
    for label, value in [
        ('PDF_DB_PATIENTS', len(folders)), ('MRI_IDENTITIES_TOTAL', len(links)),
        ('MRI_IDENTITIES_MATCHED_CURRENT_PDFDB', len(matched)), ('MRI_IDENTITIES_SAME_OLD_FALLNR', len(same_old)),
        ('MRI_IDENTITIES_SAME_PERSON_DIFFERENT_FALLNR', len(different_old)), ('MRI_IDENTITIES_UNMATCHED', len(reviews)),
        ('TARGET_CAA_TOTAL', len(target)), ('TARGET_CAA_WITH_MRI_BY_IDENTITY', len(set(target) & available)),
        ('TARGET_CAA_WITHOUT_MRI_BY_IDENTITY', len(missing)),
        ('MISSING_MRI_FILE', 'output/caa_mri_to_be_extracted_identity_linked.csv'),
        ('REVIEW_FILE', 'output/mri_identity_relink_review.csv' if reviews else 'NONE'),
        ('LLM_CALLS', 0), ('DATABASE_WRITES', 'NONE')]:
        print(f'{label} = {value}', flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        # Error details can contain demographic values; never print them.
        print('IDENTITY_RELINK_ERROR_TYPE = ' + type(error).__name__, flush=True)
        raise SystemExit(1)
