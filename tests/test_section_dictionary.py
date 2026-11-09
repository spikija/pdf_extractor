"""Physician ontology, source routing and geometry safety, entirely offline."""
import copy
import re
from pathlib import Path

import pytest
import yaml

from caa_pdf_extractor.section_dictionary import (CONFIG, build_section_tree,
    classify_heading, eeg_action, heading_matches, load_dictionary)
from caa_pdf_extractor.page_boilerplate import classify_boilerplate, clean_pages
from caa_pdf_extractor.section_dictionary_tools import known_heading_results, export
from caa_pdf_extractor.extraction.eeg_sources_v12 import discover
from caa_pdf_extractor.tfne_sources import select_sections, model_sections

DICTIONARY = load_dictionary()
MANUAL = list(dict.fromkeys(DICTIONARY['manual_entries']))


@pytest.mark.parametrize('source', MANUAL)
@pytest.mark.parametrize('suffix', ['', ' (06.02.2026)', ' vom 6.2.26'])
def test_every_manual_heading_and_dates(source, suffix):
    found = classify_heading(source.rstrip('*')+suffix, DICTIONARY)
    assert found
    if source.endswith('*'):
        assert found['match_type']=='PREFIX' and found['continuation_allowed']


@pytest.mark.parametrize('text,section,family,subtype', [
    ('RUHE EKG (04.06.2024)', 'RESTING_ECG','ECG','RESTING_ECG'),
    ('Psychiatrisches Konsil (03.06.2024)','PSYCHIATRY_CONSULT','PSYCHIATRY_CONSULT',None),
    ('Kernspintomographie: Schädel craniell nativ ... (06.02.2026)','MRI','MRI',None),
    ('Neurolinguistische Diagnostik (05.02.2026)','NEUROLINGUISTIC_ASSESSMENT','NEUROLINGUISTIC_ASSESSMENT',None),
    ('Elektroenzephalografie (EEG)-Befund (27.11.2023)','EEG','EEG',None),
    ('Mobile Video-EEG-Ableitung vom 19.5.25','VIDEO_EEG','EEG','VIDEO_EEG'),
    ('24-STUNDEN-EKG (16.01.2012)','LONG_TERM_ECG_24H','ECG','ECG_24H'),
    ('72-STUNDEN-EKG (...)','LONG_TERM_ECG_72H','ECG','ECG_72H'),
    ('Angiografie: A. carotis interna li, Angiografie: A. carotis interna re ...','ANGIOGRAPHY_MULTI_VESSEL','ANGIOGRAPHY',None),
])
def test_explicit_physician_acceptance_cases(text, section, family, subtype):
    found = classify_heading(text, DICTIONARY)
    assert (found['canonical_section'],found['section_family'])==(section,family)
    if subtype: assert found['subtype']==subtype


@pytest.mark.parametrize('text', [
    '  Ruhe   – EKG  ( 04 . 06 . 2024 ) : ',
    'R U H E   E K G (04.06.2024)',
    'RUHE\u00a0EKG (04.06.2024)',
])
def test_ocr_unicode_punctuation(text):
    assert classify_heading(text, DICTIONARY)['section_family']=='ECG'


def doc(text):
    return dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='page1',page_start=1,text=text)])


STOP_RULES = [e for e in DICTIONARY['sections'] if e['module_actions']['EEG']=='STOP' and e['match_type']!='REGEX']


@pytest.mark.parametrize('rule', STOP_RULES, ids=lambda e:e['canonical_section'])
def test_every_hard_peer_stops_before_heading_and_tfne_never_ingests_it(rule):
    heading=rule['variants'][0]+' (06.02.2026)'
    source=doc('EEG (04.06.2024)\nNormales EEG.\n'+heading+'\nFREMDTEXT_MARKER.\n')
    raw=copy.deepcopy(source)
    reports,_=discover(dict(patient_id='synthetic',documents=[source]))
    assert len(reports)==1
    assert reports[0]['eeg_report_text']=='Normales EEG.'
    assert heading not in reports[0]['eeg_report_text'] and source==raw
    if rule['canonical_section'] not in ('ANAMNESE','HOSPITAL_SUMMARY'):
        source=doc('Anamnese\nAphasie nach Minuten sistiert.\n'+heading+'\nFREMDTEXT_MARKER.\nZusammenfassung des Aufenthalts\nTaubheit erneut aufgetreten.')
        selected,_=select_sections('synthetic',source)
        payload=model_sections(selected)
        assert payload and 'FREMDTEXT_MARKER' not in repr(payload)
        assert {s['section_type'] for s in selected} <= {'ANAMNESE','ZUSAMMENFASSUNG_DES_AUFENTHALTES'}


@pytest.mark.parametrize('source', [
    'Psychiatrisches Konsil wurde im Verlauf empfohlen.',
    'CT wurde empfohlen, ohne Einfluss auf die EEG-Interpretation.',
    'Im EKG Sinusrhythmus.', 'EKG: Sinusrhythmus, HF 84/min.',
    'MRT wurde im Vorbefund erwähnt.', 'Status epilepticus links temporal.',
    'Neurologischer Befund wurde später erhoben.',
])
def test_incidental_prose_is_not_a_heading(source):
    assert classify_heading(source,DICTIONARY) is None


def test_contextual_assessment_status_and_structured_channel():
    heading=classify_heading('Beurteilung',DICTIONARY)
    assert heading['boundary_strength']=='CONTEXTUAL'
    assert eeg_action(heading,'Abnormes EEG.','Dieses EEG zeigt Theta-Aktivität.')=='KEEP'
    assert eeg_action(heading,'Abnormes EEG.\nInterpretation:\nFokale Störung.','1. Epilepsie.')=='STOP'
    heading=classify_heading('Status',DICTIONARY)
    assert eeg_action(heading,'Abnormes EEG.','epilepticus links temporal.')=='KEEP'
    assert eeg_action(heading,'Abnormes EEG.','Wach, orientiert.')=='STOP'
    assert eeg_action(classify_heading('EKG:',DICTIONARY),structured=True)=='KEEP'
    assert eeg_action(classify_heading('EKG (04.06.2024)',DICTIONARY),structured=True)=='STOP'


def test_page_join_section_tree_and_tfne_offsets():
    pages=[dict(page=1,text='Anamnese\nAphasie.\nNeurolinguistische'),
           dict(page=2,text='Diagnostik (05.02.2026)\nFREMDTEXT_MARKER.\nZusammenfassung des Aufenthalts\nErneute Taubheit.'),
           dict(page=3,text='\nTherapieempfehlung\nKontrolle.')]
    before=copy.deepcopy(pages)
    tree=build_section_tree(pages,10,DICTIONARY)
    assert [s['canonical_section'] for s in tree]==['ANAMNESE','NEUROLINGUISTIC_ASSESSMENT','HOSPITAL_SUMMARY','THERAPY_RECOMMENDATION']
    assert tree[0]['end_page']==1
    assert tree[1]['start_page']==1 and tree[1]['end_page']==2
    assert tree[1]['source_heading']=='Neurolinguistische\nDiagnostik (05.02.2026)'
    d=dict(source_document_id=10,filename='test.pdf',sections=[dict(s,section_id=f'p{i}',page_start=s['page']) for i,s in enumerate(pages)])
    selected,_=select_sections('synthetic',d)
    assert 'FREMDTEXT_MARKER' not in repr(model_sections(selected))
    assert 'Erneute Taubheit.' in repr(model_sections(selected))
    for s in selected:
        for span in s['spans']:assert s['source_text'][span['start']:span['end']]==span['text']
    assert pages==before


def test_unknown_probable_headings_are_preserved_for_review():
    text='Anamnese\nAphasie.\nUnbekannte Spezialuntersuchung:\nFremder Text.'
    tree=build_section_tree([dict(page=1,text=text)],10,DICTIONARY)
    unknown=next(s for s in tree if s['canonical_section']=='UNKNOWN_SECTION')
    assert unknown['source_heading']=='Unbekannte Spezialuntersuchung:' and unknown['heading_confidence']==.5
    assert text[unknown['start_offset']:unknown['body_start_offset']]==unknown['source_heading']


BOILERPLATE = [
    'Universitätsklinikum der', 'Paracelsus Medizinischen', 'Privatuniversität',
    'Universitätsklinik f. Neurologie', 'Telefon +43(0)5 7255-34625',
    'Neurologie@salk.at | www.salk.at',
    'Gemeinnützige Salzburger Landeskliniken Betriebsgesellschaft mbH',
    'Christian Doppler-Klinik | A-5020 Salzburg',
    'Ignaz Harrerstraße 79 | UID-Nr. ATU57476234',
    'Landesgericht Salzburg | FN 240832s',
    'Martha Reibenwein, geb.: 23.02.1947; Seite 8',
    'Anderer Beispielname, geb . : 1 . 2 . 1950; Seite 12',
    'Telefon +43 (0) 5 7255 - 12345',
    'Neurologie @ salk . at | www . salk . at',
    'Ignaz-Harrerstrasse 79 | UID Nr. ATU57476234',
]


@pytest.mark.parametrize('text',BOILERPLATE)
def test_known_boilerplate_requires_geometry_and_preserves_raw(text):
    assert classify_boilerplate(text)
    pages=[dict(page=1,width=600,height=840,blocks=[dict(block_id='f',bbox=[40,790,550,810],text=text)])]
    before=copy.deepcopy(pages)
    cleaned,excluded=clean_pages(pages)
    assert cleaned[0]['text']=='' and len(excluded)==1 and pages==before
    pages[0]['blocks'][0]['bbox']=[40,300,550,320]
    assert clean_pages(pages)[0][0]['text']==text
    if 'Seite 8' in text:
        assert excluded[0]['patient_identity_footer'] is True and excluded[0]['page_number']==8


@pytest.mark.parametrize('text',[
    'Intermittierende Theta-Aktivität links temporal.',
    'Aphasie seit gestern.', 'Die Patientin ist klinisch unverändert.',
    'Universitätsklinikum der Paracelsus Medizinischen Privatuniversität: Aphasie seit gestern.',
    'Keine Auffälligkeiten.\nUniversitätsklinikum der',
    'Parameter\nNormbereich\nEinheit',
])
def test_repetition_and_margin_do_not_remove_clinical_or_unknown_blocks(text):
    pages=[dict(page=n,width=600,height=840,blocks=[dict(block_id=f'b{n}',bbox=[40,790,550,810],text=text)]) for n in (1,2,3)]
    cleaned,excluded=clean_pages(pages)
    assert not excluded and all(p['text']==text for p in cleaned)


def test_edit_reload_and_export_without_runtime_docx(tmp_path,monkeypatch):
    data=copy.deepcopy(DICTIONARY)
    next(e for e in data['sections'] if e['canonical_section']=='MRI')['variants'].append('Neue Bildgebungsüberschrift')
    path=tmp_path/'section_dictionary.yaml';path.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
    assert classify_heading('Neue Bildgebungsüberschrift (06.02.2026)',load_dictionary(path))['section_family']=='MRI'
    # Production loading only opens YAML, never the DOCX.
    original=Path.read_text
    def checked(self,*args,**kwargs):
        assert self.suffix!='.docx'
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',checked)
    assert load_dictionary()['sections']
    assert all(r['passed'] for r in known_heading_results(data))
    export(tmp_path,data)
    assert (tmp_path/'section_dictionary_review.csv').exists()
    assert (tmp_path/'section_dictionary_ambiguous_entries.csv').exists()


def test_video_type_and_clinical_date_preserved():
    text='Mobile Video-EEG-Ableitung vom 19.5.25\nNormales EEG.\nNeurolinguistische Diagnostik (20.5.25)\nFremder Text.'
    reports,_=discover(dict(patient_id='synthetic',documents=[doc(text)]))
    assert reports[0]['recording_type']=='VIDEO_EEG' and reports[0]['eeg_date']=='2025-05-19'
    assert reports[0]['eeg_date_precision']=='DAY' and reports[0]['eeg_report_text']=='Normales EEG.'


def test_blank_lines_preserve_heading_offsets_and_bulleted_references_stay_references():
    text='\n \n\nEEG (04.06.2024)\nNormales EEG.\n'
    reports,_=discover(dict(patient_id='synthetic',documents=[doc(text)]))
    assert reports[0]['header_start']==text.index('EEG')
    text='Anamnese\n- EEG vom 23.08.2022: Keine Zeichen erhöhter zerebraler Erregungsbereitschaft\n- Liquorpunktion unauffällig.'
    reports,_=discover(dict(patient_id='synthetic',documents=[doc(text)]))
    assert reports==[]


def test_register_signature_does_not_consume_appended_clinical_text():
    text='Landesgericht Salzburg | FN 240832s | Aphasie'
    assert classify_boilerplate(text) is None


def test_yaml_edits_reach_eeg_and_tfne_routing_without_python_edits(tmp_path,monkeypatch):
    from caa_pdf_extractor import section_dictionary as runtime
    data=copy.deepcopy(DICTIONARY)
    next(e for e in data['sections'] if e['canonical_section']=='MRI')['variants'].append('Neue Bildgebung')
    next(e for e in data['sections'] if e['canonical_section']=='VIDEO_EEG')['variants'].append('Filmableitung')
    next(e for e in data['sections'] if e['canonical_section']=='ANAMNESE')['variants'].append('Erweiterte Anamnese')
    (tmp_path/'section_dictionary.yaml').write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
    monkeypatch.setattr(runtime,'CONFIG',tmp_path)
    text='Filmableitung (04.06.2024)\nNormales EEG.\nNeue Bildgebung (04.06.2024)\nFREMDTEXT_MARKER.'
    reports,_=discover(dict(patient_id='synthetic',documents=[doc(text)]))
    assert len(reports)==1 and reports[0]['recording_type']=='VIDEO_EEG'
    assert reports[0]['eeg_report_text']=='Normales EEG.'
    selected,_=select_sections('synthetic',doc('Erweiterte Anamnese\nAphasie.\nNeue Bildgebung\nFREMDTEXT_MARKER.'))
    assert selected and 'FREMDTEXT_MARKER' not in repr(model_sections(selected))


@pytest.mark.parametrize('heading',['Internistisches Konsil (31.07.2026)','Medikation: Levetiracetam','Laborbefund (31.07.2026)','Neuropsychologischer Befund (31.07.2026)','Sonographie Befund (31.07.2026)'])
def test_pre_dictionary_peer_variants_remain_stops(heading):
    reports,_=discover(dict(patient_id='synthetic',documents=[doc('EEG (30.07.2026)\nNormales EEG.\n'+heading+'\nFREMDTEXT_MARKER.')]))
    assert reports[0]['eeg_report_text']=='Normales EEG.'
