import copy
import json
import re
from pathlib import Path

import pytest

from caa_pdf_extractor.extraction import diagnosis_events_v4 as dx
from caa_pdf_extractor.extraction.assertion_scope import AF_PATTERN,af_observation
from caa_pdf_extractor.extraction.admission_summary import inputs,infer,ground,link_event,materialize
from caa_pdf_extractor.extraction.patient_diagnoses import build
from caa_pdf_extractor.workbench_store import read

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'data/debug/diagnosis_architecture_vnext_20261028T091324_154351Z'
AFFECTED=('2183012310','2435608415','2536647917','2586499918','2608083318','2695942320','2788388022','2888149624','2990288425')


def bundle(text,heading='Diagnosen'):
    return dict(patient_id='synthetic',documents=[dict(source_document_id=1,document_date='2024-03-10',
        sections=[dict(section_id='s1',patient_id='synthetic',source_document_id=1,page_start=1,page_end=1,
            heading=heading,text=text,source_text=text,tables=[],candidate_targets=[])])])


def assemble(text):return dx.assemble('synthetic',bundle(text),dict(mentions=[],audit_only=[]))


@pytest.mark.parametrize('text,state',[
    ('Sollte sich im Loop-Recording eine Vorhofflimmer-Arrhythmie detektieren lassen, so wäre eine Umstellung indiziert.','CONDITIONAL'),
    ('im 24-Stunden-EKG kam kein Vorhofflimmern zur Darstellung','NEGATED'),
    ('Kein Vorhofflimmern','NEGATED'),
    ('Im 72-h-EKG konnte kein paroxysmales Vorhofflimmern detektiert werden.','NEGATED'),
    ('Es konnte kein paroxysmales Vorhofflimmern detektiert werden.','NEGATED'),
    ('Soft signs für ein mögliches Vorhandensein eines paroxysmalen Vorhofflimmerns, welches jedoch nicht nachgewiesen wurde.','UNCERTAIN'),
])
def test_nonpositive_AF_source_states(text,state):
    match=AF_PATTERN.search(text);a=af_observation(text,*match.span(),model_assertion='ESTABLISHED')
    assert a['assertion_status']==state and not a['explicit_positive']
    if 'Soft signs' in text:assert a['objective_observation']=='NEGATIVE'
    assert not any(d['canonical_name']=='ATRIAL_FIBRILLATION' for d in assemble(text)['canonical_disease_identities'])


def test_conditional_sentence_does_not_negate_independent_established_AF():
    r=assemble('Bekanntes Vorhofflimmern.\nSollte sich im Loop-Recording eine Vorhofflimmer-Arrhythmie detektieren lassen, wäre eine Umstellung indiziert.')
    assert r['af_state']['AF_state']=='CONFIRMED_AF'
    assert any(a['assertion_status']=='CONDITIONAL' for a in r['diagnostic_assertion_timeline'])


def test_nonpositive_semantic_conditional_is_retained_without_overriding_negation():
    from caa_pdf_extractor.extraction.assertion_scope import validate_assertion
    text='Vorhofflimmern';assert validate_assertion(text,0,len(text),model_assertion='CONDITIONAL')['assertion_status']=='CONDITIONAL'
    text='Kein Vorhofflimmern';m=AF_PATTERN.search(text)
    assert validate_assertion(text,*m.span(),model_assertion='CONDITIONAL')['assertion_status']=='NEGATED'


def test_rectal_abbreviation_needs_malignancy_context():
    assert not any(h[2]=='RECTAL_CARCINOMA' for h in dx.concepts('N. recti'))
    r=assemble('Z. n. substenosierendem N. recti im Stadium G3 ypT2 pN0 L1 R0 (ED 01/11)')
    assert any(d['canonical_name']=='RECTAL_CARCINOMA' for d in r['canonical_disease_identities'])


def test_distinct_hemorrhages_keep_date_anatomy_probable_CAA_and_differential():
    r=assemble('1. Intracranielle Hämorrhagie links parieto-occipital am 22.03.2023\n- ätiologisch: am ehesten im Rahmen einer Amyloidangiopathie\n'
        '2. Intracerebrale Blutung rechts parietal 04.03.2017\n- a.e. im Rahmen einer Amyloidangiopathie, DD sekundär hämorrhagischer Infarkt')
    events=[e for e in r['canonical_clinical_events'] if e['event_type']=='INTRACEREBRAL_HEMORRHAGE']
    assert len(events)==2 and {e['event_date'] for e in events}=={'2023-03-22','2017-03-04'}
    assert all(e['etiology']=='CEREBRAL_AMYLOID_ANGIOPATHY' and e['etiology_certainty']=='PROBABLE' for e in events)


def test_bilateral_infarcts_and_month_only_uncertain_event_are_not_lost():
    r=assemble('1. Ischämische Infarkte im ACM-Stromgebiet bds.\n2. Juli 2018 Verdacht auf ischämischen Infarkt im Arteria cerebri media Stromgebiet')
    assert any(c['event_type']=='ISCHEMIC_STROKE' and c['side']=='BILATERAL' and c['multiplicity']=='MULTIPLE' and c['territory']=='MCA' for c in r['clinical_event_candidates'])
    assert any(c['event_date']=='2018-07' and c['event_date_precision']=='MONTH' and c['assertion_status']=='UNCERTAIN' for c in r['clinical_event_candidates'])


class LocalFake:
    model_name='granite-test';model_version='synthetic-digest';base_url='http://127.0.0.1:11434'
    def generate_json(self,prompt):
        assert 'Am Ende der Zusammenfassung' in prompt
        return json.dumps(dict(probable_admission_diagnosis='Schlaganfall',probable_admission_event_type='ISCHEMIC_STROKE',
            confidence='MODERATE',supporting_text_spans=['S0003']))


def test_model_sees_entire_summary_and_grounding_rejects_invented_spans():
    b=bundle('Zusammenfassung\n\nUnklare Beschwerden.\n\nAm Ende der Zusammenfassung: Aufnahme wegen Schlaganfall.','Zusammenfassung')
    encounter=[dict(source_document_id=1,admission_date='2024-03-08',discharge_date='2024-03-10')]
    source=inputs('synthetic',b,encounter)[0];result=infer(source,LocalFake())
    assert result['validated']['evidence']
    bad=dict(result['validated']);bad.pop('evidence');bad['supporting_text_spans']=['S9999']
    with pytest.raises(ValueError):ground(bad,source)
    rows=materialize('synthetic',b,encounter,{source['encounter_id']:result},[])
    assert rows[0]['source_method']=='LLM_SUMMARY_INFERENCE' and rows[0]['status']=='PROBABLE_ADMISSION_DIAGNOSIS'


def test_admission_link_does_not_create_or_redate_an_old_event():
    events=[dict(event_id='old',event_type='ISCHEMIC_STROKE',event_date='1999',source_document_ids=[1])]
    before=copy.deepcopy(events)
    source=dict(document_id=1,admission_date='2024-03-08')
    inference=dict(probable_admission_diagnosis='Neuer Schlaganfall',probable_admission_event_type='ISCHEMIC_STROKE',confidence='HIGH',evidence=[dict(raw_text='Akute Beschwerden.')])
    assert link_event(source,inference,events) is None and events==before
    events.append(dict(event_id='new',event_type='ISCHEMIC_STROKE',event_date='2024-03-08',source_document_ids=[1]))
    assert link_event(source,inference,events)['event_id']=='new' and len(events)==2


def test_transfer_links_the_existing_anatomically_supported_event_without_redating():
    source=dict(document_id=1,admission_date='2022-11-25')
    events=[dict(event_id='old-left',event_type='ISCHEMIC_STROKE',event_date='2014',side='LEFT',location=None,source_document_ids=[1]),
        dict(event_id='index',event_type='ISCHEMIC_STROKE',event_date='2022-10-29',side='RIGHT',location='RIGHT_BASAL_GANGLIA',source_document_ids=[1])]
    inference=dict(probable_admission_diagnosis='Stammganglieninfarkt rechts',probable_admission_event_type='ISCHEMIC_STROKE',confidence='MODERATE',
        evidence=[dict(raw_text='Die Übernahme erfolgte nach Stammganglieninfarkt rechts zur weiteren Behandlung.')])
    assert link_event(source,inference,events)['event_id']=='index'
    assert events[1]['event_date']=='2022-10-29'


def test_registry_unifies_epilepsy_but_keeps_event_links():
    r=assemble('1. Symptomatische Epilepsie\n2. Strukturelle Epilepsie\n3. Ischämischer Infarkt rechts 1999\n4. Ischämischer Infarkt links 2020')
    registry=build('synthetic',r)
    assert sum(d['canonical_name']=='EPILEPSY' for d in registry)==1
    history=next(d for d in registry if d['canonical_name']=='ISCHEMIC_STROKE_HISTORY')
    assert len(history['linked_event_ids'])==2


def test_admission_date_fallback_requires_acute_positive_admission_clause():
    from caa_pdf_extractor.extraction.admission_event_linkage import apply
    b=bundle('Ischämischer Infarkt rechts')
    summary=dict(b['documents'][0]['sections'][0],section_id='s2',heading='Zusammenfassung',
        text='Die Aufnahme erfolgte wegen eines akuten ischämischen Infarktes rechts.',
        source_text='Die Aufnahme erfolgte wegen eines akuten ischämischen Infarktes rechts.')
    b['documents'][0]['sections'].append(summary)
    enc=[dict(source_document_id=1,admission_date='2024-03-08',discharge_date='2024-03-10')]
    source=inputs('synthetic',b,enc)[0]
    model={source['encounter_id']:dict(validated=dict(probable_admission_diagnosis='Ischämischer Infarkt',probable_admission_event_type='ISCHEMIC_STROKE',confidence='HIGH',supporting_text_spans=['S0001']))}
    r=dx.assemble('synthetic',b,dict(mentions=[],audit_only=[]))
    apply('synthetic',b,enc,model,r)
    assert len(r['canonical_clinical_events'])==1
    event=r['canonical_clinical_events'][0]
    assert event['event_date']=='2024-03-08' and event['date_source']=='ADMISSION_DATE_IF_EVENT_IS_CLEARLY_THE_ADMISSION_EVENT'
    apply('synthetic',b,enc,model,r)
    assert len(r['canonical_clinical_events'])==1


def test_admission_positivity_cannot_replace_historical_event_date():
    from caa_pdf_extractor.extraction.admission_event_linkage import apply
    b=bundle('Zustand nach ischämischem Infarkt rechts 1999')
    b['documents'][0]['sections'].append(dict(b['documents'][0]['sections'][0],section_id='s2',heading='Zusammenfassung',
        text='Die Aufnahme erfolgte zur Rehabilitation nach einem Infarkt 1999.',source_text='Die Aufnahme erfolgte zur Rehabilitation nach einem Infarkt 1999.'))
    enc=[dict(source_document_id=1,admission_date='2024-03-08',discharge_date='2024-03-10')];source=inputs('synthetic',b,enc)[0]
    model={source['encounter_id']:dict(validated=dict(probable_admission_diagnosis='Rehabilitation nach Infarkt',probable_admission_event_type='ISCHEMIC_STROKE',confidence='HIGH',supporting_text_spans=['S0001']))}
    r=dx.assemble('synthetic',b,dict(mentions=[],audit_only=[]));before=copy.deepcopy(r['canonical_clinical_events'])
    apply('synthetic',b,enc,model,r)
    assert r['canonical_clinical_events']==before and r['canonical_clinical_events'][0]['event_date']=='1999'


def test_admission_review_actions_are_audited_overlays(tmp_path):
    from review_app.review_repository import ReviewRepository,overlay
    repo=ReviewRepository(tmp_path/'review');raw=dict(entity_id='admission1',probable_admission_diagnosis='Stroke',status='PROBABLE_ADMISSION_DIAGNOSIS')
    for action in ['ACCEPT','EDIT','REJECT','UNCERTAIN']:
        rec=repo.save('synthetic','diagnoses','admission1',action,raw,{'probable_admission_diagnosis':'TIA'} if action=='EDIT' else None,'4.3+test','hash')
        assert overlay(raw,rec,'hash')['physician_status']==action
        assert raw['probable_admission_diagnosis']=='Stroke'
    assert len((repo.root/'review_audit.jsonl').read_text(encoding='utf8').splitlines())==4


def test_registry_admissions_and_events_use_distinct_promotion_identities(tmp_path):
    from caa_pdf_extractor.workbench_store import candidate_entities
    rows={'patient_diagnoses':[dict(patient_diagnosis_id='p:PDX:1',review_required=True)],
        'canonical_disease_identities':[dict(diagnosis_identity_id='legacy-not-a-second-review')],
        'canonical_clinical_events':[dict(event_id='p:EV:1',review_required=True)],
        'admission_clinical_summary':[dict(admission_id='p:ADMISSION:1',review_required=True)]}
    artifacts=[]
    for name,data in rows.items():
        path=tmp_path/(name+'.json');path.write_text(json.dumps(data),encoding='utf8');artifacts.append(dict(path=str(path)))
    assert {r['entity_id'] for r in candidate_entities(dict(artifacts=artifacts),'diagnoses')}=={'p:PDX:1','p:EV:1','p:ADMISSION:1'}


@pytest.mark.skipif(not (EVIDENCE/'original_manifests.json').exists(),reason='Local validation sources unavailable')
def test_nine_affected_patients_source_regressions():
    original=read(EVIDENCE/'original_manifests.json')
    expected={'2183012310':{'RECTAL_CARCINOMA'},'2435608415':{'HYDROCEPHALUS'},'2536647917':{'CORONARY_ARTERY_DISEASE','CAROTID_ARTERY_STENOSIS'},
        '2586499918':{'PARKINSONISM'},'2695942320':{'PERIPHERAL_ARTERIAL_DISEASE','OBSTRUCTIVE_SLEEP_APNEA'},'2888149624':{'PULMONARY_SARCOIDOSIS'}}
    for pid in AFFECTED:
        source=Path(original[pid]['source_context']['directory']);r=dx.assemble(pid,read(source/'source_bundle.json'),read(source/'source_mentions.json'))
        assert expected.get(pid,set())<={d['canonical_name'] for d in r['canonical_disease_identities']}
        assert r['structured_diagnosis_reconciliation']['lost']==0
        if pid=='2990288425':assert not r['af_state']['confirmed_af']
        if pid=='2536647917':
            es=[e for e in r['canonical_clinical_events'] if e['event_type']=='INTRACEREBRAL_HEMORRHAGE']
            assert len(es)==2 and {e['event_date'] for e in es}=={'2023-03-22','2017-03-04'}
        if pid=='2608083318':assert any(c['event_date']=='2018-07' and c['assertion_status']=='UNCERTAIN' for c in r['clinical_event_candidates'])
        if pid=='2695942320':assert any(e['event_date']=='2022-10-29' and e['location']=='RIGHT_BASAL_GANGLIA' for e in r['canonical_clinical_events'])
        if pid=='2888149624':assert any(e['event_date']=='2024-03-08' and e['location']=='RIGHT_BASAL_GANGLIA' for e in r['canonical_clinical_events'])
