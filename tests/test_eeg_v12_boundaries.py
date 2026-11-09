"""EEG 1.2 segmentation-only regressions; no clinical assembler or model calls."""
import copy
import json
from pathlib import Path

import pytest

from caa_pdf_extractor.extraction.eeg_section_headings_v12 import classify_heading,normalize_heading,section_headings
from caa_pdf_extractor.extraction.eeg_sources_v12 import boundary,discover

FIXTURE=Path(__file__).parent/'fixtures/eeg_v12_boundary_2989288225_27700090930173.json'


def bundle(text,patient='123',document=10):
    return dict(patient_id=patient,documents=[dict(source_document_id=document,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=text,raw_pdf_text=text)])])


@pytest.mark.parametrize('heading',[
    'RUHE EKG (04.06.2024)','Ruhe-EKG:','RUHE-EKG','Ruhe EKG 04.06.2024',
    '  Ruhe   -  EKG  (04.06.2024):  ','R U H E  E K G (04.06.2024)',
    'Ruhe–EKG (04.06.2024)','RUHE\u00a0EKG (04.06.2024)',
])
def test_resting_ecg_heading_normalization(heading):
    classified=classify_heading(heading)
    assert classified and classified['section_type']=='ECG'
    assert normalize_heading(heading).replace(' ','')=='RUHEEKG'
    stop=boundary('Abnormes EEG.\n'+heading+'\nSR, HF 84/Min.')
    assert stop and stop[0].strip()==heading.strip()


@pytest.mark.parametrize('core,section',[
    ('EKG','ECG'),('ECG','ECG'),('12-KANAL-EKG','ECG'),('12-KANAL EKG','ECG'),
    ('24-STUNDEN-EKG','ECG'),('LANGZEIT-EKG','ECG'),('HOLTER-EKG','ECG'),
    ('PSYCHIATRISCHES KONSIL','PSYCHIATRIC_CONSULT'),('PSYCHIATRISCHER KONSIL','PSYCHIATRIC_CONSULT'),('PSYCHIATRISCHES KONSILIUM','PSYCHIATRIC_CONSULT'),
    ('ECHOKARDIOGRAPHIE','ECHOCARDIOGRAPHY'),('HERZECHO','ECHOCARDIOGRAPHY'),
    ('NEUROLOGISCHER STATUS','NEUROLOGICAL_STATUS'),('PSYCHIATRISCHER STATUS','PSYCHIATRIC_STATUS'),('SOMATISCHER STATUS','SOMATIC_STATUS'),
    ('MRT','MRI'),('MRI','MRI'),('CT','CT'),('CCT','CT'),('CTA','CT'),
    ('NEUROSONOLOGIE','NEUROSONOLOGY'),('DUPLEXSONOGRAPHIE','NEUROSONOLOGY'),('LABOR','LABORATORY'),
])
@pytest.mark.parametrize('suffix',['',':',' (04.06.2024)',' 04.06.2024:'])
def test_peer_headings_stop_before_the_heading(core,section,suffix):
    heading=core.lower()+suffix
    assert classify_heading(heading)['section_type']==section
    reports,_=discover(bundle('EEG (04.06.2024)\nNormales EEG.\n'+heading+'\nFremder Text.'))
    assert len(reports)==1 and reports[0]['eeg_report_text']=='Normales EEG.'
    assert reports[0]['excluded_section_type']==section


@pytest.mark.parametrize('text',[
    'Im EKG Sinusrhythmus.','EKG-Kanal tachykard.','EKG: Sinusrhythmus, HF 84/min.',
    'Im ECG Sinusrhythmus.','MRT wurde im Vorbefund erwähnt.',
    'CT wurde empfohlen, ohne Einfluss auf die EEG-Interpretation.',
    'HERZECHO wird im Vorbefund erwähnt.',
    'Laborchemisch kein Hinweis auf eine Störung.',
    'Psychiatrisches Konsil wurde im Verlauf empfohlen.',
    'Keine EEG-Veränderung im Vergleich zum MRT vom Vortag.',
])
def test_ordinary_prose_and_embedded_ecg_are_preserved(text):
    assert classify_heading(text) is None
    source='Normales EEG.\n'+text+'\nInterpretation:\nKeine epileptiformen Potenziale.'
    reports,_=discover(bundle('EEG (04.06.2024)\n'+source))
    assert reports[0]['eeg_report_text']==source


def test_structured_embedded_ecg_label_is_not_a_peer_section():
    source='Normales EEG.\nEKG:\nSinusrhythmus.\nInterpretation:\nNormales EEG.'
    assert boundary(source,structured=True) is None
    assert boundary(source+'\nEKG (04.06.2024)\nSeparater EKG-Befund.',structured=True) is not None


def test_permanent_physician_case_keeps_only_the_eeg_and_preserves_raw_source():
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    source=fixture['EEG_heading']+'\n'+fixture['expected_EEG_BOUNDED_SOURCE_TEXT']+fixture['subsequent_source_text']
    b=bundle(source,fixture['patient_id'],fixture['document_id']);before=copy.deepcopy(b)
    reports,_=discover(b)
    report,=reports
    assert report['eeg_report_text']==fixture['expected_EEG_BOUNDED_SOURCE_TEXT']
    assert report['excluded_section_heading']==fixture['expected_stop_heading']
    assert report['excluded_section_type']=='ECG'
    assert report['eeg_date']==fixture['EEG_date']
    assert all(term.casefold() not in report['eeg_report_text'].casefold() for term in ('RUHE EKG','Psychiatrisches Konsil','Sertralin','Quetiapin'))
    assert 'Minimal abnormes EEG' in report['eeg_report_text'] and 'links temporal' in report['eeg_report_text']
    assert {info['section_type'] for _,info in section_headings(source)}>={'ECG','PSYCHIATRIC_CONSULT'}
    assert b==before and 'Quetiapin' in b['documents'][0]['sections'][0]['raw_pdf_text']


def test_psychiatric_consult_stops_even_without_an_ecg_before_it():
    reports,_=discover(bundle('EEG (04.06.2024)\nNormales EEG.\nPsychiatrisches Konsilium (03.06.2024)\nSertralin und Quetiapin.'))
    assert reports[0]['eeg_report_text']=='Normales EEG.'
    assert reports[0]['excluded_section_type']=='PSYCHIATRIC_CONSULT'


def test_historical_exams_and_contiguous_ceeg_segments_stay_separate():
    text='\n'.join(f'EEG ({day}.01.19)\nAbnormes EEG mit Theta-Aktivität links temporal.' for day in (25,21,19,14))
    reports,_=discover(bundle(text))
    assert [r['eeg_date'] for r in reports]==['2019-01-25','2019-01-21','2019-01-19','2019-01-14']
    text='EEG (19.05.2025)\nNormales EEG.\nMOBILE Video-EEG-Ableitung (19.05.2025)\n'
    text+='cEEG Kurzbefund von 19.05.2025 14:08 - 20.05.2025 08:19\nAbnormes EEG mit GPDs.\n'
    text+='cEEG Kurzbefund von 20.05.2025 08:20 - 20.05.2025 11:23\nAbnormes EEG mit Delta-Aktivität.\nRuhe-EKG (20.05.2025)\nSR.'
    reports,_=discover(bundle(text))
    assert len(reports)==2
    video=next(r for r in reports if r['recording_type']=='VIDEO_EEG')
    assert len(video['segments'])==2 and all('Ruhe-EKG' not in r['eeg_report_text'] for r in video['segments'])
    assert video['segments'][1]['segment_start']=='2025-05-20T08:20'


def test_general_assessment_boundary_and_eeg_assessment_exception():
    source='EEG (04.06.2024)\nAbnormes EEG.\nInterpretation:\nLokale Funktionsstörung links temporal.\nBeurteilung\n1. Epilepsie.\nProzedere\nKlinische Empfehlungen.'
    reports,_=discover(bundle(source))
    assert reports[0]['eeg_report_text'].endswith('Lokale Funktionsstörung links temporal.')
    source='EEG (04.06.2024)\nAbnormes EEG.\nBeurteilung:\nDieses EEG zeigt eine lokale Funktionsstörung links temporal.'
    reports,_=discover(bundle(source))
    assert 'Dieses EEG zeigt' in reports[0]['eeg_report_text']
