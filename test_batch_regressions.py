"""Regressions from the controlled batch, plus immutable document artifacts."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import ExtractionRun, ClinicalHistory, Medication
from caa_pdf_extractor.extraction import clinical_history, medications
from caa_pdf_extractor.extraction.validators import validate_output
from caa_pdf_extractor.extraction.family_history_models import validate_family_history, relative_category
from caa_pdf_extractor.extraction.family_history_inputs import select_family_history_inputs
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.extraction.laboratory_layout import extract_page, page_tokens
from caa_pdf_extractor.extraction.laboratory_layout_workflow import observations, observation_fingerprint, reconcile
from caa_pdf_extractor.extraction.radiology_validators import validate_radiology
from caa_pdf_extractor.extraction.radiology_artifacts import digest
from test_clinical_extraction import bundle as clinical_bundle, output as clinical_payload
from test_medications import bundle as medication_bundle, section as medication_section, payload as medication_payload
from test_family_history import bundle as family_bundle, payload as family_payload
from test_laboratory_layout import fixture, word
from caa_pdf_extractor.preparation.models import PreparedSection, PreparedDocument, PatientDocumentBundle, DiagnosticProcedure


class AuditHarness:
    def __init__(self):
        self.runs={};self.rows=[];self.session=MagicMock();self.factory=MagicMock()
        self.factory.begin.return_value.__enter__.return_value=self.session
        self.factory.return_value.__enter__.return_value=self.session
        self.session.execute.return_value.all.return_value=[(42,)]
        self.session.get.side_effect=lambda model,key:self.runs.get(key)
        self.session.scalars.side_effect=lambda q:list(self.rows)
        def add(row):
            if isinstance(row,ExtractionRun):row.id=len(self.runs)+1;self.runs[row.id]=row
            else:
                for f in ('date_disease','date_noted','date_stopped','medication_category'):
                    if isinstance(getattr(row,f,None),Null):setattr(row,f,None)
                self.rows.append(row)
        self.session.add.side_effect=add


@pytest.fixture(params=['clinical_history','medications'])
def workflow(request,tmp_path):
    domain=request.param; module=clinical_history if domain=='clinical_history' else medications
    b=clinical_bundle() if domain=='clinical_history' else medication_bundle(medication_section())
    payload=clinical_payload() if domain=='clinical_history' else medication_payload()
    h=AuditHarness()
    client=SimpleNamespace(runtime='fake-local',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(payload)))
    def run(dry=True,bundle=None):
        with patch.object(module,'build_patient_bundle',return_value=bundle or b):
            return module.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    return SimpleNamespace(domain=domain,h=h,client=client,b=b,payload=payload,run=run,tmp=tmp_path)


def test_artifact_and_no_model_commit(workflow):
    w=workflow;dry=w.run(); assert dry['artifact_validation_status']=='VERIFIED'
    assert not w.h.rows
    calls=w.client.generate_json.call_count
    w.client.generate_json.side_effect=AssertionError('Commit called model')
    first=w.run(False); assert first['status']=='SUCCESS' and first['new_rows_inserted']==1
    second=w.run(False); assert second['status']=='SUCCESS' and second['new_rows_inserted']==0
    assert second['duplicates_skipped']==1
    assert w.client.generate_json.call_count==calls
    assert first['model_calls']==0 and not first['granite_called']


def test_missing_artifact_no_model(workflow):
    workflow.client.generate_json.side_effect=AssertionError('Missing artifact must not infer')
    assert workflow.run(False)['status']=='NO_VERIFIED_EXTRACTION'
    workflow.client.generate_json.assert_not_called()


def test_changed_source_blocked(workflow):
    workflow.run(); b=deepcopy(workflow.b); b.documents[0].sections[0].text+=' altered source'
    assert workflow.run(False,b)['status']=='VERIFIED_EXTRACTION_STALE'
    assert not workflow.h.rows


def test_changed_prompt_blocked(workflow):
    workflow.run()
    target='caa_pdf_extractor.llm.prompts.INSTRUCTIONS' if workflow.domain=='clinical_history' else 'caa_pdf_extractor.llm.medication_prompts.INSTRUCTIONS'
    with patch(target,'Changed prompt'):
        assert workflow.run(False)['status']=='VERIFIED_EXTRACTION_STALE'
    assert not workflow.h.rows


@pytest.mark.parametrize('attribute,value',[('model_name','changed'),('model_version','changed'),('runtime','changed')])
def test_model_compatibility(workflow,attribute,value):
    workflow.run();setattr(workflow.client,attribute,value)
    assert workflow.run(False)['status']=='VERIFIED_EXTRACTION_STALE'


def test_tamper_blocked_even_with_recomputed_checksum(workflow):
    out=workflow.run();p=Path(out['verified_artifact']);a=json.loads(p.read_text(encoding='utf-8'))
    a['normalized_output']=[];a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    p.write_text(json.dumps(a),encoding='utf-8')
    assert workflow.run(False)['status']=='VERIFIED_EXTRACTION_INVALID'
    assert not workflow.h.rows


def test_altered_audit_blocked(workflow):
    workflow.run();workflow.h.runs[1].raw_output={**workflow.h.runs[1].raw_output,'case_id':99}
    assert workflow.run(False)['status']=='VERIFIED_EXTRACTION_INVALID'


def test_case_identity_change_blocked(workflow):
    workflow.run();workflow.h.session.execute.return_value.all.return_value=[(99,)]
    assert workflow.run(False)['status']=='VERIFIED_EXTRACTION_STALE'


def test_existing_duplicate_preserved(workflow):
    workflow.run()
    if workflow.domain=='clinical_history':workflow.h.rows.append(SimpleNamespace(disease='Example disease',date_disease=date(2015,12,11)))
    else:workflow.h.rows.append(SimpleNamespace(medication='Ramipril 5 mg 1-0-0',date_noted=None,date_stopped=None,medication_category=None))
    result=workflow.run(False);assert result['new_rows_inserted']==0 and result['duplicates_skipped']==1


def clinical_validation(text,disease,quote,day=None):
    b=clinical_bundle();s=b.documents[0].sections[0];s.text=s.source_text=text
    p=clinical_payload();r=p['clinical_history'][0];r.update(disease=disease,source_text=quote,date_disease=day)
    return validate_output(json.dumps(p),'123',[s])


def test_normalized_label_with_literal_evidence():
    result=clinical_validation('Z.n. Infarkt','Zustand nach Infarkt','Z.n. Infarkt')
    assert result.clinical_history[0].disease=='Zustand nach Infarkt'


@pytest.mark.parametrize('text,disease,quote,day',[
 ('9. Infekt vor ca. 23 Jahren','Infekt vor ca. 22 Jahren','9. Infekt vor ca. 22 Jahren',None),
 ('1. Infarkt','Infarkt','2. Infarkt',None),
 ('Hämorrhagie 07/2017','Hämorrhagie','Hämorrhagie 07/2017','2017-07-01'),
 ('Infarkt\nAufnahme 01.02.2020','Infarkt','Infarkt','2020-02-01'),
 ('1. Infarkt\n2. Diabetes','Infarkt\n2. Diabetes','1. Infarkt\n2. Diabetes',None),
 ('Transitorische Attacke im Arteria-Stromgebiet','Attacke im Arterien-Stromgebiet','Transitorische Attacke im Arteria-Stromgebiet',None),
 ('Originale Schreibweise','Originale Schreibweise','originale Schreibweise',None),
])
def test_observed_clinical_failures_remain_rejected(text,disease,quote,day):
    with pytest.raises(ValueError):clinical_validation(text,disease,quote,day)


@pytest.mark.parametrize('relative',['Brüder','Schwestern','Geschwister'])
def test_plural_family_source(relative):
    text=f'Familienanamnese: {relative} hatten Demenz.'
    p=family_payload(relative,'Demenz','first grade',text)
    result=validate_family_history(json.dumps(p),'123',select_family_history_inputs(family_bundle(text)))
    assert result['family_history'][0]['relative_category']=='first grade'
    assert result['family_history'][0]['evidence']==text


@pytest.mark.parametrize('relative',['Familie','Angehörige','Verwandte'])
def test_vague_family_degree_blocked(relative):
    text=f'Familienanamnese: {relative} hatten Demenz.'
    with pytest.raises(ValueError):validate_family_history(json.dumps(family_payload(relative,'Demenz','unknown',text)),'123',select_family_history_inputs(family_bundle(text)))


def exam_bundle(text):
    s=PreparedSection('s1','123',1,'Neurosonologie',4,4,text,text)
    return PatientDocumentBundle('123',[PreparedDocument(1,'synthetic.pdf','123/synthetic.pdf','hash','unknown',None,[s])])


def test_combined_anatomy_and_sides_split_exactly():
    text='Neurosonologie (17.07.2018)\nExtrakranielle Carotisabschnitte: unauffällig.\nExtrakranielle Vertebralisabschnitte: unauffällig.\nTranstemporal: Ohne Kontrastmittel\nLinks: ACM unauffällig.\nRechts: ACM unauffällig.\nInternistisches Schlaganfall Konsil (18.07.2018)\nFremder Befund.'
    selected=select_examination_inputs(exam_bundle(text))
    assert [i.location for i in selected]==['neck vessels','brain','brain']
    assert len({i.examination_id for i in selected})==3
    for i in selected:
        assert i.text in text and i.date_examination=='2018-07-17' and i.page_number==4
        assert 'Fremder Befund' not in i.text


def test_unsplittable_mixed_remains_blocked():
    with pytest.raises(ValueError,match='ambiguous'):
        select_examination_inputs(exam_bundle('Neurosonologie (17.07.2018)\nICA und ACM unauffällig.'))


def test_planned_interval_and_narrative_not_candidates():
    for label in ('Laborkontrolle CRP in 7 bis 10 Tagen','HbA1c < 6,5 % durch Hausarzt','Dm II-Hba1c 5,9'):
        tokens=page_tokens([word(label,10,40,150),word('7',200,40)],1)
        assert extract_page(tokens,1)==[]


def test_unsupported_unit_retained_audit_only():
    candidates=extract_page(fixture(unit='mg/l'),1)
    assert candidates and all(c['audit_only'] for c in candidates)
    assert all('UNIT_MISMATCH' in c['issues'] for c in candidates)
    assert observations('123',42,candidates)==[]


def test_true_result_retained_and_reference_excluded():
    raw=[word('Parameter',10,10),word('Einheit',120,10),word('Erster',190,10,40),word('Min.',290,10,40),word('Max.',390,10,40),word('CRP',10,40),word('mg/dl',120,40),word('0.42',200,40),word('0.01',300,40),word('0.99',400,40)]
    values=observations('123',42,extract_page(page_tokens(raw,1),1))
    assert [v['decimal_value'] for v in values]==['0.42']
    assert values[0]['date_of_exam'] is None


def test_null_date_duplicate_fingerprints_require_links():
    candidates=extract_page(fixture(values=('0.42','0.42')),1)
    values=observations('123',42,candidates)
    assert len(values)==2
    assert [op for _,op,_ in reconcile(values,[],[])]==['ambiguous','ambiguous']
    rows=[SimpleNamespace(id=1,date_of_exam=None,crp=__import__('decimal').Decimal('0.42'))]
    audit=SimpleNamespace(status='SUCCESS',raw_output={'operation':'COMMIT_LAYOUT','policy_version':'crp_hba1c_null_date_v1','observation_links':[{'observation_fingerprint':values[0]['observation_fingerprint'],'laboratory_id':1}]})
    assert [op for _,op,_ in reconcile(values,rows,[audit])]==['skip','ambiguous']
from test_radiology import procedure as mri_procedure, empty_payload as empty_mri
from caa_pdf_extractor.extraction.radiology_inputs import report_text


def test_radiology_paraphrase_rejected():
    p=mri_procedure(result_text='Chronische Mikroblutungen frontoparietal.')
    raw=empty_mri(p);raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':'Multiple chronische Mikroblutungen frontoparietal.'}]
    with pytest.raises(ValueError,match='Unsupported source quotation'):validate_radiology(json.dumps(raw),'123',p)


def test_radiology_unsupported_phenotype_rejected():
    text='Hämosiderinreste ohne Mikroblutungen.'
    p=mri_procedure(result_text=text);raw=empty_mri(p)
    raw['findings']=[{'type':'css','acute_lesion':False,'evidence':text}]
    with pytest.raises(ValueError,match='Phenotype evidence'):validate_radiology(json.dumps(raw),'123',p)


def test_historical_report_not_current_mri_source():
    p=mri_procedure(result_text='Aktuelle MRT unauffällig.\nHistorischer CT-Befund: Frisches frontales Hämatom.')
    assert report_text(p)=='Aktuelle MRT unauffällig.'
    raw=empty_mri(p);raw['findings']=[{'type':'lobar','acute_lesion':True,'evidence':'Frisches frontales Hämatom.'}]
    with pytest.raises(ValueError,match='Unsupported source quotation'):validate_radiology(json.dumps(raw),'123',p)

def test_explicit_reference_column_audit_only():
    raw=[word('Parameter',10,10),word('Einheit',120,10),word('Erster',190,10,40),word('Referenz',290,10,40),word('CRP',10,40),word('mg/dl',120,40),word('0.42',200,40),word('0.50',300,40)]
    values=observations('123',42,extract_page(page_tokens(raw,1),1))
    assert [v['decimal_value'] for v in values]==['0.42']


def test_distinct_same_line_events_not_given_one_date():
    text='Intracerebrale Blutung links am 27.12.2020 sowie intracerebrale Blutung rechts unbekannten Alters'
    with pytest.raises(ValueError,match='Merged diagnosis events'):
        clinical_validation(text,text,text,'2020-12-27')

def test_rehashed_source_fingerprint_cannot_replace_original_audit(workflow):
    from caa_pdf_extractor.extraction.document_artifacts import fingerprint
    out=workflow.run();p=Path(out['verified_artifact']);a=json.loads(p.read_text(encoding='utf-8'))
    b=deepcopy(workflow.b); b.documents[0].sections[0].text+=' added context'
    a['source_fingerprint']=fingerprint(workflow.domain,b)
    a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    p.write_text(json.dumps(a),encoding='utf-8')
    assert workflow.run(False,b)['status']=='VERIFIED_EXTRACTION_INVALID'
    assert not workflow.h.rows


def test_split_first_line_retains_real_result_after_label():
    from caa_pdf_extractor.extraction.examination_validators import validate_examinations
    text='Neurosonologie (17.07.2018)\nExtrakranielle Carotisabschnitte: Unauffällig.\nTranskraniell: ACM unauffällig.'
    selected=select_examination_inputs(exam_bundle(text))
    for item in selected:
        assert item.header in item.text
        raw={'patient_id':'123','examination_id':item.examination_id,'records':[{'result':item.text}]}
        assert validate_examinations(json.dumps(raw),item)['records'][0]['result']==item.text

def test_observed_mittelohrs_spelling_must_not_be_rewritten_in_evidence():
    original='Infekt des Mittelohrs mit Pseudomonas aeruginosa'
    paraphrase='Infekt des Mittelohres mit Pseudomonas aeruginosa'
    with pytest.raises(ValueError,match='Unsupported source quotation'):
        clinical_validation(original,paraphrase,paraphrase)


def test_family_audit_reason_is_specific_and_sanitized(tmp_path):
    from caa_pdf_extractor.extraction.family_history import extract_patient
    text='Familienanamnese: Freund hatte Demenz.'
    h=AuditHarness()
    raw=family_payload('Freund','Demenz','unknown',text)
    client=SimpleNamespace(runtime='fake-local',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(raw)))
    with patch('caa_pdf_extractor.extraction.family_history.build_patient_bundle',return_value=family_bundle(text)):
        result=extract_patient('123',client=client,session_factory=h.factory,debug_root=tmp_path)
    assert result['status']=='FAILED_VALIDATION'
    assert h.runs[1].validation_errors==[{'type':'UNSUPPORTED_RELATIVE_TERM'}]
    assert text not in json.dumps(h.runs[1].validation_errors)
