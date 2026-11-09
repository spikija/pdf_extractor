import copy
import pytest
from caa_pdf_extractor.extraction.eeg_sources import discover,date_from
from caa_pdf_extractor.extraction.eeg_pipeline import assemble
from caa_pdf_extractor.extraction.eeg_extraction_v1 import extract
from caa_pdf_extractor.extraction.eeg_schema import defaults,validate_value
from caa_pdf_extractor.workbench_eeg import reviewed_exam,validate_edits,EEGWorkbenchStore
from review_app.review_repository import ReviewRepository


def bundle(text):
    return dict(patient_id='123',documents=[dict(source_document_id=10,filename='a.pdf',sections=[dict(section_id='p1',page_start=1,page_end=1,text=text)])])


def result(text):return assemble(bundle('EEG Befund (01.02.2020)\n'+text))


@pytest.mark.parametrize('text,field,value',[
 ('Keine epilepsietypischen Potentiale.','epileptiform_discharge_present',False),
 ('Kein sicherer Hinweis auf epilepsietypische Potentiale.','epileptiform_discharge_present',None),
 ('Ein Anfallsmuster zeigt sich nicht.','seizure_recorded',False),
 ('Ein Status epilepticusMuster ist nicht abgrenzbar.','electrographic_status_epilepticus',False),
 ('Zeichen erhöhter cerebraler Erregungsbereitschaft kamen nicht zur Darstellung.','epileptiform_discharge_present',False),
 ('Im interiktalen EEG zeigen sich sharp-waves links temporal.','seizure_recorded',None),
 ('Im iktalen EEG zeigen sich rhythmische Delta-Aktivität und Evolution.','seizure_recorded',True),
 ('Der Befund wird als non-konvulsiver Status epilepticus gewertet.','electrographic_status_epilepticus',True),
 ('EEG aufgrund massiver Artefakte nicht beurteilbar.','assessability','NOT_ASSESSABLE'),
 ('Soweit bei Muskelartefakten beurteilbar mäßig abnormes EEG.','assessability','UNKNOWN'),
 ('Epileptiforme Potenziale links temporal.','electrographic_status_epilepticus',None),
 ('Epileptiforme Potenziale links temporal.','seizure_recorded',None),
 ('Keine klinischen Zeichen eines nicht-konvulsiven Status epilepticus.','electrographic_status_epilepticus',None),
 ('Normales EEG.','epileptiform_discharge_present',None),
 ('Indikation: V.a. Epilepsie und Status epilepticus.\nBefund: Normales EEG.','epileptiform_discharge_present',None),
 ('Indikation: V.a. Epilepsie und Status epilepticus.\nBefund: Normales EEG.','electrographic_status_epilepticus',None),
 ('Epileptiform: [ ] ja [ ] nein\nAbnormes EEG.','epileptiform_discharge_present',None),
 ('Epileptiform: [x] nein [ ] ja\nAbnormes EEG.','epileptiform_discharge_present',False),
 ('Epileptiform: [ ] nein [x] ja\nAbnormes EEG.','epileptiform_discharge_present',True),
])
def test_assertion_gates(text,field,value):assert result(text)['eeg_exams'][0][field]==value


def test_coexisting_fields_and_provenance():
    r=result('Abnormes EEG. Fokale Verlangsamung links temporal. Generalisierte Verlangsamung. Epileptiforme Potenziale links temporal.')
    e=r['eeg_exams'][0]
    assert e['focal_slowing_present'] is True and e['generalized_slowing_present'] is True and e['epileptiform_discharge_present'] is True
    assert e['eeg_tfne_timing']=='UNKNOWN'
    assert all(f['document_id']==10 and f['page']==1 and f['section_id']=='p1' and f['evidence_id'] for f in r['eeg_findings'])
    ictal=result('Im iktalen EEG begannen beide Ereignisse links temporal mit repetitiven Spikes und Evolution.')['eeg_exams'][0]
    assert ictal['seizure_type_eeg']=='ELECTROGRAPHIC' and ictal['eeg_primary_finding']=='ELECTROGRAPHIC_SEIZURE'


@pytest.mark.parametrize('header,start,precision',[
 ('VIDEO-EEG (29.09. - 04.10.2024)','2024-09-29','DAY'),
 ('VIDEO-EEG (17. - 21.05.2021)','2021-05-17','DAY'),
 ('EEG 2001:','2001','YEAR'),
])
def test_date_precision(header,start,precision):
    assert date_from(header)==(start,precision)


def test_planned_is_not_performed():
    r,d=discover(bundle('EEG empfohlen (01.02.2020)\nKeine epileptiformen Potenziale erwartet.'))
    assert not r and d[0]['screening_status']=='EEG_PLANNED'


def test_boundaries_and_copies():
    b=bundle('EEG Befund (01.02.2020)\nNormales EEG.\nLaborbefund:\nStatus epilepticus.')
    b['documents'].append(dict(b['documents'][0],source_document_id=11))
    r=assemble(b)
    assert len(r['eeg_exams'])==1
    assert len(r['eeg_exams'][0]['source_occurrence_ids'])==2
    assert 'Laborbefund' not in r['eeg_exams'][0]['eeg_report_text']


def test_review_overlay_and_staleness(tmp_path):
    r=result('Fokale Verlangsamung links temporal.');e=r['eeg_exams'][0];f=next(f for f in r['eeg_findings'] if f['field']=='focal_slowing_present')
    repo=ReviewRepository(tmp_path)
    repo.save('123','eeg',f['finding_id'],'REJECT',f,{},'1.0','abc')
    records=repo.load('123','eeg')
    assert reviewed_exam(e,[f],records,'abc')['focal_slowing_present'] is None
    assert reviewed_exam(e,[f],records,'changed')['focal_slowing_present'] is True
    repo.save('123','eeg',e['eeg_exam_id'],'EDIT',e,{'focal_slowing_present':False},'1.0','abc')
    assert reviewed_exam(e,[f],repo.load('123','eeg'),'abc')['focal_slowing_present'] is False
    assert e['focal_slowing_present'] is True
    repo.save('123','eeg',e['eeg_exam_id'],'REJECT',e,{},'1.0','abc')
    repo.save('123','eeg',f['finding_id'],'ACCEPT',f,{},'1.0','abc')
    assert reviewed_exam(e,[f],repo.load('123','eeg'),'abc')['focal_slowing_present'] is True
    with pytest.raises(ValueError):validate_edits(e,{'invented':True})
    with pytest.raises(ValueError):validate_value('seizure_recorded','false')


def test_model_cannot_override_negative_or_indication():
    from caa_pdf_extractor.extraction.eeg_semantic import apply_proposals
    rs,_=discover(bundle('EEG Befund (01.02.2020)\nKeine epileptiformen Potenziale.'))
    report=rs[0];values,_=extract(report)
    response=dict(clauses=[dict(id='C1',text=report['eeg_report_text'],start=0)],proposals=[dict(field='epileptiform_discharge_present',value=True,assertion='ESTABLISHED',evidence_id='C1')])
    v,fs=apply_proposals(report,values,response)
    assert v['epileptiform_discharge_present'] is False
    assert all(f['value'] is False for f in fs)


def test_validation_promotion_disabled(tmp_path):
    with pytest.raises(ValueError,match='promotion is disabled'):EEGWorkbenchStore(tmp_path).promote('123','eeg','1.0',confirmed=True)
