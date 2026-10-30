"""Offline V2.3 physician-review package from an immutable frozen cohort.

Run: python -m caa_pdf_extractor.v23_review --source <frozen directory>
No persistence layer, PDF reader, local model or network client is invoked.
"""
import argparse,csv,hashlib,json,re,sys,os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict,Counter
from .full_cohort_report import load_csv
from .extraction.diagnosis_taxonomy_v23 import normalize,common_diagnoses
from .extraction.cardiovascular_taxonomy_v22 import findings
from .extraction.medication_taxonomy_v23 import normalize as medication,load_dictionary,key
from .extraction.clinical_scales_v23 import extract_section,resolve_observations,SCORE
from .extraction.neurosonology_v23 import parse_exam,summarize
from .extraction.cognitive_tests_v23 import extract_cerad
from .extraction.anticoagulation_decisions_v23 import extract as decisions
from .extraction.research_evidence_v23 import facts_from_section,facts_at_index,labs_from_section,status_components
from .extraction.research_scores_v23 import cha2ds2_va,orbit,stess,as_date,component
from .v23_validation import explicit_expectations,regression_gates

GATE_NAMES='''recognized_hypertension_unknown_category type2_diabetes_unknown_category type2_diabetes_as_neurologic hypercholesterolemia_unknown_category lipid_disorder_as_cardiovascular_unknown anemia_as_neurologic Atorvalan_as_OTHER Doxazosin_as_OTHER Vorhofflimmern_not_ATRIAL_FIBRILLATION TRINS_as_neurologic MINS_as_neurologic explicit_pre_mrs_missed explicit_admission_NIHSS_missed explicit_admission_mRS_missed explicit_discharge_NIHSS_missed explicit_discharge_mRS_missed stroke_scores_linked_to_wrong_event explicit_score_overwritten_by_inference future_score_used_as_admission_score nonstroke_NIHSS_forced postictal_patient_given_inferred_mRS retrospective_NIHSS_with_guessed_items isolated_seizure_with_STESS CFS_based_on_acute_deficit AF_score_using_future_information ORBIT_using_future_labs missing_score_component_treated_as_zero clinical_database_writes cloud_patient_data_calls destructive_writes'''.split()

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def freeze(path):return {str(p.relative_to(path)):sha(p) for p in sorted(path.rglob('*')) if p.is_file()}
def write_csv(path,rows,fields=None):
    keys=fields or list(dict.fromkeys(k for r in rows for k in r)) or ['patient_id','status']
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader()
        for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()})

def guard(source,out):
    """Writes are confined to this new artifact directory; deny network and DB."""
    def inside(value,path):
        try:return Path(value).resolve().is_relative_to(path)
        except (ValueError,TypeError,OSError):return False
    def audit(event,args):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo','sqlite3.connect'):raise RuntimeError('V23 offline review prohibits network/database access')
        if event=='open':
            mode=args[1] or '';flags=args[2] or 0
            if any(c in mode for c in 'wax+') or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
                if not inside(args[0],out):raise RuntimeError('V23 writes restricted to fresh output directory')
        if event in ('os.remove','os.rmdir','os.rename','os.replace'):raise RuntimeError('V23 prohibits destructive writes')
    sys.addaudithook(audit)

def taxonomy(source):
    old={}
    for r in load_csv(source/'canonical_diagnoses.csv'):old[(r['patient_id'],r['id'])]=r
    rows=[]
    for r in load_csv(source/'diagnosis_caa_v21_full/diagnosis_candidate_eligibility.csv'):
        original=old.get((r['patient_id'],r.get('canonical_diagnosis_id')),{});label=r['raw_candidate']
        # Source-label taxonomy annotation is independent from disease eligibility.
        # Non-disease rows keep their eligibility and are never promoted.
        mapped=normalize(label,original,eligible=True) or [dict(canonical_name=None,domain=None,category=None,subcategory=None,rule_version='DIAGNOSIS_TAXONOMY_V2.3',mapping_status='NO_DETERMINISTIC_DISEASE_MAPPING')]
        for d in mapped:
            rows.append(dict(patient_id=r['patient_id'],record_id=r['record_id'],raw_candidate=label,
                candidate_type=r['candidate_type'],eligible_for_disease_group=r['candidate_type']=='DIAGNOSIS_DISEASE',
                original_canonical_name=original.get('canonical_name'),original_domain=original.get('domain'),original_category=original.get('category'),
                original_canonical_diagnosis_id=r.get('canonical_diagnosis_id'),source_ids=r.get('source_ids'),
                **{k:v for k,v in d.items() if k not in ('patient_id','id','source_ids')},
                review_required=r['candidate_type']!='DIAGNOSIS_DISEASE',eligibility_unchanged=True))
    return rows

def link_scales(scales,episodes,events,encounters):
    """Link by unique type/date, or unique current stroke in that document.

    Ambiguity stays unlinked; no nearest-event heuristic is permitted.
    """
    emap={e['event_id']:e for e in episodes}
    for row in scales:
        episode=emap.get(row.get('event_id'));pid=row['patient_id'];doc=row['source_document_id'];row['source_event_type']=episode.get('event_type') if episode else None
        candidates=[e for e in events if str(e['patient_id'])==pid and e['event_type'] in ('ISCHEMIC_STROKE','INTRACEREBRAL_HEMORRHAGE','TIA')]
        if episode and episode['event_date']:
            candidates=[e for e in candidates if e.get('event_date')==episode['event_date'] and e['event_type']==episode['event_type']]
            # An explicit source episode can stand alone if the legacy event is absent.
            row['linkage_basis']='EXPLICIT_STROKE_BLOCK'
            if len(candidates)==1:row['event_id']=candidates[0]['id']
            elif len(candidates)>1:row['event_id']=None;row['review_required']=True;row['linkage_basis']='AMBIGUOUS_SOURCE_EVENT'
        elif not episode:
            candidates=[e for e in candidates if doc in [str(x) for x in e.get('source_document_ids',[])]]
            current=[e for e in candidates if e.get('event_date') and e.get('event_date')==row.get('assessment_date')]
            if len(current)==1:row['event_id']=current[0]['id'];row['event_date']=current[0]['event_date'];row['source_event_type']=current[0]['event_type'];row['linkage_basis']='UNIQUE_CURRENT_DATED_EVENT'
            else:row['event_id']=None;row['review_required']=True;row['linkage_basis']='UNRESOLVED'
        else:row['event_id']=None;row['review_required']=True;row['linkage_basis']='UNDATED_STROKE_BLOCK'
        encounter_day=row.get('encounter_id','').split(':')[-1] if row.get('encounter_id') else None
        if row.get('event_date') and as_date(encounter_day) and row['timepoint'] in ('PRE_EVENT','ADMISSION','DISCHARGE'):
            delta=(as_date(encounter_day)-as_date(row['event_date'])).days
            if delta<0 or delta>7:
                row['assessment_date']=None;row['review_required']=True;row['assessment_date_basis']='HISTORICAL_EVENT_TIMEPOINT_DATE_UNKNOWN'
        if row.get('event_date') and row.get('assessment_date') and row['timepoint']=='ADMISSION' and row['event_date']!=row['assessment_date']:
            # Admission may legitimately follow the event; historical restatements
            # are not dated to the new encounter by default.
            difference=(as_date(row['assessment_date'])-as_date(row['event_date'])).days
            if difference<0 or difference>7:row['assessment_date']=None;row['review_required']=True
        row.update(id=row['assessment_id'],scale_type='MRS' if row['scale_name']=='mRS' else row['scale_name'],
            assessment_timepoint=row['timepoint'],assessment_datetime=None,value_numeric=row['value'],value_text=None,
            source_type=row['source'],source_page=row['page'],source_section=row['section_id'])

def generate(source,out):
    bundles=read(source/'bundles.json');results=read(source/'extraction_results.json');by_pid={str(r['patient_id']):r for r in results}
    events=load_csv(source/'clinical_events.csv');diagnoses=taxonomy(source);dictionary=load_dictionary()
    med=[]
    for r in load_csv(source/'medication_summary.csv'):
        med.append(dict(patient_id=r['patient_id'],date=r.get('date'),source_document_id=r.get('source_document_id'),page=r.get('page'),section_id=r.get('section'),
            source_evidence_id=f"{r.get('source_document_id')}:{r.get('section')}:{len(med)}",original_category=r.get('category'),original_antithrombotic_role=r.get('antithrombotic_role'),**medication(r['medication'],dictionary)))
    unknown_groups=defaultdict(list)
    for r in med:
        if r['review_required']:unknown_groups[key(re.split(r'\d',r['raw_medication'],maxsplit=1)[0])].append(r)
    unknown=[dict(raw_name=rs[0]['raw_medication'],normalized_name=k,patient_count=len({r['patient_id'] for r in rs}),mention_count=len(rs),
        suggested_active_ingredient=None,suggested_ATC=None,suggested_therapeutic_class=None,suggested_research_class=None,lookup_status='UNRESOLVED') for k,rs in unknown_groups.items()]
    unknown.sort(key=lambda r:(-r['patient_count'],-r['mention_count'],r['normalized_name']))
    scales=[];episodes=[];decision_rows=[];facts=[];labs=[];census=[];sections=0;docsections={};extra_cognitive=[];cognitive_census=[]
    encmap={}
    for r in results:
        for e in r.get('encounters',[]):encmap[(str(r['patient_id']),str(e['source_document_id']))]=e
    for b in bundles:
        pid=str(b['patient_id'])
        for doc in b['documents']:
            did=str(doc['source_document_id']);enc=encmap.get((pid,did),{});ad=enc.get('admission_date');dd=enc.get('discharge_date');docsections[(pid,did)]=doc['sections']
            for sec in doc['sections']:
                sections+=1;text=sec.get('source_text') or sec.get('text') or '';sid=sec['section_id'];page=sec.get('page_start');heading=sec.get('heading') or ''
                kw=dict(patient_id=pid,document_id=did,section_id=sid,page=page)
                found,eps=extract_section(text,**kw,admission_date=ad,discharge_date=dd,case_id=by_pid[pid].get('case',{}).get('fallnr'))
                scales+=found;episodes+=eps
                obs,refs=extract_cerad(text,**kw);extra_cognitive+=obs;cognitive_census+=refs
                for n,line in enumerate(text.splitlines()):
                    if re.search(r'(?<!\w)(?:NIHSS|m\s*RS|STESS|CFS|MoCA)(?![a-z])',line,re.I):
                        matched=[r for r in found if r['evidence_quote'] and r['source_offset']>=sum(len(l)+1 for l in text.splitlines()[:n]) and r['source_offset']<sum(len(l)+1 for l in text.splitlines()[:n+1])]
                        census.append(dict(patient_id=pid,source_document_id=did,section_id=sid,page=page,line=n,evidence_quote=line[:160],
                            explicit_numeric_mentions=len(list(SCORE.finditer(line))),expected_explicit_slots=explicit_expectations(line),extracted_observations=len(matched),extracted_slots=[dict(scale_name=r['scale_name'],timepoint=r['timepoint'],value=r['value']) for r in matched],review_required=not bool(matched),
                            disposition='EXPLICIT_OBSERVATION' if matched else 'UNPARSED_SCALE_REFERENCE_REQUIRES_REVIEW'))
                decision_rows+=decisions(text,**kw,admission_date=ad,discharge_date=dd,heading=heading)
                if re.search(r'diagnos',heading,re.I):facts+=facts_from_section(text,**kw,available_date=dd)
                labs+=labs_from_section(text,**kw,admission_date=ad)
        # Existing analyte observations with an actual phlebotomy date can be
        # reused. The legacy admission-date convention is explicitly excluded.
        for lab in by_pid[pid].get('laboratory',[]):
            if not lab.get('phlebotomy_date_known') or not as_date(lab.get('measurement_date')):continue
            analyte={'hemoglobin':'HEMOGLOBIN','haemoglobin':'HEMOGLOBIN','hb':'HEMOGLOBIN','hematocrit':'HEMATOCRIT','hct':'HEMATOCRIT','egfr':'EGFR','creatinine':'CREATININE','kreatinin':'CREATININE'}.get(str(lab.get('parameter')).lower())
            if not analyte:continue
            try:value=float(str(lab.get('value')).replace(',','.'))
            except ValueError:continue
            labs.append(dict(patient_id=pid,analyte=analyte,value=value,unit=lab.get('unit'),sample_date=lab['measurement_date'],source_evidence_ids=[lab.get('source_evidence_id')],validation_status='VALID',date_basis='EXISTING_EXPLICIT_PHLEBOTOMY_DATE'))
    link_scales(scales,episodes,events,encmap)
    cognitive=[]
    for result in results:
        for c in result.get('cognitive_canonical',{}).get('canonical_assessments',[]):
            if c.get('deleted') or not c.get('accepted',True):continue
            cognitive.append(dict(id=c.get('canonical_assessment_key'),patient_id=str(result['patient_id']),case_id=c.get('case_id'),event_id=None,encounter_id=None,
                scale_type=c.get('test_type'),test_name=c.get('test_type'),raw_score=c.get('score'),maximum_score=c.get('max_score'),assessment_timepoint='OTHER',assessment_date=c.get('assessment_date'),assessment_datetime=None,
                assessment_year=c.get('assessment_year'),assessment_month=c.get('assessment_month'),date_precision=c.get('date_precision'),value_numeric=c.get('score'),value_text=None,
                source_type='EXISTING_CANONICAL_EXPLICIT',source_document_id=c.get('source_document_id'),source_page=c.get('page'),source_section=c.get('section_id'),
                evidence_id=c.get('representative_mention_id'),supporting_evidence_ids=c.get('supporting_mention_ids'),assessability='ASSESSABLE' if c.get('score') is not None else 'NOT_ASSESSABLE',
                review_required=c.get('temporal_review_required',False),rule_version='EXISTING_COGNITIVE_CANONICAL_REUSED'))
    cognitive+=extra_cognitive
    neuro=[row for r in results for exam in r.get('other_exams',[]) if exam.get('kind')=='NEUROSONOLOGY' for row in parse_exam(exam)]
    neuro_by_pid=defaultdict(list)
    for r in neuro:neuro_by_pid[str(r['patient_id'])].append(r)
    neuro_summaries=[summarize(rs) for rs in neuro_by_pid.values()]
    for pid in by_pid:
        if pid not in neuro_by_pid:neuro_summaries.append(dict(patient_id=pid,exam_ids=[],any_carotid_atherosclerosis=None,any_ica_stenosis_ge50=None,any_ica_stenosis_ge70=None,any_ica_occlusion=None,vertebral_abnormality=None,intracranial_stenosis=None,intracranial_occlusion=None,review_required=True,assessability='NOT_EXAMINED'))
    cha=[];orbits=[];dataset=[];admissions=[];stroke_attempts=[]
    candidates=load_csv(source/'diagnosis_caa_v21_full/diagnosis_candidate_eligibility.csv')
    # Every existing or newly recovered stroke event receives all seven slots,
    # including explicit missing observations rather than fabricated totals.
    all_strokes={}
    for event in events:
        if event['event_type'] in ('ISCHEMIC_STROKE','INTRACEREBRAL_HEMORRHAGE'):all_strokes[(str(event['patient_id']),event['id'])]=event
    for event in episodes:
        if event['event_type'] not in ('ISCHEMIC_STROKE','INTRACEREBRAL_HEMORRHAGE'):continue
        matches=[e for e in events if str(e['patient_id'])==event['patient_id'] and e.get('event_date')==event.get('event_date') and e['event_type']==event['event_type']] if event.get('event_date') else []
        if len(matches)!=1 and event.get('event_date'):all_strokes[(event['patient_id'],event['event_id'])]=event
    for (pid,event_id),event in all_strokes.items():
        for scale,tp in [('mRS','PRE_EVENT'),('NIHSS','ADMISSION'),('mRS','ADMISSION'),('NIHSS','DISCHARGE'),('mRS','DISCHARGE'),('NIHSS','FOLLOWUP'),('mRS','FOLLOWUP')]:
            observations=[r for r in scales if r.get('event_id')==event_id and r['scale_name']==scale and r['timepoint']==tp];vals={r['value'] for r in observations if r['value'] is not None}
            stroke_attempts.append(dict(patient_id=pid,event_id=event_id,event_date=event.get('event_date'),event_type=event['event_type'],scale_type='MRS' if scale=='mRS' else scale,assessment_timepoint=tp,
                value_numeric=next(iter(vals)) if len(vals)==1 else None,source_type='EXPLICIT' if observations else 'NOT_ASSESSABLE',conflict=len(vals)>1,
                source_evidence_ids=[r['evidence_id'] for r in observations],review_required=len(vals)!=1))
    baseline={r['patient_id']:r for r in load_csv(source/'patient_cohort_summary.csv')}
    for pid,result in by_pid.items():
        case=result.get('case') or {};dob=case.get('date_of_birth');sex=str(case.get('gender') or '').lower()
        pfacts=[r for r in facts if r['patient_id']==pid]
        afdays=[r['evidence_date'] for r in pfacts if r['component']=='AF' and r['value'] is True]
        afday=min(afdays) if afdays else None
        indexes=[dict(index_id='FIRST_CONFIRMED_AF',index_date=afday,decision=None)] if afday else []
        indexes+=[dict(index_id=d['decision_id'],index_date=d['decision_date'],decision=d) for d in decision_rows if d['patient_id']==pid]
        for idx in indexes:
            day=idx['index_date'];available=facts_at_index(pfacts,day);af=available.get('AF',{}).get('value') is True
            c=cha2ds2_va(confirmed_af=af,index_date=day,dob=dob,facts=available)
            c.update(patient_id=pid,index_id=idx['index_id'],af_index_date=afday,af_index_date_basis='FIRST_SOURCE_CONFIRMED_AVAILABLE_DATE',cha2ds2_va_score=c['score'],component_evidence=available,dob_source_provenance=case.get('source_provenance'))
            for k,v in c['components'].items():c['component_'+k]=v
            cha.append(c)
            if idx['decision'] is None:continue
            d=idx['decision'];ofacts=dict(available)
            # An explicit dated discharge medication list supports ACTIVE use only
            # on that date. It never proves prior use or absence of treatment.
            active=[m for m in med if m['patient_id']==pid and m.get('date')==day and m.get('research_class')=='ANTIPLATELET']
            if active:ofacts['antiplatelet']=component(True,day,[m['source_evidence_id'] for m in active])
            o=orbit(patient_id=pid,index_date=day,dob=dob,sex=sex,facts=ofacts,labs=labs,admission_date=d.get('admission_date'))
            o.update(patient_id=pid,index_id=idx['index_id'],component_evidence=ofacts);orbits.append(o)
            event_candidates=[e for e in events if str(e['patient_id'])==pid and d['source_document_id'] in [str(x) for x in e.get('source_document_ids',[])] and e.get('event_date') and as_date(day) and as_date(e['event_date'])<=as_date(day) and e['event_type'] in ('ISCHEMIC_STROKE','INTRACEREBRAL_HEMORRHAGE','TIA')]
            linked=event_candidates[0]['id'] if len(event_candidates)==1 else None
            linked_scores=[r for r in stroke_attempts if r['patient_id']==pid and r['event_id']==linked] if linked else []
            phenotype=[]
            for imaging in result.get('imaging',[]):
                scan_day=as_date(imaging.get('raw_source_date'))
                if not scan_day or not as_date(day) or scan_day>as_date(day):continue
                for feature in imaging.get('all_existing_positive_findings',[]):
                    if feature.get('finding_type') in ('ICH','CMB','CSS','SAH'):
                        phenotype.append(dict(imaging_id=imaging.get('imaging_id'),date=scan_day.isoformat(),finding_type=feature.get('finding_type'),location=feature.get('location'),laterality=feature.get('laterality'),temporal_status=feature.get('temporal_status'),source_document_id=imaging.get('source_document_id')))
            dataset.append(dict(patient_id=pid,event_id=linked,event_candidate_ids=[e['id'] for e in event_candidates],event_linkage='UNIQUE_SOURCE_DOCUMENT_EVENT' if linked else 'UNCERTAIN_REQUIRES_REVIEW',decision_id=d['decision_id'],admission_date=d.get('admission_date'),af_index_date=afday,oac_decision_index_date=day,
                confirmed_af_at_index=af,cha2ds2_va_score=c['score'],cha2ds2_va_complete=c['score_complete'],cha2ds2_va_components=c['components'],cha2ds2_va_missing_components=c['missing_components'],esc_oac_recommendation=c['esc_oac_recommendation'],
                orbit_score=o['score'],orbit_category=o['orbit_category'],orbit_complete=o['score_complete'],orbit_missing_components=o['missing_components'],
                caa_source_membership_frozen=baseline.get(pid,{}).get('caa_positive_yes_no'),caa_at_index=available.get('CAA',{}).get('value'),
                hemorrhagic_phenotype_at_index=phenotype,stroke_scores=linked_scores,cognitive_assessments=[r for r in cognitive if r['patient_id']==pid and as_date(r.get('assessment_date')) and as_date(day) and as_date(r['assessment_date'])<=as_date(day)],
                **{k:d[k] for k in ('decision_type','decision_status','decision_date_basis','document_available_date','indication','indication_link_explicit','reason_codes','reason_evidence','reason_direction','source_document_id','page','section_id','evidence_id','review_required')},
                phenotype_temporal_review_required=True,source_evidence_ids=sorted(set(c['source_evidence_ids']+o['source_evidence_ids']+[d['evidence_id']]))) )
        # STESS attempts are restricted to explicitly classified true status events.
        for e in events:
            if str(e['patient_id'])!=pid or e['event_type'] not in ('STATUS_EPILEPTICUS','CONVULSIVE_STATUS_EPILEPTICUS','NONCONVULSIVE_STATUS_EPILEPTICUS'):continue
            # Legacy event labels can be wrong (e.g. St.p. acute renal failure).
            labels=[d['raw_candidate'] for d in candidates if d['patient_id']==pid and d['record_id'] in (e.get('record_ids') or [])]
            true=any(re.search(r'Status\s+(?:epilepticus|aphasicus)',x,re.I) for x in labels) or (not labels and e.get('review')=='VALIDATED_CURRENT_ADMISSION_EVENT_RETAINED')
            if not true:continue
            if not labels:labels=[e['event_type'].replace('_',' ')]
            parts=status_components(e,labels,[sec for did in e.get('source_document_ids',[]) for sec in docsections.get((pid,str(did)),[])],pfacts)
            s=stess(true_status=True,index_date=e.get('event_date'),dob=dob,**parts)
            s.update(stess_score=s['score'],stess_complete=s['score_complete'],component_evidence=dict(parts,age=dict(date_of_birth=dob,index_date=e.get('event_date'),source_provenance=case.get('source_provenance'))))
            for k,v in s['components'].items():s['component_'+k]=v
            admissions.append(dict(patient_id=pid,event_id=e['id'],scale_type='STESS',source_type='DETERMINISTIC_COMPONENTS',**s,
                component_review_required=True,review_required=True))
        # Every admission gets separate functional/cognitive availability; no
        # nonstroke NIHSS or acute-illness CFS is reconstructed.
        seen=set()
        for enc in result.get('encounters',[]):
            ad=enc.get('admission_date')
            if not ad or ad in seen:continue
            seen.add(ad);eid=f'{pid}:{ad}'
            observations=[r for r in scales if r.get('encounter_id')==eid]
            for scale in ('mRS','CFS'):
                for tp in ('PRE_EVENT','ADMISSION') if scale=='mRS' else ('PRE_EVENT',):
                    explicit=[r for r in observations if r['scale_name']==scale and r['timepoint']==tp and r['assessment_date']]
                    vals={r['value'] for r in explicit if r['value'] is not None}
                    context=' '.join((sec.get('source_text') or sec.get('text') or '') for sec in docsections.get((pid,str(enc['source_document_id'])),[]) if re.search(r'Aufnahme|Status',sec.get('heading') or '',re.I))
                    state='POSTICTAL' if re.search(r'postiktal|postictal',context,re.I) else 'SEDATED_INTUBATED' if re.search(r'sediert|intubiert',context,re.I) else 'ALTERED_CONSCIOUSNESS' if re.search(r'bewusstseinsgest[öo]rt|vigilanzgemindert',context,re.I) else 'NOT_ASSESSABLE'
                    admissions.append(dict(patient_id=pid,encounter_id=eid,event_id=None,scale_type=scale.upper(),assessment_timepoint=tp,assessment_date=ad,
                        value_numeric=next(iter(vals)) if len(vals)==1 else None,source_type='EXPLICIT' if explicit else 'NOT_INFERRED',
                        assessability='ASSESSABLE' if len(vals)==1 else state if tp=='ADMISSION' and scale=='mRS' else 'NOT_ASSESSABLE',source_evidence_ids=[r['evidence_id'] for r in explicit],
                        review_required=len(vals)!=1,conflict=len(vals)>1,reason='EXPLICIT_VALUES_ONLY; NO_ACUTE_STATE_RECONSTRUCTION'))
    for row in dataset:
        pid=row['patient_id'];eid=f"{pid}:{row.get('admission_date')}"
        row['functional_assessments']=[r for r in admissions if r['patient_id']==pid and r.get('encounter_id')==eid]
        row['status_epilepticus_assessments']=[r for r in admissions if r['patient_id']==pid and r.get('scale_type')=='STESS' and r.get('event_id') in row['event_candidate_ids']]
        row['medications_at_index']=[dict(canonical_drug=m.get('canonical_drug'),research_class=m.get('research_class'),research_subclass=m.get('research_subclass'),anticoagulant_role=m.get('anticoagulant_role'),source_evidence_id=m['source_evidence_id']) for m in med if m['patient_id']==pid and m.get('date') and m.get('date')==row['oac_decision_index_date']]
    preserved_events=[]
    for event in events:
        row=dict(event,original_event_type=event['event_type'],original_hemorrhagic_transformation=event.get('hemorrhagic_transformation'))
        if event['event_type'] in ('ISCHEMIC_STROKE','INTRACEREBRAL_HEMORRHAGE','SUBARACHNOID_HEMORRHAGE','TIA'):
            row.update(domain='NEUROLOGIC',category='CEREBROVASCULAR',subcategory=event['event_type'])
        preserved_events.append(row)
    return dict(diagnosis_taxonomy_v23_review=diagnoses,clinical_events_v23_review=preserved_events,medication_taxonomy_v23_review=med,medications_unknown_review=unknown,
        neurosonology_v23_review=neuro,neurosonology_patient_summary=neuro_summaries,stroke_score_review=scales,
        clinical_scale_assessment=scales+cognitive,clinical_scale_resolved_review=resolve_observations(scales),
        cognitive_test_v23_review=cognitive,cognitive_test_lexical_review=cognitive_census,
        stroke_episode_review=episodes,stroke_score_timepoint_review=stroke_attempts,scale_lexical_census=census,cha2ds2_va_review=cha,orbit_review=orbits,
        admission_scores_review=admissions,anticoagulation_decision_review=decision_rows,anticoagulation_decision_dataset=dataset,
        dated_component_evidence=facts,orbit_laboratory_evidence=labs),dict(patients=len(results),source_documents=sum(len(b['documents']) for b in bundles),source_sections_scanned=sections)

def quality(tables):
    gates=dict.fromkeys(GATE_NAMES,0);dx=tables['diagnosis_taxonomy_v23_review'];med=tables['medication_taxonomy_v23_review']
    eventmap={(str(e['patient_id']),e.get('id') or e.get('event_id')):e for e in tables['clinical_events_v23_review']+tables['stroke_episode_review']}
    for r in dx:
        label=r['raw_candidate'];name=r.get('canonical_name');cat=r.get('category');domain=r.get('domain')
        names={x['canonical_name'] for x in common_diagnoses(label)}
        gates['recognized_hypertension_unknown_category']+=name=='ARTERIAL_HYPERTENSION' and cat!='HYPERTENSION'
        gates['type2_diabetes_unknown_category']+=name=='DIABETES_MELLITUS_TYPE_2' and cat!='DIABETES'
        gates['type2_diabetes_as_neurologic']+=name=='DIABETES_MELLITUS_TYPE_2' and domain=='NEUROLOGIC'
        gates['hypercholesterolemia_unknown_category']+=name=='HYPERCHOLESTEROLEMIA' and cat!='LIPID_DISORDER'
        gates['lipid_disorder_as_cardiovascular_unknown']+=name in ('LIPID_DISORDER','HYPERCHOLESTEROLEMIA','HYPERTRIGLYCERIDEMIA','MIXED_HYPERLIPIDEMIA') and domain!='ENDOCRINE_METABOLIC'
        gates['anemia_as_neurologic']+=cat=='ANEMIA' and domain=='NEUROLOGIC'
        gates['Vorhofflimmern_not_ATRIAL_FIBRILLATION']+=any(f['canonical_name']=='ATRIAL_FIBRILLATION' for f in findings(label)) and name not in names|{'ATRIAL_FIBRILLATION','TRICUSPID_REGURGITATION','MITRAL_REGURGITATION'}
        gates['TRINS_as_neurologic']+=bool(re.search(r'TRINS',label,re.I)) and domain=='NEUROLOGIC'
        gates['MINS_as_neurologic']+=bool(re.search(r'MINS',label,re.I)) and domain=='NEUROLOGIC'
    for r in med:
        for drug in ('Atorvalan','Doxazosin'):
            gates[drug+'_as_OTHER']+=bool(re.match(drug+r'\b',r['raw_medication'],re.I)) and r.get('research_class') not in ('LIPID_LOWERING','ANTIHYPERTENSIVE')
    for row in tables['clinical_scale_resolved_review']:
        members=[r for r in tables['stroke_score_review'] if r['evidence_id'] in row['source_evidence_ids']]
        gates['explicit_score_overwritten_by_inference']+=any(r['source']=='EXPLICIT' for r in members) and row['source']!='EXPLICIT'
    for c in tables['scale_lexical_census']:
        if c['explicit_numeric_mentions']!=c['extracted_observations']:gates['explicit_numeric_scale_observation_missed']=gates.get('explicit_numeric_scale_observation_missed',0)+1
        for expected in c['expected_explicit_slots']:
            if expected not in c['extracted_slots']:
                gate={('mRS','PRE_EVENT'):'explicit_pre_mrs_missed',('mRS','ADMISSION'):'explicit_admission_mRS_missed',('NIHSS','ADMISSION'):'explicit_admission_NIHSS_missed',('mRS','DISCHARGE'):'explicit_discharge_mRS_missed',('NIHSS','DISCHARGE'):'explicit_discharge_NIHSS_missed'}.get((expected['scale_name'],expected['timepoint']))
                if gate:gates[gate]+=1
    for r in tables['stroke_score_review']:
        gates['retrospective_NIHSS_with_guessed_items']+=r['scale_name']=='NIHSS' and r['source']!='EXPLICIT'
        gates['nonstroke_NIHSS_forced']+=r['scale_name']=='NIHSS' and r['source']!='EXPLICIT' and not r.get('event_id')
        gates['CFS_based_on_acute_deficit']+=r['scale_name']=='CFS' and r['source']!='EXPLICIT'
        gates['future_score_used_as_admission_score']+=r['timepoint']=='ADMISSION' and bool(r.get('assessment_date') and r.get('encounter_id') and r['assessment_date']>r['encounter_id'].split(':')[-1])
        if r.get('event_id'):
            linked=eventmap.get((r['patient_id'],r['event_id']))
            gates['stroke_scores_linked_to_wrong_event']+=not linked or bool(linked and r.get('event_date') and linked.get('event_date') and r['event_date']!=linked['event_date']) or bool(linked and r.get('source_event_type') and r['source_event_type']!=linked['event_type'])
    for r in tables['admission_scores_review']:
        gates['postictal_patient_given_inferred_mRS']+=r.get('assessability') in ('POSTICTAL','SEDATED_INTUBATED') and r.get('value_numeric') is not None and r.get('source_type')!='EXPLICIT'
        gates['isolated_seizure_with_STESS']+=r.get('scale_type')=='STESS' and not r.get('applicable')
    for r in tables['cha2ds2_va_review']+tables['orbit_review']:
        gates['missing_score_component_treated_as_zero']+=bool(r['missing_components']) and r['score'] is not None
        for evidence in r.get('component_evidence',{}).values():
            gates['AF_score_using_future_information']+=bool(as_date(evidence.get('evidence_date')) and as_date(r['score_index_date']) and as_date(evidence['evidence_date'])>as_date(r['score_index_date']))
        for lab in r.get('labs_used',{}).values():
            if lab:gates['ORBIT_using_future_labs']+=as_date(lab['sample_date'])>as_date(r['score_index_date'])
    gates['stroke_event_type_or_transformation_changed']=sum(e['event_type']!=e['original_event_type'] or e.get('hemorrhagic_transformation')!=e.get('original_hemorrhagic_transformation') for e in tables['clinical_events_v23_review'])
    gates.update(regression_gates())
    # The implementations prohibit inference by construction; these checks also
    # cover synthetic regressions when the frozen cohort has no such example.
    return gates

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('data/debug/full_cohort_20261022T201512+0200'));p.add_argument('--output',type=Path);args=p.parse_args(argv)
    source=args.source.resolve();stamp=datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S%z');out=(args.output or Path('data/debug')/('v23_review_'+stamp)).resolve()
    if source.is_relative_to(out) or out.is_relative_to(source):raise ValueError('Output must be separate from frozen inputs')
    out.mkdir(parents=True,exist_ok=False);guard(source,out);before=freeze(source)
    tables,counts=generate(source,out);gates=quality(tables);after=freeze(source);gates['frozen_inputs_changed']=sum(before.get(k)!=after.get(k) for k in before.keys()|after.keys())
    for name,rows in tables.items():write_csv(out/(name+'.csv'),rows)
    (out/'source_artifact_sha256.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    (out/'hard_gates.json').write_text(json.dumps(gates,indent=2),encoding='utf-8')
    status='MORE_V23_WORK_REQUIRED' if any(gates.values()) else 'READY_FOR_V23_PHYSICIAN_REVIEW'
    implementation=[Path(__file__)]+[Path(__file__).parent/'v23_validation.py']+list((Path(__file__).parent/'extraction').glob('*v23.py'))
    summary=dict(status=status,source=str(source),output=str(out),**counts,counts={k:len(v) for k,v in tables.items()},hard_gates=gates,
        implementation_sha256={str(p):sha(p) for p in implementation},
        medication_dictionary_sha256=sha(Path('data/reference/medication_dictionary.csv')),frozen_artifact_count=len(before))
    (out/'manifest.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    missing=Counter(k for r in tables['cha2ds2_va_review']+tables['orbit_review'] for k in r['missing_components'])
    unknown=tables['medications_unknown_review'];conflicts=[r for r in tables['clinical_scale_resolved_review'] if r['conflict']]
    text=f'# V2.3 physician review\n\n{status}\n\nFrozen source: `{source.name}`. {counts["patients"]} patients; {counts["source_documents"]} documents; {counts["source_sections_scanned"]} sections scanned locally. All {len(before)} frozen artifact hashes checked. No PDF extraction, clinical database access, network calls or destructive writes were permitted by the execution guard.\n\n'
    text+='This package contains research proposals requiring physician validation. Source membership is preserved separately from dated evidence. Missing components remain null. A complete score is never manufactured from a lower bound. ESC recommendations express thromboembolic indication only.\n\n'
    text+='## Outputs\n\n| File | Rows |\n|---|---:|\n'+''.join(f'| [{k}.csv]({k}.csv) | {len(v)} |\n' for k,v in tables.items())
    text+='\n## Hard gates\n\n| Gate | Failures |\n|---|---:|\n'+''.join(f'| {k} | {v} |\n' for k,v in gates.items())
    text+='\n## Review holds and missingness\n\n'
    complete_cha=sum(r['score_complete'] for r in tables['cha2ds2_va_review']);complete_orbit=sum(r['score_complete'] for r in tables['orbit_review'])
    text+=f'{len(unknown)} unresolved medication names; {len(conflicts)} conflicting scale groups; {sum(not r.get("event_id") for r in tables["stroke_score_review"])} scale observations with unresolved event linkage. Complete CHA2DS2-VA: {complete_cha}/{len(tables["cha2ds2_va_review"])} assessments. Complete ORBIT: {complete_orbit}/{len(tables["orbit_review"])} assessments. Details are in the linked CSVs.\n\n'
    text+='Missing score components (assessment rows, not patients): '+json.dumps(dict(missing))+'.\n\n'
    text+='Frequent unresolved medication names: '+', '.join(f'{r["normalized_name"]} ({r["patient_count"]} patients)' for r in unknown[:20])+'.\n\n'
    text+='Decision extraction uses bounded local semantic rules; every decision is held for physician review. Document-discharge dates indicate availability by that date, not an asserted exact treatment-decision time. Undated or contradictory facts cannot complete scores. Laboratory table cells without attributable sample dates are excluded from ORBIT. All unmatched scale references remain in the lexical census. STESS components not explicitly attributable to true status episodes remain missing. CFS is explicit only; no acute-state reconstruction. Existing canonical MMSE assessments are reused without duplicating raw mentions or converting MoCA/CERAD.\n\n'
    text+='## References and definitions\n\n[ESC 2024 AF guideline](https://academic.oup.com/eurheartj/article/45/36/3314/7738779); [original ORBIT](https://pmc.ncbi.nlm.nih.gov/articles/4670965/); [STESS guideline](https://link.springer.com/article/10.1007/s12028-025-02425-8); [ATC reference](https://termgit.elga.gv.at/CodeSystem-atc-deutsch-wido.html). Medication dictionary rows contain their individual sources. ORBIT prior laboratory lookback: 90 days.\n'
    (out/'V23_physician_review.md').write_text(text,encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()
