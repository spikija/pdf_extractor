import copy
from dataclasses import replace

import pytest

from caa_pdf_extractor.extraction import cognitive as c
from caa_pdf_extractor.preparation.models import PreparedDocument, PreparedSection, PatientDocumentBundle


def document(text, date='2026-01-20', heading=None, docid=1):
    sec = PreparedSection('doc' + str(docid) + '_sec1', '123', docid, heading, 1, 1, text, text)
    return PreparedDocument(docid, 'test.pdf', 'test.pdf', 'sha', 'discharge_letter', date, [sec])


def extract(text, **kwargs):
    return c.extract_document(document(text, **kwargs), '123')


@pytest.mark.parametrize('text,score', [
    ('MMSE 25/30',25), ('MMSE: 25/30',25), ('MMSE von 25/30 Punkten',25),
    ('MMSE 25 Punkte',25), ('Mini-Mental-Status 25 von 30 Punkten',25),
    ('MMST 18',18), ('MMSE aktuell 17 Punkte',17), ('MMSE von 6/30Pkt.',6),
    ('Hier zeigte sich ein MMSE von 18.',18), ('Mini-Mental-Status von 25 von 30 Punkten',25),
    ('Mini Mental State Examination 27/30',27), ('Mini Mental Status 30 Punkte',30),
    ('MMSE 0/30',0), ('MMSE 30/30',30),
    ('MMSE ist 14/30 Punkten',14), ('MMSE liegt bei 6/30 Punkten',6),
])
def test_explicit_mmse_forms(text, score):
    row, = extract(text)['assessments']
    assert (row['test_type'],row['score'],row['max_score']) == ('MMSE',score,30)
    assert text[row['source_start']:row['source_end']] == row['source_text']


def test_two_values_one_sentence_without_date_leakage():
    out = extract('Im MMSE 2018 25 Punkte, aktuell in der Testung 17 Punkte.')
    first, second = out['assessments']
    assert len(out['occurrences']) == 1
    assert (first['score'],first['assessment_year'],first['date_precision'],first['context']) == (25,2018,'YEAR','HISTORICAL')
    assert (second['score'],second['context']) == (17,'CURRENT')
    assert second['assessment_date'] is None and second['assessment_year'] is None
    assert first['source_end'] != second['source_end']


@pytest.mark.parametrize('raw,month', [('Jänner 2015',1), ('Januar 2015',1), ('01/2015',1), ('März 2015',3)])
def test_partial_month_never_padded(raw, month):
    row, = extract('MMSE 22/30 ' + raw)['assessments']
    assert row['assessment_date'] is None
    assert (row['assessment_year'],row['assessment_month'],row['date_precision'],row['date_basis']) == (2015,month,'MONTH','EXPLICIT_MONTH_YEAR')


def test_explicit_date_and_year():
    full, = extract('MMSE vom 05.02.2019: 25/30')['assessments']
    assert full['assessment_date'] == '2019-02-05' and full['date_precision'] == 'DAY'
    year, = extract('MMSE 2018 25 Punkte')['assessments']
    assert year['assessment_date'] is None and year['assessment_month'] is None
    assert year['assessment_year'] == 2018 and year['date_basis'] == 'EXPLICIT_YEAR'


@pytest.mark.parametrize('text', [
    'MMSE 25%', 'MMSE 31/30', 'MMSE -1/30', 'MMSE 25.5/30',
    'MMSE 18.03.2020', 'MMSE 2018', 'MMSE bei einer 25 Jahre alten Frau',
    'MMSE 25 Jahre', 'MMSE 25/100', 'MMSE 20-25 Punkte',
    'MMSE Zielwert 25 Punkte', 'MMSE 25 Punkte wären angestrebt.',
])
def test_non_measurements_are_not_scores(text):
    assert not extract(text)['assessments']


@pytest.mark.parametrize('text', [
    'MMSE 25/30. MRT am 10.01.2026.',
    'MRT am 10.01.2026, MMSE 25/30.',
    'Diagnose 2018, MMSE 25/30.',
    'Aufnahme am 10.01.2026. MMSE 25/30.',
    'MMSE 25/30; Entlassung 20.01.2026.',
])
def test_unrelated_dates_and_document_date_cannot_own_score(text):
    row, = extract(text)['assessments']
    assert row['assessment_date'] is None and row['assessment_year'] is None
    assert row['date_basis'] == 'UNKNOWN'


def test_historical_context_not_current():
    row, = extract('Im Vorbefund zeigte sich ein MMSE 25/30.')['assessments']
    assert row['context'] == 'HISTORICAL' and row['date_precision'] == 'UNKNOWN'


def test_dated_neuropsychology_only_current_test_inherits_date():
    raw = 'Neuropsychologischer Befund (10.01.2026)\nMMSE 25/30.\nIm Vorbefund MMSE 20/30.\nMRT (12.01.2026)\nMMSE 18/30.'
    first, historic, after_peer = extract(raw)['assessments']
    assert first['assessment_date'] == '2026-01-10' and first['date_basis'] == 'CURRENT_SECTION_DATE'
    assert first['context'] == 'CURRENT'
    assert historic['context'] == 'HISTORICAL' and historic['assessment_date'] is None
    assert after_peer['assessment_date'] is None


def test_moca_and_clock_do_not_become_mmse():
    out = extract('MMSE 25/30, MoCA 22/30, Uhrentest 6/10.')
    assert [(r['test_type'],r['score']) for r in out['assessments']] == [('MMSE',25),('MOCA',22)]
    assert not extract('Uhrentest 10/10. CERAD 24.')['assessments']


def test_historical_copy_groups_without_new_measurement_or_date():
    docs = [document('MMSE 22/30 Jänner 2015', docid=i, date=year+'-01-20') for i,year in [(1,'2020'),(2,'2024')]]
    bundle = PatientDocumentBundle('123',docs)
    before = copy.deepcopy(bundle)
    result = c.extract_bundle(bundle)
    assert len(result['assessments']) == 2 and len(result['canonical_candidates']) == 1
    group, = result['canonical_candidates']
    assert group['copied_forward_candidate'] and group['coarse_date_requires_review']
    assert not group['new_measurement_authorized']
    assert all(r['assessment_date'] is None for r in result['assessments'])
    assert bundle == before and result['Granite_calls'] == 0


def test_undated_scores_are_not_merged_by_score_alone():
    bundle = PatientDocumentBundle('123',[document('MMSE 25/30',docid=1),document('MMSE 25/30',docid=2)])
    result = c.extract_bundle(bundle)
    assert len(result['canonical_candidates']) == 2
    assert all(g['status']=='UNRESOLVED_UNDATED_IDENTITY' and not g['new_measurement_authorized'] for g in result['canonical_candidates'])


def test_invalid_full_test_date_remains_unknown_not_padded_or_partially_repaired():
    row, = extract('MMSE vom 31.02.2020: 25/30')['assessments']
    assert row['assessment_date'] is None and row['date_precision'] == 'UNKNOWN'
    assert row['temporal_review_required']


def test_unattributed_second_score_is_visible_for_bounded_temporal_review():
    result = extract('MMSE 25/30, 17/30.')
    assert len(result['assessments']) == 1
    occurrence, = result['occurrences']
    assert occurrence['fallback_eligible']
    assert occurrence['unattributed_candidates'][0]['raw_numeric_text'] == '17/30'
    assert occurrence['unattributed_candidates'][0]['reason'] == 'AMBIGUOUS_ADDITIONAL_TEST_VALUE'


def test_document_year_cannot_decide_current_versus_historical_context():
    row, = extract('MMSE 2018 25 Punkte', date='2019-01-02')['assessments']
    assert row['assessment_year'] == 2018 and row['date_precision'] == 'YEAR'
    assert row['context'] == 'UNKNOWN'


def test_here_alone_is_not_current_temporal_ownership():
    row, = extract('Hier zeigte sich ein MMSE von 18.')['assessments']
    assert row['score'] == 18 and row['context'] == 'UNKNOWN'
