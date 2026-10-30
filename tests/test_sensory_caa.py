from dataclasses import replace
from copy import deepcopy
import pytest
from caa_pdf_extractor.extraction import admission_sensory as s,caa_diagnostic as c
from caa_pdf_extractor.extraction.encounter_granite_v2 import Snippet
from caa_pdf_extractor.preparation.models import PreparedDocument,PreparedSection


def sensory(present=True):
    return dict(sensory_symptom_present=present,sensory_polarity='NEGATIVE',sensory_type='NUMBNESS',
      sensory_laterality='LEFT',sensory_distribution='MOUTH',sensory_spreading=None,sensory_transient=True,
      sensory_duration_minutes=2,sensory_stereotyped_recurrent=True,positive_to_negative_evolution=None,
      sensory_duration_min_minutes=None,sensory_duration_max_minutes=None,
      complete_resolution=True,temporal_ownership='CURRENT_PRESENTING_EPISODE',evidence_ids=['A1-001'],
      presenting_event_evidence_ids=['A1-001'],phenomenology_evidence_ids=['A1-001'],confidence='HIGH')


def reason():
    return dict(primary_reason='SENSORY_SYMPTOMS',flags=['SENSORY_SYMPTOMS'],evidence_ids=['A1-001'],
      confidence='HIGH',index_presentation='SENSORY_SYMPTOMS',current_encounter_purpose='ACUTE_TREATMENT',
      index_evidence_ids=['A1-001'],purpose_evidence_ids=['A1-001'])


def snippet():
    return Snippet('A1-001','123',1,'s','Anamnese',None,1,1,0,20,'Aktuelle Taubheit links.')


def test_sensory_is_independent_and_components_do_not_invalidate_siblings():
    raw={'admission_reason':reason(),'sensory':sensory()}
    raw['admission_reason']['flags']+=['FOCAL_NEUROLOGICAL_DEFICIT','EPILEPTIC_EVENT']
    assert s.validate(raw,[snippet()],'123',1)['admission_reason']['value']==raw['admission_reason']
    raw['sensory']['evidence_ids']=['missing']
    out=s.validate(raw,[snippet()],'123',1)
    assert out['admission_reason']['status']=='VALID' and out['sensory']['status']=='TECHNICAL_FAILURE'
    raw={'admission_reason':{'bad':True},'sensory':sensory()}
    out=s.validate(raw,[snippet()],'123',1)
    assert out['sensory']['status']=='VALID' and out['admission_reason']['status']=='TECHNICAL_FAILURE'


@pytest.mark.parametrize('value',[True,-1,float('nan'),float('inf')])
def test_duration_invalid_numeric_shapes_rejected(value):
    row=sensory();row['sensory_duration_minutes']=value
    assert s.validate_component(row,s.Sensory,[snippet()],'123',1)['status']=='TECHNICAL_FAILURE'


@pytest.mark.parametrize('owner',['CHRONIC','HISTORICAL','UNRELATED_EXAM','UNKNOWN'])
def test_positive_sensory_requires_current_ownership(owner):
    row=sensory();row['temporal_ownership']=owner
    assert s.validate_component(row,s.Sensory,[snippet()],'123',1)['status']=='TECHNICAL_FAILURE'


def test_absence_not_forced_to_false_and_model_semantics_not_overridden():
    row=sensory(None);row['temporal_ownership']='CHRONIC'
    result=s.validate_component(row,s.Sensory,[snippet()],'123',1)
    assert result['value']==row and result['value']['sensory_symptom_present'] is None


def test_sensory_source_stops_embedded_peer_imaging_report():
    value='Anamnese\nAktuell Taubheit am Mund.\nKernspintomographie: Schädel (01.01.2020)\nAlte Sensibilitätsstörung.'
    sec=PreparedSection('s','123',1,'Anamnese',1,1,value,value)
    doc=PreparedDocument(1,'x.pdf','x.pdf','hash','discharge_letter',None,[sec])
    snippets,omissions=s.prepare_snippets(doc,'123')
    assert snippets and all('Kernspintomographie' not in x.text and 'Alte Sensibilitätsstörung' not in x.text for x in snippets)
    assert not omissions


@pytest.mark.parametrize('statement,kind,status',[
 ('V.a. Amyloidangiopathie.','SUSPICION','SUSPECTED'),
 ('Verdacht auf CAA.','SUSPICION','SUSPECTED'),
 ('DD Amyloidangiopathie.','SUSPICION','SUSPECTED'),
 ('CAA möglich.','SUSPICION','SUSPECTED'),
 ('possible CAA nach Boston Criteria.','CRITERIA_CLASSIFICATION','POSSIBLE'),
 ('probable CAA nach Boston Kriterien 2.0.','CRITERIA_CLASSIFICATION','PROBABLE'),
 ('Boston 2.0: probable CAA.','CRITERIA_CLASSIFICATION','PROBABLE'),
 ('Es wurde die Diagnose einer zerebralen Amyloidangiopathie gestellt.','CLINICAL_DIAGNOSIS','DIAGNOSED_UNSPECIFIED'),
 ('CAA wurde histologisch gesichert.','PATHOLOGY_CONFIRMATION','DEFINITE'),
 ('probable CAA with supporting pathology nach Boston 1.5.','CRITERIA_CLASSIFICATION','PROBABLE_WITH_SUPPORTING_PATHOLOGY'),
])
def test_explicit_diagnostic_classes_remain_distinct(statement,kind,status):
    out=c.deterministic(statement)
    assert out['event_type']==kind and out['caa_status']==status


@pytest.mark.parametrize('statement',[
 'Multiple lobäre Mikroblutungen, kompatibel mit CAA.',
 'Multiple lobar microbleeds compatible with CAA.',
 'Im MRT 2018 bestanden bereits multiple lobäre Mikroblutungen, vereinbar mit Amyloidangiopathie.',
])
def test_imaging_compatibility_does_not_become_diagnosis(statement):
    assert c.deterministic(statement)['disposition']=='IMAGING_COMPATIBILITY_ONLY'
    assert c.owned_dates(statement)==[]


@pytest.mark.parametrize('statement',[
 'Falls weitere Läsionen bestehen, probable CAA nach Boston 2.0.',
 'Je nach Bewertung possible oder probable CAA nach Boston.',
 'Eine Autopsie zur Abklärung einer CAA wurde abgelehnt.',
])
def test_uncertain_multiple_stage_and_generic_pathology_not_promoted(statement):
    assert c.deterministic(statement)['disposition']=='SEMANTIC_REVIEW'


@pytest.mark.parametrize('statement,precision,year,month,day',[
 ('Am 15.03.2018 wurde die Diagnose einer CAA gestellt.','DAY',2018,3,'2018-03-15'),
 ('Bei der Patientin wurde 2018 die Diagnose einer CAA gestellt.','YEAR',2018,None,None),
 ('Im März 2018 wurde die Diagnose einer CAA gestellt.','MONTH',2018,3,None),
 ('Die CAA wurde am 15.03.2018 diagnostiziert.','DAY',2018,3,'2018-03-15'),
])
def test_direct_diagnosis_date_ownership(statement,precision,year,month,day):
    offer,=c.owned_dates(statement)
    assert (offer['date_precision'],offer['event_year'],offer['event_month'],offer['event_date'])==(precision,year,month,day)


@pytest.mark.parametrize('statement',[
 'Im MRT 2018 zeigten sich Mikroblutungen bei CAA.',
 'ICH am 15.03.2018 bei CAA.',
 'Im MRT am 15.03.2018 zeigten sich Mikroblutungen, danach wurde die Diagnose einer CAA gestellt.',
 'Entlassung am 15.03.2018. Diagnose einer CAA gestellt.',
 '2018 wurde die Diagnose einer Epilepsie gestellt, die CAA ist bekannt.',
 'Pathologiepräparat 15.03.2018 bei CAA.',
])
def test_no_borrowed_dates(statement):
    assert c.owned_dates(statement)==[]


def test_current_diagnostic_encounter_requires_explicit_relationship():
    offer,=c.owned_dates('Bei dieser Vorstellung wurde heute die Diagnose einer CAA gestellt.',context_date='2018-03-15')
    assert offer['date_basis']=='CURRENT_DIAGNOSTIC_ENCOUNTER'
    assert c.owned_dates('CAA bekannt.',context_date='2018-03-15')==[]
    assert c.owned_dates('Es wurde die Diagnose einer CAA gestellt.',context_date='2018-03-15')==[]


def test_boston_version_never_inferred_from_event_year():
    assert c.criteria_system('probable CAA nach Boston 2018')=='UNSPECIFIED'
    assert c.criteria_system('probable CAA nach Boston 1.5')=='BOSTON_1_5'


def test_suspicion_and_diagnosis_in_separate_clauses_remain_two_candidates():
    value='2018 bestand Verdacht auf CAA; 2020 wurde die Diagnose einer CAA gestellt.'
    sec=PreparedSection('s','123',1,'Anamnese',1,1,value,value)
    doc=PreparedDocument(1,'x.pdf','x.pdf','hash','discharge_letter',None,[sec])
    out=c.census_document(doc,'123')
    assert len(out['candidates'])==2
    assert [r['deterministic']['event_type'] for r in out['candidates']]==['SUSPICION','CLINICAL_DIAGNOSIS']


def test_no_case_date_derived_from_suspicion_only():
    e={'event_type':'SUSPICION','caa_status':'SUSPECTED','date_precision':'DAY','event_date':'2018-03-15','canonical_event_key':'x'}
    out=c.timeline([e],None)
    assert out['first_caa_suspicion_date']=='2018-03-15' and out['first_caa_diagnosis_date'] is None
    assert out['case_update_authorized'] is False


def test_generic_pathology_of_other_disease_does_not_prove_CAA():
    assert c.deterministic('Histologisch gesicherte Alzheimer-Erkrankung, daneben CAA.')['disposition']=='SEMANTIC_REVIEW'


def test_CAA_model_cannot_invent_date_or_reference_id():
    value='Es wurde die Diagnose einer CAA gestellt.'
    sec=PreparedSection('s','123',1,'Anamnese',1,1,value,value)
    doc=PreparedDocument(1,'x.pdf','x.pdf','hash','discharge_letter',None,[sec])
    row,=c.census_document(doc,'123')['candidates']
    decision={'snippet_id':row['snippet_id'],'disposition':'ASSERTED_EVENT','event_type':'CLINICAL_DIAGNOSIS',
        'caa_status':'DIAGNOSED_UNSPECIFIED','criteria_system':'UNSPECIFIED','temporal_ownership':'CURRENT_DIAGNOSTIC_ENCOUNTER',
        'date_evidence_id':'invented','evidence_ids':[row['snippet_id']]}
    result=c.validate_semantic({'decisions':[decision]},[row],'123',1)
    assert result['decisions']==[] and result['errors'][0]['code']=='INVALID_DATE_EVIDENCE_ID'
    decision['date_evidence_id']=None;decision['evidence_ids']=['missing']
    result=c.validate_semantic({'decisions':[decision]},[row],'123',1)
    assert result['decisions']==[] and result['errors'][0]['code']=='MISSING_SNIPPET_ID'


def test_historical_undated_identity_is_held_not_new_canonical_diagnosis():
    row={'patient_id':'123','source_document_id':1,'disposition':'ASSERTED_EVENT','event_type':'CLINICAL_DIAGNOSIS',
        'caa_status':'DIAGNOSED_UNSPECIFIED','criteria_system':'UNSPECIFIED','date_precision':'UNKNOWN','temporal_ownership':'HISTORICAL'}
    out=c.canonicalize([row])
    assert out['canonical_events']==[] and len(out['identity_held_assertions'])==1


def test_duration_range_is_not_projected_to_exact_endpoint():
    row=sensory();row.update(sensory_duration_minutes=None,sensory_duration_min_minutes=15,sensory_duration_max_minutes=20)
    assert s.Sensory.model_validate(row).sensory_duration_minutes is None
    row['sensory_duration_minutes']=15
    with pytest.raises(ValueError):s.Sensory.model_validate(row)


def test_birth_date_not_diagnostic_date():
    assert c.owned_dates('geb. 15.03.1950 Diagnose einer CAA gestellt.')==[]


def test_current_encounter_date_option_requires_explicit_semantic_linkage():
    value='Es wurde die Verdachtsdiagnose einer Amyloidangiopathie gestellt.'
    sec=PreparedSection('s','123',1,'Zusammenfassung des Aufenthalts',1,1,value,value)
    doc=PreparedDocument(1,'x.pdf','x.pdf','hash','discharge_letter',None,[sec])
    candidate,=c.census_document(doc,'123')['candidates']
    c.offer_current_encounter(candidate,{'admission_date':'2018-03-15','encounter_evidence':{'source_document_id':1}})
    offer,=candidate['date_offers']
    assert c.resolve_deterministic(candidate) is None
    decision={'snippet_id':candidate['snippet_id'],'temporal_ownership':'HISTORICAL','current_encounter_linked':False,
        'date_evidence_id':offer['date_id'],'evidence_ids':[candidate['snippet_id']]}
    out=c.validate_temporal({'decisions':[decision]},[candidate],'123',1)
    assert out['decisions'][0]['temporal_validation_status']=='TECHNICAL_FAILURE' and out['errors'][0]['code']=='UNLINKED_CURRENT_DIAGNOSTIC_DATE'
    assert out['decisions'][0]['event_date'] is None and out['decisions'][0]['event_type']=='SUSPICION'
    decision.update(temporal_ownership='CURRENT_DIAGNOSTIC_ENCOUNTER',current_encounter_linked=True)
    result,=c.validate_temporal({'decisions':[decision]},[candidate],'123',1)['decisions']
    assert result['event_type']=='SUSPICION' and result['caa_status']=='SUSPECTED'
    assert result['event_date']=='2018-03-15' and result['date_basis']=='CURRENT_DIAGNOSTIC_ENCOUNTER'


def test_known_diagnosis_without_new_act_cannot_receive_encounter_date_option():
    candidate={'date_offers':[],'deterministic':{'disposition':'REFERENCE_ONLY'},'evidence_text':'CAA bekannt.'}
    c.offer_current_encounter(candidate,{'admission_date':'2018-03-15','encounter_evidence':{}})
    assert candidate['date_offers']==[]


def test_adjectival_diagnosed_CAA_is_not_new_diagnostic_act():
    out=c.deterministic('Bei vorbestehender Demenz und diagnostizierter CAA wird eine Therapiezieländerung besprochen.')
    assert out['disposition']=='REFERENCE_ONLY'


def test_history_section_cannot_offer_current_date_without_explicit_link():
    value='Bei der Evaluierung wurde die Verdachtsdiagnose einer CAA gestellt.'
    candidate={'date_offers':[],'deterministic':c.deterministic(value),'evidence_text':value,'heading':'Anamnese','context_text':'Seit Jahren wiederholte Abklärung in einem anderen Krankenhaus.'}
    c.offer_current_encounter(candidate,{'admission_date':'2020-11-16','encounter_evidence':{}})
    assert candidate['date_offers']==[]


def test_retrospective_criteria_and_contemporaneous_diagnosis_are_not_collapsed():
    base={'patient_id':'123','source_document_id':1,'disposition':'ASSERTED_EVENT','event_type':'CRITERIA_CLASSIFICATION',
        'caa_status':'PROBABLE','criteria_system':'BOSTON_2_0','event_date':'2020-01-02','date_precision':'DAY'}
    out=c.canonicalize([base,{**base,'retrospective_criteria_fulfillment':True}])
    assert len(out['canonical_events'])==2
    result=c.timeline([out['canonical_events'][1]],None)
    assert result['first_caa_diagnosis_date'] is None
    assert result['first_probable_caa_date']=='2020-01-02'


def test_undated_identity_holds_prevent_claiming_chronological_first():
    event={'event_type':'SUSPICION','caa_status':'SUSPECTED','date_precision':'DAY','event_date':'2020-01-02','canonical_event_key':'x'}
    held={**event,'date_precision':'UNKNOWN','event_date':None,'snippet_id':'historical'}
    result=c.timeline([event],None,uncertain_events=[held])
    assert result['first_caa_suspicion_date']=='2020-01-02'
    assert result['first_caa_suspicion_date_review']['chronological_first_proven'] is False


def test_occurrence_and_context_each_have_exact_source_offsets():
    value='Andere Diagnose. Verdacht auf Amyloidangiopathie. Keine Autopsie.'
    sec=PreparedSection('s','123',1,'Anamnese',1,1,value,value)
    doc=PreparedDocument(1,'x.pdf','x.pdf','hash','discharge_letter',None,[sec])
    for row in c.census_document(doc,'123')['occurrences']:
        assert value[row['source_start']:row['source_end']]==row['evidence_text']==row['term']
        assert value[row['context_start']:row['context_end']]==row['context_text']
