from copy import deepcopy

import pytest

from caa_pdf_extractor.extraction import cognitive as c
from caa_pdf_extractor.extraction.cognitive_canonical import canonicalize
from caa_pdf_extractor.preparation.models import PreparedDocument, PreparedSection


def setup_rows(texts):
    rows, sections = [], {}
    for i, value in enumerate(texts, 1):
        sec = PreparedSection(f'd{i}', '123', i, None, 1, 1, value, value)
        doc = PreparedDocument(i, 'test.pdf', 'test.pdf', 'hash', 'discharge_letter', None, [sec])
        parsed = c.extract_document(doc, '123')['assessments']
        for row in parsed:
            row['case_id'] = 7
        rows += parsed
        sections[i, f'd{i}'] = value
    return rows, sections


def decision(row, source, anchor=None, role='UNKNOWN_MENTION', basis='UNRESOLVED_IDENTITY'):
    return {'mention_id': row['mention_id'], 'mention_sha256': c.digest(row),
            'anchor_mention_id': anchor, 'evidence_role': role, 'basis': basis,
            'reason': 'Synthetic reviewed source context.', 'identity_evidence': [{
                'source_document_id': row['source_document_id'], 'section_id': row['section_id'],
                'start': 0, 'end': len(source), 'text': source}]}


def test_bare_equal_scores_remain_unlinked_evidence():
    rows, sections = setup_rows(['MMSE 25/30', 'MMSE 25/30'])
    review = [decision(r, sections[r['source_document_id'], r['section_id']]) for r in rows]
    out = canonicalize(rows, review, sections)
    assert out['canonical_assessments'] == []
    assert len(out['unresolved_mentions']) == 2
    assert out['metrics']['cross_document_copied_forward_mentions'] == 0


def test_bare_score_cannot_seed_event_through_review_label_alone():
    rows, sections = setup_rows(['MMSE 25/30'])
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'CURRENT_PERFORMED', 'CURRENT_TEST_CONTEXT')]
    with pytest.raises(ValueError, match='BARE_SCORE_IS_NOT_PERFORMED_TEST'):
        canonicalize(rows, review, sections)


def test_doc12_two_events_no_year_leakage_and_no_source_loss():
    rows, sections = setup_rows(['Im MMSE 2018 25 Punkte, aktuell in der Testung 17 Punkte.'])
    original = deepcopy(rows)
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'HISTORICAL_REFERENCE', 'OWNED_TEST_DATE'),
              decision(rows[1], sections[1, 'd1'], rows[1]['mention_id'], 'CURRENT_PERFORMED', 'CURRENT_TEST_CONTEXT')]
    out = canonicalize(rows, review, sections)
    a, b = out['canonical_assessments']
    assert (a['score'], a['assessment_year'], a['assessment_role']) == (25, 2018, 'HISTORICAL_REFERENCE')
    assert (b['score'], b['assessment_year'], b['assessment_date']) == (17, None, None)
    assert a['canonical_assessment_key'] != b['canonical_assessment_key']
    assert rows == original
    assert [e['mention_id'] for e in out['evidence']] == [r['mention_id'] for r in rows]


def paired():
    rows, sections = setup_rows(['Aktuell MMSE 25/30.', 'Vorbefund MMSE vom 10.02.2015: 25/30.'])
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'CURRENT_PERFORMED', 'CURRENT_TEST_CONTEXT'),
              decision(rows[1], sections[2, 'd2'], rows[0]['mention_id'], 'COPIED_FORWARD', 'DATED_PRIOR_REPORT_REFERENCE')]
    return rows, sections, review


def test_reviewed_prior_report_link_supplies_existing_date_only():
    rows, sections, review = paired()
    out = canonicalize(rows, review, sections)
    event, = out['canonical_assessments']
    assert event['assessment_date'] == rows[1]['assessment_date'] == '2015-02-10'
    assert event['date_evidence_mention_id'] == rows[1]['mention_id']
    assert rows[0]['assessment_date'] is None
    assert out['metrics']['cross_document_copied_forward_mentions'] == 1
    assert len(out['evidence']) == 2
    assert event['clinical_approval'] is False and event['persistence_authorized'] is False


def test_distinct_reviewed_performances_with_equal_scores_stay_separate():
    rows, sections = setup_rows(['Aktuell MMSE 29/30.', 'Aktuell MMSE 29/30.'])
    review = [decision(r, sections[r['source_document_id'], r['section_id']], r['mention_id'],
                       'CURRENT_PERFORMED', 'CURRENT_TEST_CONTEXT') for r in rows]
    out = canonicalize(rows, review, sections)
    assert len(out['canonical_assessments']) == 2
    assert out['metrics']['cross_document_copied_forward_mentions'] == 0


@pytest.mark.parametrize('change,code', [
    ('missing', 'INCOMPLETE_OR_DUPLICATE_REVIEW'),
    ('duplicate', 'INCOMPLETE_OR_DUPLICATE_REVIEW'),
    ('stale', 'STALE_MENTION_REVIEW'),
    ('quote', 'IDENTITY_EVIDENCE_MISMATCH'),
    ('negative_span', 'IDENTITY_EVIDENCE_MISMATCH'),
    ('oversized_span', 'IDENTITY_EVIDENCE_MISMATCH'),
    ('no_proof', 'MISSING_IDENTITY_REVIEW_EVIDENCE'),
    ('bad_role', 'INVALID_REVIEW_ROLE_OR_REASON'),
    ('no_reason', 'INVALID_REVIEW_ROLE_OR_REASON'),
    ('score_alone', 'SCORE_ALONE_IS_NOT_IDENTITY'),
    ('wrong_doc', 'DOCUMENT_IDENTITY_MISMATCH'),
    ('copy_seed', 'UNSUPPORTED_EVENT_SEED'),
    ('unowned_date', 'MISSING_OWNED_TEST_DATE'),
    ('historical_as_current', 'CURRENT_HISTORICAL_ROLE_CONFLICT'),
    ('current_as_prior', 'PRIOR_TEST_ROLE_CONFLICT'),
    ('cross_doc_role', 'CROSS_DOCUMENT_COPY_ROLE_REQUIRED'),
    ('linked_unknown', 'UNKNOWN_MENTION_CANNOT_ESTABLISH_IDENTITY'),
    ('bad_anchor', 'INVALID_OR_CHAINED_ANCHOR'),
    ('cycle', 'INVALID_OR_CHAINED_ANCHOR'),
])
def test_fail_closed_review_guards(change, code):
    rows, sections, review = paired()
    if change == 'missing': review.pop()
    if change == 'duplicate': review.append(review[0])
    if change == 'stale': rows[0]['score'] = 24
    if change == 'quote': review[0]['identity_evidence'][0]['text'] = 'invented'
    if change == 'negative_span': review[0]['identity_evidence'][0]['start'] = -1
    if change == 'oversized_span': review[0]['identity_evidence'][0]['end'] += 100
    if change == 'no_proof': review[0]['identity_evidence'] = []
    if change == 'bad_role': review[0]['evidence_role'] = 'APPROXIMATE'
    if change == 'no_reason': review[0]['reason'] = ''
    if change == 'score_alone': review[1]['basis'] = 'EQUAL_SCORE'
    if change == 'wrong_doc': review[1]['basis'] = 'SAME_DOCUMENT_TEST_IDENTITY'
    if change == 'copy_seed': review[0]['evidence_role'] = 'COPIED_FORWARD'
    if change == 'unowned_date': review[0]['basis'] = 'OWNED_TEST_DATE'
    if change == 'historical_as_current': review[0]['evidence_role'] = 'HISTORICAL_REFERENCE'
    if change == 'current_as_prior': review[0]['basis'] = 'EXPLICIT_PRIOR_TEST'
    if change == 'cross_doc_role': review[1]['evidence_role'] = 'HISTORICAL_REFERENCE'
    if change == 'linked_unknown': review[1]['evidence_role'] = 'UNKNOWN_MENTION'
    if change == 'bad_anchor': review[1]['anchor_mention_id'] = 'absent'
    if change == 'cycle': review[0]['anchor_mention_id'] = rows[1]['mention_id']
    with pytest.raises(ValueError, match=code):
        canonicalize(rows, review, sections)


@pytest.mark.parametrize('field,value', [('case_id', 8), ('patient_id', '456'), ('test_type', 'MOCA'), ('score', 24), ('max_score', 29)])
def test_cross_owner_or_score_link_rejected(field, value):
    rows, sections, review = paired()
    rows[1][field] = value
    review[1]['mention_sha256'] = c.digest(rows[1])
    with pytest.raises(ValueError, match='INCOMPATIBLE_EVENT_LINK|CROSS_PATIENT_IDENTITY_EVIDENCE'):
        canonicalize(rows, review, sections)


def test_conflicting_owned_dates_reject_event_merge():
    rows, sections = setup_rows(['MMSE vom 10.02.2015: 25/30', 'MMSE vom 11.02.2015: 25/30'])
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'HISTORICAL_REFERENCE', 'OWNED_TEST_DATE'),
              decision(rows[1], sections[2, 'd2'], rows[0]['mention_id'], 'COPIED_FORWARD', 'DATED_PRIOR_REPORT_REFERENCE')]
    with pytest.raises(ValueError, match='CONFLICTING_EVENT_DATES'):
        canonicalize(rows, review, sections)


def test_same_document_multiple_evidence_one_event_and_replay_stable():
    rows, sections = setup_rows(['Aktuell MMSE 25/30. Zusammenfassung MMSE 25/30.'])
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'CURRENT_PERFORMED', 'CURRENT_TEST_CONTEXT'),
              decision(rows[1], sections[1, 'd1'], rows[0]['mention_id'], 'COPIED_FORWARD', 'SAME_DOCUMENT_TEST_IDENTITY')]
    one = canonicalize(rows, review, sections)
    assert one == canonicalize(rows, review, sections)
    assert len(one['canonical_assessments']) == 1 and len(one['evidence']) == 2
    assert one['metrics']['same_document_duplicates_collapsed'] == 1


@pytest.mark.parametrize('raw,precision', [('MMSE 2018 25 Punkte', 'YEAR'), ('MMSE 22/30 Jänner 2015', 'MONTH')])
def test_partial_dates_not_padded(raw, precision):
    rows, sections = setup_rows([raw])
    review = [decision(rows[0], sections[1, 'd1'], rows[0]['mention_id'], 'HISTORICAL_REFERENCE', 'OWNED_TEST_DATE')]
    event, = canonicalize(rows, review, sections)['canonical_assessments']
    assert event['date_precision'] == precision and event['assessment_date'] is None
