"""Insert-only projection of the explicitly authorized frozen same-20 artifact.

No extraction, canonicalization, preparation, model client, or inference imports.
The caller owns the transaction, backup, rehearsal and durable commit manifest.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import text

AUTHORIZED_SHA256 = 'd7b55ad6ac11c5e36b5eae5b2c68a060e599c00c7ff727d5078aa927ed266970'
EVENT_TABLE = 'cognitive_assessment'
EVIDENCE_TABLE = 'cognitive_assessment_evidence'
TABLES = (EVENT_TABLE, EVIDENCE_TABLE)
EVENT_FIELDS = ('case_id','source_document_id','test_type','score','max_score',
    'assessment_date','assessment_year','assessment_month','date_precision','date_basis',
    'context','source_text','page','section_id','source_start','source_end')
EVIDENCE_FIELDS = ('case_id','source_document_id','page','section_id','source_start','source_end',
    'score_start','score_end','evidence_text','evidence_role','test_type','score','max_score','date_text')
EVENT_IDENTITY = ('case_id','source_document_id','section_id','source_start','source_end','test_type')
EVIDENCE_IDENTITY = ('case_id','source_document_id','section_id','score_start','score_end','test_type')


@dataclass(frozen=True)
class FrozenMMSE:
    raw: bytes

    def __post_init__(self):
        if type(self.raw) is not bytes:
            raise ValueError('ARTIFACT_BYTES_MUST_BE_IMMUTABLE')
        if sha256(self.raw).hexdigest() != AUTHORIZED_SHA256:
            raise ValueError('UNAUTHORIZED_OR_MODIFIED_ARTIFACT')
        a = self.document()
        if a['decision'] != 'READY_FOR_MMSE_CANONICAL_BACKFILL' or any(a['safety_counts'].values()):
            raise ValueError('FROZEN_READINESS_GATE_FAILED')
        if (len(a['canonical_assessments']),len(a['evidence']),len(a['unresolved_mentions'])) != (22,55,8):
            raise ValueError('FROZEN_CARDINALITY_MISMATCH')
        events = {e['canonical_assessment_key']:e for e in a['canonical_assessments']}
        mentions = {e['mention_id']:e for e in a['evidence']}
        if len(events) != 22 or len(mentions) != 55:
            raise ValueError('DUPLICATE_FROZEN_IDENTITY')
        for e in a['evidence']:
            key = e['cognitive_assessment_key']
            if key is None:
                if e['evidence_role'] != 'UNKNOWN_MENTION':
                    raise ValueError('UNRESOLVED_ROLE_MISMATCH')
            else:
                parent = events[key]
                if e['evidence_role'] == 'UNKNOWN_MENTION' or any(e[k] != parent[k] for k in
                        ('patient_id','case_id','test_type','score','max_score')):
                    raise ValueError('FROZEN_LINK_MISMATCH')
        for e in events.values():
            if {m for m,r in mentions.items() if r['cognitive_assessment_key']==e['canonical_assessment_key']} != set(e['supporting_mention_ids']):
                raise ValueError('FROZEN_SUPPORT_MISMATCH')
            owner = mentions[e['date_evidence_mention_id']]
            if any(e[k]!=owner[k] for k in ('assessment_date','assessment_year','assessment_month','date_precision','date_basis')):
                raise ValueError('FROZEN_DATE_PROVENANCE_MISMATCH')

    def document(self):
        # Decode a fresh object: callers cannot mutate the authorized bytes.
        return json.loads(self.raw)


def load_frozen(path):
    return FrozenMMSE(Path(path).read_bytes())


def event_projection(row):
    return {**{k:row[k] for k in EVENT_FIELDS}, 'deleted':False}


def evidence_projection(row, parent_ids):
    key = row['cognitive_assessment_key']
    if key is None:
        if row['evidence_role'] != 'UNKNOWN_MENTION':
            raise ValueError('UNRESOLVED_ROLE_MISMATCH')
        parent_id = None
    else:
        if key not in parent_ids or row['evidence_role']=='UNKNOWN_MENTION':
            raise ValueError('MISSING_OR_UNSUPPORTED_PARENT')
        parent_id = parent_ids[key]
    return {**{k:row[k] for k in EVIDENCE_FIELDS}, 'cognitive_assessment_id':parent_id, 'deleted':False}


def row_key(row, identity):
    return tuple(row[k] for k in identity)


def classify(expected, actual):
    if actual is None:
        return 'INSERT_MISSING'
    if actual != {**expected,'id':actual['id']}:
        raise ValueError('CONFLICT_EXISTING_NO_OVERWRITE')
    return 'EXACT_MATCH_EXISTING'


def images(conn, table):
    if table not in TABLES:
        raise ValueError('TABLE_OUTSIDE_BACKFILL_SCOPE')
    return [dict(r) for r in conn.execute(text(
        f'SELECT to_jsonb(t) AS row, xmin::text AS xmin FROM public."{table}" t ORDER BY id')).mappings()]


def insert(conn, table, values):
    if table not in TABLES:
        raise ValueError('TABLE_OUTSIDE_BACKFILL_SCOPE')
    columns = list(values)
    placeholders = ['CAST(:assessment_date AS date)' if k=='assessment_date' else ':'+k for k in columns]
    sql = f'INSERT INTO public."{table}" ('+','.join('"'+k+'"' for k in columns)+') VALUES ('+','.join(placeholders)+') RETURNING id'
    return conn.execute(text(sql),values).scalar_one()


def check_current(conn, frozen, complete=False):
    """Compare every stored column, including soft-deletion, before any inserts."""
    a = frozen.document()
    current = {t:images(conn,t) for t in TABLES}
    by_event = {row_key(r['row'],EVENT_IDENTITY):r['row'] for r in current[EVENT_TABLE]}
    by_evidence = {row_key(r['row'],EVIDENCE_IDENTITY):r['row'] for r in current[EVIDENCE_TABLE]}
    expected_event_keys = {row_key(e,EVENT_IDENTITY) for e in a['canonical_assessments']}
    expected_evidence_keys = {row_key(e,EVIDENCE_IDENTITY) for e in a['evidence']}
    if not set(by_event)<=expected_event_keys or not set(by_evidence)<=expected_evidence_keys:
        raise ValueError('UNEXPECTED_EXISTING_COGNITIVE_ROWS')
    parent_ids = {}
    for e in a['canonical_assessments']:
        existing = by_event.get(row_key(e,EVENT_IDENTITY))
        if classify(event_projection(e),existing)=='EXACT_MATCH_EXISTING':
            parent_ids[e['canonical_assessment_key']] = existing['id']
        elif complete:
            raise ValueError('MISSING_COMMITTED_EVENT')
    for e in a['evidence']:
        existing = by_evidence.get(row_key(e,EVIDENCE_IDENTITY))
        if existing is not None:
            classify(evidence_projection(e,parent_ids),existing)
        elif complete:
            raise ValueError('MISSING_COMMITTED_EVIDENCE')
    if complete:
        if (len(by_event),len(by_evidence)) != (22,55):
            raise ValueError('POST_COMMIT_CARDINALITY_MISMATCH')
        nulls = [r for r in by_evidence.values() if r['cognitive_assessment_id'] is None]
        if len(nulls)!=8 or any(r['evidence_role']!='UNKNOWN_MENTION' for r in nulls):
            raise ValueError('UNRESOLVED_EVIDENCE_LINK_MISMATCH')
    return current, parent_ids


def apply(conn, frozen):
    """Exactly two INSERT targets; existing rows require exact frozen equality."""
    conn.execute(text('LOCK TABLE public.cognitive_assessment, public.cognitive_assessment_evidence IN SHARE ROW EXCLUSIVE MODE'))
    before, parent_ids = check_current(conn,frozen)
    a = frozen.document()
    inserted = {t:[] for t in TABLES}
    for e in a['canonical_assessments']:
        key = e['canonical_assessment_key']
        if key not in parent_ids:
            parent_ids[key] = insert(conn,EVENT_TABLE,event_projection(e))
            inserted[EVENT_TABLE].append(parent_ids[key])
    evidence_keys = {row_key(r['row'],EVIDENCE_IDENTITY) for r in before[EVIDENCE_TABLE]}
    for e in a['evidence']:
        if row_key(e,EVIDENCE_IDENTITY) not in evidence_keys:
            inserted[EVIDENCE_TABLE].append(insert(conn,EVIDENCE_TABLE,evidence_projection(e,parent_ids)))
    after, verified_ids = check_current(conn,frozen,complete=True)
    assert verified_ids == parent_ids
    return {'inserted_assessments':len(inserted[EVENT_TABLE]),'inserted_evidence':len(inserted[EVIDENCE_TABLE]),
            'updated':0,'deleted':0,'conflicts':0,'parent_ids':parent_ids,'inserted_ids':inserted,
            'before':before,'after':after,'Granite_calls':0,'cloud_calls':0}


def quote(value):
    return "'"+str(value).replace("'","''")+"'"


def rollback_sql(receipt):
    """Delete only inserted rows, with full-row/xmin guards against later edits."""
    statements = ['-- Exact guarded rollback of this insert-only MMSE transaction.',
        '-- Sequences are intentionally not reset; later generated IDs remain safe.',
        'BEGIN;', 'SET LOCAL standard_conforming_strings=on;',
        'LOCK TABLE public.cognitive_assessment, public.cognitive_assessment_evidence IN SHARE ROW EXCLUSIVE MODE;']
    guard_parts = []
    for table in TABLES:
        inserted_ids = set(receipt['inserted_ids'][table])
        for image in receipt['after'][table]:
            row = image['row']
            if row['id'] not in inserted_ids:
                continue
            literal = quote(json.dumps(row,ensure_ascii=False))
            guard_parts.append(f'IF NOT EXISTS (SELECT 1 FROM public."{table}" t WHERE id={int(row["id"])} '
                f'AND xmin={quote(image["xmin"])}::xid AND to_jsonb(t)={literal}::jsonb) THEN '
                "RAISE EXCEPTION 'MMSE rollback refused: inserted row missing or changed'; END IF;")
    guard = '\n'.join(guard_parts)
    delimiter = '$mmse_guard_'+sha256(guard.encode()).hexdigest()[:16]+'$'
    assert delimiter not in guard
    statements.append('DO '+delimiter+' BEGIN\n'+guard+'\nEND '+delimiter+';')
    for table in reversed(TABLES):
        ids = receipt['inserted_ids'][table]
        if ids:
            statements.append(f'DELETE FROM public."{table}" WHERE id IN ('+','.join(str(int(i)) for i in ids)+');')
    statements.append('COMMIT;')
    return '\n'.join(statements)+'\n'
