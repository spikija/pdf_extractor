"""Read-only derivation of descriptive variables from a frozen review run.

No extraction, model calls, CAA reclassification or clinical persistence.
Missing clinical status is None, distinct from a documented negative.
"""
from collections import defaultdict
from datetime import date
import hashlib,json,re
from pathlib import Path
from .full_cohort_report import load_csv
from .extraction.research_metadata_v2 import macro_location,mri_technique
from .extraction.pet_rules import normalize_pet
from .extraction.death_rules import explicit_death_dates,VERSION as DEATH_RULE_VERSION

PRIMARY=('SPONTANEOUS_ICH','ISCHEMIC_STROKE','TIA','SEIZURE_OR_STATUS','TFNE',
         'COGNITIVE_BEHAVIORAL','HEADACHE','OTHER_FOCAL_NEUROLOGICAL_DEFICIT','OTHER','UNKNOWN')
COMPONENTS=('motor_deficit','sensory_deficit','language_speech_deficit','visual_deficit',
            'cognitive_behavioral','seizure','status_epilepticus','headache','altered_consciousness','tfne')
CSF_ANALYTES=('CSF_ABETA_UNSPECIFIED','CSF_ABETA42','CSF_ABETA40','CSF_ABETA42_40_RATIO','CSF_TOTAL_TAU','CSF_PTAU')


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def iso(value):
    try:return date.fromisoformat(str(value))
    except (ValueError,TypeError):return None


def exact_age(dob,day):
    b,d=iso(dob),iso(day)
    if not b or not d or d<b:return None
    return (d-b).days/365.2425


def consensus(values):
    values={v for v in values if v is not None}
    return next(iter(values)) if len(values)==1 else None


def unique_encounters(rows):
    groups=defaultdict(list)
    for r in rows:
        if r.get('status')=='VERIFIED_SOURCE_ENCOUNTER' and iso(r.get('admission_date')):
            groups[r['admission_date']].append(r)
    output=[]
    for day,items in sorted(groups.items()):
        ends={r['discharge_date'] for r in items if iso(r.get('discharge_date'))}
        output.append(dict(admission_date=day,discharge_date=next(iter(ends)) if len(ends)==1 else None,
                           discharge_conflict=len(ends)>1,rows=items,source_document_ids=sorted({r['source_document_id'] for r in items})))
    return output


def normalize_report(text):
    return re.sub(r'\s+',' ',text or '').strip().casefold()


def exam_inventory(pid,rows,family,kind):
    """Exact same-day copies merge; different same-day reports are unresolved.

    A dated examination-day is a supported lower bound. We do not assume
    different reports on one day are either separate exams or definite copies.
    """
    groups=defaultdict(list)
    for row in rows:
        day=row.get('raw_source_date') if kind=='IMAGING' else row.get('eeg_date') if kind=='EEG' else row.get('date')
        key=(day if iso(day) else 'UNDATED:'+str(row.get('imaging_id') or row.get('procedure_id') or row.get('id')))
        groups[key].append(row)
    audit=[]
    for day,items in sorted(groups.items()):
        reports=[normalize_report(e.get('raw_report_text') or e.get('eeg_report_text') or e.get('report')) for e in items]
        # A copied short report embedded verbatim in the longer report is the
        # same examination evidence, provided the dated family is identical.
        distinct=set(reports)
        substantial=[s for s in distinct if s]
        nested=bool(substantial) and all(any(s in t for t in substantial if len(t)==max(map(len,substantial))) for s in substantial)
        exact=not day.startswith('UNDATED:') and (len(items)==1 or len(distinct)==1 or nested)
        ids=[e.get('imaging_id') or e.get('procedure_id') or e.get('id') for e in items]
        audit.append(dict(patient_id=pid,family=family,exam_date=None if day.startswith('UNDATED:') else day,
            examination_key=f'{pid}|{family}|{day}',source_ids=ids,source_document_ids=sorted({e['source_document_id'] for e in items}),
            source_rows=len(items),distinct_report_count=len(distinct),identity_status='SUPPORTED_DEDUPLICATED_EXAM' if exact else 'SAME_DAY_IDENTITY_UNRESOLVED' if not day.startswith('UNDATED:') else 'UNDATED_IDENTITY_UNRESOLVED',
            lower_bound_contribution=1 if not day.startswith('UNDATED:') else 0))
    exact_count=len(audit) if all(a['identity_status']=='SUPPORTED_DEDUPLICATED_EXAM' for a in audit) else None
    return exact_count,sum(a['lower_bound_contribution'] for a in audit),audit


def mrs_score(rows,when):
    scores=[]
    for r in rows:
        v=r.get('mrs',{}).get(when,{})
        value=v.get('value') or {}
        score=value.get('score')
        if v.get('status')=='VALID' and isinstance(score,int) and 0<=score<=6:scores.append(score)
    return consensus(scores)


def first_presentation(first,events,episodes=None):
    if not first:return 'UNKNOWN',[],{'basis':'NO_VALID_ADMISSION'}
    direct={'EPILEPTIC_EVENT':'SEIZURE_OR_STATUS','COGNITIVE_DECLINE':'COGNITIVE_BEHAVIORAL',
            'TFNE':'TFNE','HEADACHE':'HEADACHE','SPONTANEOUS_ICH':'SPONTANEOUS_ICH','ISCHEMIC_STROKE':'ISCHEMIC_STROKE','TIA':'TIA',
            'OTHER':'OTHER','UNKNOWN':'UNKNOWN','FOCAL_NEUROLOGICAL_DEFICIT':'OTHER_FOCAL_NEUROLOGICAL_DEFICIT','SENSORY_SYMPTOMS':'OTHER_FOCAL_NEUROLOGICAL_DEFICIT'}
    reasons={direct.get(r['reason'].get('primary_reason'),'UNKNOWN') for r in first['rows']}
    reason=next(iter(reasons)) if len(reasons)==1 else 'UNKNOWN'
    docs=set(first['source_document_ids'])
    matched=[e for e in events if e.get('event_date')==first['admission_date'] and docs.intersection(e.get('source_document_ids',[]))]
    # A source clinical event can refine a broad focal presentation. Imaging
    # findings, historical events and model-generated prose cannot do so.
    event_map={'ISCHEMIC_STROKE':'ISCHEMIC_STROKE','TIA':'TIA','FOCAL_SEIZURE':'SEIZURE_OR_STATUS',
        'EPILEPTIC_SEIZURE':'SEIZURE_OR_STATUS','GENERALIZED_TONIC_CLONIC_SEIZURE':'SEIZURE_OR_STATUS',
        'STATUS_EPILEPTICUS':'SEIZURE_OR_STATUS','CONVULSIVE_STATUS_EPILEPTICUS':'SEIZURE_OR_STATUS','NONCONVULSIVE_STATUS_EPILEPTICUS':'SEIZURE_OR_STATUS'}
    specific={event_map[e['event_type']] for e in matched if e['event_type'] in event_map}
    # Refinement requires BOTH an admission-linked clinical ICH event and the
    # existing episode's explicit spontaneous/current-ICH fields, never CT alone.
    ich_proofs=[]
    for doc in docs:
        ep=(episodes or {}).get('DOC-'+str(doc),{})
        if ep.get('presentation')!='SPONTANEOUS_ICH' or ep.get('spontaneous_ich') is not True or not ep.get('current_ich_date'):continue
        owned=[e for e in events if e['event_type']=='INTRACEREBRAL_HEMORRHAGE' and e.get('event_date')==ep['current_ich_date'] and doc in e.get('source_document_ids',[])]
        if owned:specific.add('SPONTANEOUS_ICH');ich_proofs.extend(e['id'] for e in owned)
    if reason=='OTHER_FOCAL_NEUROLOGICAL_DEFICIT' and len(specific)==1:reason=next(iter(specific))
    return reason,matched,dict(basis='VALIDATED_PRIMARY_REASON_AND_SAME_ADMISSION_CLINICAL_EVENTS',
        source_document_ids=sorted(docs),event_ids=[e['id'] for e in matched],conflicting_reason=len(reasons)>1,
        competing_event_categories=sorted(specific),spontaneous_ich_clinical_event_ids=ich_proofs)


EXPOSURES=('any_antithrombotic','antiplatelet','aspirin','p2y12','dual_antiplatelet','anticoagulant',
           'doac','vka','lmwh','ufh','lmwh_ufh','vte_prophylaxis','therapeutic_anticoagulation')


def medication_exposure(rows,available):
    if not available:return {k:None for k in EXPOSURES}
    active=[];held=[]
    for row in rows:
        if row.get('category')!='ANTITHROMBOTIC':continue
        if re.search(r'pausiert|pausieren|abgesetzt|absetzen|gestoppt|nicht\s+(?:einnehmen|verabreichen)|derzeit\s+keine',row.get('source_row',''),re.I):held.append(row)
        else:active.append(row)
    agents={r.get('agent_class') for r in active}
    platelets=[r for r in active if r.get('antithrombotic_subclass')=='ANTIPLATELET']
    anticoags=[r for r in active if r.get('antithrombotic_subclass')=='ANTICOAGULANT']
    roles={r.get('antithrombotic_role') for r in anticoags}
    def role(name):return True if name in roles else None if any(x not in ('THERAPEUTIC_ANTICOAGULATION','VTE_PROPHYLAXIS') for x in roles) else False
    return dict(any_antithrombotic=bool(active),antiplatelet=bool(platelets),aspirin='ASPIRIN' in agents,
        p2y12='P2Y12_INHIBITOR' in agents,dual_antiplatelet={'ASPIRIN','P2Y12_INHIBITOR'}<=agents,
        anticoagulant=bool(anticoags),doac='DOAC' in agents,vka='VKA' in agents,lmwh='LMWH' in agents,ufh='UFH' in agents,
        lmwh_ufh=bool(agents&{'LMWH','UFH'}),vte_prophylaxis=role('VTE_PROPHYLAXIS'),therapeutic_anticoagulation=role('THERAPEUTIC_ANTICOAGULATION'))


def build_dataset(source):
    summaries=load_csv(source/'patient_cohort_summary.csv')
    baseline=dict(patients=len(summaries),caa=sum(p['caa_positive_yes_no'] for p in summaries),
        epilepsy=sum(p['epilepsy_yes_no'] for p in summaries),af=sum(p['atrial_fibrillation_yes_no'] for p in summaries))
    if baseline!=dict(patients=50,caa=20,epilepsy=9,af=11):raise ValueError('STOP: source baseline discrepancy '+str(baseline))
    results={p['patient_id']:p for p in read(source/'extraction_results.json')}
    grouped={}
    for name,path in dict(events='clinical_events.csv',diseases='canonical_diagnoses.csv',meds='medication_summary.csv',csf='csf_biomarkers.csv',
                         caa='caa_source_assertions.csv',boston='diagnosis_caa_v21_full/boston_features.csv',edinburgh='edinburgh_results.csv',
                         candidates='diagnosis_caa_v21_full/diagnosis_candidate_eligibility.csv').items():
        grouped[name]=defaultdict(list)
        for r in load_csv(source/path):grouped[name][r['patient_id']].append(r)
    assemblies=read(source/'diagnosis_caa_v21_full/assembly_inputs_audit.json')
    death_proofs=defaultdict(list)
    for patient in read(source/'bundles.json'):
        for document in patient['documents']:
            for section in document['sections']:
                for proof in explicit_death_dates(section['source_text']):
                    death_proofs[patient['patient_id']].append(dict(patient_id=patient['patient_id'],death_date=proof['value'],
                        source_document_id=document['source_document_id'],page=section['page_start'],section_id=section['section_id'],
                        evidence=proof['evidence'],source_start=proof['start'],source_end=proof['end'],rule_version=DEATH_RULE_VERSION,
                        basis='EXPLICIT_PATIENT_DEATH_DATE_IN_FROZEN_SECTION'))
    rows=[];audits=dict(encounters=[],imaging=[],eeg=[],ich=[],medications=[],csf=[],pet=[],deaths=[],derivations=[],holds=[])
    for summary in summaries:
        pid=summary['patient_id'];p=results[pid];g={k:v[pid] for k,v in grouped.items()}
        encounters=unique_encounters(p['encounters']);first=encounters[0] if encounters else None
        dob=p['case'].get('date_of_birth');day=first['admission_date'] if first else None
        age=exact_age(dob,day)
        row=dict(patient_id=pid,caa_positive=summary['caa_positive_yes_no'],research_caa_status=summary['research_caa_status'],
            sex=summary['sex'],birth_year=summary.get('birth_year'),female=True if summary['sex']=='woman' else False if summary['sex']=='man' else None,
            male=True if summary['sex']=='man' else False if summary['sex']=='woman' else None,date_of_birth=dob,
            first_valid_admission_date=day,first_discharge_date=first['discharge_date'] if first else None,
            age_at_first_admission=age,age_precision='EXACT' if age is not None else 'APPROXIMATE_YEAR' if summary.get('birth_year') and day else 'UNKNOWN',
            number_of_admissions=len(encounters),epilepsy=summary['epilepsy_yes_no'],any_seizure=summary['seizure_history_yes_no'],
            any_status=summary['status_epilepticus_yes_no'],af=summary['atrial_fibrillation_yes_no'],
            any_ich=summary['ich_history_yes_no'],ischemic_stroke=summary['ischemic_stroke_history_yes_no'],
            cognitive_impairment=summary['dementia_yes_no'])
        for encounter in encounters:
            audits['encounters'].append(dict(patient_id=pid,admission_date=encounter['admission_date'],discharge_date=encounter['discharge_date'],
                source_document_ids=encounter['source_document_ids'],source_rows=len(encounter['rows']),discharge_conflict=encounter['discharge_conflict']))
        row['primary_presentation'],matched,proof=first_presentation(first,g['events'],assemblies[pid]['episodes'])
        audits['derivations'].append(dict(patient_id=pid,variable='primary_presentation',value=row['primary_presentation'],**proof))
        row.update({'presentation_'+k:None for k in COMPONENTS})
        if first:
            row['presentation_sensory_deficit']=consensus(r['phenotype'].get('sensory_present') for r in first['rows'])
            flags={f for r in first['rows'] for f in r['phenotype'].get('flags',[])}
            if 'COGNITIVE_DECLINE' in flags:row['presentation_cognitive_behavioral']=True
            if 'EPILEPTIC_EVENT' in flags:row['presentation_seizure']=True
            positive={'COGNITIVE_BEHAVIORAL':'cognitive_behavioral','SEIZURE_OR_STATUS':'seizure','HEADACHE':'headache','TFNE':'tfne'}
            if row['primary_presentation'] in positive:row['presentation_'+positive[row['primary_presentation']]]=True
            if any(e['event_type'] in ('EPILEPTIC_SEIZURE','FOCAL_SEIZURE','GENERALIZED_TONIC_CLONIC_SEIZURE') for e in matched):row['presentation_seizure']=True
            if any(e['event_type'] in ('STATUS_EPILEPTICUS','CONVULSIVE_STATUS_EPILEPTICUS','NONCONVULSIVE_STATUS_EPILEPTICUS') for e in matched):
                row['presentation_status_epilepticus']=True;row['presentation_seizure']=True
        row.update(first_admission_mrs=mrs_score(first['rows'],'admission') if first else None,
                   first_discharge_mrs=mrs_score(first['rows'],'discharge') if first else None)
        dated_mrs=[(e['admission_date'],mrs_score(e['rows'],'admission')) for e in encounters]
        dated_mrs += [(e['discharge_date'],mrs_score(e['rows'],'discharge')) for e in encounters if e['discharge_date']]
        dated_mrs=sorted((d,v) for d,v in dated_mrs if v is not None)
        row['last_available_mrs']=dated_mrs[-1][1] if dated_mrs else None
        row['last_available_mrs_date']=dated_mrs[-1][0] if dated_mrs else None
        for label,when in [('admission','first_admission_mrs'),('discharge','first_discharge_mrs')]:
            for suffix,predicate in [('0_2',lambda x:x<=2),('3_5',lambda x:3<=x<=5),('6',lambda x:x==6)]:
                row[f'mrs_{label}_{suffix}']=predicate(row[when]) if row[when] is not None else None
        row.update(admissions_1=len(encounters)==1,admissions_2_3=2<=len(encounters)<=3,admissions_ge4=len(encounters)>=4)
        types={e['event_type'] for e in g['events']}
        row.update(any_tia='TIA' in types,focal_seizure='FOCAL_SEIZURE' in types,
            generalized_seizure='GENERALIZED_TONIC_CLONIC_SEIZURE' in types,
            convulsive_status='CONVULSIVE_STATUS_EPILEPTICUS' in types,nonconvulsive_status='NONCONVULSIVE_STATUS_EPILEPTICUS' in types)
        findings=[f for i in p['imaging'] for f in i['all_existing_positive_findings']]
        negatives={f['finding_type'] for i in p['imaging'] for f in i['all_existing_negative_findings']}
        ftypes={f['finding_type'] for f in findings}
        row['lobar_ich']=any(f['finding_type']=='ICH' and macro_location(f.get('location') or '')['macro_location']=='LOBAR' for f in findings) or any(int(b.get('lobar_ich') or 0)>0 for b in g['boston'])
        row['cmb']=True if 'CMB' in ftypes else None
        row['lobar_cmb']=True if any(f['finding_type']=='CMB' and macro_location(f.get('location') or '')['macro_location']=='LOBAR' for f in findings) or any(int(b.get('lobar_cmb') or 0)>0 for b in g['boston']) else None
        row['deep_cmb']=True if any(f['finding_type']=='CMB' and macro_location(f.get('location') or '')['macro_location']=='DEEP' for f in findings) or any(b.get('deep_cmb') is True for b in g['boston']) else None
        if row['lobar_cmb'] is True or row['deep_cmb'] is True:row['cmb']=True
        row['css']=True if 'CSS' in ftypes else False if 'NO_CSS' in negatives else None
        row['csah']=True if 'CONVEXITY_SUBARACHNOID_HEMORRHAGE' in types or 'CSAH' in ftypes else None
        # Combined Boston cSS/cSAH foci and generic SAH do not identify cSAH.
        scans=assemblies[pid]['scans'];episodes=assemblies[pid]['episodes']
        clinical_ich_docs={str(d) for e in g['events'] if e['event_type']=='INTRACEREBRAL_HEMORRHAGE' for d in e.get('source_document_ids',[])}
        row['spontaneous_ich']=True if any(episodes.get('DOC-'+d,{}).get('spontaneous_ich') is True for d in clinical_ich_docs) else None
        if row['lobar_ich'] and not row['any_ich']:
            audits['holds'].append(dict(patient_id=pid,code='CLINICAL_ICH_HISTORY_VS_RADIOLOGY_MARKER_DISCORDANCE',
                clinical_ich_history=row['any_ich'],source_lobar_ich_marker=row['lobar_ich'],action='SOURCE_STREAMS_RETAINED_SEPARATELY_NO_RECLASSIFICATION'))
        confirmed={e['id']:e for e in g['edinburgh'] if e.get('id') and iso(e.get('event_date'))}
        days={e['event_date'] for e in confirmed.values()}
        row['number_of_confirmed_dated_ich_events']=len(days);row['multiple_ich']=len(days)>=2
        for e in confirmed.values():audits['ich'].append(dict(patient_id=pid,event_id=e['id'],event_date=e['event_date'],ct_ids=e.get('ct_ids',[])))
        for family in ('MRI','CT','CTA'):
            ims=[i for i in p['imaging'] if i.get('performed') is True and i.get('scope')=='BRAIN' and i['modality']==family]
            count,lower,audit=exam_inventory(pid,ims,family,'IMAGING');audits['imaging']+=audit
            row['number_of_brain_'+family]=count;row[family.lower()+'_exam_days_lower_bound']=lower
            row['any_'+family.lower()]=bool(ims)
        sequence=[mri_technique(i['raw_report_text']) for i in p['imaging'] if i['modality']=='MRI']
        row['any_swi_gre']=True if any(s['blood_sensitive_sequence_present'] is True for s in sequence) else False if not sequence else None
        eegs=[e for e in p['eeg'] if e.get('eeg_status')=='performed']
        row['any_eeg']=bool(eegs)
        row['number_of_eegs'],row['eeg_exam_days_lower_bound'],ea=exam_inventory(pid,eegs,'EEG','EEG');audits['eeg']+=ea
        for target,field in [('epileptiform_activity','eeg_epileptiform_discharges'),('focal_slowing','eeg_focal_slowing')]:
            vals=[e.get(field) for e in eegs];row[target]=True if True in vals else False if vals and all(v is False for v in vals) else None
        primary={e.get('eeg_primary_finding') for e in eegs}
        row['abnormal_eeg']=True if primary-{'normal',None,'unknown'} else False if primary=={'normal'} else None
        row['normal_eeg']=True if 'normal' in primary else None
        row['generalized_slowing']=True if 'generalized_slowing' in primary or any(x.get('value') is True for e in eegs for x in e.get('evidence',{}).get('generalized_slowing',[])) else None
        af=[d for d in g['diseases'] if d['canonical_name']=='ATRIAL_FIBRILLATION']
        aftypes={d.get('af_type') for d in af}
        for t in ('PAROXYSMAL','PERSISTENT','LONG_STANDING_PERSISTENT','PERMANENT','UNSPECIFIED'):
            row['af_'+t.lower()]=t in aftypes if af else None
        # A source medication list is available only when exported discharge
        # rows exist for that exact encounter; an absent list is not no therapy.
        docgroups=[]
        for encounter in encounters:
            meds=[m for m in g['meds'] if str(m['source_document_id']) in {str(d) for d in encounter['source_document_ids']} and m['date']==encounter['discharge_date']]
            exposure=medication_exposure(meds,bool(meds) and not encounter['discharge_conflict'])
            docgroups.append(exposure)
            audits['medications'].append(dict(patient_id=pid,admission_date=encounter['admission_date'],discharge_date=encounter['discharge_date'],
                source_document_ids=encounter['source_document_ids'],list_available=bool(meds),**exposure))
        first_exposure=docgroups[0] if docgroups else medication_exposure([],False)
        for key,value in first_exposure.items():row['first_discharge_'+key]=value
        row['first_discharge_medication_list_available']=bool(docgroups and first_exposure['any_antithrombotic'] is not None)
        for k in EXPOSURES:
            vals=[e[k] for e in docgroups if e[k] is not None]
            # Ever means across available discharge lists, not complete lifetime exposure.
            row['ever_discharge_'+k]=True if True in vals else False if vals and all(e[k] is not None for e in docgroups if e['any_antithrombotic'] is not None) else None
        death=[c for c in g['candidates'] if re.match(r'^\s*Exitus\s+letalis\b',c['raw_candidate'],re.I)]
        death_dates=[proof['death_date'] for proof in death_proofs[pid]]
        audits['deaths']+=death_proofs[pid]
        for c in death:
            match=re.search(r'\bExitus\s+letalis\s+(?:am\s+)?(\d{1,2})\.(\d{1,2})\.(\d{4})\b',c['raw_candidate'],re.I)
            if match:
                try:
                    day=date(int(match[3]),int(match[2]),int(match[1])).isoformat();death_dates.append(day)
                    audits['deaths'].append(dict(patient_id=pid,death_date=day,source_record_id=c['record_id'],source_ids=c['source_ids'],
                        evidence=match[0],basis='EXPLICIT_DEATH_DATE_IN_RETAINED_CANDIDATE'))
                except ValueError:pass
        death_date=consensus(death_dates)
        if len(set(death_dates))>1:audits['holds'].append(dict(patient_id=pid,code='EXPLICIT_DEATH_DATE_DISAGREEMENT',dates=sorted(set(death_dates))))
        row.update(death_documented=bool(death or death_proofs[pid]),death_date=death_date,age_at_death=exact_age(dob,death_date),
            death_during_hospitalization=any(e['admission_date']<=death_date<=e['discharge_date'] for e in encounters if e['discharge_date']) if death_date else None if death or death_proofs[pid] else False,
            death_source_record_ids=[c['record_id'] for c in death])
        # Existing deterministic PET canonicalizer may annotate the retained
        # report in this separate analysis, with exact source quotations.
        # This never changes a source artifact or the frozen CAA classification.
        pets=[];pet_results=[]
        for e in p['other_exams']:
            if 'pet' not in e['kind']:continue
            explicit_brain=e.get('location')=='brain' or ('amyloid' in e['kind'] and bool(re.search(r'kortikal|cortical|cerebral|zerebral|Hirn|Gehirn',e['report'],re.I)))
            if not explicit_brain:
                audits['pet'].append(dict(patient_id=pid,source_id=e['id'],date=e.get('date'),type=e['kind'],included=False,reason='NO_VALIDATED_CEREBRAL_SCOPE'));continue
            kind='AMYLOID_PET' if 'amyloid' in e['kind'] else 'FDG_PET' if 'fdg' in e['kind'] else 'OTHER_PET'
            pets.append(e)
            normalized=normalize_pet(e['kind'],e['report'])
            result=e.get('amyloid_pet_result') if kind=='AMYLOID_PET' else e.get('fdg_pet_result')
            result=result or normalized.get('amyloid_pet_result' if kind=='AMYLOID_PET' else 'fdg_pet_result')
            pattern=e.get('fdg_pet_pattern') or normalized.get('fdg_pet_pattern')
            pet_results.append((kind,result,pattern))
            audits['pet'].append(dict(patient_id=pid,source_id=e['id'],source_document_id=e['source_document_id'],date=e.get('date'),type=kind,included=True,
                result=result,pattern=pattern,reason='EXISTING_SCOPE_OR_EXPLICIT_CEREBRAL_AMYLOID_REPORT',
                normalization_basis='EXISTING_CANONICAL_PET_RULES_ON_FROZEN_REPORT_ANALYSIS_ONLY',
                classification_evidence=normalized['classification_evidence'],rule_version=normalized['rule_version'],
                brain_component_present=normalized['brain_component_present']))
        row['any_pet']=bool(pets);row['amyloid_pet']=any('amyloid' in e['kind'] for e in pets);row['fdg_pet']=any('fdg' in e['kind'] for e in pets)
        row['number_of_cerebral_pet'],row['pet_exam_days_lower_bound'],_=exam_inventory(pid,pets,'PET','PET')
        amyloid_results=[v for k,v,_ in pet_results if k=='AMYLOID_PET'];fdg_results=[v for k,v,_ in pet_results if k=='FDG_PET']
        def assessed_any(values,positive,negative):return True if positive in values else False if values and all(v==negative for v in values) else None
        row['amyloid_pet_positive']=assessed_any(amyloid_results,'POSITIVE','NEGATIVE')
        row['amyloid_pet_negative']=assessed_any(amyloid_results,'NEGATIVE','POSITIVE')
        row['fdg_pet_abnormal']=assessed_any(fdg_results,'ABNORMAL','NORMAL')
        row['amyloid_pet_result_enums']=sorted(set(amyloid_results)-{None})
        row['fdg_pet_result_enums']=sorted(set(fdg_results)-{None})
        row['fdg_pet_pattern_enums']=sorted({pat for k,_,pat in pet_results if k=='FDG_PET' and pat})
        assays=defaultdict(list)
        for csf in g['csf']:assays[csf['canonical_analyte']].append(csf)
        row['csf_amyloid_available']=any('ABETA' in k for k in assays)
        row['csf_total_tau_available']='CSF_TOTAL_TAU' in assays
        row['csf_ptau_available']=any(k.startswith('CSF_PTAU') for k in assays)
        for assay in CSF_ANALYTES:
            row[assay+'_value']=None;row[assay+'_unit']=None;row[assay+'_abnormal']=None
        for assay,items in assays.items():
            dated=sorted((c for c in items if iso(c.get('date'))),key=lambda c:c['date'])
            candidates=[c for c in dated if c['date']==dated[0]['date']] if dated else items
            values={str(c['value']) for c in candidates};units={c.get('unit') for c in candidates}
            valid=len(values)==1 and len(units)==1
            chosen=candidates[0] if valid else None
            row[assay+'_value']=float(chosen['value']) if chosen else None
            row[assay+'_unit']=chosen.get('unit') if chosen else None
            flags={c.get('research_abnormality') for c in candidates}
            abnormal=True if valid and flags and flags<={'LOW','HIGH'} else False if valid and flags=={'NORMAL'} else None
            row[assay+'_abnormal']=abnormal
            audits['csf'].append(dict(patient_id=pid,analyte=assay,value=row[assay+'_value'],unit=row[assay+'_unit'],
                selection='EARLIEST_SUPPORTED_ADMISSION_MEASUREMENT' if dated else 'SINGLE_CONSISTENT_UNDATED_VALUE' if valid else 'UNRESOLVED_CONFLICT',
                selected_admission_date=dated[0]['date'] if dated else None,abnormal=abnormal,
                laboratory_references=sorted({c.get('reference_text') or '' for c in candidates}),
                source_document_ids=sorted({c['source_document_id'] for c in items}),original_values=sorted({str(c['value']) for c in items}),
                original_isoforms=sorted({c['canonical_analyte'] for c in items})))
        for k in ('clinician_diagnosed','formal_probable','formal_possible','pathology','computed_probable','computed_possible','edinburgh_positive','suspicion_only','caa_negative','caa_indeterminate'):
            row[k]=summary[k]
        sd=iso(summary.get('first_source_suspicion_date'));dd=iso(summary.get('first_explicit_source_diagnosis_date'))
        row['suspicion_preceding_diagnosis']=sd<dd if sd and dd else None
        row['any_tfne']=True if any(e.get('presentation')=='TFNE' for e in episodes.values()) else None
        rows.append(row)
    return rows,audits,baseline
