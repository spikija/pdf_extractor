"""Permanent physician counterexamples and two-pass provenance/safety contracts."""
import copy
import json
from pathlib import Path

import pytest

from caa_pdf_extractor.tfne_schema import defaults
from caa_pdf_extractor.tfne_v02_schema import FIELDS
from caa_pdf_extractor.tfne_v02_validation import ground,populated,support,duration
from caa_pdf_extractor.tfne_v02_granite import detect,extract
from caa_pdf_extractor.tfne_sources import span_index

CASES=json.loads((Path(__file__).parent/'fixtures/tfne_v02_physician_examples.json').read_text(encoding='utf-8'))


def sections(text):
    return [dict(document_id=10,page=1,section_id='history',parent_section_id='page1',section_type='ANAMNESE',source_text=text,
        spans=[dict(start=0,end=len(text),text=text)])]


def detection(text):return dict(episode_index=1,source_span_ids=['S0001'],short_exact_description=text,episode_date_text=None)


def proposal(text,**changes):
    e=defaults();e.update(symptom_description=text,**changes)
    e['evidence']={f:dict(source_span_ids=['S0001'] if populated(e[f]) else [],quote=text if populated(e[f]) else None,assertion='ESTABLISHED') for f in FIELDS}
    return e


def malicious(case):
    text=case['text'];expected=case.get('expected',{})
    ds=expected.get('symptom_domains',['COGNITIVE','OTHER'])
    e=proposal(text,symptom_domains=list(dict.fromkeys(ds+['MOTOR','SENSORY','COGNITIVE'])),
        positive_symptoms=['POSITIVE_SENSORY','POSITIVE_MOTOR','POSITIVE_VISUAL'],negative_symptoms=['NEGATIVE_MOTOR','NEGATIVE_SENSORY'],
        focality=expected.get('focality','NONFOCAL'),fully_resolved='TRUE',temporal_course='TRANSIENT_FULLY_RESOLVED',
        persistent_deficit_domains=ds,duration_min_minutes=0,duration_max_minutes=60,duration_precision='RANGE')
    if case['case_id'].startswith('B_'):
        e['duration_text']='seit 2 Tagen';e['evidence']['duration_text']=dict(source_span_ids=['S0001'],quote='seit 2 Tagen',assertion='ESTABLISHED')
    if case['case_id'].startswith('C_'):
        e.update(side='LEFT',body_regions=['ARM','HAND','FACE'],spreading_progression='TRUE',spread_sequence=['HAND','ARM','FACE'],spread_type='SOMATOTOPIC_CONTIGUOUS')
        for f in ('side','body_regions','spreading_progression','spread_sequence','spread_type'):
            e['evidence'][f]=dict(source_span_ids=['S0001'],quote=text,assertion='ESTABLISHED')
    return e


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['case_id'])
def test_physician_examples_reject_plausibility_completion(case):
    text=case['text'];index=span_index(sections(text))
    if 'expected_detection_count' in case:
        found,flags=detect(dict(episodes=[detection(text)]),index)
        assert len(found)==case['expected_detection_count'];return
    e,flags=ground(malicious(case),detection(text),index)
    for field,value in case['expected'].items():assert e[field]==value,(field,e[field],value)
    assert flags and any(f['code']=='REVIEW_REQUIRED' for f in flags)
    for field in FIELDS:
        if populated(e[field]):assert e['field_evidence'].get(field),field


@pytest.mark.parametrize('text,expected',[
    ('10 Minuten',(10,10,'EXACT')),('2 Stunden',(120,120,'EXACT')),
    ('seit 2 Tagen',(2880,None,'APPROXIMATE')),('10 bis 20 Minuten',(10,20,'RANGE')),
    ('wenige Minuten',None),('15 Jahre',None),('0-60 Minuten',(0,60,'RANGE')),
])
def test_explicit_duration_conversion_and_open_bounds(text,expected):assert duration(text)==expected


@pytest.mark.parametrize('text',[
    'Keine Aphasie, keine Parese und kein Schwindel.',
    'Ein EEG wurde empfohlen.',
    'Bei erneuter Sprachstörung sollte eine Untersuchung erfolgen.',
    'Bekannte Epilepsie. Medikamentendosis erhöht.',
])
def test_detection_rejects_denial_plans_diagnosis_without_symptoms(text):
    d,flags=detect(dict(episodes=[detection(text)]),span_index(sections(text)))
    assert not d and flags


def test_empty_field_evidence_is_removed_and_metadata_dates_never_enter():
    text='Sprachstörung.';e=proposal(text,side='RIGHT',episode_date_text='2026-11-01',temporal_course='TRANSIENT_FULLY_RESOLVED',fully_resolved='TRUE')
    e['evidence']['side']['source_span_ids']=[]
    out,flags=ground(e,detection(text),span_index(sections(text)))
    assert out['side']=='UNKNOWN' and out['episode_date_text'] is None
    assert out['fully_resolved']=='UNKNOWN' and out['temporal_course']=='UNKNOWN'
    assert 'UNSUPPORTED_FIELD_REMOVED' in {f['code'] for f in flags}


def test_recurrence_does_not_supply_stereotypy_or_seizure_features():
    text='Wiederholte Aphasie.'
    e=proposal(text,symptom_domains=['LANGUAGE'],recurrent='TRUE',stereotyped='TRUE',clonic_jerking='TRUE',historical_diagnosis='SEIZURE')
    out,flags=ground(e,detection(text),span_index(sections(text)))
    assert out['recurrent']=='TRUE' and out['stereotyped']=='UNKNOWN' and out['clonic_jerking']=='UNKNOWN'
    assert out['historical_diagnosis']=='NONE_DOCUMENTED'


def test_background_migraine_cannot_supply_current_episode_fields():
    text='Sprachstörung.';index=span_index(sections(text))
    index['S0002']=dict(index['S0001'],evidence_text='Bekannte Migräne mit visueller Aura seit 15 Jahren.')
    e=proposal(text,historical_diagnosis='MIGRAINE',positive_symptoms=['POSITIVE_VISUAL'])
    for field in ('historical_diagnosis','positive_symptoms'):
        e['evidence'][field]=dict(source_span_ids=['S0002'],quote=index['S0002']['evidence_text'],assertion='ESTABLISHED')
    out,_=ground(e,detection(text),index)
    assert out['historical_diagnosis']=='NONE_DOCUMENTED' and out['positive_symptoms']==[]


def test_resolution_negation_and_partial_improvement_are_not_complete():
    for text in ('Aphasie nicht vollständig rückläufig.','Aphasie teilweise regredient.','Aphasie besser.'):
        out,_=ground(proposal(text,fully_resolved='TRUE',temporal_course='TRANSIENT_FULLY_RESOLVED'),detection(text),span_index(sections(text)))
        assert out['fully_resolved']=='UNKNOWN' and out['temporal_course']=='UNKNOWN'


def test_true_resolution_with_explicit_interval_is_preserved():
    text='Aphasie, nach 10 Minuten vollständig sistiert.'
    e=proposal(text,symptom_domains=['LANGUAGE'],focality='FOCAL',fully_resolved='TRUE',temporal_course='TRANSIENT_FULLY_RESOLVED',duration_text='10 Minuten')
    e['evidence']['duration_text']['quote']='10 Minuten'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['temporal_course']=='TRANSIENT_FULLY_RESOLVED' and out['TFNE_clinical_screen']=='TFNE_SCREEN_POSSIBLE'
    assert out['duration_min_minutes']==out['duration_max_minutes']==10


def test_two_pass_input_is_restricted_and_global_ids_are_preserved():
    text='Aphasie.';source=sections(text)
    source[0]['spans'].append(dict(start=10,end=30,text='Schwindel als andere Episode.'))
    e=proposal(text,symptom_domains=['LANGUAGE'],focality='FOCAL')
    class Client:
        model_name='granite4.2:latest'
        def __init__(self):self.requests=[]
        def _request(self,path,p):
            self.requests.append(p)
            raw=dict(episodes=[detection(text)]) if len(self.requests)==1 else dict(episodes=[e])
            return dict(done=True,done_reason='stop',message=dict(content=json.dumps(raw)))
    client=Client();logs={};result,flags,calls=extract(source,client,lambda n,v:logs.update({n:v}))
    assert len(client.requests)==2 and calls['pass2_calls']==1
    second=json.loads(client.requests[1]['messages'][1]['content'])
    assert second['episode_source_spans']==[dict(span_id='S0001',text=text)]
    assert 'Schwindel' not in client.requests[1]['messages'][1]['content']
    assert all('source_text' not in r['messages'][1]['content'] for r in client.requests)


def test_pdf_wrap_resolution_returns_exact_original_without_accepting_paraphrase():
    from caa_pdf_extractor.tfne_v02_validation import exact_quote
    text='Taubheit des\nlinken Armes und fluktuierende\nVerwirrtheit.'
    quote='Taubheit des linken Armes und fluktuierende Verwirrtheit.'
    assert exact_quote(quote,text)==text
    assert exact_quote('Taubheit des rechten Armes',text) is None
    d=detection(quote)
    found,_=detect(dict(episodes=[d]),span_index(sections(text)))
    assert found[0]['short_exact_description']==text
    assert found[0]['source_quote_resolution']=='WHITESPACE_ONLY_EXACT_SOURCE_SUBSTRING'


def test_second_pass_schema_cannot_emit_multiple_episode_objects():
    from caa_pdf_extractor.tfne_v02_schema import phenotype_schema
    rule=phenotype_schema(['S0001'])['properties']['episodes']
    assert rule['minItems']==rule['maxItems']==1


def test_short_quote_cannot_hide_source_negation_or_uncertainty():
    text='Aphasie, keine Parese; möglicherweise Kribbeln.'
    e=proposal(text,symptom_domains=['LANGUAGE','MOTOR','SENSORY'],negative_symptoms=['NEGATIVE_MOTOR'],positive_symptoms=['POSITIVE_SENSORY'])
    e['evidence']['negative_symptoms']['quote']='Parese'
    e['evidence']['positive_symptoms']['quote']='Kribbeln'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['negative_symptoms']==out['positive_symptoms']==[]
    assert out['symptom_domains']==['LANGUAGE']


def test_short_resolution_quote_cannot_hide_not_in_original_source():
    text='Aphasie, nicht vollständig rückläufig.'
    e=proposal(text,fully_resolved='TRUE',temporal_course='TRANSIENT_FULLY_RESOLVED')
    e['evidence']['fully_resolved']['quote']='vollständig rückläufig'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['fully_resolved']=='UNKNOWN' and out['temporal_course']=='UNKNOWN'


def test_assertion_occurrence_quote_and_uncertain_fallback_always_have_provenance():
    text='Am 13.06.2020 kam es zu Aphasie.'
    e=proposal(text);e['evidence']['assertion']['quote']='kam es'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['assertion']=='ESTABLISHED' and out['field_evidence']['assertion']
    e['evidence']['assertion']['quote']='13.06.2020'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['assertion']=='UNCERTAIN' and out['field_evidence']['assertion']


def test_abbreviated_uncertainty_is_not_lost_at_periods():
    text='Aphasie; V.a. Parese.'
    e=proposal(text,negative_symptoms=['NEGATIVE_MOTOR']);e['evidence']['negative_symptoms']['quote']='Parese'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['negative_symptoms']==[]


def test_uncertain_negative_feature_is_unknown_not_false():
    text='Aphasie; möglicherweise keine Zuckungen.'
    e=proposal(text,clonic_jerking='FALSE');e['evidence']['clonic_jerking']['assertion']='UNCERTAIN'
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['clonic_jerking']=='UNKNOWN'


def test_increasing_headache_does_not_establish_a_residual_deficit():
    text='Zunehmende Kopfschmerzen.'
    e=proposal(text,temporal_course='PROGRESSIVE',persistent_deficit_domains=['OTHER'],persistent_course='PROGRESSIVE')
    out,_=ground(e,detection(text),span_index(sections(text)))
    assert out['temporal_course']=='PROGRESSIVE' and out['persistent_deficit_domains']==[] and out['persistent_course']=='UNKNOWN'
