"""TFNE pilot isolation, provenance, assertion and deterministic screening safety."""
import copy
import json
import pytest

from caa_pdf_extractor.tfne_sources import select_sections,model_sections,span_index
from caa_pdf_extractor.tfne_schema import defaults,validate,response_schema
from caa_pdf_extractor.tfne_granite import ground,request,PROMPT_PATH
from caa_pdf_extractor.tfne_screen import derive


def source(text):
    return [dict(document_id=10,page=1,section_id='history',parent_section_id='page1',section_type='ANAMNESE',source_text=text,spans=[dict(start=0,end=len(text),text=text)])]


def episode(**values):
    e=defaults();e.update(symptom_description='Aphasie',**values);return e


def supported(e,text,state='ESTABLISHED'):
    fields=[k for k,v in e.items() if k not in ('evidence','episode_index') and v not in (None,'UNKNOWN','NONE_DOCUMENTED',[],{})]
    e['evidence']={field:dict(source_span_ids=['S0001'],assertion=state) for field in fields}
    return ground(dict(episodes=[e]),source(text))


@pytest.mark.parametrize('historical',['EPILEPSY','TIA','TFNE','NONE_DOCUMENTED'])
def test_screen_is_independent_of_historical_diagnosis(historical):
    e=episode(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',focality='FOCAL',symptom_domains=['LANGUAGE'],historical_diagnosis=historical)
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_POSSIBLE'


@pytest.mark.parametrize('domain',['DIZZINESS','COGNITIVE','CONSCIOUSNESS'])
def test_nonfocal_resolved_symptoms_are_atypical_not_tfne(domain):
    out=derive(episode(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',focality='NONFOCAL',symptom_domains=[domain]))
    assert out['TFNE_clinical_screen']=='TFNE_SCREEN_NEGATIVE'
    assert out['retrospective_clinical_phenotype']=='TRANSIENT_ATYPICAL'


def test_recurrence_and_aphasia_alone_are_not_strong():
    e=episode(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',focality='FOCAL',symptom_domains=['LANGUAGE'],recurrent='TRUE')
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_POSSIBLE'
    e['stereotyped']='TRUE';assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_STRONG'


@pytest.mark.parametrize('course',['PERSISTENT','PROGRESSIVE','MIXED','FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'])
def test_persistent_and_mixed_course_cannot_be_screened_positive(course):
    e=episode(temporal_course=course,fully_resolved='FALSE',focality='FOCAL',symptom_domains=['MOTOR'],spreading_progression='TRUE',recurrent='TRUE',stereotyped='TRUE')
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_NEGATIVE'


def test_resolution_is_required_and_long_resolved_episodes_are_retained():
    e=episode(temporal_course='UNKNOWN',focality='FOCAL',symptom_domains=['LANGUAGE'])
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_UNCLASSIFIABLE'
    e.update(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',duration_min_minutes=120,duration_max_minutes=120)
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_POSSIBLE'
    e.update(duration_min_minutes=2880,duration_max_minutes=2880)
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_NEGATIVE'


def test_spread_and_mixed_polarity_are_separate_high_value_features():
    e=episode(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',focality='FOCAL',symptom_domains=['SENSORY'],spreading_progression='TRUE',spread_sequence=['FINGERS','HAND','ARM','FACE'])
    assert derive(e)['TFNE_clinical_screen']=='TFNE_SCREEN_STRONG'
    e.update(spreading_progression='UNKNOWN',positive_symptoms=['POSITIVE_SENSORY'],negative_symptoms=['NEGATIVE_MOTOR'])
    assert derive(e)['mixed_positive_negative_symptoms'] is True


def test_progressive_cognition_and_mixed_components_remain_distinct():
    e=episode(temporal_course='PROGRESSIVE',symptom_domains=['COGNITIVE'])
    assert derive(e)['retrospective_clinical_phenotype']=='PROGRESSIVE_COGNITIVE'
    e=episode(temporal_course='MIXED',symptom_components=[dict(domain='MOTOR',temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',source_span_ids=['S0001']),dict(domain='LANGUAGE',temporal_course='PERSISTENT',fully_resolved='FALSE',source_span_ids=['S0001'])])
    assert 'MIXED_TRANSIENT_PERSISTENT_DEFICIT' in derive(e)['research_categories']


def test_grounding_abstains_from_resolution_silence_and_recurrence_stereotypy():
    e=episode(temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE',recurrent='TRUE',stereotyped='TRUE',focality='FOCAL',symptom_domains=['LANGUAGE'])
    rows,flags=supported(e,'Wiederholte Aphasie. Danach keine weiteren Angaben.')
    assert rows[0]['fully_resolved']=='UNKNOWN' and rows[0]['stereotyped']=='UNKNOWN'
    assert rows[0]['temporal_course']=='UNKNOWN' and rows[0]['TFNE_clinical_screen']=='TFNE_SCREEN_UNCLASSIFIABLE'
    assert {r['field'] for r in flags}>={'fully_resolved','stereotyped','temporal_course'}


def test_uncertain_recurrence_and_jerking_do_not_establish_seizure():
    e=episode(recurrent='TRUE',clonic_jerking='TRUE',historical_diagnosis='SEIZURE')
    rows,flags=supported(e,'Möglicherweise wiederholte Aphasie mit fraglichen Zuckungen.',state='UNCERTAIN')
    assert rows[0]['recurrent']=='UNKNOWN' and rows[0]['clonic_jerking']=='UNKNOWN'
    assert rows[0]['historical_diagnosis']=='NONE_DOCUMENTED' and rows[0]['assertion']=='UNCERTAIN'


def test_epilepsy_label_cannot_be_replaced_by_model_tfne_label():
    rows,flags=supported(episode(historical_diagnosis='TFNE'),'Aphasie, damals als Epilepsie diagnostiziert.')
    assert rows[0]['historical_diagnosis']!='TFNE' and flags
    with pytest.raises(ValueError):validate(dict(episodes=[],TFNE_SCREEN_STRONG=True))
    with pytest.raises(ValueError):validate(dict(episodes=[dict(episode(),TFNE_clinical_screen='TFNE_SCREEN_STRONG')]))


def test_every_nonunknown_field_requires_individual_provenance():
    e=episode(side='RIGHT');e['evidence']={'symptom_description':dict(source_span_ids=['S0001'],assertion='ESTABLISHED')}
    rows,flags=ground(dict(episodes=[e]),source('Aphasie.'))
    assert rows[0]['side']=='UNKNOWN'
    e['evidence']['symptom_description']['source_span_ids']=['EEG_NOT_ALLOWED']
    with pytest.raises(ValueError):ground(dict(episodes=[e]),source('Aphasie.'))


def test_negative_symptoms_and_motor_jerking_cannot_be_invented():
    rows,flags=supported(episode(positive_symptoms=['POSITIVE_MOTOR'],negative_symptoms=['NEGATIVE_MOTOR']),'Aphasie ohne weitere Beschreibung.')
    assert rows[0]['positive_symptoms']==rows[0]['negative_symptoms']==[]


def test_only_allowed_narratives_and_no_eeg_imaging_caa_reach_granite():
    text='Diagnosen\nEpilepsie.\nAnamnese\nAphasie nach 15 Minuten sistiert.\nDas EEG war normal.\nDie MRT zeigt cSS bei CAA.\nNeurologischer Status\nHemiparese.\nZusammenfassung des Aufenthalts\nErneute Taubheit der Hand.\nEntlassungsmedikation\nQuetiapin.'
    doc=dict(source_document_id=10,filename='synthetic.pdf',sections=[dict(section_id='p1',page_start=1,text=text)])
    before=copy.deepcopy(doc);sections,excluded=select_sections('123',doc)
    payload=model_sections(sections);encoded=json.dumps(payload,ensure_ascii=False)
    assert 'Aphasie' in encoded and 'Taubheit' in encoded
    assert all(word not in encoded for word in ('EEG','MRT','cSS','CAA','Hemiparese','Quetiapin','Diagnosen'))
    assert doc==before and excluded
    for s in sections:
        for row in s['spans']:assert s['source_text'][row['start']:row['end']]==row['text']


def test_static_prompt_and_separate_data_message_and_strict_no_screen_schema():
    class Client:model_name='granite4.2:latest'
    payload=request(source('Aphasie.'),Client())
    assert payload['messages'][0]['content']==PROMPT_PATH.read_text(encoding='utf-8')
    assert json.loads(payload['messages'][1]['content'])['source_sections'][0]['section_type']=='ANAMNESE'
    schema=payload['format']['properties']['episodes']['items']['properties']
    assert 'TFNE_clinical_screen' not in schema and not payload['format']['additionalProperties']
    assert schema['evidence']['type']=='object' and 'fully_resolved' in schema['evidence']['required']


def test_numeric_duration_cannot_be_guessed_from_few_minutes():
    from caa_pdf_extractor.tfne_validation import validate_grounded
    e=episode(duration_min_minutes=5,duration_max_minutes=5,duration_precision='EXACT',duration_text='wenige Minuten')
    rows,initial_flags=supported(e,'Aphasie für wenige Minuten.')
    validated,flags=validate_grounded(rows[0],span_index(source('Aphasie für wenige Minuten.')))
    assert validated['duration_min_minutes'] is None and validated['duration_max_minutes'] is None and (flags or initial_flags)


def test_explicit_duration_conversion_and_negated_complete_resolution():
    from caa_pdf_extractor.tfne_validation import duration_values,supported_resolution
    assert duration_values('zwei Stunden')==[(120,120)]
    assert duration_values('5-10 Minuten')==[(5,10)]
    assert not supported_resolution('Die Aphasie war nicht vollständig rückläufig.')
    assert supported_resolution('Die Aphasie war vollständig rückläufig.')


def test_diagnosis_alone_does_not_establish_seizure_like_features():
    from caa_pdf_extractor.tfne_validation import validate_grounded
    rows,_=supported(episode(clonic_jerking='TRUE'),'Aphasie bei bekannter Epilepsie.')
    validated,flags=validate_grounded(rows[0],span_index(source('Aphasie bei bekannter Epilepsie.')))
    assert validated['clonic_jerking']=='UNKNOWN'


def test_negated_paresis_does_not_create_a_mixed_positive_negative_episode():
    rows,_=supported(episode(positive_symptoms=['POSITIVE_SENSORY'],negative_symptoms=['NEGATIVE_MOTOR']),'Aphasie mit Kribbeln, ohne Parese.')
    assert rows[0]['positive_symptoms']==['POSITIVE_SENSORY'] and rows[0]['negative_symptoms']==[]
    assert rows[0]['mixed_positive_negative_symptoms'] is False
