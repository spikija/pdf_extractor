"""Whole-corpus parser repair and conservative CAA/MRI export; no model or DB writes.

Run with .venv/Scripts/python scripts/audit_parser_caa_mri.py.
The action CSV records the INITIAL audit; the coverage CSV records the FINAL audit.
Reuses the existing validation_sources parser-keyed cache and utils.parse_pdf API.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import pymupdf
from caa_pdf_extractor import utils
from caa_pdf_extractor.workbench_store import atomic
from caa_pdf_extractor.preparation.patient_bundle import prepare_document
from caa_pdf_extractor.section_dictionary import load_dictionary, heading_matches

TZ = ZoneInfo('Europe/Vienna')
SINCE = datetime(2026, 10, 25, tzinfo=TZ).timestamp()
PARSER_KEY = pymupdf.__version__ + '-' + utils.get_sha256(Path(utils.__file__))[:16]
CACHE = ROOT / 'data/debug/review_app/parsed_pdf_cache' / PARSER_KEY
REPAIRABLE = {'OLD_VERSION_PARSED', 'NEVER_PARSED', 'SOURCE_CHANGED_AFTER_PARSE',
              'PARSED_ARTIFACT_MISSING', 'PARSED_ARTIFACT_CORRUPT'}
PRIMARY = {'CAA_ESTABLISHED', 'CAA_SUSPECTED'}
RANK = {'CAA_ESTABLISHED': 5, 'CAA_SUSPECTED': 4, 'CAA_DIFFERENTIAL_OR_WORKUP': 3,
        'CAA_REVIEW_CANDIDATE': 2, 'CAA_NEGATED': 1}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def csvout(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.csv.tmp')
    with temp.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c)).replace('\u00ad', '')


def truth(value):
    return str(value).lower() in {'true', '1', 'yes'}


def inventory(pdfroot):
    patients = {p.name for p in pdfroot.iterdir() if p.is_dir()}
    rows = []
    # File items at the root are not silently assigned to an invented patient.
    orphan_items = 0
    for path in sorted(pdfroot.rglob('*')):
        if not path.is_file() or path.suffix.lower() != '.pdf':
            continue
        relative = path.relative_to(pdfroot)
        pid = relative.parts[0] if len(relative.parts) > 1 else ''
        if not pid:
            orphan_items += 1
        stat = path.stat()
        created = getattr(stat, 'st_birthtime', stat.st_ctime if sys.platform == 'win32' else None)
        rows.append(dict(patient_id=pid, filename=path.name, pdf_path=path,
                         relative_path=relative.as_posix(), file_size=stat.st_size,
                         file_modified_time=datetime.fromtimestamp(stat.st_mtime, TZ).isoformat(),
                         file_created_time=datetime.fromtimestamp(created, TZ).isoformat() if created else '',
                         source_hash=utils.get_sha256(path),
                         recent_since_2026_10_25=max(stat.st_mtime, created or 0) >= SINCE))
    return patients, rows, orphan_items


def valid_payload(payload):
    if not isinstance(payload, dict):
        return False
    meta, pages, stats = (payload.get(k) for k in ('document', 'pages', 'statistics'))
    if not isinstance(meta, dict) or not isinstance(pages, list) or not pages or not isinstance(stats, dict):
        return False
    if not re.fullmatch(r'[0-9a-f]{64}', str(meta.get('sha256', ''))):
        return False
    if meta.get('page_count') != len(pages):
        return False
    return all(isinstance(p, dict) and p.get('page_number') == i and isinstance(p.get('text'), str)
               and isinstance(p.get('tables'), list) and isinstance(p.get('headings'), list)
               for i, p in enumerate(pages, 1))


@dataclass
class Artifact:
    path: Path | None
    metadata: dict
    valid: bool
    current: bool
    payload: dict | None = None

    def load(self):
        return self.payload if self.payload is not None else read(self.path)


class ParsedIndex:
    def __init__(self):
        self.by_hash = defaultdict(list)
        self.by_name = defaultdict(list)
        self.corrupt_names = set()
        self.corrupt_hashes = set()
        self.indicated = set()
        self.db_sources = []
        self.db_caa = []
        self.db_cases = []

    def add(self, payload, path=None, *, keep=False):
        if not isinstance(payload, dict) or not isinstance(payload.get('document'), dict):
            return
        meta = payload['document']
        valid = valid_payload(payload)
        # PyMuPDF's library version alone cannot identify changes in utils.py.
        signature = meta.get('parser_signature')
        keyed = path is not None and path.parent.name == PARSER_KEY
        current = (valid and payload.get('schema_version') == '2.0'
                   and meta.get('parser') == 'PyMuPDF'
                   and meta.get('parser_version') == pymupdf.__version__
                   and (signature == PARSER_KEY or keyed))
        art = Artifact(path, meta, valid, current, payload if keep else None)
        self.by_hash[meta.get('sha256', '')].append(art)
        self.by_name[(str(meta.get('patient_id', '')), meta.get('filename', ''))].append(art)

    def scan(self):
        paths = set((ROOT / 'data/parsed').rglob('*.json'))
        paths.update((ROOT / 'data/debug/review_app/parsed_pdf_cache').rglob('*.json'))
        for folder in (ROOT / 'data/debug').rglob('parsed'):
            if folder.is_dir():
                paths.update(folder.rglob('*.json'))
        for path in sorted(paths):
            try:
                self.add(read(path), path)
            except (ValueError, OSError, TypeError):
                self.corrupt_names.add((path.parent.name, path.name.split('__')[0]))
                if re.fullmatch(r'[0-9a-f]{64}', path.stem):
                    self.corrupt_hashes.add(path.stem)
        # Existing frozen exports contain source_documents.document_json, not a new store.
        aggregate = set((ROOT / 'data/debug').rglob('sources.json'))
        aggregate.update((ROOT / 'data/debug').rglob('snapshot.json'))
        for path in sorted(aggregate):
            try:
                value = read(path)
                for doc in value.get('tables', {}).get('source_documents', []):
                    if doc.get('document_json'):
                        self.add(doc['document_json'], keep=True)
                    else:
                        self.indicated.add((doc.get('sha256'), doc.get('filename')))
            except (ValueError, OSError, AttributeError):
                continue
        self.read_database()

    def read_database(self):
        # PostgreSQL enforces read-only for this connection, including accidental SQL writes.
        from sqlalchemy import create_engine, text
        from caa_pdf_extractor.config import database_url
        engine = create_engine(database_url(), connect_args={
            'options': '-c default_transaction_read_only=on', 'connect_timeout': 10})
        with engine.connect() as connection:
            assert connection.execute(text('SHOW transaction_read_only')).scalar() == 'on'
            self.db_sources = [dict(r) for r in connection.execute(text(
                'SELECT id,filename,sha256,document_json FROM source_documents')).mappings()]
            for row in self.db_sources:
                if row['document_json']:
                    self.add(row['document_json'], keep=True)
                else:
                    self.indicated.add((row['sha256'], row['filename']))
            self.db_caa = [dict(r) for r in connection.execute(text(
                'SELECT e.*,c.fallnr::text AS patient_id FROM caa_diagnostic_event e '
                'JOIN "case" c ON c.id=e.case_id WHERE NOT coalesce(e.deleted,false)')).mappings()]
            self.db_cases = [dict(r) for r in connection.execute(text(
                'SELECT fallnr::text AS patient_id,caa_criteria,caa_criteria_stage FROM "case" '
                'WHERE NOT coalesce(deleted,false) AND '
                '(caa_criteria IS NOT NULL OR caa_criteria_stage IS NOT NULL)')).mappings()]
        engine.dispose()

    def classify(self, row):
        if not row['patient_id']:
            return 'AMBIGUOUS', None, 'PDF outside patient folder'
        matches = self.by_hash.get(row['source_hash'], [])
        good = [a for a in matches if a.valid]
        current = [a for a in good if a.current]
        if current:
            return 'CURRENT_PARSED', current[-1], 'Source hash, schema and parser code signature match'
        if good:
            return 'OLD_VERSION_PARSED', good[-1], 'Parser code signature absent or different'
        if matches or row['source_hash'] in self.corrupt_hashes:
            return 'PARSED_ARTIFACT_CORRUPT', None, 'Matching artifact fails schema validation'
        named = self.by_name.get((row['patient_id'], row['filename']), [])
        exact_path = [a for a in named if a.metadata.get('relative_path', '').replace('\\', '/') == row['relative_path']]
        if len({a.metadata.get('sha256') for a in (exact_path or named)}) > 1 and not exact_path:
            return 'AMBIGUOUS', None, 'Multiple same-name source identities'
        if named:
            return 'SOURCE_CHANGED_AFTER_PARSE', None, 'Existing patient/filename artifact has different SHA-256'
        if (row['source_hash'], row['filename']) in self.indicated:
            return 'PARSED_ARTIFACT_MISSING', None, 'Index indicates parse without payload'
        if (row['patient_id'], Path(row['filename']).stem) in self.corrupt_names:
            return 'PARSED_ARTIFACT_CORRUPT', None, 'Patient artifact cannot be decoded'
        return 'NEVER_PARSED', None, 'No matching parse in existing stores'


def coverage(patients, rows):
    result = []
    for pid in sorted(patients):
        own = [r for r in rows if r['patient_id'] == pid]
        counts = Counter(r['status'] for r in own)
        total = len(own)
        failure = counts['PARSED_ARTIFACT_CORRUPT'] + counts['PARSED_ARTIFACT_MISSING']
        if any(r.get('parse_failure') for r in own) or failure:
            status = 'HAS_PARSE_FAILURE'
        elif not total or counts['AMBIGUOUS']:
            status = 'AMBIGUOUS'
        elif counts['CURRENT_PARSED'] == total:
            status = 'FULLY_CURRENT'
        elif counts['NEVER_PARSED']:
            status = 'HAS_NEVER_PARSED_PDFS'
        elif counts['OLD_VERSION_PARSED'] == total:
            status = 'ALL_OLD_VERSION'
        else:
            status = 'PARTIALLY_CURRENT'
        result.append(dict(patient_id=pid, pdf_count=total,
                           current_parsed_count=counts['CURRENT_PARSED'],
                           old_version_count=counts['OLD_VERSION_PARSED'],
                           never_parsed_count=counts['NEVER_PARSED'],
                           failed_or_corrupt_count=sum(bool(r.get('parse_failure')) or r['status'] in
                                                      {'PARSED_ARTIFACT_CORRUPT', 'PARSED_ARTIFACT_MISSING'} for r in own),
                           source_changed_count=counts['SOURCE_CHANGED_AFTER_PARSE'],
                           patient_parse_status=status,
                           has_recent_files_since_2026_10_25=any(r['recent_since_2026_10_25'] for r in own)))
    return result


ENTITY = re.compile(r'\bamyloid\s*angiopath\w*|\bcerebral\s+amyloid\s+angiopath\w*|\bcaa\b', re.I)
MARKER = re.compile(r'(?:kortikal\w*\s+)?superfiziell\w*\s+(?:siderose|hamosiderose)|'
                    r'oberflachlich\w*\s+hamosiderose|\bcss\b|\bcsah\b|convexity\s+sah|'
                    r'lobar\w*\s+(?:microbleeds|mikroblutungen)', re.I)
NEGATIVE = re.compile(r'kein\w*\s+(?:(?:eindeutig\w*|sicher\w*|relevant\w*)\s+)?(?:hinweis|anhalt|nachweis)|'
                      r'keine?\s+(?:zerebrale?\s+|cerebrale?\s+)?amyloid|\bohne\s+(?:hinweis|anhalt|nachweis)|'
                      r'ausgeschlossen|nicht.{0,90}(?:passend|vereinbar|kompatibel)|\bnot\s+(?:consistent|compatible)', re.I)
WORKUP = re.compile(r'\bdd\b|differen[tz]ial\w*|abklar\w*|ausschluss\w*|auszuschlie.en|'
                    r'(?<!kein )(?<!ohne )ausschlie.en|\bworkup\b|\brule out\b', re.I)
SUSPICION = re.compile(r'verdacht\w*|\bv\s*\.?\s*a\s*\.?(?=\s)|\ba\.?\s*e\.(?=\s)|suspekt\w*|verdachtig\w*|vereinbar|kompatibel|'
                      r'im\s+sinne|hinweis\w*\s+(?:auf|fur)|moglich\w*|wahrscheinlich\w*|'
                      r'\bpossible\b|\bprobable\b|\bsuspected\b|spricht\s+fur|sprechen\s+fur|am\s+ehesten', re.I)
ESTABLISHED = re.compile(r'bekannt\w*|diagnos\w*|gesichert\w*|bestatigt\w*|definit\w*|nachgewiesen\w*|'
                        r'\bbei\b|aufgrund|bedingt\w*|\bbedingt\s+durch\b', re.I)
DIAG_HEADING = re.compile(r'^(?:haupt|neben|entlassungs|aufnahme|vor|weitere\s+)?diagnos\w*\s*:?$|^vorerkrankungen\s*:?$', re.I)
SECTION_END = re.compile(r'^(?:anamnese|befund\w*|verlauf|epikrise|therapie\w*|medikation|empfehlungen|'
                         r'zusammenfassung|untersuchungen|diagnostik|sozialanamnese|familienanamnese)\s*:?$', re.I)


def assertion(clause, *, diagnosis=False):
    value = norm(' '.join(clause.split()))
    if not ENTITY.search(value):
        return None
    for part in reversed(value.split(',')[1:]):
        if ENTITY.search(part) and re.match(r'\s*(?:bekannt|gesichert|diagnos|v\.?\s*a\.?|verdacht|dd\b|kein|ohne)', part):
            value = part.strip()
            break
    # Clause boundaries matter: a negation of a different diagnosis must not erase CAA.
    if re.search(r'nicht\s+(?:(?:sicher|eindeutig)\s+)?(?:ausgeschlossen|auszuschlie.en)', value):
        return 'CAA_DIFFERENTIAL_OR_WORKUP'
    # Attach negative support to CAA, rather than to another diagnosis on the same line.
    entity_start = ENTITY.search(value).start()
    prefix = value[:entity_start]
    suffix = value[ENTITY.search(value).end():]
    negative_prefix = re.search(
        r'(?:kein\w*|ohne)\s+(?:(?:eindeutig\w*|sicher\w*|relevant\w*)\s+)?'
        r'(?:hinweis\w*|anhalt\w*|nachweis\w*)\s+(?:(?:auf|fur|einer?|eines)\s+)?'
        r'(?:(?:eine\w*|zerebral\w*|cerebral\w*)\s+)*$|'
        r'keine?\s+(?:(?:zerebral\w*|cerebral\w*)\s+)*$', prefix)
    negative_pattern = (re.search(r'nicht.{0,55}(?:muster|passend|vereinbar|kompatibel)', value)
                        and not re.search(r'[,;]', value))
    if negative_prefix or negative_pattern or re.match(r'\s*(?:wurde\s+|ist\s+)?(?:ausgeschlossen|nicht\s+(?:bestatigt|nachweisbar))', suffix):
        return 'CAA_NEGATED'
    if re.search(r'familienanamnese|\b(?:mutter|vater|bruder|schwester)\b|\bfamily history\b|\bif\b|\bfalls\b|\bsofern\b', value):
        return 'CAA_REVIEW_CANDIDATE'
    if WORKUP.search(value):
        return 'CAA_DIFFERENTIAL_OR_WORKUP'
    if re.search(r'fraglich\w*|\bfragl\.|unbestatigt\w*|\bunklar\w*|\?', value):
        return 'CAA_REVIEW_CANDIDATE'
    if SUSPICION.search(value):
        return 'CAA_SUSPECTED'
    if ESTABLISHED.search(value) or diagnosis:
        return 'CAA_ESTABLISHED'
    return 'CAA_REVIEW_CANDIDATE'


def caa_text(payload):
    hits = []
    diagnosis, family = False, False
    for page in payload['pages']:
        previous = ''
        lines = page['text'].splitlines()
        for i, line in enumerate(lines):
            value = norm(line.strip())
            if DIAG_HEADING.match(value):
                diagnosis, family = True, False
            elif re.match(r'^familienanamnese\s*:?$', value):
                diagnosis, family = False, True
            elif SECTION_END.match(value) or re.match(r'^(?:kernspin|computertomo|mrt\b|ct\b)', value):
                diagnosis, family = False, False
            # Preserve a short wrapped qualifier, but never import another full sentence.
            prev = norm(previous.strip())
            wrapped = (len(prev) < 200 and not re.search(r'[.!?]$', prev)
                       and (SUSPICION.search(prev) or WORKUP.search(prev) or NEGATIVE.search(prev)))
            prefix = previous if wrapped or re.fullmatch(r'(?:v\.?\s*a\.?|verdacht auf|dd\s*:?|zur abklarung(?: einer)?)', prev) else ''
            # Join a wrapped entity or an assertion suffix without consuming the next diagnosis.
            following = lines[i + 1] if i + 1 < len(lines) else ''
            if ENTITY.search(norm(line)):
                context = prefix + ' ' + line
                if re.match(r'^\s*(?:ausgeschlossen|nicht\s+bestatigt|passend|vereinbar|wahrscheinlich|moglich)\b', norm(following)):
                    context += ' ' + following
                # Comma-separated DD qualifiers remain attached; semicolons separate assertions.
                for clause in re.split(r';|(?<=[!?])\s+(?=[A-ZÄÖÜ])', context):
                    status = assertion(('Familienanamnese: ' if family else '') + clause, diagnosis=diagnosis)
                    if status:
                        hits.append(status)
            previous = line
        if not hits:
            for line in page['text'].splitlines():
                if MARKER.search(norm(line)) and not re.search(r'kein\w*|ohne\s+(?:hinweis|nachweis)', norm(line)):
                    hits.append('CAA_REVIEW_CANDIDATE')
                    break
    return hits


MRI_TERM = r'(?:kernspintomogra(?:phie|fie)|c?mrt|c?mri|c?mr)'
MRI_RE = re.compile(r'\b' + MRI_TERM + r'\b', re.I)
BRAIN = re.compile(r'schadel|gehirn|cerebr|zerebr|cranial|kranial|\bbrain\b|\bcmrt\b|\bcmri\b|\bcmr\b|\bkopf\b', re.I)
BRAIN_ANATOMY = re.compile(r'supratentori|infratentori|seitenventrikel|hirnparenchym|hirnstamm|kleinhirn|'
                           r'cerebell|zerebell|gro.hirn|marklager|frontallappen|temporallappen', re.I)
NONBRAIN = re.compile(r'\bhws\b|\bbws\b|\blws\b|wirbelsaule|spinal|ruckenmark|abdomen|abdomin|becken|'
                      r'gelenk|schulter|knie|hufte|herz|cardia|kardial|prostata|mamma|leber|thorax', re.I)
PLANNED = re.compile(r'empfohl\w*|empfehl\w*|geplant\w*|termin\w*|soll\w*|vorgesehen|ausstandig|ausstehend|'
                     r'zu\s+(?:veranlassen|erfolgen|empfehlen)|nicht\s+(?:erfolgt|durchgefuhrt|moglich)|'
                     r'kein\w*\s+(?:mrt|mri|mr|kernspin)|abgelehnt|verweigert|kontraindiz|ambulant.{0,30}durchzufuhr|'
                     r'(?:wird|werden).{0,30}(?:erfolgen|durchgefuhrt)|erbeten|angemeldet|angefordert|'
                     r'(?:bitte|zur)\s+(?:durchfuhrung|kontrolle)|scheduled|planned|recommended', re.I)
FINDING = re.compile(r'\bbefund\b|beurteilung|unauffallig|nachweis|nachweisbar|zeigte|zeigten|zeigt|ergab|'
                     r'darstellung|signal|diffusion|mikroblut|microbleed|siderose|blutung|infarkt|glios|'
                     r'atrophie|marklager|ventrikel|raumforderung|ischam|flair|\bswi\b|\bt2\b', re.I)
PERFORMED = re.compile(r'durchgefuhrt|erfolgte|angefertigt|untersucht|zeigte|zeigten|zeigt|ergab|im\s+(?:mrt|mr|mri)|'
                       r'(?:mrt|mr|mri).{0,40}(?:nachweis|unauffallig|befund)', re.I)
DATE = re.compile(r'(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})(?!\d)')


def dates_in(text):
    values = set()
    for m in DATE.finditer(text):
        try:
            values.add(datetime(int(m[3]), int(m[2]), int(m[1])).date().isoformat())
        except ValueError:
            pass
    return values


def procedure_evidence(header, body, *, date=None):
    head, content = norm(header), norm(body)
    if NONBRAIN.search(head) and not BRAIN.search(head):
        return False
    if PLANNED.search(head) or PLANNED.search(content[:180]):
        return False
    brain = BRAIN.search(head) or ((BRAIN.search(content[:700]) or BRAIN_ANATOMY.search(content[:700]))
                                  and not NONBRAIN.search(content[:180]))
    # A generic MRI with a clearly cerebral report is acceptable; a date alone is insufficient.
    return bool(brain and len(content.strip()) >= 12 and (FINDING.search(content) or PERFORMED.search(content)))


def brain_mri(payload, dictionary):
    """Return deduplicatable procedure identities; no extraction of full findings."""
    identities = set()
    meta = payload['document']
    alltext = '\n'.join(page['text'] for page in payload['pages'])
    if not MRI_RE.search(norm(alltext)):
        return identities
    prepared = prepare_document(str(meta['patient_id']), 1, meta['filename'], meta['sha256'], payload)

    def add(header, body, date=None):
        dates = dates_in(header)
        day = date or (next(iter(dates)) if len(dates) == 1 else None)
        if procedure_evidence(header, body, date=day):
            # Repeat mentions of a dated examination collapse across documents.
            identities.add(('date', day) if day else ('report', hashlib.sha256(norm(' '.join(body.split())).encode()).hexdigest()))

    for procedure in prepared.diagnostic_procedures:
        if procedure.modality == 'MRI':
            header = procedure.source_text[:len(procedure.source_text) - len(procedure.result_text)]
            add(header, procedure.result_text, procedure.date)
    # Physician-maintained section dictionary includes non-colon/wrapped MR headings.
    for page in payload['pages']:
        text = page['text']
        # The structured splitter is primary; dictionary fallback is needed only for
        # pages with an MRI-style heading rather than incidental prose mentions.
        candidate_heading = re.search(r'^\s*(?:' + MRI_TERM + r'|brain\s+mri|cranial\s+mri)\b', norm(text), re.M)
        boundaries = list(heading_matches(text, dictionary)) if candidate_heading and not identities else []
        for i, item in enumerate(boundaries):
            if item.get('section_family') != 'MRI':
                continue
            start, end = item['start'], item['end']
            limit = boundaries[i + 1]['start'] if i + 1 < len(boundaries) else len(text)
            add(text[start:end], text[end:limit])
        # The existing splitter predates MR/cMRT and English report headings.
        # This is a bounded availability check on current text, not another PDF parser.
        if not identities:
            headers = list(re.finditer(r'^[ \t]*(?:' + MRI_TERM + r'|brain\s+mri|cranial\s+mri)\b[^\n]{0,180}', text, re.I | re.M))
            for i, match in enumerate(headers):
                limit = headers[i + 1].start() if i + 1 < len(headers) else min(len(text), match.end() + 3500)
                body = text[match.end():limit]
                stop = re.search(r'^[ \t]*(?:EEG|EKG|CT|Computertomographie|Zusammenfassung|Epikrise|'
                                 r'Entlassungsmedikation|Empfehlungen|Internistisches\s+Konsil)\b', body, re.I | re.M)
                add(match[0], body[:stop.start()] if stop else body)
        # Explicit completed brain MRI in prose, including historical external examinations.
        for line in text.splitlines():
            value = norm(line)
            if not MRI_RE.search(value) or not BRAIN.search(value) or NONBRAIN.search(value) or PLANNED.search(value):
                continue
            if (re.search(r'durchgefuhrt|erfolgte|angefertigt|zeigte|zeigten|ergab', value)
                    or (PERFORMED.search(value) and FINDING.search(value))):
                dates = dates_in(line)
                identities.add(('date', next(iter(dates))) if len(dates) == 1 else
                               ('report', hashlib.sha256(value.encode()).hexdigest()))
    # PDF line wrapping must not hide an explicitly completed examination in prose.
    # Use this only when no structured report or complete-line evidence was found.
    if not identities:
        for sentence in re.split(r'\n\s*\n|(?<=[!?])\s+|(?<=\.)\s+(?=[A-ZÄÖÜ])', alltext):
            value = norm(' '.join(sentence.split()))
            if len(value) > 1800 or not MRI_RE.search(value) or not BRAIN.search(value):
                continue
            if PLANNED.search(value) or NONBRAIN.search(value):
                continue
            strong = re.search(r'durchgefuhrt|erfolgte|angefertigt|zeigte|zeigten|ergab', value)
            if strong or (PERFORMED.search(value) and FINDING.search(value)):
                dates = dates_in(sentence)
                identities.add(('date', next(iter(dates))) if len(dates) == 1 else
                               ('report', hashlib.sha256(value.encode()).hexdigest()))
    # A dated report subsumes its undated narrative summary in that document.
    dated = {key for key in identities if key[0] == 'date'}
    return dated or identities


def strongest(values):
    return max(values, key=lambda s: RANK[s]) if values else None


def structured_status(value):
    value = str(value or '').upper()
    if value in {'DEFINITE', 'DEFINITE_CAA', 'DEFINITE (AUTOPSY)', 'PROBABLE_WITH_SUPPORTING_PATHOLOGY',
                 'PROBABLE WITH SUPP. PATHOLOGY', 'DIAGNOSED_UNSPECIFIED', 'DIAGNOSED', 'PROBABLE', 'PROBABLE_CAA'}:
        return 'CAA_ESTABLISHED'
    if value in {'SUSPECTED', 'POSSIBLE', 'POSSIBLE_CAA'}:
        return 'CAA_SUSPECTED'
    return None


def priority_sources(index, source_rows, text_status):
    """Return physician / accepted structure / deterministic criteria in that order."""
    choices = defaultdict(list)
    by_patient = defaultdict(set)
    for row in source_rows:
        by_patient[row['patient_id']].add(row['source_hash'])
    # Active stable versions are authoritative. Never fall back behind a stale pointer.
    from caa_pdf_extractor.workbench_store import active_entries, fingerprint
    for path in (ROOT / 'data/patient_artifacts').rglob('module_manifest.json'):
        manifest = read(path)
        pid = str(manifest.get('patient_id', path.parent.name))
        if pid not in by_patient:
            continue
        selected, _, _ = active_entries(manifest)
        entry = selected.get('CAA')
        if entry:
            for f in entry.get('artifacts', []):
                if Path(f['path']).suffix != '.json':
                    continue
                data = read(f['path'])
                for result in data.get('boston_patient_timepoints', []):
                    status = structured_status(result.get('classification'))
                    if status and truth(result.get('assessable')) and not result.get('review'):
                        choices[pid].append((1 if entry.get('validation', {}).get('physician_approved') else 2, status))
        diagnosis = selected.get('diagnoses')
        reviewfile = ROOT / 'data/review' / pid / 'diagnoses_review.json'
        if diagnosis and reviewfile.exists():
            digest = fingerprint(diagnosis)
            for review in read(reviewfile).values():
                if review.get('action') not in {'ACCEPT', 'EDIT'} or review.get('source_artifact_hash') != digest:
                    continue
                raw = copy.deepcopy(review.get('raw_value') or {})
                if review['action'] == 'EDIT':
                    raw.update(review.get('physician_value') or {})
                label = raw.get('canonical_name', '')
                if ENTITY.search(norm(label)):
                    status = assertion(label, diagnosis=True)
                    state = str(raw.get('status', '')).upper()
                    if state in {'SUSPECTED', 'POSSIBLE', 'UNCERTAIN'} and status == 'CAA_ESTABLISHED':
                        status = 'CAA_SUSPECTED'
                    choices[pid].append((1, status))
    # Accepted source-linked diagnostic events, checked against current source hashes.
    db_hash = {str(r['id']): r['sha256'] for r in index.db_sources}
    for event in index.db_caa:
        pid = event['patient_id']
        if db_hash.get(str(event['source_document_id'])) not in by_patient.get(pid, set()):
            continue
        status = structured_status(event.get('caa_status'))
        if status:
            choices[pid].append((2, status))
    # Existing deterministic Boston results are eligible only with intact CSV/source provenance.
    pointer = ROOT / 'data/debug/full_cohort_latest.txt'
    if pointer.exists():
        folder = Path(pointer.read_text().strip())
        file = folder / 'boston_results.csv'
        manifests = folder / 'csv_manifest.json'
        sources = folder / 'source_manifest.json'
        if file.exists() and manifests.exists() and sources.exists():
            checksums = read(manifests)
            frozen = defaultdict(set)
            for row in read(sources):
                frozen[str(row['patient_id'])].add(row['sha256'])
            if checksums.get(file.name) == utils.get_sha256(file):
                for row in csv.DictReader(file.open(encoding='utf-8-sig')):
                    pid = row['patient_id']
                    if not frozen[pid] or not frozen[pid].issubset(by_patient.get(pid, set())):
                        continue
                    status = structured_status(row.get('classification'))
                    holds = json.loads(row.get('holds') or '[]')
                    review = json.loads(row.get('review') or '[]')
                    if status and truth(row.get('assessable')) and not holds and not review:
                        choices[pid].append((3, status))
    # Reuse the existing clinical database's accepted formal CAA classification.
    # 'Not categorised' and a framework name alone convey no positive CAA status.
    for row in index.db_cases:
        pid = row['patient_id']
        status = structured_status(row.get('caa_criteria_stage'))
        if pid in by_patient and status:
            choices[pid].append((2, status))
    result = {}
    for pid, values in choices.items():
        priority = min(p for p, _ in values)
        result[pid] = strongest([s for p, s in values if p == priority])
    return result


def parse_one(row):
    """Only native PDF parsing and atomic cache write, isolated from clinical modules."""
    try:
        payload = utils.parse_pdf(row['pdf_path'], row['relative_path'], row['patient_id'])
        if not valid_payload(payload) or payload['document']['sha256'] != row['source_hash']:
            raise ValueError('SourceChangedOrInvalidParse')
        payload['document']['parser_signature'] = PARSER_KEY
        cache = CACHE / (row['source_hash'] + '.json')
        atomic(cache, payload)
        return str(cache), None
    except Exception as exc:
        return None, type(exc).__name__


def run(*, workers=4, resume=False):
    output = ROOT / 'output'
    patients, rows, orphans = inventory(ROOT / 'pdf_db')
    print(f'INVENTORY patient_items={len(patients) + orphans} pdfs={len(rows)}', flush=True)
    index = ParsedIndex()
    index.scan()
    for row in rows:
        row['status'], row['artifact'], row['reason'] = index.classify(row)
    initial = Counter(r['status'] for r in rows)
    print('INITIAL_COVERAGE ' + json.dumps(dict(initial), sort_keys=True), flush=True)
    actionfile = output / 'pdfs_requiring_parse_or_reparse.csv'
    if not resume or not actionfile.exists():
        csvout(actionfile,
               ['patient_id', 'filename', 'status', 'file_modified_time', 'recent_since_2026_10_25', 'reason'],
               [r for r in rows if r['status'] != 'CURRENT_PARSED'])
    failures = []
    pending = [r for r in rows if r['status'] in REPAIRABLE]
    unique = {r['source_hash']: {k: r[k] for k in ('pdf_path', 'relative_path', 'patient_id', 'source_hash')} for r in pending}
    errors = {}
    if unique:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(parse_one, row): digest for digest, row in unique.items()}
            for number, future in enumerate(as_completed(futures), 1):
                cache, error = future.result()
                digest = futures[future]
                if error:
                    errors[digest] = error
                else:
                    index.add(read(cache), Path(cache))
                if number % 50 == 0 or number == len(unique):
                    print(f'PARSE_PROGRESS {number}/{len(unique)} failures={len(errors)}', flush=True)
    for row in pending:
        if row['source_hash'] in errors:
            error = errors[row['source_hash']]
            row['parse_failure'] = error
            failures.append(dict(patient_id=row['patient_id'], filename=row['filename'], failure_type=error))
    # Recheck saved bytes rather than relying on success counters.
    for row in rows:
        row['status'], row['artifact'], row['reason'] = index.classify(row)
        if row['status'] == 'CURRENT_PARSED':
            payload = row['artifact'].load()
            if not valid_payload(payload) or payload['document']['sha256'] != row['source_hash']:
                row['status'] = 'PARSED_ARTIFACT_CORRUPT'
    summary = coverage(patients, rows)
    csvout(output / 'pdf_parser_coverage_summary.csv',
           ['patient_id', 'pdf_count', 'current_parsed_count', 'old_version_count', 'never_parsed_count',
            'failed_or_corrupt_count', 'source_changed_count', 'patient_parse_status', 'has_recent_files_since_2026_10_25'], summary)
    failurepath = output / 'pdf_parse_failures.csv'
    if failures:
        csvout(failurepath, ['patient_id', 'filename', 'failure_type'], failures)
    elif failurepath.exists():
        failurepath.unlink()
    text_status, mri, scan_failures = defaultdict(list), defaultdict(set), set()
    dictionary = load_dictionary()
    for number, row in enumerate(rows, 1):
        pid = row['patient_id']
        if row['status'] != 'CURRENT_PARSED':
            scan_failures.add(pid)
            continue
        payload = copy.deepcopy(row['artifact'].load())
        payload['document'].update(patient_id=pid, filename=row['filename'], relative_path=row['relative_path'])
        text_status[pid].extend(caa_text(payload))
        if payload['statistics'].get('pages_without_text', 0):
            scan_failures.add(pid)
        if number % 200 == 0:
            print(f'CORPUS_SCAN {number}/{len(rows)}', flush=True)
    higher = priority_sources(index, rows, text_status)
    statuses = {pid: higher.get(pid) or strongest(text_status[pid]) for pid in patients}
    mri_rows = [r for r in rows if statuses.get(r['patient_id']) in PRIMARY and r['status'] == 'CURRENT_PARSED']
    for number, row in enumerate(mri_rows, 1):
        pid = row['patient_id']
        payload = copy.deepcopy(row['artifact'].load())
        payload['document'].update(patient_id=pid, filename=row['filename'], relative_path=row['relative_path'])
        try:
            mri[pid].update(brain_mri(payload, dictionary))
        except Exception:
            scan_failures.add(pid)
        if number % 50 == 0 or number == len(mri_rows):
            print(f'MRI_SCAN {number}/{len(mri_rows)}', flush=True)
    main, ambiguous = [], []
    for pid in sorted(patients):
        status = statuses[pid]
        if status in PRIMARY:
            if pid in scan_failures and not mri[pid]:
                ambiguous.append(dict(patient_id=pid, CAA_status=status,
                                      short_reason='MRI availability unresolved: source parse failure or pages without text'))
                continue
            main.append(dict(patient_id=pid, CAA_status=status,
                             MRI_brain_present='TRUE' if mri[pid] else 'FALSE', MRI_count=len(mri[pid])))
        elif status in {'CAA_DIFFERENTIAL_OR_WORKUP', 'CAA_REVIEW_CANDIDATE'} or pid in scan_failures:
            ambiguous.append(dict(patient_id=pid, CAA_status=status or 'CAA_REVIEW_CANDIDATE',
                                  short_reason='Differential/workup only' if status == 'CAA_DIFFERENTIAL_OR_WORKUP'
                                  else 'Incomplete source text' if pid in scan_failures else 'Uncertain assertion or imaging markers only'))
    without = [{k: r[k] for k in ('patient_id', 'CAA_status')} for r in main if r['MRI_brain_present'] == 'FALSE']
    assert len(main) == len({r['patient_id'] for r in main})
    byid = {r['patient_id']: r for r in main}
    assert all(r['patient_id'] in byid and byid[r['patient_id']]['MRI_brain_present'] == 'FALSE' for r in without)
    assert all(r['CAA_status'] in PRIMARY and (r['MRI_brain_present'] == 'TRUE') == (r['MRI_count'] > 0) for r in main)
    assert all(not (set(text_status[r['patient_id']]) == {'CAA_NEGATED'} and r['patient_id'] not in higher) for r in main)
    csvout(output / 'caa_all_patients_mri_status.csv', ['patient_id', 'CAA_status', 'MRI_brain_present', 'MRI_count'], main)
    csvout(output / 'caa_patients_without_mri.csv', ['patient_id', 'CAA_status'], without)
    reviewpath = output / 'caa_ambiguous_for_review.csv'
    if ambiguous:
        csvout(reviewpath, ['patient_id', 'CAA_status', 'short_reason'], ambiguous)
    elif reviewpath.exists():
        reviewpath.unlink()
    counts = dict(PATIENT_ITEMS_IN_PDF_DB=len(patients) + orphans, TOTAL_PDFS=len(rows),
                  PDF_CURRENT_AFTER_UPDATE=sum(r['status'] == 'CURRENT_PARSED' for r in rows),
                  PDF_PARSE_FAILURES=len(failures),
                  PATIENTS_WITH_FILES_SINCE_2026_10_25=sum(r['has_recent_files_since_2026_10_25'] for r in summary),
                  CAA_ESTABLISHED_OR_SUSPECTED=len(main), CAA_WITH_BRAIN_MRI=len(main) - len(without),
                  CAA_WITHOUT_BRAIN_MRI=len(without), CAA_AMBIGUOUS=len(ambiguous),
                  WITHOUT_MRI_FILE='output/caa_patients_without_mri.csv', LLM_CALLS=0, DATABASE_WRITES='NONE')
    for key, value in counts.items():
        print(f'{key} = {value}', flush=True)
    return counts


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--resume', action='store_true', help='Preserve initial action CSV when resuming/rechecking')
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('--workers must be between 1 and 8')
    run(workers=args.workers, resume=args.resume)
