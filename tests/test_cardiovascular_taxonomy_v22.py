import copy
import pytest
from caa_pdf_extractor.extraction.cardiovascular_taxonomy_v22 import findings, canonicalize_cardiovascular, audit_cardiovascular


@pytest.mark.parametrize('label', ['Vorhofflimmern','VHF','Vorhofflimmern paroxysmal',
    'paroxysmales Vorhofflimmern','persistierendes Vorhofflimmern','permanentes Vorhofflimmern',
    'Atrial fibrillation','AF'])
def test_af_required_synonyms(label):
    f=findings(label)[0]
    assert (f['canonical_name'],f['display_name'],f['domain'],f['category'],f['subcategory'])==(
        'ATRIAL_FIBRILLATION','Atrial fibrillation','CARDIOVASCULAR','ARRHYTHMIA','ATRIAL_FIBRILLATION')


@pytest.mark.parametrize('label,af_type', [
    ('paroxysmales Vorhofflimmern','PAROXYSMAL'),('Vorhofflimmern paroxysmal','PAROXYSMAL'),
    ('persistierendes Vorhofflimmern','PERSISTENT'),('long-standing persistent AF','LONG_STANDING_PERSISTENT'),
    ('lang anhaltend persistierendes Vorhofflimmern','LONG_STANDING_PERSISTENT'),
    ('permanentes VHF','PERMANENT'),('Vorhofflimmern','UNSPECIFIED'),
    ('intermittierendes Vorhofflimmern','UNSPECIFIED')])
def test_af_types(label,af_type):
    types=findings(label)[0]['af_types']
    assert (types[0] if types else 'UNSPECIFIED')==af_type


@pytest.mark.parametrize('label,name',[
    ('TRINS','TRICUSPID_REGURGITATION'),('Trikuspidalinsuffizienz','TRICUSPID_REGURGITATION'),
    ('Trikuspidalklappeninsuffizienz','TRICUSPID_REGURGITATION'),
    ('MINS','MITRAL_REGURGITATION'),('Mitralinsuffizienz','MITRAL_REGURGITATION'),
    ('Mitralklappeninsuffizienz','MITRAL_REGURGITATION')])
def test_valve_synonyms(label,name):
    f=findings(label)[0]
    assert f['canonical_name']==f['subcategory']==name
    assert f['domain']=='CARDIOVASCULAR' and f['category']=='STRUCTURAL_HEART_DISEASE'


@pytest.mark.parametrize('label,expected', [
    ('trace TRINS',['TRACE']),('geringgradige MINS',['MILD']),('Mittelgradige TRINS',['MODERATE']),
    ('Schwere Mitralinsuffizienz',['SEVERE']),('MINS Grad II',['MODERATE']),
    ('TRINS III',['SEVERE']),('Leicht- bis mittelgradige MINS',['MILD','MODERATE']),
    ('TRINS',['UNKNOWN'])])
def test_valve_severity_stays_separate(label,expected):
    f=findings(label)[0]
    assert (f['valvular_severities'] or ['UNKNOWN'])==expected
    assert f['canonical_name'] in ('MITRAL_REGURGITATION','TRICUSPID_REGURGITATION')


def test_qualifiers_do_not_cross_valve_conjunctions():
    rows=findings('leichte TRINS und schwere MINS')
    assert rows[0]['valvular_severities']==['MILD']
    assert rows[1]['valvular_severities']==['SEVERE']


@pytest.mark.parametrize('label',['Vorhofflattern','Atrial flutter','AFFE','VHFx',
    'keine Trikuspidalinsuffizienz ableitbar','kein Vorhofflimmern','ohne Mitralinsuffizienz','V.a. Vorhofflimmern'])
def test_no_flutter_substring_negation_or_suspicion_promotion(label):
    assert not findings(label)


def tables_for(labels):
    candidates=[];dx=[];mentions=[]
    for i,label in enumerate(labels,1):
        rid=f'R{i:03}';dxid=f'D{i}'
        candidates.append(dict(patient_id='p',record_id=rid,raw_candidate=label,candidate_type='DIAGNOSIS_DISEASE',
            canonical_diagnosis_id=dxid,accepted_date=f'2020-01-{i:02}',source_ids=[dict(doc=i,section='s',text=label)]))
        dx.append(dict(patient_id='p',id=dxid,canonical_name=label,domain='NEUROLOGIC',category='OTHER_NEUROLOGIC',
            subcategory='OTHER_NEUROLOGIC',member_ids=[rid],etiology='UNKNOWN',status='UNKNOWN',first_supported_date=f'2020-01-{i:02}',evidence_ids=[f'M{i}']))
        mentions.append(dict(patient_id='p',id=f'M{i}',record_ids=[rid],canonical_diagnosis_ids=[dxid],original_mention={'text':label}))
    return dict(diagnosis_candidate_eligibility=candidates,canonical_diagnoses=dx,diagnosis_mentions=mentions,diagnosis_rejected_non_disease_text=[])


def test_merges_synonyms_preserves_qualifiers_dates_mentions_and_flutter():
    tables=tables_for(['paroxysmales Vorhofflimmern','permanentes VHF','Vorhofflattern',
        'Leicht- bis mittelgradige MINS','schwere Mitralinsuffizienz'])
    original=[copy.deepcopy(m['original_mention']) for m in tables['diagnosis_mentions']]
    review,gates=canonicalize_cardiovascular(tables)
    assert all(v==0 for v in gates.values())
    af=next(d for d in tables['canonical_diagnoses'] if d['canonical_name']=='ATRIAL_FIBRILLATION')
    assert af['af_type']=='UNSPECIFIED' and af['af_types']==['PAROXYSMAL','PERMANENT'] and af['af_type_conflict']
    assert af['member_ids']==['R001','R002'] and af['evidence_ids']==['M1','M2']
    assert af['first_supported_date']=='2020-01-01'
    valve=next(d for d in tables['canonical_diagnoses'] if d['canonical_name']=='MITRAL_REGURGITATION')
    assert valve['valvular_severity']=='UNKNOWN' and set(valve['valvular_severities'])=={'MILD','MODERATE','SEVERE'}
    assert valve['original_mention_count']==2
    assert [m['original_mention'] for m in tables['diagnosis_mentions']]==original
    assert len(review)==4
    assert any(d['canonical_name']=='Vorhofflattern' for d in tables['canonical_diagnoses'])


def test_secondary_af_keeps_primary_heart_failure_and_separates_old_flutter_group():
    tables=tables_for(['Vorhofflattern','HFpEF bei permanentem VHF'])
    tables['canonical_diagnoses'][0]['member_ids']=['R001','R002']
    tables['canonical_diagnoses']=tables['canonical_diagnoses'][:1]
    tables['diagnosis_candidate_eligibility'][1]['canonical_diagnosis_id']='D1'
    _,gates=canonicalize_cardiovascular(tables)
    assert all(v==0 for v in gates.values())
    af=next(d for d in tables['canonical_diagnoses'] if d['canonical_name']=='ATRIAL_FIBRILLATION')
    assert af['member_ids']==['R002'] and af['af_type']=='PERMANENT'
    assert tables['diagnosis_candidate_eligibility'][1]['canonical_diagnosis_id']=='D1'
    assert len(tables['diagnosis_candidate_eligibility'][1]['canonical_diagnosis_ids'])==2
    assert len(tables['diagnosis_mentions'][0]['canonical_diagnosis_ids'])==1


def test_named_diagnosis_misclassified_as_heading_is_auditable():
    tables=tables_for(['Vorhofflimmern (ED: Juni 2019)'])
    row=tables['diagnosis_candidate_eligibility'][0]
    row.update(candidate_type='HEADING_OR_FRAGMENT',canonical_diagnosis_id=None,accepted_date=None)
    tables['canonical_diagnoses']=[];tables['diagnosis_rejected_non_disease_text']=[row]
    _,gates=canonicalize_cardiovascular(tables)
    assert all(v==0 for v in gates.values())
    assert row['original_candidate_type']=='HEADING_OR_FRAGMENT'
    assert tables['canonical_diagnoses'][0]['first_supported_date'] is None
    assert not tables['diagnosis_rejected_non_disease_text']


def test_type_present_only_in_source_evidence_is_retained():
    tables=tables_for(['Vorhofflimmern'])
    tables['diagnosis_candidate_eligibility'][0]['source_ids'][0]['text']='Persistierendes Vorhofflimmern'
    _,gates=canonicalize_cardiovascular(tables)
    assert all(v==0 for v in gates.values())
    d=tables['canonical_diagnoses'][0]
    assert d['af_type']=='PERSISTENT'
    assert d['qualifier_observations'][0]['source_qualifier_observations'][0]['doc']==1


def test_hard_gates_detect_bad_export():
    tables=tables_for(['Vorhofflimmern','TRINS','MINS'])
    gates=audit_cardiovascular(tables)
    assert gates['vorhofflimmern_not_canonicalized_to_AF']==1
    assert gates['TRINS_as_neurologic']==gates['MINS_as_neurologic']==1
    assert gates['TRINS_not_structural_heart_disease']==gates['MINS_not_structural_heart_disease']==1
