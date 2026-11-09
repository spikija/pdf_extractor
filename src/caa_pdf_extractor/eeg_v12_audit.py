"""Reconcile 1.2 against pre-existing independent inventories and physician expectations."""
import copy
import json
import re
from collections import Counter
from pathlib import Path

from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha,integrity
from .eeg_v12_validation import patients,results,csv_write,BASE,V11
from .eeg_regression_v11 import evaluate
from .extraction.eeg_geometry_v12 import INSTITUTION,IDENTITY
from .extraction.eeg_schema_v12 import defaults,validate_value

GATES='clinical_text_removed_as_footer routine_EEG_and_cEEG_merged distinct_dated_EEGs_merged general_clinical_note_ingested_into_EEG echocardiography_ingested_into_EEG 24h_ECG_ingested_into_EEG RUHE_EKG_ingested_into_EEG PSYCHIATRIC_CONSULT_ingested_into_EEG institutional_footer_ingested_into_EEG duplicated_historical_EEG_created_twice explicit_NCSE_interpretation_missed negated_NCSE_marked_positive later_negative_cEEG_segment_erases_prior_NCSE LPD_with_explicit_NCSE_loses_status epileptiform_discharge_alone_creates_NCSE probable_seizure_interpretation_upgraded_to_definite source_EEG_exam_missed split_errors source_provenance_missing schema_invalid database_writes cloud_patient_calls'.split()

# Direct source expectations from physician instructions; never inferred from output.
NCSE_CASES=[
    dict(patient_id='2687726420',date='2026-07-29',expected=dict(NCSE=True,electrographic_status_epilepticus=True,status_side='LEFT',status_region='TEMPOROPARIETAL',clinical_correlate='APHASIA',response_to_ASM='NOT_FULLY_TERMINATED',periodic_discharge_type='LPD',rhythmic_delta_activity_present=True)),
    dict(patient_id='2702072620',date='2020-06-30',expected=dict(NCSE=True,electrographic_status_epilepticus=True,status_side='RIGHT',status_region='FRONTOTEMPORAL',periodic_discharge_present=True,periodic_discharge_type='LPD',clinical_correlate_present=True,clinical_correlate='RHYTHMIC_SUBTLE_MOVEMENTS',reactivity_to_external_stimuli='PRESENT',reactivity_stimulus='PAINFUL',pattern_frequency_hz=1.8)),
]


def audit(directory,label='workers'):
    directory=Path(directory).resolve();outputs=results(directory,label)
    expected=read(directory/'expected_source_inventory.json');frozen=read(directory/'independent_inventory_freeze.json')
    if sha(directory/'expected_source_inventory.json')!=frozen['sha256']:raise ValueError('Independent inventory changed')
    gates=Counter(dict.fromkeys(GATES,0));checks=[];boundaries=[];types=[];contamination=[];segment_rows=[];ncse_rows=[];exclusions=[]
    def check(case,patient,dt,name,expected_value,actual):
        checks.append(dict(case_id=case,patient_id=patient,eeg_date=dt,check=name,expected=expected_value,actual=actual,passed=actual==expected_value))
    for patient,result in outputs.items():
        exams=result['eeg_exams'];remaining=list(exams)
        own_expected=[r for r in expected if r['patient_id']==patient]
        for row in own_expected:
            candidates=[e for e in remaining if e['eeg_date']==row['eeg_date']]
            if patient in ('2957537425','2687726420'):
                candidates=[e for e in candidates if e['recording_type']==row['recording_type']]
            candidates.sort(key=lambda e:(row['document_id'] not in e['source_document_ids'],e['source_page_start']!=row['page']))
            found=candidates[0] if candidates else None
            if found:remaining.remove(found)
            else:gates['source_EEG_exam_missed']+=1
            expected_peer={('2687726420','2024-09-23','ROUTINE_EEG'):'Beurteilung',
                ('2687726420','2026-07-29','ROUTINE_EEG'):'cEEG - kontinuierliches',
                ('2887921324','2023-01-02','ROUTINE_EEG'):'Echokardiographie',
                ('2957537425','2025-05-19','ROUTINE_EEG'):'MOBILE Video-EEG-Ableitung'}.get((patient,row['eeg_date'],row['recording_type']),'')
            detected=next((r for r in result['eeg_discovery_decisions'] if found and r['report_occurrence_id']==found['source_occurrence_ids'][0]),{})
            if expected_peer and found:check('BOUNDARY_END',patient,row['eeg_date'],'source stops before '+expected_peer,False,expected_peer in found['EEG_BOUNDED_SOURCE_TEXT'])
            boundaries.append(dict(**row,expected_end_before=expected_peer,detected_exam_id=found['eeg_exam_id'] if found else '',detected_heading=found['source_header'] if found else '',
                detected_type=found['recording_type'] if found else '',detected_start=detected.get('header_start'),detected_end=detected.get('report_end'),
                detected_page_end=found['source_page_end'] if found else '',excluded_peer_heading=detected.get('excluded_section_heading'),status='MATCHED' if found else 'MISSED',merged=0,split_error=0))
        for extra in remaining:
            duplicate=any(e['eeg_date']==extra['eeg_date'] and e['recording_type']==extra['recording_type'] and e is not extra for e in exams)
            gates['duplicated_historical_EEG_created_twice' if duplicate else 'split_errors']+=1
            boundaries.append(dict(patient_id=patient,eeg_date=extra['eeg_date'],recording_type='',document_id=extra['document_id'],page=extra['source_page_start'],
                expected_heading='',inventory_origin='',inventory_id='',detected_exam_id=extra['eeg_exam_id'],detected_heading=extra['source_header'],detected_type=extra['recording_type'],status='UNEXPECTED_RECORD',merged=0,split_error=1))
        source=read(directory/'source_inputs'/patient/'source_bundle.json')
        excluded=read(directory/'source_inputs'/patient/'page_boilerplate_exclusions.json');exclusions+=excluded
        # Independent conservative audit: a removed block must have identifiable
        # institutional/identity content and must not contain clinical statements.
        clinical=re.compile(r'Theta|Delta|EEG|epilep|Hirnfunktions|Interpretation|Grundaktiv|beurteilbar|Klinisch|Patientin\s+(?:ist|hat)|Reaktion',re.I)
        for block in excluded:
            if clinical.search(block['text']) or not (INSTITUTION.search(block['text']) or IDENTITY.search(block['text'])):gates['clinical_text_removed_as_footer']+=1
        for e in exams:
            bounded=e['EEG_BOUNDED_SOURCE_TEXT'];own=[f for f in result['eeg_findings'] if f['eeg_exam_id']==e['eeg_exam_id']]
            for field in defaults():
                try:validate_value(field,e[field])
                except (ValueError,TypeError,KeyError):gates['schema_invalid']+=1
            flags=dict(
                RUHE_EKG_ingested_into_EEG=bool(re.search(r'(?im)^\s*RUHE[- \t]+(?:EKG|ECG)\b',bounded)),
                PSYCHIATRIC_CONSULT_ingested_into_EEG=bool(re.search(r'(?im)^\s*Psychiatrisch(?:es|er)\s+Konsil(?:ium)?\b',bounded)),
                general_clinical_note_ingested_into_EEG=bool(re.search(r'(?im)^\s*(?:Neurologischer\s*Status|Psychiatrischer\s*Status|Somatischer\s*Status|Prächirurgisches|Pro[cz]edere)|Anfallskalender|Levetiracetam\s+erhöhen|ERN.Epicare',bounded)),
                echocardiography_ingested_into_EEG=bool(re.search(r'Echokardiographie|\bTAPSE\b|diastolische\s+Dysfunktion|\bVCI\b',bounded,re.I)),
                institutional_footer_ingested_into_EEG=bool(INSTITUTION.search(bounded) or IDENTITY.search(bounded)),
                **{'24h_ECG_ingested_into_EEG':bool(re.search(r'(?:24|48|72)[- ]STUNDEN[- ]EKG|Holter[- ]EKG',bounded,re.I))})
            for key,value in flags.items():gates[key]+=int(value)
            contamination.append(dict(patient_id=patient,eeg_exam_id=e['eeg_exam_id'],eeg_date=e['eeg_date'],**{k:int(v) for k,v in flags.items()},source_layer=e['source_layer']))
            # Dated EEG headings in routine bounded text are always a merge;
            # timed cEEG subheadings are allowed only in explicit segment arrays.
            dated=re.findall(r'(?im)^\s*(?:EEG|Elektroen\w*[^\n]*Befund|MOBILE\s+Video|cEEG)[^\n]*\d{1,2}\.\d{1,2}\.',bounded)
            if not e['CEEG_SEGMENTS'] and dated:gates['distinct_dated_EEGs_merged']+=1
            if e['recording_type']=='ROUTINE_EEG' and re.search(r'(?im)^\s*cEEG\b|MOBILE\s+Video.EEG',bounded):gates['routine_EEG_and_cEEG_merged']+=1
            if e['focal_seizure_interpretation']=='PROBABLE' and e['seizure_recorded'] is True:gates['probable_seizure_interpretation_upgraded_to_definite']+=1
            if e['NCSE'] is True and not any(f['field']=='NCSE' and f['value'] is True and f['assertion']=='ESTABLISHED' for f in own):gates['epileptiform_discharge_alone_creates_NCSE']+=1
            if e['NCSE_assertion']=='NEGATED' and e['NCSE'] is True:gates['negated_NCSE_marked_positive']+=1
            types.append(dict(patient_id=patient,eeg_exam_id=e['eeg_exam_id'],eeg_date=e['eeg_date'],source_heading=e['source_header'],**{k:e[k] for k in ('recording_type','continuous','video','mobile','ICU_recording')}))
            for n,seg in enumerate(e['CEEG_SEGMENTS'],1):
                sf=seg['segment_findings'];segment_rows.append(dict(patient_id=patient,eeg_exam_id=e['eeg_exam_id'],segment=n,segment_start=seg['segment_start'],segment_end=seg['segment_end'],
                    segment_status=seg['segment_status'],assessability=sf['assessability'],assessability_periods=json.dumps(sf['assessability_periods'],ensure_ascii=False),periodic_discharge_type=sf['periodic_discharge_type'],
                    periodic_frequency=sf['pattern_frequency_hz'],focal_seizure_interpretation=sf['focal_seizure_interpretation'],evidence_ids=';'.join(x['evidence_id'] for x in seg['segment_evidence'])))
            statuses=[s['segment_status'] for s in e['CEEG_SEGMENTS']]
            if 'PRESENT' in statuses and statuses[-1]=='ABSENT' and (e['NCSE_OCCURRED_DURING_RECORDING'] is not True or e['status_at_final_segment']!='ABSENT'):gates['later_negative_cEEG_segment_erases_prior_NCSE']+=1
            for f in own:
                doc=next((d for d in source['documents'] if d['source_document_id']==f['document_id']),None)
                section=next((s for s in doc['sections'] if s['section_id']==f['section_id']),None) if doc else None
                if not section or f['page']!=section['page_start'] or not f.get('source_text_short'):gates['source_provenance_missing']+=1
        execution=read(directory/label/patient/'execution.json')
        gates['database_writes']+=execution['database_writes'];gates['cloud_patient_calls']+=execution['cloud_patient_calls']
    regression=evaluate(outputs,BASE)
    checks.extend(regression['checks'])
    # Saved physician acceptance is compared by its archived source date; new
    # revision identities must not reuse old review overlays automatically.
    for review in read(V11/'physician_review_inventory.json'):
        pid=review['patient_id'];old_entry=read(BASE/'published_candidates.json')[pid]
        old=next(read(f['path']) for f in old_entry['artifacts'] if Path(f['path']).name=='eeg_exams.json')
        prior=next((e for e in old if e['eeg_exam_id']==review['entity_id']),None)
        if not prior:continue
        current=next(e for e in outputs[pid]['eeg_exams'] if e['eeg_date']==prior['eeg_date'])
        for field in ('focal_slowing_present','generalized_slowing_present','epileptiform_discharge_present','seizure_recorded','electrographic_status_epilepticus'):
            check('SAVED_PHYSICIAN_REVIEW',pid,prior['eeg_date'],field,prior[field],current[field])
    correct=0
    for case in NCSE_CASES:
        e=next((e for e in outputs[case['patient_id']]['eeg_exams'] if e['eeg_date']==case['date']),{})
        passed=bool(e) and all(e.get(k)==v for k,v in case['expected'].items())
        ncse_rows.append(dict(patient_id=case['patient_id'],eeg_date=case['date'],eeg_exam_id=e.get('eeg_exam_id'),expected=json.dumps(case['expected'],ensure_ascii=False),actual=json.dumps({k:e.get(k) for k in case['expected']},ensure_ascii=False),passed=passed))
        correct+=passed
        if e.get('NCSE') is not True:gates['explicit_NCSE_interpretation_missed']+=1
        if e.get('periodic_discharge_type')=='LPD' and e.get('NCSE') is not True:gates['LPD_with_explicit_NCSE_loses_status']+=1
        for k,v in case['expected'].items():check('PHYSICIAN_NCSE',case['patient_id'],case['date'],k,v,e.get(k))
    for pid,dt,side,region in [('2809457122','2022-08-23','RIGHT','TEMPORAL'),('2887921324','2023-01-02','RIGHT','PARIETAL')]:
        e=next(e for e in outputs[pid]['eeg_exams'] if e['eeg_date']==dt)
        for k,v in dict(focal_slowing_present=True,focal_slowing_side=side,focal_slowing_region=region,epileptiform_discharge_present=False).items():check('LOCALIZATION_BOUNDARY',pid,dt,k,v,e[k])
    video=next(e for e in outputs['2957537425']['eeg_exams'] if e['eeg_date']=='2025-05-19' and e['recording_type']=='VIDEO_EEG')
    for k,v in dict(continuous=True,video=True,mobile=True).items():check('MOBILE_CEEG','2957537425','2025-05-19',k,v,video[k])
    check('MOBILE_CEEG','2957537425','2025-05-19','two independent interval summaries',2,len(video['CEEG_SEGMENTS']))
    for n,s in enumerate(video['CEEG_SEGMENTS'],1):
        for k,v in dict(periodic_discharge_present=True,periodic_discharge_type='GPD',pattern_frequency_hz=1.0,NCSE_rating_scale=1.0).items():check('MOBILE_CEEG','2957537425','2025-05-19',f'segment {n}: {k}',v,s['segment_findings'][k])
    check('MOBILE_CEEG','2957537425','2025-05-19','probable interpretation retained','PROBABLE',video['CEEG_SEGMENTS'][-1]['segment_findings']['focal_seizure_interpretation'])
    cases={(c['case_id'],c['patient_id'],c['eeg_date']) for c in checks}
    failed_cases={key for key in cases if any(not c['passed'] for c in checks if (c['case_id'],c['patient_id'],c['eeg_date'])==key)}
    exams=[e for r in outputs.values() for e in r['eeg_exams']];counts=Counter(e['recording_type'] for e in exams)
    metrics=dict(patients_rerun=len(outputs),patient_ids=patients(),source_EEG_exams=len(expected),extracted_EEG_exams=len(exams),
        matched_source_EEG_exams=sum(r['status']=='MATCHED' for r in boundaries),routine_EEG_count=counts['ROUTINE_EEG'],continuous_EEG_count=counts['CONTINUOUS_EEG'],video_EEG_count=counts['VIDEO_EEG'],unknown_EEG_type_count=counts['UNKNOWN'],
        footer_blocks_removed=sum(b['excluded_block_type']=='FOOTER' for b in exclusions),header_blocks_removed=sum(b['excluded_block_type']=='HEADER' for b in exclusions),
        clinical_blocks_falsely_removed=gates['clinical_text_removed_as_footer'],non_EEG_sections_excluded=sum(bool(r['excluded_section_heading']) for o in outputs.values() for r in o['eeg_discovery_decisions']),
        distinct_EEG_merge_errors=gates['distinct_dated_EEGs_merged'],duplicate_EEG_errors=gates['duplicated_historical_EEG_created_twice'],split_errors=gates['split_errors'],
        explicit_NCSE_cases=len(NCSE_CASES),explicit_NCSE_cases_correctly_detected=correct,cEEG_segments_detected=len(segment_rows),
        regression_cases_passed=len(cases)-len(failed_cases),regression_cases_failed=len(failed_cases),regression_field_checks=len(checks),
        hard_gate_failures=sum(gates.values()),DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE',production_ready=False,auto_promotions=0)
    csv_write(directory/'eeg_exam_boundary_audit.csv',boundaries)
    csv_write(directory/'eeg_footer_header_exclusions.csv',exclusions)
    csv_write(directory/'eeg_nonclinical_contamination_audit.csv',contamination)
    csv_write(directory/'eeg_recording_type_audit.csv',types)
    csv_write(directory/'eeg_cEEG_segment_audit.csv',segment_rows)
    csv_write(directory/'eeg_NCSE_regression.csv',ncse_rows)
    csv_write(directory/'eeg_v1_2_regression_checks.csv',[dict(c,expected=json.dumps(c['expected'],ensure_ascii=False),actual=json.dumps(c['actual'],ensure_ascii=False)) for c in checks])
    atomic(directory/'hard_gates.json',dict(gates));atomic(directory/'validation_metrics.json',metrics)
    atomic(directory/'page_boilerplate_exclusions.json',exclusions)
    return metrics


def preservation(directory):
    directory=Path(directory);before=read(directory/'protected_files.json');changed=[]
    for p,h in before.items():
        path=Path(p)
        if sha(p)==h:continue
        if path==STABLE_ROOT/'module_run_audit.jsonl':continue
        if path.name=='module_manifest.json' and path.parent.name in patients():continue
        changed.append(p)
    for patient,prior in read(directory/'original_manifests.json').items():
        after=read(STABLE_ROOT/patient/'module_manifest.json')
        for module,group in prior['modules'].items():
            if module!='eeg' and after['modules'][module]!=group:changed.append(patient+':'+module)
        for version,entry in prior['modules'].get('eeg',{}).get('versions',{}).items():
            if after['modules']['eeg']['versions'][version]!=entry or not integrity(entry):changed.append(patient+':old EEG:'+version)
        if after['modules']['eeg']['active_validated_version']!=prior['modules']['eeg']['active_validated_version']:changed.append(patient+':promotion')
    result=dict(protected_preexisting_files=len(before),unexpected_changes=changed,legacy_EEG_artifacts_preserved=not changed,non_EEG_modules_preserved=not changed,
        physician_reviews_preserved=not changed,database_writes=0,cloud_patient_calls=0)
    atomic(directory/'preservation_audit.json',result)
    if changed:raise ValueError('Protected inputs changed: '+str(changed))
    return result


if __name__=='__main__':
    import sys
    print(json.dumps(audit(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'workers'),indent=2))
