"""Structural regressions from the seven-case retained diagnostic report."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from caa_pdf_extractor.extraction import clinical_history, radiology
from caa_pdf_extractor.extraction.diagnosis_items import diagnosis_items, validate_independent
from caa_pdf_extractor.extraction.radiology_validators import validate_independent_radiology
from caa_pdf_extractor.extraction.radiology_artifacts import digest
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.extraction.examination_validators import source_response, validate_examinations
from test_batch_regressions import AuditHarness, clinical_bundle, exam_bundle
from test_radiology import procedure, empty_payload
import test_radiology as radiology_tests


def source(text):
    b=clinical_bundle()
    s=b.documents[0].sections[0]
    s.text=s.source_text='Diagnosen bei Entlassung\n'+text
    return b,s


def records(sections, diseases=None):
    items=diagnosis_items(sections)
    return {'patient_id':'123','clinical_history':[
        {'item_id':i.item_id,'disease':diseases[n] if diseases else i.source_text,
         'date_disease':None} for n,i in enumerate(items)]}


def test_source_item_evidence_is_never_model_quotation():
    b,s=source('9. Infekt des Mittelohrs mit Pseudomonas aeruginosa')
    p=records([s],['Infekt des Mittelohrs'])
    result,rejected,metadata=validate_independent(json.dumps(p),'123',[s])
    assert not rejected
    assert result.clinical_history[0].source_text=='Infekt des Mittelohrs mit Pseudomonas aeruginosa'
    # Even a model-generated source_text field is refused by this protocol.
    p['clinical_history'][0]['source_text']='Infekt des Mittelohres mit Pseudomonas aeruginosa'
    assert validate_independent(json.dumps(p),'123',[s])[1]
    assert metadata[0]['item_source_text'] in s.text
    assert s.text[metadata[0]['clause_start']:metadata[0]['clause_end']]==result.clinical_history[0].source_text


def test_normalized_caa_supported_by_longer_exact_source():
    b,s=source('1. Ätiologie am ehesten im Rahmen einer zerebralen Amyloidangiopathie')
    result,rejected,_=validate_independent(json.dumps(records([s],['zerebrale Amyloidangiopathie'])),'123',[s])
    assert not rejected
    assert result.clinical_history[0].source_text in s.text
    assert result.clinical_history[0].disease=='zerebrale Amyloidangiopathie'


def test_repeated_ich_splits_into_exact_clauses_and_independent_dates():
    b,s=source('1. Intracerebrale Blutung links suprainsulär am 27.12.2020 sowie intracerebrale Blutung rechts parietal unbekannten Alters')
    items=diagnosis_items([s])
    assert len(items)==2
    result,rejected,meta=validate_independent(json.dumps(records([s])),'123',[s])
    assert not rejected
    assert [i.date_disease for i in result.clinical_history]==[date(2020,12,27),None]
    assert meta[0]['item_source_text']==meta[1]['item_source_text']
    assert all(i.source_text in s.text for i in result.clinical_history)


@pytest.mark.parametrize('text,partial',[
    ('Intracerebrale Hämorrhagie 07/2017','07/2017'),
    ('Demenz, MMSE 25/30 08/17','08/17'),
])
def test_partial_dates_are_metadata_never_imputed(text,partial):
    b,s=source('1. '+text)
    p=records([s]);p['clinical_history'][0]['date_disease']='2017-07-01'
    result,rejected,meta=validate_independent(json.dumps(p),'123',[s])
    assert not rejected
    assert result.clinical_history[0].date_disease is None
    assert partial in meta[0]['partial_date_text']


def test_no_general_conjunction_split():
    _,s=source('1. Diabetes sowie Hypertonie')
    assert len(diagnosis_items([s]))==1


def test_diagnosis_footer_not_part_of_evidence():
    _,s=source('1. Hypertonie\n\nUniversitätsklinikum der Universität\nAdresse')
    assert diagnosis_items([s])[0].source_text=='Hypertonie'


@pytest.mark.parametrize('text',[
    'Mamma-Karzinom, ED 2005\nSegmentresektion mit Axilladissektion links am 07.04.2005',
    'V.a Blasentumor Rezidiv\n- Z.n. TUR-B 9/23\n- Transurethrale Resektion am 13.02.2024',
    'Demenz, MRT vom 01.02.2020',
    'Demenz, MRT vom 2020-02-01',
])
def test_treatment_and_imaging_dates_never_become_diagnosis_onset(text):
    _,s=source('1. '+text)
    p=records([s],[text.splitlines()[0]])
    result,rejected,_=validate_independent(json.dumps(p),'123',[s])
    assert not rejected and result.clinical_history[0].date_disease is None


def test_full_onset_date_wrapped_after_explicit_association():
    _,s=source('1. Intracerebrale Blutung links am\n27.12.2020')
    result,rejected,_=validate_independent(json.dumps(records([s],['Intracerebrale Blutung links'])),'123',[s])
    assert not rejected and result.clinical_history[0].date_disease==date(2020,12,27)


def test_long_item_kept_in_provenance_with_contiguous_bounded_evidence():
    text='Zerebrale Vaskulitis\n'+('Diagnostische Zusatzinformationen unverändert.\n'*25)
    _,s=source('1. '+text)
    result,rejected,meta=validate_independent(json.dumps(records([s],['Zerebrale Vaskulitis'])),'123',[s])
    assert not rejected
    evidence=result.clinical_history[0].source_text
    assert len(evidence)<=600 and evidence in s.text
    assert len(meta[0]['source_text'])>600 and meta[0]['item_source_text'] in s.text
    assert s.text[meta[0]['evidence_start']:meta[0]['evidence_end']]==evidence


def test_abbreviation_punctuation_preserved_in_audit_only_surgery():
    _,s=source('1. Z.n roboterassistierter Patchplastik (TAPP) 08/2020')
    result,rejected,meta=validate_independent(json.dumps(records([s],['Z.n. roboterassistierter Patchplastik (TAPP) 08/2020'])),'123',[s])
    assert not rejected and not result.clinical_history
    assert meta[0]['record']['date_disease'] is None
    assert meta[0]['target_classification']=='SURGERY_ONLY' and meta[0]['audit_only'] is True


def test_partial_history_artifact_commit_accepts_only_valid_records(tmp_path):
    b,s=source('1. Hypertonie\n2. Diabetes')
    p=records([s],['Hypertonie','invented diagnosis'])
    h=AuditHarness()
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(side_effect=[json.dumps(p),json.dumps({'patient_id':'123','clinical_history':[]})]))
    def run(dry=True):
        with patch.object(clinical_history,'build_patient_bundle',return_value=b):
            return clinical_history.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    dry=run()
    assert dry['status']=='PARTIAL_VALID' and dry['diagnosis_count']==1
    path=Path(dry['verified_artifact']);a=json.loads(path.read_text(encoding='utf-8'))
    assert a['validation_status']=='PARTIAL_VALID'
    assert len(a['accepted_records'])==len(a['rejected_records'])==1
    assert a['rejected_records'][0]['rejection_reason']
    client.generate_json.side_effect=AssertionError('Commit must never infer')
    assert run(False)['new_rows_inserted']==1
    assert run(False)['new_rows_inserted']==0
    assert [row.disease for row in h.rows]==['Hypertonie']
    assert client.generate_json.call_count==2
    # Rehashed attempted authorization of rejected output is blocked by original audit.
    a['accepted_records'].append(deepcopy(a['accepted_records'][0]))
    a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    path.write_text(json.dumps(a),encoding='utf-8')
    assert run(False)['status']=='VERIFIED_EXTRACTION_INVALID'


def test_wrong_patient_never_partial():
    _,s=source('1. Hypertonie')
    p=records([s]);p['patient_id']='wrong'
    with pytest.raises(ValueError,match='Patient provenance'):validate_independent(json.dumps(p),'123',[s])


@pytest.mark.parametrize('text',[
    'Multiple punktförmige Hämosiderinablagerungen frontoparietal.',
    'Mikro-Hämosiderinresiduen frontoparietal.',
    'Multiple Microbleeds frontoparietal.',
    'Chronische Mikroblutungen frontoparietal.',
])
def test_explicit_cmb_terms_and_exact_source_reanchoring(text):
    p=procedure(result_text=text);raw=empty_payload(p)
    raw['findings']=[{'type':'cmb','acute_lesion':True,'evidence':'A paraphrase of microbleeds.'}]
    event=validate_independent_radiology(json.dumps(raw),'123',p)
    assert event['findings']==[{'type':'cmb','acute_lesion':False,'evidence':text}]
    assert event['evidence_reconciliation'][0]['normalization_method']=='explicit_source_phenotype_span'


@pytest.mark.parametrize('text',['Hämosiderin.','Suszeptibilitätsartefakt.','Keine Mikroblutungen.',
                                'Keine Mikro-Hämosiderinresiduen.',
                                'Ohne punktförmige Hämosiderinablagerungen.'])
def test_generic_or_negated_iron_never_cmb(text):
    p=procedure(result_text=text);raw=empty_payload(p)
    raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':text}]
    e=validate_independent_radiology(json.dumps(raw),'123',p)
    assert not e['findings'] and e['rejected_records']


def test_wrong_other_type_normalized_only_from_explicit_exact_cmb():
    text='Multiple Microbleeds frontoparietal.'
    p=procedure(result_text=text);raw=empty_payload(p)
    raw['findings']=[{'type':'other','acute_lesion':True,'evidence':text}]
    e=validate_independent_radiology(json.dumps(raw),'123',p)
    assert e['findings'][0]['type']=='cmb'
    assert e['evidence_reconciliation'][0]=={'index':0,'model_type':'other','normalized_type':'cmb','normalization_method':'deterministic_phenotype_rule',
        'normalization_rule':'explicit_mri_phenotype_v1','exact_evidence':text}


@pytest.mark.parametrize('text',[
    'Verdacht auf Amyloidangiopathie.', 'suspekt für Amyloidangiopathie.',
    'Befund vereinbar mit einer Amyloidangiopathie.',
    'vereinbar mit Amyloidangiopathie.', 'Amyloidangiopathie wahrscheinlich.',
    'erscheint eine Amyloidangiopathie wahrscheinlich.',
])
def test_caa_compatibility_is_audit_only_verified_empty(text):
    p=procedure(result_text=text)
    e=validate_independent_radiology(json.dumps(empty_payload(p)),'123',p)
    assert e['validation_status']=='SUCCESS' and not e['findings']
    assert e['additional_findings']['caa_suspicion_explicit'] is True
    assert e['additional_findings']['caa_evidence']==text


def test_bad_auxiliary_does_not_erase_valid_phenotype():
    p=procedure(result_text='Multiple Microbleeds frontoparietal.')
    raw=empty_payload(p);raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':p.result_text}]
    raw['additional_findings']['other_findings']=['Invented wording.']
    e=validate_independent_radiology(json.dumps(raw),'123',p)
    assert e['validation_status']=='PARTIAL_VALID' and len(e['findings'])==1
    assert e['additional_findings']['other_findings']==[]
    assert e['rejected_records'][0]['field']=='additional_findings.other_findings[0]'


def test_partial_radiology_artifact_and_inference_free_commit():
    h=radiology_tests.PipelineTests();h.setUp()
    try:
        p=procedure(result_text='Multiple Microbleeds frontoparietal.')
        raw=empty_payload(p);raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':p.result_text}]
        raw['additional_findings']['other_findings']=['Invented wording.']
        dry=h.extract([p],[json.dumps(raw)])
        a=json.loads(Path(dry['verified_artifact']).read_text(encoding='utf-8'))
        assert a['validation_status']=='PARTIAL_VALID' and len(a['accepted_records'])==1
        assert len(a['rejected_records'])==1
        first=h.commit_saved([p])
        assert first['new_rows_inserted']==1 and not first['granite_called']
        assert h.commit_saved([p])['new_rows_inserted']==0
        assert all(r.type=='cmb' for r in h.rows)
    finally: h.doCleanups()


def test_caa_only_radiology_creates_verified_empty_artifact():
    h=radiology_tests.PipelineTests();h.setUp()
    try:
        p=procedure(result_text='Befund vereinbar mit einer Amyloidangiopathie.')
        dry=h.extract([p],[json.dumps(empty_payload(p))])
        a=json.loads(Path(dry['verified_artifact']).read_text(encoding='utf-8'))
        assert a['validation_status']=='VERIFIED_EMPTY' and a['accepted_records']==[]
        assert a['procedures'][0]['normalized_output']['additional_findings']['caa_suspicion_explicit'] is True
        assert h.commit_saved([p])['new_rows_inserted']==0
        assert not h.rows
    finally: h.doCleanups()


def test_partial_radiology_rehashed_rejection_tamper_is_blocked():
    h=radiology_tests.PipelineTests();h.setUp()
    try:
        p=procedure(result_text='Multiple Microbleeds frontoparietal.')
        raw=empty_payload(p);raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':p.result_text}]
        raw['additional_findings']['other_findings']=['Invented wording.']
        dry=h.extract([p],[json.dumps(raw)])
        path=Path(dry['verified_artifact']);a=json.loads(path.read_text(encoding='utf-8'))
        a['rejected_records']=[];a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
        path.write_text(json.dumps(a),encoding='utf-8')
        assert h.commit_saved([p])['status']=='VERIFIED_EXTRACTION_INVALID'
        assert not h.rows
    finally: h.doCleanups()


@pytest.mark.parametrize('matches,status',[([], 'CASE_NOT_FOUND'), ([(42,),(43,)],'CASE_AMBIGUOUS')])
def test_partial_radiology_never_overrides_database_identity_failure(matches,status):
    h=radiology_tests.PipelineTests();h.setUp()
    try:
        h.session.execute.return_value.all.return_value=matches
        p=procedure(result_text='Multiple Microbleeds frontoparietal.')
        raw=empty_payload(p);raw['findings']=[{'type':'cmb','acute_lesion':False,'evidence':p.result_text}]
        raw['additional_findings']['other_findings']=['Invented wording.']
        dry=h.extract([p],[json.dumps(raw)])
        assert dry['status']==dry['validation_status']==status
        assert not dry.get('verified_artifact') and not h.rows
    finally: h.doCleanups()


def test_exact_exam_subreport_preserves_internal_labels_excludes_legend():
    text=('Neurosonologie (04.03.2021)\nExtrakranielle Carotisabschnitte: ICA unauffällig.\n'
          'Extrakranielle Vertebralisabschnitte: Keine Wandverdickungen/Halo-Zeichen.\n'
          'Gray-Weale-Klassifikation:\nReferenzlegende')
    i=select_examination_inputs(exam_bundle(text))[0]
    e=validate_examinations(source_response(i),i)
    result=e['records'][0]['result']
    assert result in text and result.startswith('Extrakranielle Carotisabschnitte:')
    assert 'Extrakranielle Vertebralisabschnitte:' in result
    assert 'Wandverdickungen/Halo-Zeichen' in result
    assert 'Gray-Weale' not in result


def test_doc42_anatomical_split_retained_with_exact_results():
    text=('Neurosonologie (17.07.2018)\nExtrakranielle Carotisabschnitte: ICA unauffällig.\n'
          'Extrakranielle Vertebralisabschnitte: Unauffällig.\nTranstemporal: ACM unauffällig.')
    selected=select_examination_inputs(exam_bundle(text))
    assert [i.location for i in selected]==['neck vessels','brain']
    assert all(validate_examinations(source_response(i),i)['records'][0]['result'] in text for i in selected)
