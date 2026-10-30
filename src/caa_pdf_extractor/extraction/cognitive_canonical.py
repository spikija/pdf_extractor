"""Reviewed identity projection over immutable cognitive score/date mentions.

This module neither extracts scores/dates nor infers identity from equal scores.
An explicit, source-bound review supplies every event seed and evidence link.
Unlinked mentions remain evidence. There is deliberately no database writer.
"""
from collections import Counter
from copy import deepcopy
import re

from .cognitive import digest

VERSION = 'cognitive_canonical_v1'
ROLES = ('CURRENT_PERFORMED', 'HISTORICAL_REFERENCE', 'EXTERNAL_REPORT',
         'COPIED_FORWARD', 'UNKNOWN_MENTION')
SEED_BASES = ('CURRENT_TEST_CONTEXT', 'OWNED_TEST_DATE', 'EXPLICIT_PRIOR_TEST')
LINK_BASES = ('SAME_DOCUMENT_TEST_IDENTITY', 'DATED_PRIOR_REPORT_REFERENCE',
              'NAMED_PRIOR_NEUROLOGY_TEST')
DATE_FIELDS = ('assessment_date', 'assessment_year', 'assessment_month',
               'date_precision', 'date_basis')


def canonicalize(mentions, review, sections):
    """Validate a complete frozen review and return canonical events/evidence.

    sections maps (document_id, section_id) to the exact prepared source text.
    review entries bind the entire input mention hash, seed/link basis, and one
    or more exact source spans supporting the reviewed identity decision.
    These checks establish provenance, not independent clinical approval.
    """
    rows = {r['mention_id']: deepcopy(r) for r in mentions}
    if len(rows) != len(mentions):
        raise ValueError('DUPLICATE_MENTION_ID')
    decisions = {r['mention_id']: r for r in review}
    if len(decisions) != len(review) or set(rows) != set(decisions):
        raise ValueError('INCOMPLETE_OR_DUPLICATE_REVIEW')
    evidence, events = [], {}
    for mid, row in rows.items():
        d = decisions[mid]
        if d['mention_sha256'] != digest(row):
            raise ValueError('STALE_MENTION_REVIEW')
        source = sections[row['source_document_id'], row['section_id']]
        if source[row['source_start']:row['source_end']] != row['source_text']:
            raise ValueError('SOURCE_SPAN_MISMATCH')
        if d['evidence_role'] not in ROLES or not d.get('reason'):
            raise ValueError('INVALID_REVIEW_ROLE_OR_REASON')
        proofs = d.get('identity_evidence', [])
        if not proofs:
            raise ValueError('MISSING_IDENTITY_REVIEW_EVIDENCE')
        for proof in proofs:
            psource = sections[proof['source_document_id'], proof['section_id']]
            if (not proof['text'] or proof['start'] < 0 or proof['end'] > len(psource) or proof['end'] <= proof['start']
                    or psource[proof['start']:proof['end']] != proof['text']):
                raise ValueError('IDENTITY_EVIDENCE_MISMATCH')
            owners = [r for r in rows.values() if r['source_document_id'] == proof['source_document_id']
                      and r['section_id'] == proof['section_id'] and r['patient_id'] == row['patient_id']
                      and r['case_id'] == row['case_id']]
            if not owners:
                raise ValueError('CROSS_PATIENT_IDENTITY_EVIDENCE')
        anchor = d['anchor_mention_id']
        if anchor is None:
            if d['evidence_role'] != 'UNKNOWN_MENTION' or d['basis'] != 'UNRESOLVED_IDENTITY':
                raise ValueError('UNRESOLVED_MENTION_MUST_ABSTAIN')
        else:
            if anchor not in rows or decisions[anchor]['anchor_mention_id'] != anchor:
                raise ValueError('INVALID_OR_CHAINED_ANCHOR')
            target = rows[anchor]
            if any(row[k] != target[k] for k in ('patient_id', 'case_id', 'test_type', 'score', 'max_score')):
                raise ValueError('INCOMPATIBLE_EVENT_LINK')
            if d['evidence_role'] == 'UNKNOWN_MENTION':
                raise ValueError('UNKNOWN_MENTION_CANNOT_ESTABLISH_IDENTITY')
            if mid == anchor:
                if d['basis'] not in SEED_BASES or d['evidence_role'] == 'COPIED_FORWARD':
                    raise ValueError('UNSUPPORTED_EVENT_SEED')
                if d['basis'] == 'CURRENT_TEST_CONTEXT' and d['evidence_role'] != 'CURRENT_PERFORMED':
                    raise ValueError('CURRENT_HISTORICAL_ROLE_CONFLICT')
                if d['basis'] == 'OWNED_TEST_DATE' and row['date_precision'] == 'UNKNOWN':
                    raise ValueError('MISSING_OWNED_TEST_DATE')
                if d['basis'] == 'EXPLICIT_PRIOR_TEST' and d['evidence_role'] not in ('HISTORICAL_REFERENCE', 'EXTERNAL_REPORT'):
                    raise ValueError('PRIOR_TEST_ROLE_CONFLICT')
                own_proofs = [p for p in proofs if p['source_document_id'] == row['source_document_id']
                              and p['section_id'] == row['section_id']
                              and p['start'] <= row['score_start'] < row['score_end'] <= p['end']]
                if not own_proofs:
                    raise ValueError('SEED_PROOF_MUST_COVER_SCORE')
                # A review label alone must not turn a bare score into a test.
                # This is a minimum source-support guard, not automatic role
                # classification: explicit reviewed ownership is still required.
                own_text = ' '.join(p['text'] for p in own_proofs)
                performed_context = re.search(r'neuropsycholog\w*\s*(?:diagnostik|testung|untersuchung)|psychodiagnostisch\w*\s*befund', own_text, re.I)
                if d['basis'] == 'CURRENT_TEST_CONTEXT' and row['context'] != 'CURRENT' and not performed_context:
                    raise ValueError('BARE_SCORE_IS_NOT_PERFORMED_TEST')
                if d['basis'] == 'EXPLICIT_PRIOR_TEST' and not re.search(r'vorbefund|früher|damals|extern|auswärt|laut.*bericht', own_text, re.I):
                    raise ValueError('MISSING_PRIOR_TEST_IDENTITY')
            else:
                if d['basis'] not in LINK_BASES:
                    raise ValueError('SCORE_ALONE_IS_NOT_IDENTITY')
                if (row['source_document_id'] != target['source_document_id']
                        and d['basis'] == 'SAME_DOCUMENT_TEST_IDENTITY'):
                    raise ValueError('DOCUMENT_IDENTITY_MISMATCH')
                if row['source_document_id'] != target['source_document_id'] and d['evidence_role'] != 'COPIED_FORWARD':
                    raise ValueError('CROSS_DOCUMENT_COPY_ROLE_REQUIRED')
            # Dated mentions must not silently contradict another linked date.
            dates = [rows[m] for m in rows if decisions[m]['anchor_mention_id'] == anchor
                     and rows[m]['date_precision'] != 'UNKNOWN']
            for other in dates:
                for key in ('assessment_date', 'assessment_year', 'assessment_month'):
                    if row[key] is not None and other[key] is not None and row[key] != other[key]:
                        raise ValueError('CONFLICTING_EVENT_DATES')
        span = row['date_source_span']
        evidence.append({**deepcopy(row), 'cognitive_assessment_key': None if anchor is None else digest(['event', anchor]),
                         'evidence_text': row['source_text'], 'evidence_role': d['evidence_role'],
                         'date_text': source[span[0]:span[1]] if span else None,
                         'canonicalization_basis': d['basis'], 'canonicalization_reason': d['reason'],
                         'identity_evidence': deepcopy(proofs), 'deleted': False})
    for mid, row in rows.items():
        d = decisions[mid]
        if d['anchor_mention_id'] != mid:
            continue
        members = [e for e in evidence if e['cognitive_assessment_key'] == digest(['event', mid])]
        # Use only unchanged, accepted date fields from a linked source mention.
        order = {'DAY': 0, 'MONTH': 1, 'YEAR': 2, 'UNKNOWN': 3}
        date_owner = min(members, key=lambda e: (order[e['date_precision']], e['mention_id']))
        context = {'CURRENT_PERFORMED': 'CURRENT', 'HISTORICAL_REFERENCE': 'HISTORICAL',
                   'EXTERNAL_REPORT': 'EXTERNAL_REPORT'}[d['evidence_role']]
        events[mid] = {**deepcopy(row), **{k: date_owner[k] for k in DATE_FIELDS},
                       'canonical_assessment_key': digest(['event', mid]), 'context': context,
                       'assessment_role': d['evidence_role'], 'date_evidence_mention_id': date_owner['mention_id'],
                       'canonicalization_reason': d['reason'], 'seed_basis': d['basis'],
                       'supporting_mention_ids': [e['mention_id'] for e in members],
                       'supporting_source_mentions': members, 'representative_mention_id': mid,
                       'representative_source_mention': deepcopy(row),
                       'clinical_approval': False, 'persistence_authorized': False}
    counts = Counter(e['evidence_role'] for e in evidence)
    same_doc = sum(e['mention_id'] != decisions[e['mention_id']]['anchor_mention_id']
                   and e['source_document_id'] == rows[decisions[e['mention_id']]['anchor_mention_id']]['source_document_id']
                   for e in evidence if e['cognitive_assessment_key'] is not None)
    cross_doc = sum(e['source_document_id'] != rows[decisions[e['mention_id']]['anchor_mention_id']]['source_document_id']
                    for e in evidence if e['cognitive_assessment_key'] is not None)
    return {'version': VERSION, 'canonical_assessments': list(events.values()), 'evidence': evidence,
            'unresolved_mentions': [e for e in evidence if e['cognitive_assessment_key'] is None],
            'metrics': {'total_explicit_score_mentions': len(mentions), 'canonical_assessments': len(events),
                        'evidence_only_unresolved_mentions': counts['UNKNOWN_MENTION'],
                        'same_document_duplicates_collapsed': same_doc,
                        'cross_document_copied_forward_mentions': cross_doc,
                        'historical_assessments': sum(e['assessment_role'] == 'HISTORICAL_REFERENCE' for e in events.values()),
                        'external_assessments': sum(e['assessment_role'] == 'EXTERNAL_REPORT' for e in events.values()),
                        'current_performed_assessments': sum(e['assessment_role'] == 'CURRENT_PERFORMED' for e in events.values()),
                        'evidence_roles': dict(counts)}}
