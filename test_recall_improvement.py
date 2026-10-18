"""Positive source recall and negative controls, with immutable audit replay."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from caa_pdf_extractor.extraction import case_rules, clinical_history
from caa_pdf_extractor.extraction.diagnosis_items import diagnosis_items, validate_recall_responses
from caa_pdf_extractor.extraction.radiology_validators import validate_independent_radiology
from test_source_evidence import source, records
from test_radiology import procedure, empty_payload
from test_batch_regressions import AuditHarness


@pytest.mark.parametrize('text,kind',[
    ('Kortikale superfizielle Siderose.','css'),
    ('Kortikale oberflächliche Siderose.','css'),
    ('Cortical superficial siderosis.','css'),
    ('Kortikale Hämosiderose.','css'),
    ('Oberflächliche Hämosiderinablagerungen.','css'),
    ('Ausgeprägte oberflächliche Hämosiderose rechtshirnig.','css'),
    ('Primär rechtshemisphärischesuperfizielle Hämosiderose.','css'),
    ('Stattgehabte atypische ICB frontal rechts mit superfiziellen Siderose.','css'),
    ('Das Blutungsareal rechts hochfrontolateral zeigt sich im chronischen Stadium.','lobar'),
    ('Intracerebral hemorrhage high frontal.','lobar'),
    ('Hämatom subkortikal parietal.','lobar'),
    ('Die alten Mikroblutungen könnten für eine Vasculitis sprechen.','cmb'),
])
def test_source_candidates_survive_empty_model(text,kind):
    p=procedure(result_text=text)
    event=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    found=next(f for f in event['findings'] if f['type']==kind)
    assert found['evidence']==text
    assert event['mri_date']==p.date
    if kind in ('cmb','css'): assert found['acute_lesion'] is False
    rule=next(r for r in event['evidence_reconciliation'] if r.get('normalized_type')==kind)
    assert rule['model_type'] is None and rule['exact_evidence']==text


@pytest.mark.parametrize('text',[
    'Alte Hämosiderinresiduen basalgangliennah.', 'Hämosiderose der Stirnlappen.',
    'Keine kortikale superfizielle Siderose.', 'Ohne oberflächliche Hämosiderose.',
    'DD Microbleeds im Rahmen einer Vaskulitis.', 'Verdacht auf Mikroblutungen.',
    'Blutung ohne Lokalisation.', 'ICB.', 'Blutung im Thalamus.',
    'Hämatom im Kleinhirn.', 'Basalganglienblutung; frontal normale Verhältnisse.',
    'Blutungsresiduen entlang der Hirnfurchen rechts frontoparietal.',
    'Geringe sulkale Hämosiderose frontoparietal?',
])
def test_weak_negated_differential_or_deep_source_has_no_recalled_phenotype(text):
    p=procedure(result_text=text)
    event=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    assert event['findings']==[]


@pytest.mark.parametrize('cue',['Exitus letalis am','verstorben am','Tod am'])
def test_explicit_death_date_only(cue):
    text=cue+' 20.05.2025'
    _,s=source(text)
    s.page_end=s.page_start
    bundle=SimpleNamespace(documents=[SimpleNamespace(source_document_id=1,sections=[s])])
    out=case_rules.extract(case_rules.select_inputs(bundle))
    assert out['update_fields']=={'death_date':'2025-05-20'}
    assert out['provenance']['death_date'][0]['evidence']==text


@pytest.mark.parametrize('text',[
    'Palliative Versorgung am 20.05.2025', 'DNR/DNE 20.05.2025',
    'Hospiz am 20.05.2025', 'Mutter verstorben am 20.05.2025',
    'Kein Tod am 20.05.2025', 'Exitus letalis am 05/2025',
])
def test_death_inference_is_never_allowed(text):
    item=case_rules.CaseInput('death_autopsy',1,1,'s',text)
    assert case_rules.extract([item])['update_fields']=={}


def test_focused_recovery_replays_exact_item_and_preserves_null_date(tmp_path):
    b,s=source('1. Hypertonie\n2. Diabetes mellitus, MRT vom 01.02.2020')
    initial=records([s],['Hypertonie','Diabetes mellitus'])
    retry={'patient_id':'123','clinical_history':[initial['clinical_history'].pop()]}
    h=AuditHarness()
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',
        generate_json=MagicMock(side_effect=[json.dumps(initial),json.dumps(retry)]))
    def run(dry=True):
        with patch.object(clinical_history,'build_patient_bundle',return_value=b):
            return clinical_history.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    out=run()
    assert out['status']=='SUCCESS' and out['diagnosis_count']==2
    focused=client.generate_json.call_args_list[1].args[0]
    assert 'Diabetes mellitus' in focused and 'Hypertonie' not in focused
    a=json.loads(Path(out['verified_artifact']).read_text(encoding='utf-8'))
    assert all(r['date_disease'] is None and r['source_text'] in s.text for r in a['accepted_records'])
    client.generate_json.side_effect=AssertionError('No commit inference')
    assert run(False)['new_rows_inserted']==2
    assert run(False)['new_rows_inserted']==0
    assert client.generate_json.call_count==2


def test_focused_retry_cannot_change_patient_or_item():
    _,s=source('1. Hypertonie\n2. Diabetes')
    ids=[i.item_id for i in diagnosis_items([s])]
    raw=json.dumps({'patient_id':'123','clinical_history':[]})
    response={'patient_id':'123','clinical_history':[{'item_id':ids[1],'disease':'Diabetes','date_disease':None}]}
    with pytest.raises(ValueError,match='provenance'):
        validate_recall_responses(raw,[{'item_id':ids[0],'response':json.dumps(response)}],'123',[s])
    response['patient_id']='other'
    with pytest.raises(ValueError,match='provenance'):
        validate_recall_responses(raw,[{'item_id':ids[1],'response':json.dumps(response)}],'123',[s])


def test_focused_retry_never_repairs_unsupported_spelling():
    _,s=source('1. Pulmonaler Hypertonus')
    raw=json.dumps({'patient_id':'123','clinical_history':[]})
    response=records([s],['Pulmonaler Hypertononus'])
    result,rejected,_=validate_recall_responses(raw,[{'item_id':diagnosis_items([s])[0].item_id,'response':json.dumps(response)}],'123',[s])
    assert not result.clinical_history and rejected


def test_chronic_abbreviation_does_not_create_acute_lobar_lesion():
    text='Das Blutungsareal rechts hochfrontolateral zeigt sich am Übergang v. subakuten ins\n chron. Stadium mit Resorptionszone.'
    p=procedure(result_text=text)
    event=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    assert event['findings']==[{'type':'lobar','acute_lesion':False,'evidence':text}]


def test_microbleed_location_is_not_borrowed_for_lobar_hemorrhage():
    text='Intrakranielle Blutung\nAlte Mikroblutungen temporal rechts.'
    p=procedure(result_text=text)
    event=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    assert event['findings']==[{'type':'cmb','acute_lesion':False,'evidence':text}]
