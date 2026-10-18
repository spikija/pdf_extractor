"""Second-pass source recall, qualifier ownership, and negative controls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import pytest
from caa_pdf_extractor.extraction.case_rules import CaseInput,extract,select_inputs,stage_classification
from caa_pdf_extractor.extraction.diagnosis_items import diagnosis_items,deterministic_diagnosis,validate_recall_responses
from caa_pdf_extractor.extraction.radiology_validators import validate_independent_radiology
from caa_pdf_extractor.extraction.family_history_models import validate_independent_family_history,relative_category
from caa_pdf_extractor.extraction.family_history_inputs import FamilyHistoryInput
from caa_pdf_extractor.extraction.medication_inputs import MedicationInput
from caa_pdf_extractor.extraction.medication_validators import validate_independent_medications,validate_medications
from caa_pdf_extractor.extraction import clinical_history
from test_source_evidence import source
from test_radiology import procedure,empty_payload
from test_family_history import payload
from test_batch_regressions import AuditHarness


def case(*texts,domain='lifestyle'):
    return extract([CaseInput(domain,1,1,'s'+str(n),t) for n,t in enumerate(texts)])


@pytest.mark.parametrize('text,years',[
    ('Nikotin ex vor einigen Jahren, insgesamt ca. 40 p/y.',40),
    ('Nikotin ex 2013 (50 p/y)',50),
    ('Nikotinabusus bis 2015, 40 pack-years',40),
])
def test_explicit_former_and_pack_years(text,years):
    out=case(text)
    assert out['update_fields']=={'smoking':'former','pack_years':years}
    assert all(p['evidence'] in text for proofs in out['provenance'].values() for p in proofs)
    assert 'date_of_birth' not in out['update_fields']


@pytest.mark.parametrize('text', ['50 p/y','COPD, 40 pack years','Nikotin-Ersatz','20 Zigaretten pro Tag seit 20 Jahren'])
def test_pack_years_or_risk_never_infer_smoking(text):
    out=case(text)['update_fields']
    assert 'smoking' not in out
    if 'Zigaretten' in text:assert 'pack_years' not in out


@pytest.mark.parametrize('text,status',[
    ('Mäßiger Alkoholkonsum','active'),('gelegentlicher Alkoholkonsum','active'),
    ('regel- und leicht übermäßiger Alkoholkonsum','active'),
    ('Alkohol verneint','never'),('Alkoholkonsum wird verneint','never'),
    ('Früher Alkoholkonsum, jetzt abstinent','former'),
])
def test_explicit_alcohol_normalization(text,status):
    out=case(text)
    assert out['update_fields']['alcohol']==status
    assert out['provenance']['alcohol'][0]['evidence'] in text


@pytest.mark.parametrize('texts',[
    ('Sozialanamnese',),('Alkohol verneint','Gestern vermutlich Alkoholkonsum bei einer Feier.'),
    ('Mäßiger Alkoholkonsum','Alkohol verneint'),
])
def test_unresolved_alcohol_is_audit_only(texts):
    out=case(*texts)
    assert 'alcohol' not in out['update_fields'] and not out['source_conflicts']


@pytest.mark.parametrize('label',['Boston-Kriterien','Boston Kriterien','Boston-Criteria','Boston Criteria'])
def test_explicit_formal_version(label):
    text='CAA nach '+label+' 2.0'
    out=case(text,domain='caa_classification')
    assert out['update_fields']=={'caa_criteria':'Boston Criteria 2.0'}
    assert out['provenance']['caa_criteria'][0]['evidence']==label+' 2.0'


@pytest.mark.parametrize('text,classification',[
    ('Probable CAA','EXPLICIT_UNCONDITIONAL_STAGE'),
    ('probable CAA falls die Klinik passt','CONDITIONAL_STAGE'),
    ('mindestens possible CAA','AMBIGUOUS'),
    ('possible CAA, je nach Klinik probable CAA','MULTIPLE_STAGES'),
])
def test_stage_qualifier_scope(text,classification):
    out=case(text,domain='caa_classification')
    assert stage_classification(text)==classification
    assert 'caa_criteria' not in out['update_fields'] and 'caa_diagnosis_date' not in out['update_fields']
    assert ('caa_criteria_stage' in out['update_fields'])==(classification=='EXPLICIT_UNCONDITIONAL_STAGE')


def test_wrapped_conditional_stages_stay_together():
    b,s=source('Nach Boston Criteria 2.0 ist eine Amyloidangiopathie zumindest\n möglich, je nach Klinik wahrscheinlich.')
    s.page_end=s.page_start
    out=extract(select_inputs(b))
    assert out['update_fields']['caa_criteria']=='Boston Criteria 2.0'
    assert 'caa_criteria_stage' not in out['update_fields']
    assert any(a['classification']=='MULTIPLE_STAGES' for a in out['stage_assessments'])


@pytest.mark.parametrize('text,accepted',[
    ('Oberflächliche Hämosiderose - länger zurückliegender Zustand nach SAB?',True),
    ('Superfizielle Siderose frontal. Z. n. SAB?',True),
    ('Superfizielle Siderose?',False),('Fragliche superfizielle Siderose.',False),
    ('V. a. superfizielle Siderose.',False),('DD superfizielle Siderose.',False),
])
def test_css_question_mark_ownership(text,accepted):
    p=procedure(result_text=text)
    event=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    assert bool(event['findings'])==accepted
    if accepted:
        assert event['findings'][0]['evidence'] in text
        assert event['findings'][0]['type']=='css' and event['findings'][0]['acute_lesion'] is False
    assert event['mri_date']==p.date


def family(text,relative='Bruder',category='first grade',section='wrong'):
    raw=payload(relative,'Demenz',category,text)
    raw['family_history'][0]['section_id']=section
    inputs=[FamilyHistoryInput(1,'actual',2,text,True)]
    return raw,inputs


@pytest.mark.parametrize('text,relative,target,degree',[
    ('Bruder des Vaters hatte Demenz.','Bruder','Onkel','second grade'),
    ('Der Vater und dessen Brüder an Demenz erkrankt.','Brüder','Onkel','second grade'),
    ('Schwester der Mutter hatte Demenz.','Schwester','Tante','second grade'),
    ('Sohn des Bruders hatte Demenz.','Sohn','Neffe','second grade'),
    ("father's brother had Demenz.",'brother','uncle','second grade'),
    ('Bruder hatte Demenz.','Bruder','Bruder','first grade'),
    ('Großmutter hatte Demenz.','Großmutter','Großmutter','second grade'),
])
def test_patient_perspective_kinship(text,relative,target,degree):
    raw,inputs=family(text,relative,category=degree if relative=='Großmutter' else 'first grade')
    out=validate_independent_family_history(json.dumps(raw),'123',inputs)
    assert not out['rejected_records']
    record=out['family_history'][0]
    assert record['relative']==target and record['relative_category']==degree and record['evidence']==text
    assert out['source_reconciliation'][0]['model_section_id']=='wrong'
    assert out['source_reconciliation'][0]['resolved_section_id']=='actual'


def test_exact_plural_evidence_can_normalize_singular_semantic_relative():
    text='Vater sowie 1 Schwester und 2 von 4 Brüder Hirnblutung (Ursache jedoch nicht bekannt)'
    raw=payload('Bruder','Hirnblutung','first grade',text)
    xs=[FamilyHistoryInput(1,'doc1_sec01',2,text,True)]
    out=validate_independent_family_history(json.dumps(raw),'123',xs)
    assert out['family_history'][0]['evidence']==text and out['family_history'][0]['relative_category']=='first grade'
    assert out['relative_category_reconciliation'][0]['source_relative_text']=='Brüder'
    assert len(out['family_history'])==1


def test_ambiguous_or_paraphrased_family_quote_cannot_reanchor():
    raw,xs=family('Bruder hatte Demenz.')
    with pytest.raises(ValueError,match='ambiguous'):
        validate_independent_family_history(json.dumps(raw),'123',xs+[replace(xs[0],section_id='other')])
    raw['family_history'][0]['evidence']='Bruder war dement.'
    with pytest.raises(ValueError,match='provenance'):
        validate_independent_family_history(json.dumps(raw),'123',xs)


@pytest.mark.parametrize('text', ['Appendektomie 1985','Tonsillektomie','Hernien-Operation rechts inguinal vor ca. 30 Jahren','Rippenfraktur?','Therapie mit 5 mg','Referenzbefund'])
def test_deterministic_diagnosis_fallback_excludes_non_target(text):
    _,s=source('1. '+text)
    assert deterministic_diagnosis(diagnosis_items([s])[0]) is None


def test_rib_fracture_fallback_exact_source_and_no_inference(tmp_path):
    text='Fract. costae IV-VI dext rec. et. III sin. non rec.'
    b,s=source('1. '+text)
    h=AuditHarness();raw=json.dumps({'patient_id':'123','clinical_history':[]})
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(return_value=raw))
    def run(dry=True):
        with patch.object(clinical_history,'build_patient_bundle',return_value=b):
            return clinical_history.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    out=run();assert out['diagnosis_count']==1 and client.generate_json.call_count==1
    a=json.loads(Path(out['verified_artifact']).read_text(encoding='utf-8'))
    assert a['accepted_records'][0]['disease']==text and a['accepted_records'][0]['date_disease'] is None
    assert a['evidence_metadata'][0]['normalization_method']=='deterministic_source_item'
    client.generate_json.side_effect=AssertionError('No commit inference')
    assert run(False)['inserted']==1 and run(False)['inserted']==0
    assert client.generate_json.call_count==1


@pytest.mark.parametrize('schedule',['0          0           1/2','0          0           0.5'])
def test_narrative_medication_uses_exact_half_tablet_span(schedule):
    text='Sortis 40 mg                                        '+schedule
    s=MedicationInput('s','123',1,1,'Empfohlene Medikation',text,[])
    value={'medication':'Sortis 40 mg 0 0 1/2 0','medication_category':None,'date_noted':None,'date_stopped':None,
        'source_document_id':1,'page_number':1,'section_id':'s','source_table_index':None,'source_text':'Invented, quote 0'}
    raw=json.dumps({'patient_id':'123','medications':[value]})
    with pytest.raises(ValueError):validate_medications(raw,'123',[s])
    result,rejected,metadata=validate_independent_medications(raw,'123',[s])
    assert rejected and len(result.medications)==1
    assert result.medications[0].source_text==text
    assert result.medications[0].medication==' '.join(text.split())
    assert result.medications[0].date_noted is None and result.medications[0].date_stopped is None
    assert metadata[0]['source_row'] is None


def test_narrative_fallback_excludes_reference_or_invented_schedule():
    s=MedicationInput('s','123',1,1,'Referenztext','Sortis 40 mg    0 0 1/2',[])
    raw=json.dumps({'patient_id':'123','medications':[]})
    assert not validate_independent_medications(raw,'123',[s])[0].medications
