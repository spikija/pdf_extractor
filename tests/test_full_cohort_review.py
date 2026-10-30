from pathlib import Path
import pymupdf
from caa_pdf_extractor.full_cohort_sources import discover
from caa_pdf_extractor.full_cohort_report import patient_flags,cohort_statistics,render_html,tab
from caa_pdf_extractor.extraction.cardiovascular_taxonomy_v22 import findings
from caa_pdf_extractor.extraction.neurologic_taxonomy import classify_neurologic
from caa_pdf_extractor.extraction.diagnosis_caa_v21_addendum import csf_census
from caa_pdf_extractor.full_cohort_laboratory import reference_flag,parse_reference


def test_dynamic_discovery_includes_new_folders_and_uppercase_pdfs(tmp_path):
    for pid in ['old','new']:
        folder=tmp_path/pid;folder.mkdir()
        pdf=pymupdf.open();pdf.new_page().insert_text((40,40),'Source');pdf.save(folder/'source.PDF');pdf.close()
    (tmp_path/'empty').mkdir()
    manifest,files=discover(tmp_path)
    assert {r['patient_id'] for r in manifest}=={'old','new','empty'}
    assert len(files)==2 and sum(r['total_pages'] for r in manifest)==2
    assert next(r for r in manifest if r['patient_id']=='empty')['error_status']==['NO_PDFS']


def flags(diseases=(),events=(),assertions=(),boston=(),edinburgh=()):
    return patient_flags(list(diseases),list(events),list(assertions),list(boston),list(edinburgh))


def test_patient_counts_do_not_count_repeated_diagnoses_or_seizure_as_epilepsy():
    chronic=flags(diseases=[dict(subcategory='SYMPTOMATIC_EPILEPSY')]*4,
        events=[dict(event_type='CONVULSIVE_STATUS_EPILEPTICUS')]*3,
        assertions=[dict(source_caa_status='CLINICIAN_DIAGNOSED_CAA')]*6)
    isolated=flags(events=[dict(event_type='EPILEPTIC_SEIZURE')],assertions=[dict(source_caa_status='SUSPECTED_CAA')])
    rows=[dict(patient_id='1',sex='woman',**chronic),dict(patient_id='2',sex='man',**isolated)]
    metrics={r['metric']:r for r in cohort_statistics(rows)}
    assert metrics['ANY_CAA_POSITIVE']['n']==1 and metrics['ANY_CAA_POSITIVE']['denominator']==2
    assert metrics['CHRONIC_EPILEPSY']['n']==1 and metrics['ANY_SEIZURE_EVENT']['n']==2
    assert metrics['SEIZURE_ONLY']['n']==1 and metrics['EPILEPSY_AND_STATUS']['n']==1
    assert not isolated['epilepsy_yes_no']


def test_caa_source_computed_and_suspicion_streams_are_distinct():
    suspected=flags(assertions=[dict(source_caa_status='SUSPECTED_CAA')])
    assert suspected['research_caa_status']=='CAA_SUSPECTED_ONLY' and not suspected['caa_positive_yes_no']
    formal=flags(assertions=[dict(source_caa_status='FORMAL_POSSIBLE_CAA')])
    assert formal['caa_positive_yes_no'] and formal['research_caa_status']=='CAA_POSSIBLE'
    computed=flags(boston=[dict(classification='PROBABLE_CAA')])
    assert computed['caa_positive_yes_no'] and not computed['clinician_diagnosed']
    assert flags()['research_caa_status']=='CAA_INDETERMINATE'


def test_edinburgh_positive_requires_confirmed_absence_of_adequate_mri():
    for available in [True,None]:
        assert not flags(edinburgh=[dict(adequate_mri_available=available,simplified_edinburgh='HIGH_PROBABILITY')])['edinburgh_positive']
    assert flags(edinburgh=[dict(adequate_mri_available=False,simplified_edinburgh='HIGH_PROBABILITY')])['edinburgh_positive']


def test_pending_amyloid_is_not_a_missed_numeric_result_and_outpatient_copy_does_not_gain_admission():
    snapshot=dict(patient_ids=['p'],tables=dict(source_documents=[dict(id=1,document_json=dict(document=dict(patient_id='p'),
        pages=[dict(page_number=1,text='Liquor\nß-Amyloid 567-1027 pg/ml folgt\nBeta-Amyloid: 364 pg/ml',tables=[])]))]))
    census,measurements,gates,audit=csf_census(snapshot,[],{},allow_unavailable_admission=True)
    assert all(v==0 for v in gates.values())
    assert len(measurements)==1 and measurements[0]['value']=='364' and measurements[0]['date'] is None
    assert census[0]['reason_if_not_extracted']=='SOURCE_RESULT_PENDING_NO_NUMERIC_MEASUREMENT'
    assert audit['unavailable_admission_dates_preserved_null']==1


def test_glued_mins_and_primary_ich_and_sleep_apnea_regressions():
    assert findings('Leicht- bis mittelgradigeMINS')[0]['canonical_name']=='MITRAL_REGURGITATION'
    assert findings('MitralklappeninsuffizienzI°')[0]['valvular_severities']==['MILD']
    assert findings('MitralklappeninsuffizienzGrad II')[0]['valvular_severities']==['MODERATE']
    row=classify_neurologic('Intracerebrale Blutung links frontal',domain='OTHER',legacy_subcategory='INTRACEREBRAL_HEMORRHAGE',layer='EVENT')
    assert (row['domain'],row['category'],row['subcategory'])==('NEUROLOGIC','CEREBROVASCULAR','INTRACEREBRAL_HEMORRHAGE')
    assert classify_neurologic('Vorwiegend zentrales Schlafapnoesyndrom')['subcategory']=='CENTRAL_SLEEP_APNEA'
    assert classify_neurologic('Gemischtes Schlafapnoesyndrom')['subcategory']=='MIXED_SLEEP_APNEA'
    assert classify_neurologic('St.p. Akutes Nierenversagen',domain='OTHER',legacy_subcategory='STATUS_EPILEPTICUS',layer='EVENT')['domain']=='RENAL_UROLOGIC'
    assert classify_neurologic('Pseudoaneurysma der Arteria femoralis',legacy_subcategory='ICH')['domain']=='CARDIOVASCULAR'
    assert classify_neurologic('Cerebrales Multiinfarktgeschehen',legacy_subcategory='ISCHEMIC_STROKE')['subcategory']=='OTHER_CEREBROVASCULAR'
    assert classify_neurologic('Basilaristhrombose',legacy_subcategory='TIA')['subcategory']=='OTHER_CEREBROVASCULAR'
    transformed=classify_neurologic('Intracerebrale Massenblutung rechts bei sekundär hämmorrhagischem Infarkt',layer='EVENT')
    assert transformed['subcategory']=='ISCHEMIC_STROKE' and transformed['hemorrhagic_transformation']


def test_portrait_renderer_repeats_headers_keeps_unicode_and_rows(tmp_path):
    path=tmp_path/'review.pdf'
    rows=[[str(i),'β / ß / ä ö ü. '+'Source-supported concise finding. '*6,'doc1 / p2 / sec3'] for i in range(25)]
    pages=render_html('<h1>Patient TEST</h1>'+tab(['Date','Finding','Source'],rows),path)
    assert pages>1
    with pymupdf.open(path) as pdf:
        assert len(pdf)==pages
        for page in pdf:
            assert page.rect.width<page.rect.height
            assert all(word in page.get_text() for word in ['Date','Finding','Source'])
            assert all(w[0]>=43 and w[2]<=page.rect.width-43 for w in page.get_text('words'))
        text=''.join(p.get_text() for p in pdf)
        assert all(c in text for c in 'βßäöü')


def test_reference_direction_and_first_value_flags():
    assert parse_reference('0,0-0,5')==('0.0','0.5')
    assert parse_reference('>600')==('600',None)
    assert reference_flag('425','600',None,'>600')=='LOW'
    assert reference_flag('0.2',None,'0.5','<0.5')=='NORMAL'
    assert reference_flag('0.5',None,'0.5','<0.5')=='HIGH'
    assert reference_flag('61','0','60','0-60')=='HIGH'
    assert reference_flag('42',None,None)=='UNKNOWN'
    from caa_pdf_extractor.extraction.research_metadata_v2 import medication_proposal
    assert medication_proposal('Pregabalin (Lyrica) 50 mg','discharge medication','2020-01-01')['category']=='ANTISEIZURE'
    assert not flags()['epilepsy_yes_no']


def test_incremental_cognitive_review_cannot_form_chained_anchors():
    from caa_pdf_extractor.full_cohort_cognitive import review
    import json
    sections={(1,'s1'):'Neuropsychologische Testung: MMSE 24/30.',
        (1,'s2'):'Derselbe MMSE-Befund 24/30 wurde wiederholt genannt.',(2,'s3'):'MMSE 24/30.'}
    mentions=[]
    for i,((doc,sec),text) in enumerate(sections.items(),1):
        score=text.index('24')
        mentions.append(dict(mention_id='M'+str(i),patient_id='SYN',case_id=None,source_document_id=doc,section_id=sec,
            source_text=text,source_start=0,source_end=len(text),score_start=score,score_end=score+2,
            test_type='MMSE',score=24,max_score=30,assessment_date=None,assessment_year=None,assessment_month=None,
            date_precision='UNKNOWN',date_basis='UNKNOWN',date_source_span=None,context='CURRENT' if i==1 else 'UNKNOWN'))
    class FakeLocal:
        def run(self,key,prompt,schema,validator):
            data=json.loads(prompt.split('\n')[-1]);mid=data['mention']['mention_id']
            if mid=='M1':decision=dict(anchor_mention_id='M1',evidence_role='CURRENT_PERFORMED',basis='CURRENT_TEST_CONTEXT',reason='Explicit current testing')
            elif mid=='M2':
                assert data['compatible_accepted_roots'][0]['accepted_root_decision']['anchor_mention_id']=='M1'
                decision=dict(anchor_mention_id='M1',evidence_role='COPIED_FORWARD',basis='SAME_DOCUMENT_TEST_IDENTITY',reason='Explicit same test')
            else:decision=dict(anchor_mention_id=None,evidence_role='UNKNOWN_MENTION',basis='UNRESOLVED_IDENTITY',reason='Equal score alone is insufficient')
            return validator(decision)
    output=review(FakeLocal(),'SYN',mentions,sections)
    assert len(output['canonical_assessments'])==1
    assert output['canonical_assessments'][0]['supporting_mention_ids']==['M1','M2']
    assert [m['mention_id'] for m in output['unresolved_mentions']]==['M3']
