"""Source identity, exact row evidence, and independent accepted-only commits."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import pytest
from caa_pdf_extractor.extraction import medications,family_history
from caa_pdf_extractor.extraction.radiology_identity import observations,plan_reconciliation
from caa_pdf_extractor.extraction.radiology_artifacts import VerifiedExtractionError,digest
from caa_pdf_extractor.extraction.examination_inputs import select_examination_inputs
from caa_pdf_extractor.extraction.examination_validators import source_response,validate_examinations
from caa_pdf_extractor.extraction.examination_identity import plan_reconciliation as exam_plan,ExaminationIdentityError
from caa_pdf_extractor.extraction.family_history_inputs import select_family_history_inputs
from caa_pdf_extractor.extraction.family_history_models import validate_independent_family_history
from caa_pdf_extractor.extraction.medication_inputs import select_medication_inputs
from caa_pdf_extractor.extraction.medication_validators import validate_independent_medications,validate_medications
from caa_pdf_extractor.preparation.models import PreparedTable
from test_batch_regressions import AuditHarness,exam_bundle
import test_radiology as radio
import test_medications as med
import test_family_history as family


def candidates(*procedures):
    entries={p.procedure_id:{'normalized_output':{'mri_date':p.date,'findings':[{'type':'cmb','acute_lesion':False,'evidence':'Microbleeds.'}]}} for p in procedures}
    return observations(procedures,entries)


def test_exact_observation_twice_and_same_study_copy_are_safe():
    p=radio.procedure(result_text='Microbleeds.')
    c=candidates(p);plan,groups=plan_reconciliation(c+c,[],[])
    assert len(plan)==1 and groups[0]['resolution']=='SAME_STUDY_DUPLICATE'
    q=deepcopy(p);q.procedure_id='copy'
    plan,groups=plan_reconciliation(candidates(p,q),[],[])
    assert len({v['canonical'] for v in plan.values()})==1


@pytest.mark.parametrize('day',[None,'2020-01-01'])
def test_distinct_mri_reports_same_db_key_block(day):
    a=radio.procedure(date=day,result_text='First Microbleeds.')
    b=radio.procedure(procedure_id='different',date=day,result_text='Second Microbleeds.')
    with pytest.raises(VerifiedExtractionError,match='DUPLICATE_IDENTITY_AMBIGUOUS'):
        plan_reconciliation(candidates(a,b),[],[])


def test_existing_unlinked_mri_row_blocks_and_audited_replay_reuses():
    p=radio.procedure(date=None,result_text='Microbleeds.');c=candidates(p)
    row=SimpleNamespace(id=3,mri_date=None,type='cmb',acute_lesion=False)
    with pytest.raises(VerifiedExtractionError,match='DUPLICATE_IDENTITY_AMBIGUOUS'):plan_reconciliation(c,[row],[])
    audit=SimpleNamespace(status='SUCCESS',raw_output={'operation':'COMMIT_VERIFIED_EXTRACTION','radiology_observations':[{**c[0],'row_id':3}]})
    plan,_=plan_reconciliation(c,[row],[audit]);assert next(iter(plan.values()))['reason']=='IDEMPOTENT_REPLAY'
    q=radio.procedure(procedure_id='different',date=None,result_text='Different Microbleeds.')
    with pytest.raises(VerifiedExtractionError,match='DUPLICATE_IDENTITY_AMBIGUOUS'):plan_reconciliation(candidates(q),[row],[audit])


def test_radiology_runtime_legacy_row_blocks_no_inference_or_partial_writes():
    h=radio.PipelineTests();h.setUp()
    try:
        h.extract();h.rows.append(SimpleNamespace(id=7,mri_date=date(2015,12,14),type='cmb',acute_lesion=False,deleted=False))
        result=h.commit_saved()
        assert result['status']=='DUPLICATE_IDENTITY_AMBIGUOUS' and len(h.rows)==1
    finally:h.doCleanups()


def test_same_parent_sides_one_exact_result_with_side_offsets():
    text='Neurosonologie (17.07.2018)\nExtrakranielle Carotisabschnitte: ICA unauffällig.\nTranstemporal: Ohne Kontrastmittel\nLinks: ACM 40 cm/s.\nRechts: ACM 42 cm/s.'
    xs=select_examination_inputs(exam_bundle(text));assert len(xs)==2
    brain=xs[1];record=validate_examinations(source_response(brain),brain)['records'][0]
    assert 'Links:' in record['result'] and 'Rechts:' in record['result'] and record['result'] in text
    assert brain.parent_examination_id==xs[0].parent_examination_id
    sides=[s for s in brain.source_spans if s['role']=='side_subreport'];assert len(sides)==2
    assert all(text[s['start']:s['end']]==s['text'] for s in sides)


@pytest.mark.parametrize('day',['17.07.2018',None])
def test_distinct_parent_studies_with_identical_keys_are_blocked(day):
    header='ECHOKARDIOGRAPHIE'+(f' ({day})' if day else '')
    xs=select_examination_inputs(exam_bundle(header+'\nLinks: Befund.\n'+header+'\nRechts: Anderer Befund.'))
    events=[validate_examinations(source_response(x),x) for x in xs]
    with pytest.raises(ExaminationIdentityError,match='DUPLICATE_IDENTITY_AMBIGUOUS'):exam_plan(events,[],[])


def test_identical_examination_source_copy_deduplicates_by_provenance():
    x=select_examination_inputs(exam_bundle('ECHOKARDIOGRAPHIE (17.07.2018)\nGute Pumpfunktion.'))[0]
    e=validate_examinations(source_response(x),x)
    plan,_=exam_plan([e,deepcopy(e)],[],[])
    assert len(plan)==1 and next(iter(plan.values()))[1]=='SAME_SOURCE_DUPLICATE'


@pytest.mark.parametrize('prefix',['Untersuchungstechnik: Ohne Kontrastmittel.','Administrativer Hinweis: digital gespeichert.'])
def test_nonclinical_intro_only_in_provenance(prefix):
    text='Neurosonologie (17.07.2018)\n'+prefix+'\nExtrakranielle Carotisabschnitte: ICA unauffällig.\nTranstemporal: ACM unauffällig.'
    xs=select_examination_inputs(exam_bundle(text));assert len(xs)==2
    assert all(x.introductory_metadata and prefix not in validate_examinations(source_response(x),x)['records'][0]['result'] for x in xs)


def test_material_unassignable_intro_remains_ambiguous():
    text='Neurosonologie (17.07.2018)\nHochgradige Stenose, Lokalisation unklar.\nExtrakranielle Carotisabschnitte: ICA frei.\nTranstemporal: ACM frei.'
    with pytest.raises(ValueError,match='unassigned introductory findings'):select_examination_inputs(exam_bundle(text))


def test_actual_mixed_source_carotis_intro_is_only_neck():
    text=('Neurosonologie (04.01.2023)\nA. carotis communis: - |- | nicht untersucht | - | - | nicht untersucht\n'
          'Extrakranielle Vertebralisabschnitte:nicht untersucht\nTranstemporal: Ohne Kontrastmittel\nSehr schlechtes Schallfenster:\n'
          'ACM links: Vmax 37cm/s ACP links: 34cm/s\nACM rechts: Vmax 47cm/s ACP rechts: 28cm/s.\nACA bds nicht sicher einsehbar')
    xs=select_examination_inputs(exam_bundle(text));assert [x.location for x in xs]==['brain']
    assert 'A. carotis communis' not in xs[0].text
    assert xs[0].excluded_subreports[0]['reason']=='NOT_PERFORMED'
    assert xs[0].introductory_metadata[0]['classification']=='NECK_VESSEL'
    assert xs[0].introductory_metadata[0]['persisted_as_result'] is False
    assert all(validate_examinations(source_response(x),x)['records'][0]['result'] in text for x in xs)


def test_identical_text_from_different_parent_studies_never_deduplicates():
    report='Neurosonologie (17.07.2018)\nExtrakranielle Carotisabschnitte: ICA frei.\nTranstemporal: ACM frei.\n'
    text=report+report
    xs=select_examination_inputs(exam_bundle(text))
    assert len(xs)==4
    assert xs[0].source_spans[0]['start']!=xs[2].source_spans[0]['start']
    assert xs[1].source_spans[0]['start']!=xs[3].source_spans[0]['start']
    events=[validate_examinations(source_response(x),x) for x in xs]
    with pytest.raises(ExaminationIdentityError):exam_plan(events,[],[])


def test_wrong_parent_cannot_reuse_a_copied_source_span():
    x=select_examination_inputs(exam_bundle('ECHOKARDIOGRAPHIE (17.07.2018)\nGute Pumpfunktion.'))[0]
    e=validate_examinations(source_response(x),x);other=deepcopy(e)
    other['examination_id']='different';other['parent_examination_id']='different'
    with pytest.raises(ExaminationIdentityError):exam_plan([e,other],[],[])


@pytest.mark.parametrize('complete',[True,False])
def test_wrapped_family_sentence_exact_and_completion_rejected(complete):
    text='Familienanamnese: Großmutter mütterlicherseits mit Schlaganfall, keine Operationen am Gehirn in der'+ ('\n Vergangenheit.' if complete else '')
    b=family.bundle(text);raw=family.payload('Großmutter mütterlicherseits','Schlaganfall','second grade',text.replace('\n','') if complete else text+' Vergangenheit.')
    # Retain the source whitespace equivalence, never add words beyond the source.
    if complete:raw['family_history'][0]['evidence']=' '.join(text.split())
    result=validate_independent_family_history(json.dumps(raw),'123',select_family_history_inputs(b))
    assert bool(result['family_history'])==complete
    assert bool(result['rejected_records'])!=complete


def test_family_negated_target_still_rejected():
    text='Familienanamnese: Mutter ohne Demenz, keine Operationen am Gehirn.'
    result=validate_independent_family_history(json.dumps(family.payload(evidence=text)),'123',select_family_history_inputs(family.bundle(text)))
    assert not result['family_history'] and result['rejected_records']


@pytest.mark.parametrize('domain',['medications','family_history'])
def test_partial_artifact_commit_only_accepted_idempotent_and_zero_inference(tmp_path,domain):
    h=AuditHarness();module=medications if domain=='medications' else family_history
    if domain=='medications':
        b=med.bundle(med.section('Ramipril 5 mg 1-0-0\nAmlodipin 5 mg 1-0-0'))
        raw=med.payload();raw['medications'].append(med.payload('Invented drug')['medications'][0])
    else:
        b=family.bundle('Familienanamnese: Mutter hatte Demenz.\nVater hatte Schlaganfall.')
        raw=family.payload();bad=family.payload('Vater','Schlaganfall',evidence='Vater hatte Schlaganfall und erfundene Krankheit.')['family_history'][0]
        raw['family_history'].append(bad)
    client=SimpleNamespace(runtime='fake',model_name='test',model_version='digest',generate_json=MagicMock(return_value=json.dumps(raw)))
    def run(dry):
        with patch.object(module,'build_patient_bundle',return_value=b):return module.extract_patient('123',dry_run=dry,client=client,session_factory=h.factory,debug_root=tmp_path)
    first=run(True);assert first['status']=='PARTIAL_VALID'
    a=json.loads(Path(first['verified_artifact']).read_text(encoding='utf-8'))
    assert len(a['accepted_records'])==len(a['rejected_records'])==1
    client.generate_json.side_effect=AssertionError('No inference')
    assert run(False)['new_rows_inserted']==1
    assert run(False)['new_rows_inserted']==0
    assert len(h.rows)==1 and client.generate_json.call_count==1
    # Rehashing an attempt to authorize a rejected row cannot bypass original audits.
    a['rejected_records']=[];a.pop('artifact_sha256');a['artifact_sha256']=digest(a)
    Path(first['verified_artifact']).write_text(json.dumps(a),encoding='utf-8')
    assert run(False)['status']=='VERIFIED_EXTRACTION_INVALID'


def medication_table():
    rows=[['Ramipril 5mg','1','0','1','0'],['Amlodipin 5 mg pausiert, sonst 1-0-1'],
          ['Doxazosin 2 mg (pausiert), bei erhöhtem Blutdruck 1-2 / Tag'],['Levetiracetam 750 mg (neu)','1','0','1','0'],
          ['Kalioral (vorübergehend bis zur nächsten E-lyt Kontrolle)','1','0','0','0']]
    b=med.bundle(med.section(tables=[PreparedTable(1,1,rows,'cell_text_match')]))
    xs=select_medication_inputs(b)
    raw={'patient_id':'123','medications':[med.payload(text,source_table_index=1)['medications'][0] for text in xs[0].tables[0]['source_rows']]}
    raw['medications'][-1]['source_text']=raw['medications'][-1]['source_text'].replace('Kontrolle)', 'Kontrolle),')
    return b,xs,raw


def test_table_row_is_evidence_authority_without_punctuation_relaxation():
    _,xs,raw=medication_table()
    with pytest.raises(ValueError,match='quotation mismatch'):validate_medications(json.dumps(raw),'123',xs)
    result,rejected,meta=validate_independent_medications(json.dumps(raw),'123',xs)
    assert len(result.medications)==5 and not rejected
    assert result.medications[-1].source_text==xs[0].tables[0]['source_rows'][-1]
    assert meta[-1]['model_source_text']!=result.medications[-1].source_text
    assert meta[-1]['source_row']['source_cells'] and meta[-1]['source_row']['row_fingerprint']


def test_one_bad_table_semantic_record_preserves_four_valid_siblings():
    _,xs,raw=medication_table();raw['medications'][-1]['medication']='Invented medicine dose'
    result,rejected,_=validate_independent_medications(json.dumps(raw),'123',xs)
    assert len(result.medications)==4 and len(rejected)==1


@pytest.mark.parametrize('domain',['family_history','medications'])
def test_wrong_patient_is_global_not_partial(domain):
    if domain=='medications':
        _,xs,raw=medication_table();validator=validate_independent_medications
    else:
        xs=select_family_history_inputs(family.bundle());raw=family.payload();validator=validate_independent_family_history
    raw['patient_id']='wrong'
    with pytest.raises(ValueError,match='Patient provenance'):validator(json.dumps(raw),'123',xs)
