"""Event-local hemorrhage acuity and six-target layout persistence regressions."""
from copy import deepcopy
from decimal import Decimal
import pytest
from caa_pdf_extractor.extraction.radiology_validators import derive_acuity
from caa_pdf_extractor.extraction.laboratory_layout import extract_page, page_tokens
from caa_pdf_extractor.extraction.laboratory_layout_workflow import observations
from caa_pdf_extractor.extraction.radiology_artifacts import VerifiedExtractionError
from test_laboratory_layout import fixture, word
import test_laboratory_layout_workflow as workflow_tests


@pytest.mark.parametrize('text', [
    'Nach Resorption einer Blutung rechts frontal supraventrikulär ventral Defekte und Hämosiderose.',
    'Resorbierte Blutung rechts frontal.', 'Residuelle Blutung rechts frontal.',
    'Residuen einer Blutung frontal.', 'Hämosiderinresiduen nach Blutung frontal.',
    'Defekt nach Blutung rechts frontal.', 'Alte Blutung frontal.',
    'Chronische Blutung frontal.', 'Stattgehabte Blutung frontal.',
    'Zustand nach Blutung frontal.', 'Posthämorrhagischer Defekt frontal nach Blutung.',
])
def test_resolved_event_is_nonacute(text):
    assert derive_acuity('lobar', text, text) is False


@pytest.mark.parametrize('quote,report', [
    ('Akute Blutung frontal rechts', 'Akute Blutung frontal rechts; alte Blutung temporal links.'),
    ('Akute Blutung frontal rechts', 'Alte Blutung temporal links; Akute Blutung frontal rechts.'),
    ('Frische Blutung frontal rechts', 'Alte Blutung temporal links und Frische Blutung frontal rechts.'),
    ('Akute Blutung frontal rechts bei alten Hämosiderinresiduen', 'Akute Blutung frontal rechts bei alten Hämosiderinresiduen.'),
    ('Akute Blutung frontal mit Hämosiderin', 'Akute Blutung frontal mit Hämosiderin.'),
])
def test_neighbor_history_or_generic_hemosiderin_does_not_change_current_event(quote, report):
    assert derive_acuity('lobar', quote, report) is True


def test_distinct_old_and_current_events_in_full_quote_are_indeterminate():
    text = 'Alte Blutung frontal links; akute Blutung temporal rechts.'
    assert derive_acuity('lobar', text, text) is None


@pytest.mark.parametrize('label,value,marker', [
    ('Total-Tau-Protein','342','csf_tau_total'), ('Total Tau','749','csf_tau_total'),
    ('Gesamt-Tau','749','csf_tau_total'), ('Tau gesamt','749','csf_tau_total'),
    ('t-tau','749','csf_tau_total'), ('tTau','749','csf_tau_total'),
    ('phospho-tau','54','csf_ptau_total'), ('phospho-tau','61','csf_ptau_total'),
    ('phospho Tau','61','csf_ptau_total'), ('p-tau','61','csf_ptau_total'),
    ('pTau','61','csf_ptau_total'), ('phosphoryliertes Tau','61','csf_ptau_total'),
])
def test_tau_alias_geometry_and_observation_routing(label,value,marker):
    candidates = extract_page(fixture(label, 'pg/ml', (value,)), 1)
    result = observations('123',42,candidates)
    assert len(result)==1
    assert result[0]['biomarker']==marker
    assert Decimal(result[0]['decimal_value'])==Decimal(value)
    assert result[0]['unit_text']=='pg/ml'
    assert result[0]['date_of_exam'] is None


@pytest.mark.parametrize('label', ['Tau','Protein','Beta-Amyloid','Phosphat','Total-Tau geplant', 'Total-Tau Referenzbereich'])
def test_unspecified_or_planned_or_reference_not_observations(label):
    assert observations('123',42,extract_page(fixture(label,'pg/ml',('342',)),1))==[]


def test_heading_without_result_and_reference_column_not_persisted():
    assert extract_page(fixture('Total-Tau','pg/ml',()),1)==[]
    tokens=fixture('Total-Tau','pg/ml',('342',))
    raw=[(t.x0,t.y0,t.x1,t.y1,'Referenz' if t.text=='Erster' else t.text) for t in tokens]
    assert observations('123',42,extract_page(page_tokens(raw,1),1))==[]


def test_csf_units_preserved_without_conversion_and_forged_units_blocked():
    for unit in ('pg/ml','ng/ml','ng/l'):
        candidate=extract_page(fixture('Total-Tau',unit,('342.00',)),1)[0]
        assert observations('123',42,[candidate])[0]['decimal_value']=='342.00'
        assert observations('123',42,[candidate])[0]['unit_text']==unit
        forged=deepcopy(candidate); forged['unit_text']='mg/dl'
        with pytest.raises(VerifiedExtractionError):observations('123',42,[forged])


def test_csf_commit_decimal_original_audit_and_idempotency():
    test=workflow_tests.WorkflowTests()
    test.setUp()
    try:
        test.current['candidates']=extract_page(fixture('Total-Tau','pg/ml',('342',)),1)
        assert test.extract()['persistable_observations']==1
        assert test.extract(False)['new_rows_inserted']==1
        assert test.rows[0].csf_tau_total==Decimal('342')
        assert test.rows[0].crp is None
        assert test.extract(False)['new_rows_inserted']==0
        assert test.extract(False)['duplicates_skipped']==1
    finally:
        test.doCleanups()
