"""Synthetic safety cases and read-only regressions for exactly the seven audited patients."""
import csv
import json
import re
import unicodedata
from pathlib import Path

import pytest

from caa_pdf_extractor.extraction import diagnosis_events_v4 as dx
from caa_pdf_extractor.extraction.assertion_scope import validate_assertion
from caa_pdf_extractor.extraction.medication_inputs import source_rows

AUDIT=Path(__file__).resolve().parents[1]/'data/debug/physician_failure_audit_20261026T145111_042022Z'
PATIENTS=('2110307009','2984303525','2892132324','2834596323','2788388022','2709535720','2620249118')


def bundle(text,heading='Diagnosen',date='2009-09-02'):
    return dict(patient_id='synthetic',documents=[dict(source_document_id=1,document_date=date,
        sections=[dict(section_id='s1',patient_id='synthetic',source_document_id=1,page_start=1,page_end=1,
            heading=heading,text=text,source_text=text,tables=[],candidate_targets=[])])])


def assemble(text,**kwargs):
    return dx.assemble('synthetic',bundle(text,**kwargs),dict(mentions=[],audit_only=[]))


def test_unknown_list_items_are_preserved_and_numbered_before_mapping():
    result=assemble('1. Völlig neue seltene Krankheit\n2. Chronische Niereninsuffizienz G2\n3. Neue zweite Erkrankung')
    recon=result['structured_diagnosis_reconciliation']
    assert recon==dict(source_items=3,mapped=1,non_disease=0,unmapped=2,lost=0)
    assert [r['diagnosis_item_number'] for r in result['unmapped_diagnosis_candidates']]==[1,3]
    assert all(r['review_required'] and r['document_id']==1 and r['page']==1 and r['section']=='s1' for r in result['unmapped_diagnosis_candidates'])


def test_uncertain_and_negated_unknown_diagnoses_remain_reviewable():
    result=assemble('1. Verdacht auf seltenes neues Syndrom\n2. Kein Hinweis auf eine neue Erkrankung')
    assert [r['assertion'] for r in result['unmapped_diagnosis_candidates']]==['UNCERTAIN','NEGATED']
    assert result['structured_diagnosis_reconciliation']['unmapped']==2


@pytest.mark.parametrize('text,state',[
    ('Die Mikroblutugen nicht zum Muster einer Amyloidangiopathiepassend.','NEGATED'),
    ('Zur weiterführenden Abklärung der Emboliequelle im Sinne einer Amyloidangiopathie.','DIFFERENTIAL'),
    ('Bei suspizierter Amyloidangiopathie aufgrund Bewegungsartefakten keine sichere Aussage.','NOT_ASSESSABLE'),
    ('Bezüglich einer Amyloidangiopathie keine suffiziente Aussage getätigt werden kann.','NOT_ASSESSABLE'),
    ('Kein eindeutiger Nachweis einer Amyloidangiopathie.','NEGATED'),
    ('DD: cerebrale Amyloidangiopathie(CAA).','SUSPECTED'),
    ('Zerebrale Amyloidangiopathie gesichert.','ESTABLISHED'),
])
def test_CAA_source_predicates_and_acronyms(text,state):
    result=assemble(text,heading='Zusammenfassung')
    aa=result['diagnostic_assertion_timeline']
    assert aa and all(a['assertion_status']==state for a in aa)
    assert bool(result['canonical_disease_identities'])==(state=='ESTABLISHED')


def test_CAA_nonpositive_exam_does_not_erase_independent_historical_positive():
    b=bundle('Zerebrale Amyloidangiopathie gesichert.',heading='Diagnosen')
    other=bundle('Bezüglich einer Amyloidangiopathie keine suffiziente Aussage.',heading='Zusammenfassung')['documents'][0]
    other['source_document_id']=2;other['sections'][0]['section_id']='s2';b['documents'].append(other)
    result=dx.assemble('synthetic',b,dict(mentions=[],audit_only=[]))
    assert len(result['canonical_disease_identities'])==1
    assert {a['assertion_status'] for a in result['diagnostic_assertion_timeline']}=={'ESTABLISHED','NOT_ASSESSABLE'}


def test_model_cannot_override_negation_and_can_retain_nondiagnostic_state():
    text='Kein eindeutiger Nachweis einer Amyloidangiopathie.';m=re.search('Amyloidangiopathie',text)
    assert validate_assertion(text,*m.span(),model_assertion='ESTABLISHED')['assertion_status']=='NEGATED'
    text='Amyloidangiopathie';assert validate_assertion(text,0,len(text),model_assertion='NOT_ASSESSABLE')['assertion_status']=='NOT_ASSESSABLE'


def test_generic_stroke_stays_generic_without_corroboration():
    event=assemble('Insult 1999')['canonical_clinical_events'][0]
    assert event['event_type']=='STROKE_UNSPECIFIED' and event['historical']


def test_relative_historical_date_requires_reliable_document_date():
    text='Zustand nach links cerebralem ischämischen Infarkt vor 10 Jahren'
    result=assemble(text);event=result['canonical_clinical_events'][0]
    assert (event['event_date'],event['event_date_precision'],event['date_source'],event['side'])==('1999','APPROXIMATE_YEAR','RELATIVE_HISTORICAL_INFERENCE','LEFT')
    assert not assemble(text,date=None)['canonical_clinical_events']


def test_local_numeric_reference_does_not_resolve_to_another_document():
    r=assemble('1. Strukturelle Epilepsie bei Diagnose 3\n3. Höhergradiges Gliom rechts frontal - ED 03/2024')
    m=next(m for m in r['diagnosis_mentions'] if m['canonical_candidate']=='STRUCTURAL_EPILEPSY')
    assert m['attributes']['causal_reference']['canonical_candidates']==['HIGH_GRADE_GLIOMA']
    r=assemble('1. Strukturelle Epilepsie bei Diagnose 3')
    assert 'causal_reference' not in r['diagnosis_mentions'][0]['attributes']


def test_embedded_medication_scope_excludes_allergies_and_history():
    b=bundle('Anamnese\nVorgeschichte\nPrämedikation:\nTelmisartan\nMarcoumar laut Ausweis\nAllergien:\nPenicillin\n',heading='Anamnese')
    assert [r['raw_medication'] for r in source_rows(b)]==['Telmisartan','Marcoumar laut Ausweis']


def test_unmapped_diagnosis_cannot_bypass_promotion_review(tmp_path):
    from caa_pdf_extractor.workbench_store import WorkbenchStore,atomic
    from review_app.display_models import diagnosis_entities,evidence_for
    store=WorkbenchStore(tmp_path/'stable');pid='1234567890'
    atomic(store.root/'validation_cohort.json',dict(patient_ids=[pid]))
    unknown=assemble('1. Seltene noch nicht kartierte Krankheit')['unmapped_diagnosis_candidates']
    entry=store.publish(pid,'diagnoses','4.2+synthetic',{
        'canonical_disease_identities.json':'[]','unmapped_diagnosis_candidates.json':json.dumps(unknown)},
        dict(module_version='4.2',validation_test_results={'hard_gates':{'structured_items_preserved':True}}))
    assert diagnosis_entities(entry)[0]['entity_id']==unknown[0]['candidate_id']
    assert evidence_for(diagnosis_entities(entry)[0],entry)[0]['page']==1
    with pytest.raises(ValueError,match='explicit current physician adjudication'):
        store.promote(pid,'diagnoses',entry['version'],confirmed=True,reviews={})


@pytest.mark.skipif(not AUDIT.exists(),reason='Local seven-patient audit evidence is optional outside this workspace')
def test_all_seven_source_inventory_rows_and_reported_diagnoses():
    index=json.loads((AUDIT/'input_index.json').read_text(encoding='utf8'))
    inventory=list(csv.DictReader((AUDIT/'medication_source_row_audit.csv').open(encoding='utf-8-sig')))
    norm=lambda s:' '.join(unicodedata.normalize('NFKC',s).split()).casefold()
    expected={
        '2110307009':set(),'2984303525':{'CHRONIC_KIDNEY_DISEASE'},
        '2892132324':{'HIGH_GRADE_GLIOMA','STRUCTURAL_EPILEPSY'},
        '2834596323':{'CHRONIC_KIDNEY_DISEASE','RENAL_CELL_CARCINOMA','VESTIBULAR_NEURITIS'},
        '2788388022':{'DEMENTIA','CHRONIC_KIDNEY_DISEASE'},
        '2709535720':{'SYMPTOMATIC_EPILEPSY','BREAST_CANCER','DEMENTIA'},'2620249118':{'DEMENTIA'}}
    for pid in PATIENTS:
        source=Path(index[pid]['source_dir']);b=json.loads((source/'source_bundle.json').read_text(encoding='utf8'))
        h=json.loads((source/'source_mentions.json').read_text(encoding='utf8'))
        result=dx.assemble(pid,b,h);meds=source_rows(b);used=set()
        for row in [r for r in inventory if r['patient_id']==pid]:
            hits=[n for n,x in enumerate(meds) if n not in used and str(x['source_document_id'])==row['document_id']
                and int(x['page'])==int(row['page']) and norm(x['raw_medication']).startswith(norm(row['product_source_text']))]
            assert hits,(pid,row['product_source_text']);used.add(hits[0])
        assert len(used)==len(meds),(pid,'unexplained extra medication rows')
        names={d['canonical_name'] for d in result['canonical_disease_identities']}
        assert expected[pid]<=names
        if pid in ('2788388022','2620249118'):assert 'CEREBRAL_AMYLOID_ANGIOPATHY' not in names
        assert result['structured_diagnosis_reconciliation']['lost']==0
        assert len({e['event_id'] for e in result['canonical_clinical_events']})==len(result['canonical_clinical_events'])
        if pid=='2834596323':
            ckd=next(d for d in result['canonical_disease_identities'] if d['canonical_name']=='CHRONIC_KIDNEY_DISEASE')
            assert any(a.get('stage')=='G2' for a in ckd['source_attributes'])
        if pid=='2892132324':
            tumor=next(d for d in result['canonical_disease_identities'] if d['canonical_name']=='HIGH_GRADE_GLIOMA')
            assert any(a.get('side')=='RIGHT' and a.get('location')=='FRONTAL' and a.get('secondary_hemorrhage') and a.get('diagnosis_date')=='2024-03' for a in tumor['source_attributes'])
