"""Independent EEG 1.2 scope, geometry, status, interval and clinical regressions."""
import copy
import pytest
from caa_pdf_extractor.extraction.eeg_pipeline_v12 import assemble
from caa_pdf_extractor.extraction.eeg_sources_v12 import attributes
from caa_pdf_extractor.extraction.eeg_geometry_v12 import clean_pages
from caa_pdf_extractor.extraction.eeg_schema_v12 import defaults,validate_value


def bundle(text):
    return dict(patient_id='123',documents=[dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=text)])])


def result(text):return assemble(bundle(text))
def exam(text):return result('EEG Befund (01.02.2020)\n'+text)['eeg_exams'][0]


@pytest.mark.parametrize('heading,typ',[
    ('EEG','ROUTINE_EEG'),('Routine-EEG','ROUTINE_EEG'),('Standard-EEG','ROUTINE_EEG'),
    ('cEEG','CONTINUOUS_EEG'),('kontinuierliches EEG auf der Intensivstation','CONTINUOUS_EEG'),
    ('continuous EEG','CONTINUOUS_EEG'),('Video EEG','VIDEO_EEG'),('MOBILE Video-EEG-Ableitung','VIDEO_EEG'),
    ('interiktales EEG','UNKNOWN'),
])
def test_recording_types(heading,typ):assert attributes(heading)['recording_type']==typ


def test_four_historical_exams_and_duplicate_copies():
    body='\n'.join(f'EEG ({day}.01.19)\nAbnormes EEG mit fokaler Verlangsamung links temporal.' for day in (25,21,19,14))
    b=bundle(body);copy_doc=copy.deepcopy(b['documents'][0]);copy_doc['source_document_id']=11;b['documents'].append(copy_doc)
    r=assemble(b)
    assert len(r['eeg_exams'])==4
    assert {e['eeg_date'] for e in r['eeg_exams']}=={'2019-01-25','2019-01-21','2019-01-19','2019-01-14'}
    assert all(len(e['source_document_ids'])==2 for e in r['eeg_exams'])


@pytest.mark.parametrize('peer',[
    'Echokardiographie (02.01.2023)','24-STUNDEN-EKG (02.01.2023)','Neurologischer Status:',
    'Psychiatrischer Status:','Somatischer Status:','MRT (02.01.2023)','CT (02.01.2023)','Prozedere:',
    'Beurteilung\n1. Epilepsie mit fokalen Anfällen. Status aphasicus bei Erstmanifestation 01/2020.',
])
def test_peer_sections_cannot_contaminate_eeg(peer):
    e=exam('Abnormes EEG mit fokaler Verlangsamung rechts parietal.\nInterpretation:\nLokale Funktionsstörung rechts parietal.\n'+peer+'\nFremder klinischer Text. Levetiracetam erhöhen.')
    assert 'Fremder' not in e['eeg_report_text'] and 'Levetiracetam' not in e['eeg_report_text']
    assert e['NCSE'] is not True


def test_eeg_assessment_and_template_summary_are_retained():
    e=exam('Abnormes EEG.\nBeurteilung:\nDieses EEG zeigt eine lokale Funktionsstörung links temporal.')
    assert 'Dieses EEG zeigt' in e['eeg_report_text'] and e['focal_slowing_present'] is True


@pytest.mark.parametrize('source',[
    'Zeichen eines nicht konvulsiven Status epilepticus ausgehend links temporo-parietal, der nach Medikamentengabe nicht vollständig durchbrochen werden konnte.',
    'Dieses EEG zeigt das Bild eines nicht-konvulsiven Status epilepticus ausgehend links temporo-parietal.',
    'NCSE links temporo-parietal.', 'Status aphasicus links temporo-parietal.',
])
def test_explicit_current_ncse_positive(source):
    e=exam('Abnormes EEG.\nInterpretation:\n'+source)
    assert e['NCSE'] is True and e['electrographic_status_epilepticus'] is True
    assert e['status_type']=='NONCONVULSIVE_STATUS_EPILEPTICUS'
    assert e['status_side']=='LEFT' and e['status_region']=='TEMPOROPARIETAL'
    assert 'STATUS_EPILEPTICUS' in e['clinical_phenotype']


@pytest.mark.parametrize('source',[
    'Kein Status epilepticus-Muster.','Kein nicht-konvulsiver Status.',
    'Keine klinischen Zeichen eines NCSE.','V.a. NCSE.','Fragestellung NCSE.',
    'Anamnestisch NCSE.','Epileptiforme Potenziale links temporal.',
])
def test_ncse_not_established_from_nonpositive_scope(source):
    e=exam('Abnormes EEG. '+source)
    assert e['NCSE'] is not True and e['electrographic_status_epilepticus'] is not True


def test_routine_and_mobile_video_are_separate_and_temporal_status_survives():
    r=result('EEG (19.05.2025)\nNormales EEG.\n'
        'MOBILE Video-EEG-Ableitung vom 19.5.25\n'
        'cEEG Kurzbefund von 19.05.2025 14:08 - 20.05.2025 08:19\n'
        'Abnormes EEG. Interpretation: NCSE links temporal.\n'
        'cEEG Kurzbefund von 20.05.2025 08:20 - 20.05.2025 11:23\n'
        'Abnormes EEG. Kein Status-epilepticus Muster. Keine iktale Aktivität.')
    assert len(r['eeg_exams'])==2
    e=next(e for e in r['eeg_exams'] if e['recording_type']=='VIDEO_EEG')
    assert e['continuous'] is True and e['video'] is True and e['mobile'] is True
    assert len(e['CEEG_SEGMENTS'])==2
    assert e['CEEG_SEGMENTS'][0]['segment_status']=='PRESENT'
    assert e['CEEG_SEGMENTS'][1]['segment_status']=='ABSENT'
    assert e['NCSE_OCCURRED_DURING_RECORDING'] is True and e['status_at_final_segment']=='ABSENT'
    assert e['electrographic_status_epilepticus'] is True


def test_quality_changes_do_not_invalidate_preceding_eeg():
    e=exam('Abnormes EEG mit diffuser Theta-Aktivität. Ab ca 16:00\nist das EEG aufgrund starker Artefaktüberlagerung nicht beurteilbar. Um ca 18:00 Uhr entfernt die Patientin die Elektroden.')
    assert e['assessability']!='NOT_ASSESSABLE'
    assert [p['from_time'] for p in e['assessability_periods']]==['16:00','18:00']


def test_periodic_frequency_reactivity_and_probable_seizures():
    e=exam('Abnormes EEG. Rechts fronto-temporal periodische sharp-waves um 18 pro 10 Sekunden (LPDs). Erhaltene Reaktion auf Schmerzreiz.\nInterpretation:\nDieses EEG zeigt das Bild eines nicht-konvulsiven Status epilepticus rechts fronto-temporal.')
    assert e['NCSE'] is True and e['periodic_discharge_type']=='LPD'
    assert e['periodic_frequency_raw']=='18 pro 10 Sekunden' and e['pattern_frequency_hz']==1.8
    assert e['reactivity_stimulus']=='PAINFUL' and e['reactivity_to_external_stimuli']=='PRESENT'
    probable=exam('Abnormes EEG. GPDs um 1/s. Anhand der EEG-Veränderungen sind entsprechend der Anamnese (Aphasien) fokale Anfälle wahrscheinlich.')
    assert probable['periodic_discharge_type']=='GPD' and probable['pattern_frequency_hz']==1.0
    assert probable['focal_seizure_interpretation']=='PROBABLE' and probable['seizure_recorded'] is not True
    assert probable['clinical_correlate']=='APHASIA'


def test_geometry_only_removes_supported_blocks_and_keeps_adjacent_clinical_text():
    pages=[]
    for n in (1,2):
        pages.append(dict(page=n,width=600,height=840,blocks=[
            dict(block_id=f'{n}:h',bbox=[400,20,580,30],text=f'Patient, geb.: 01.01.1950; Seite {n}\n'),
            dict(block_id=f'{n}:clinical',bbox=[50,740,550,770],text='Intermittierende Theta-Aktivität links temporal.\n'),
            dict(block_id=f'{n}:f',bbox=[40,790,550,810],text=f'Landesgericht Salzburg | FN {n} | Telefon 123-{n}\n')]))
    before=copy.deepcopy(pages);cleaned,excluded=clean_pages(pages)
    assert len(excluded)==4 and {e['excluded_block_type'] for e in excluded}=={'HEADER','FOOTER'}
    assert all('Theta' in p['text'] for p in cleaned)
    assert all(e['bbox'] and e['normalized_signature'] for e in excluded)
    assert pages==before


def test_all_emitted_fields_validate_and_no_recording_end_returns():
    e=exam('Abnormes EEG. GPDs um 1/s. NCSE links temporal.')
    for field in defaults():validate_value(field,e[field])
    assert 'eeg_date_end' not in e and e['eeg_date_precision']=='DAY'


def test_standalone_consecutive_ceeg_summaries_keep_both_segments():
    r=result('cEEG Kurzbefund\n19.05.2025 14:08 - 20.05.2025 08:19\n'
        'Abnormes EEG. NCSE links temporal.\n'
        'cEEG Kurzbefund\n20.05.2025 08:20 - 20.05.2025 11:23\n'
        'Abnormes EEG. Kein Status epilepticus-Muster.')
    e,=r['eeg_exams']
    assert e['recording_type']=='CONTINUOUS_EEG' and len(e['CEEG_SEGMENTS'])==2
    assert [s['segment_status'] for s in e['CEEG_SEGMENTS']]==['PRESENT','ABSENT']
    assert e['NCSE_OCCURRED_DURING_RECORDING'] is True and e['status_at_final_segment']=='ABSENT'


def test_split_line_medication_word_does_not_cut_video_report():
    e=result('VIDEO-EEG-MONITORING (29.09. - 04.10.2024):\nWährend der 3-tägigen\n'
        'Video-EEG-Monitoringuntersuchung konnten unter Beibehaltung der antiepileptischen\n'
        'Medikation keine habituellen Ereignisse aufgezeichnet werden.\n'
        'Keine fokale Verlangsamung. Keine epileptiformen Potentiale.\nLaborwerte von 29.09.2024')['eeg_exams']
    assert len(e)==1 and e[0]['eeg_date']=='2024-09-29'
    assert 'Medikation keine habituellen' in e[0]['eeg_report_text']
    assert 'Laborwerte' not in e[0]['eeg_report_text']


def test_repeated_clinical_table_headers_are_not_page_furniture():
    pages=[dict(page=n,width=600,height=840,blocks=[
        dict(block_id=str(n),bbox=[40,47,550,58],text='Parameter\nNormbereich\nEinheit\nErster Wert\nLetzter Wert\n'),
        dict(block_id=str(n)+'bottom',bbox=[40,760,550,780],text='Die Patientin ist klinisch unverändert.\n')]) for n in (1,2)]
    cleaned,excluded=clean_pages(pages)
    assert not excluded and all('Normbereich' in p['text'] and 'klinisch' in p['text'] for p in cleaned)


def test_mixed_clinical_and_institutional_block_is_retained_entirely():
    page=dict(page=1,width=600,height=840,blocks=[dict(block_id='mixed',bbox=[40,760,550,810],text='Keine Auffälligkeiten.\nUniversitätsklinikum Salzburg\n')])
    cleaned,excluded=clean_pages([page])
    assert not excluded and cleaned[0]['text']==page['blocks'][0]['text']


def test_empty_blocks_are_not_recorded_as_excluded_clinical_content():
    page=dict(page=1,width=600,height=840,blocks=[dict(block_id='blank',bbox=[40,800,550,810],text=' \n')])
    _,excluded=clean_pages([page])
    assert excluded==[]


def test_structured_periodic_scope_is_separate_from_negative_evolution():
    e=exam('Abnormes EEG.\nEvolutionsmuster (Frequenz, Raum, Morphologie): keine/ keine /keine\nPeriodische Muster:\nGPDs um 1/s\nKeine epileptiformen Potenziale.')
    assert e['periodic_discharge_present'] is True and e['periodic_discharge_type']=='GPD' and e['pattern_frequency_hz']==1
    assert exam('Abnormes EEG. Keine periodischen Entladungen (LPDs).')['periodic_discharge_present'] is not True


def test_denied_epilepsy_template_does_not_create_exams_or_status():
    r=result('EEG (28.01.20)\nNormales EEG.\nInterpretation:\nKeine epileptiformen Potentiale.\n'
        'Video-EEG-Monitoring: Nein\nInteriktales EEG: Ja\nIktales EEG: Nein\n'
        'Prächirurgisches Setting: nein\nNeurologischer Status: Status aphasicus.\n')
    assert len(r['eeg_exams'])==1
    assert r['eeg_exams'][0]['NCSE'] is not True and 'Prächirurgisches' not in r['eeg_exams'][0]['eeg_report_text']


def test_diffuse_encephalopathy_is_not_an_independent_focal_finding():
    e=exam('Mäßig abnormes EEG mit verlangsamter Grundaktivität und bifrontalen Delta-Wellen.\nInterpretation:\nMäßig- bis mittelschwere diffuse Hirnfunktionsstörung mit Zeichen der Enzephalopathie. Keine epileptiformen Potenziale. Erhaltene Reagibilität auf externe Stimuli.')
    assert {'GENERALIZED_SLOWING','ENCEPHALOPATHY_PATTERN'}<=set(e['clinical_phenotype'])
    assert e['focal_slowing_present'] is not True
    assert e['reactivity_to_external_stimuli']=='PRESENT'


def test_sleep_features_survive_a_cleaned_page_break():
    texts=['EEG (01.02.2020)\nAbnormes EEG mit Phasen reduzierter Vigilanz, Alpha-dropout und subvigil Beta-Aktivität.\n',
        'Diffuse Theta-Aktivität, polymorphe Delta-Wellen. Vertex-Transienten, K-Komplexe und Schlafspindeln, Schlafstadium N2.']
    pages=[dict(page=n,width=600,height=840,blocks=[dict(block_id=f'{n}:c',bbox=[40,100,550,700],text=text),dict(block_id=f'{n}:f',bbox=[40,790,550,810],text='Universitätsklinikum Salzburg\n')]) for n,text in enumerate(texts,1)]
    cleaned,excluded=clean_pages(pages);b=bundle('');b['documents'][0]['sections']=[dict(section_id=f'p{p["page"]}',page_start=p['page'],page_end=p['page'],text=p['text']) for p in cleaned]
    e,=assemble(b)['eeg_exams']
    assert len(excluded)==2 and e['source_page_end']==2 and 'N2' in e['eeg_report_text']
    assert e['alpha_dropout'] is True and e['subvigil_beta_activity'] is True and e['reduced_vigilance_phases'] is True
    assert e['vertex_transients']==e['k_complexes']==e['sleep_spindles']=='PRESENT'
