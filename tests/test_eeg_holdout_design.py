"""Holdout design checks do not invoke or alter EEG extraction."""
import pytest
from caa_pdf_extractor.eeg_holdout_design import screen_source_text


@pytest.mark.parametrize('source',[
 'EEG empfohlen',
 'EEG geplant',
 'EEG sollte durchgeführt werden',
 'EEG wurde nicht durchgeführt.',
 'EEG-Befund nicht vorliegend.',
])
def test_planned_and_unavailable_are_not_performed(source):
    status,windows=screen_source_text(source)
    assert status!='EEG_PERFORMED' and not windows


def test_dated_report_with_negative_findings_is_performed():
    status,windows=screen_source_text('EEG Befund (01.02.2020)\nNormales EEG. Keine epilepsietypischen Potentiale.')
    assert status=='EEG_PERFORMED' and len(windows)==1


def test_future_plan_does_not_hide_a_separate_performed_report():
    text='EEG geplant (02.02.2020)\nEEG Befund (01.02.2020)\nMäßig abnormes EEG mit verlangsamtem Basisrhythmus.'
    status,windows=screen_source_text(text)
    assert status=='EEG_PERFORMED' and len(windows)==1 and windows[0]['date_signal']=='01.02.2020'


def test_reference_is_not_completion_evidence():
    status,windows=screen_source_text('EEG in den Vorbefunden erwähnt. Ein Befund liegt hier nicht vor.')
    assert status=='EEG_REFERENCED' and not windows
