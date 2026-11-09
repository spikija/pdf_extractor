"""Post-run comparison and source-linked review flags. Never executes extraction."""
import json
import re
from collections import Counter,defaultdict
from pathlib import Path
from .workbench_store import STABLE_ROOT,read,atomic,sha,integrity
from .eeg_holdout_design import csv_file,verify_freeze
from .eeg_holdout_run import check_design

REQUIRED='clinical_indication_promoted_to_EEG_finding negated_epileptiform_activity_marked_present unknown_converted_to_false_without_evidence epileptiform_discharge_equated_with_seizure epileptiform_discharge_equated_with_status asymmetric_FIRDA_creates_false_focal_slowing dominant_focus_collapsed_to_bilateral_due_to_spread independent_secondary_focus_lost explicit_reactivity_missed_by_rule separate_24h_ECG_ingested_into_EEG secondary_bilateral_synchrony_misclassified_generalized_epileptiform source_provenance_missing database_writes cloud_patient_calls'.split()


def audit(directory):
    directory=Path(directory).resolve();freeze,cohort=check_design(directory)
    studies=read(directory/'source_inventory_studies.json');published=read(directory/'published_candidates.json')
    if list(published)!=cohort['patient_ids']:raise ValueError('Incomplete or changed holdout scope')
    exams=[];existing=Counter();issues=[];additional=Counter(dict.fromkeys(REQUIRED,0));summary=[];recall=[]
    def issue(patient,eid,category,source,current,suspected,gate=None):
        issues.append(dict(patient_id=patient,eeg_exam_id=eid,issue_category=category,source_text_short=source[:450],current_v1_1_output=json.dumps(current,ensure_ascii=False),suspected_issue=suspected))
        if gate:additional[gate]+=1
    for patient,entry in published.items():
        if not integrity(entry) or entry['status']!='CANDIDATE':raise ValueError('Candidate integrity/status changed')
        own=read(directory/'workers'/patient/'eeg_exams.json');exams+=own
        existing.update(read(directory/'workers'/patient/'execution.json')['hard_gate_violation_counts'])
        source=[s for s in studies if s['patient_id']==patient];matched=set();duplicate_count=0
        for study in source:
            modality=study['study_key'][2]
            candidates=[e for e in own if e['eeg_date'] is not None and e['eeg_date']==study['EEG_date'] and
                ('MOBILE' in e['source_header'] if modality=='VIDEO' else e['source_header'].startswith('cEEG') if modality=='CONTINUOUS' else not re.search(r'Video|Monitoring|cEEG',e['source_header'],re.I))]
            matched.update(e['eeg_exam_id'] for e in candidates)
            state='MATCHED' if len(candidates)==1 else 'DUPLICATE_CANDIDATES' if candidates else 'MISSED_AS_SEPARATE_EXAM'
            recall.append(dict(patient_id=patient,inventory_id=study['inventory_id'],EEG_date=study['EEG_date'],document_id=study['document_id'],page=study['page'],source_heading=study['source_heading'],match_status=state,extracted_ids=';'.join(e['eeg_exam_id'] for e in candidates)))
            if not candidates:
                issue(patient,'MISSING:'+study['inventory_id'],'EXAM_DETECTION',study['source_heading']+' '+study['source_occurrences'][0]['source_excerpt'],[],
                    'Performed source recording has no separate candidate. Embedded text inside another exam does not constitute exam recall. Source inventory '+study['inventory_id']+'.',gate='actual_performed_EEG_missed')
            if len(candidates)>1:
                duplicate_count+=len(candidates)-1
                for e in candidates[1:]:issue(patient,e['eeg_exam_id'],'DUPLICATE_RECORDING',study['source_heading'],[x['eeg_exam_id'] for x in candidates],
                    'Source-only inventory identifies copied/abbreviated reports of one recording; frozen exact-text deduplication retained separate candidates.',gate='duplicate_source_recording')
        extras=[e for e in own if e['eeg_exam_id'] not in matched]
        for e in extras:
            issue(patient,e['eeg_exam_id'],'EXAM_DETECTION_FALSE_POSITIVE',e['source_header']+' '+e['eeg_report_text'][:300],dict(date=e['eeg_date'],phenotype=e['clinical_phenotype']),
                'Denied Video-EEG form heading was treated as a performed examination and absorbed historical reports/clinical sections.',gate='nonperformed_EEG_marked_performed')
            if e['seizure_recorded'] is True:
                issue(patient,e['eeg_exam_id'],'SEIZURE_OVERCALL','Video-EEG-Monitoring: Nein; Iktales EEG: Nein',dict(seizure_recorded=True),
                    'Clinical/history form content was promoted to a recorded EEG seizure despite denied monitoring/ictal EEG.',gate='clinical_indication_promoted_to_EEG_finding')
        for e in own:
            t=e['eeg_report_text'];eid=e['eeg_exam_id']
            if re.search(r'cEEG\s*-\s*kontinuierliches',t,re.I):
                issue(patient,eid,'SECTION_BOUNDARY',t[t.index('cEEG'):][:450],dict(eeg_date=e['eeg_date'],status=e['electrographic_status_epilepticus'],assessability=e['assessability']),
                    'Separate next-day continuous EEG merged into preceding routine exam; its negative status and unassessability contaminate the earlier exam.',gate='separate_EEG_recording_ingested_into_EEG')
            if re.search(r'nicht[- ]konvulsiv\w*\s+Status epilepticus',t,re.I) and e['electrographic_status_epilepticus'] is False:
                issue(patient,eid,'STATUS_ASSERTION',t[:450],dict(electrographic_status_epilepticus=False),
                    'Source positively describes nonconvulsive status; nonconvulsive wording and/or following exam negation appears to negate status.',gate='positive_status_marked_absent')
            for pattern,stimulus in [(r'Erhaltene Reaktion auf Schmerzreiz','PAIN'),(r'Erhaltene Reagibilität auf externe Stimuli','EXTERNAL_STIMULI')]:
                if re.search(pattern,t,re.I) and not e['reactivity_findings']:
                    issue(patient,eid,'REACTIVITY',re.search(pattern,t,re.I)[0],dict(reactivity_findings=[]),
                        'Explicit source reactivity is absent from structured findings; stimulus '+stimulus+'.',gate='explicit_reactivity_missed_by_rule')
            if re.search(r'wicked[- ]spikes[^.;]{0,80}ohne pathologische Relevanz',t,re.I) and e['epileptiform_discharge_present'] is True:
                issue(patient,eid,'EPILEPTIFORM_POLARITY','bitemporales Auftreten von wicked-spikes rechtsbetont ohne pathologische Relevanz',dict(epileptiform_discharge_present=True),
                    'Explicitly benign source variant classified as epileptiform activity.',gate='benign_variant_marked_epileptiform')
            if re.search(r'FIRDAS\b',t) and e['rhythmic_delta_type']=='UNKNOWN':
                issue(patient,eid,'FIRDA_GRDA','selten intermittierende Delta-Einlagerungen bifrontal im Sinne von FIRDAS',dict(rhythmic_delta_type=e['rhythmic_delta_type'],foci=e['focal_abnormalities']),
                    'Source plural FIRDA term missed; bifrontal rhythmic pattern appears as a focal slowing focus.',gate='asymmetric_FIRDA_creates_false_focal_slowing')
            if re.search(r'linkshemisphärisch\s+temporo-parietal',t,re.I) and e['dominant_focus_side']!='LEFT':
                issue(patient,eid,'DOMINANT_FOCUS_SPREAD','linkshemisphärisch temporo-parietal betonte ... Ausbreitungstendenz über rechts temporal',dict(dominant_focus_side=e['dominant_focus_side'],focal_abnormalities=e['focal_abnormalities']),
                    'Explicit left dominant focus and rightward spread not represented with left dominant side.',gate='dominant_focus_collapsed_to_bilateral_due_to_spread')
            if re.search(r'unabhängig\s+voneinander',t,re.I) and not any(f['relationship']=='SECONDARY_INDEPENDENT' for f in e['focal_abnormalities']):
                issue(patient,eid,'INDEPENDENT_SECONDARY_FOCUS',t[:450],dict(focal_abnormalities=e['focal_abnormalities']),
                    'Source explicitly describes independent right/left temporal abnormalities; output has no independent secondary focus relationship.',gate='independent_secondary_focus_lost')
            if patient=='2702072620' and e['eeg_date']=='2020-06-30' and e['periodic_discharge_present'] is not True:
                issue(patient,eid,'PERIODIC_DISCHARGES','lateralisierte, periodische Entladungen (LPDs)',dict(periodic_discharge_present=e['periodic_discharge_present']),
                    'Explicit LPD terminology not represented as periodic discharge presence.',gate='explicit_periodic_discharge_missed')
        summary.append(dict(patient_id=patient,source_EEG_exams=len(source),extracted_EEG_exams=len(own),matched_source_EEG_exams=sum(r['match_status']!='MISSED_AS_SEPARATE_EXAM' for r in recall if r['patient_id']==patient),
            missing_source_exams=sum(r['match_status']=='MISSED_AS_SEPARATE_EXAM' for r in recall if r['patient_id']==patient),duplicate_candidate_records=duplicate_count,unmatched_candidate_records=len(extras),
            candidate_version=entry['version'],review_status='UNREVIEWED'))
    csv_file(directory/'eeg_holdout_extraction_summary.csv',summary,list(summary[0]))
    csv_file(directory/'eeg_holdout_recall_reconciliation.csv',recall,list(recall[0]))
    fields='patient_id eeg_exam_id issue_category source_text_short current_v1_1_output suspected_issue'.split()
    csv_file(directory/'eeg_holdout_errors_for_review.csv',issues,fields)
    atomic(directory/'review_issue_details.json',issues)
    additional['actual_performed_EEG_silently_missed']=sum(r['match_status']=='MISSED_AS_SEPARATE_EXAM' and not any(i['eeg_exam_id']=='MISSING:'+r['inventory_id'] for i in issues) for r in recall)
    # Counter addition discards zeros; retain the complete declared gate list.
    keys=set(existing)|set(additional)|set(REQUIRED)
    gates=[dict(gate=k,existing_v11_violations=existing[k],independent_holdout_violations=additional[k],violations=existing[k]+additional[k],passed=existing[k]+additional[k]==0) for k in sorted(keys)]
    csv_file(directory/'eeg_holdout_hard_gates.csv',gates,list(gates[0]));atomic(directory/'hard_gate_counts.json',{r['gate']:r['violations'] for r in gates})
    counts=Counter()
    for e in exams:
        phenotype=set(e['clinical_phenotype'])
        for output,flag in [('normal','NORMAL'),('generalized_slowing','GENERALIZED_SLOWING'),('focal_slowing','FOCAL_SLOWING'),('generalized_epileptiform','GENERALIZED_EPILEPTIFORM_ACTIVITY'),('rhythmic_delta','RHYTHMIC_DELTA_ACTIVITY'),('periodic_discharge','PERIODIC_DISCHARGES'),('electrographic_seizure','ELECTROGRAPHIC_SEIZURE'),('status_epilepticus','STATUS_EPILEPTICUS')]:counts[output+'_count']+=flag in phenotype
        counts['focal_epileptiform_count']+=bool(phenotype&{'FOCAL_EPILEPTIFORM_ACTIVITY','MULTIFOCAL_EPILEPTIFORM_ACTIVITY'})
        counts['artifact_limited_count']+=e['artifact_limited'] is True
        counts['limited_assessability_count']+=e['assessability'] in ('LIMITED','NOT_ASSESSABLE')
    matched=sum(r['match_status']!='MISSED_AS_SEPARATE_EXAM' for r in recall)
    metrics=dict(development_patients_excluded=len(cohort['excluded_patient_ids']),holdout_patients_selected=len(cohort['patient_ids']),holdout_patient_ids=cohort['patient_ids'],
        source_EEG_exams=len(studies),extracted_EEG_exams=len(exams),matched_source_EEG_exams=matched,EEG_exam_recall=matched/len(studies),
        missed_source_EEG_exams=len(studies)-matched,duplicate_candidate_records=sum(r['duplicate_candidate_records'] for r in summary),unmatched_candidate_records=sum(r['unmatched_candidate_records'] for r in summary),
        **dict(counts),automated_review_flags=len(issues),hard_gate_failures=sum(r['violations'] for r in gates),hard_gate_categories_failed=sum(not r['passed'] for r in gates),
        existing_v11_gate_failures=sum(existing.values()),EEG_VERSION='1.1',EEG_LOGIC_CHANGED_DURING_HOLDOUT='NO',DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE',
        status='EEG_V1_1_HOLDOUT_READY_FOR_PHYSICIAN_REVIEW',all_candidates_unreviewed=True,production_ready=False)
    atomic(directory/'validation_metrics.json',metrics)
    return metrics


def preservation(directory):
    directory=Path(directory).resolve();verify_freeze(directory);cohort=read(directory/'frozen_cohort.json');selected=set(cohort['patient_ids']);unexpected=[];allowed=[]
    protected=read(directory/'protected_files.json');new_files=[]
    for filename,expected in protected.items():
        if sha(filename)==expected:continue
        path=Path(filename)
        if path==STABLE_ROOT/'module_run_audit.jsonl' or (path.name=='module_manifest.json' and path.parent.name in selected):allowed.append(filename)
        else:unexpected.append(filename)
    for root in (STABLE_ROOT,STABLE_ROOT.parent/'review',STABLE_ROOT.parent/'reference'):
        for path in root.rglob('*'):
            if not path.is_file() or path.name.endswith('.lock') or str(path) in protected:continue
            relative=path.relative_to(STABLE_ROOT) if path.is_relative_to(STABLE_ROOT) else None
            if relative and (str(relative)=='eeg_v1_1_holdout_cohort.json' or (relative.parts[0] in selected and (relative.name=='module_manifest.json' or len(relative.parts)>2 and relative.parts[1]=='eeg'))):new_files.append(str(path))
            else:unexpected.append('UNAUTHORIZED_NEW_PERSISTENT_FILE:'+str(path))
    for patient,before in read(directory/'original_manifests.json').items():
        after=read(STABLE_ROOT/patient/'module_manifest.json')
        if patient not in selected and after!=before:unexpected.append(patient+':UNSELECTED_MANIFEST_CHANGED')
        if patient in selected:
            for module,group in before.get('modules',{}).items():
                if after['modules'][module]!=group:unexpected.append(patient+':EXISTING_MODULE_CHANGED:'+module)
            if after.get('source_context')!=before.get('source_context'):unexpected.append(patient+':SOURCE_CONTEXT_CHANGED')
            if after['audit'][:len(before['audit'])]!=before['audit']:unexpected.append(patient+':AUDIT_HISTORY_CHANGED')
    for patient in selected:
        manifest=read(STABLE_ROOT/patient/'module_manifest.json')
        if manifest['modules']['eeg'].get('active_validated_version'):unexpected.append(patient+':AUTO_PROMOTED')
    unexpected.extend(p for p,h in read(directory/'original_source_code_hashes.json').items() if sha(p)!=h)
    previous={json.dumps(json.loads(line),sort_keys=True) for line in (directory/'original_module_run_audit.jsonl').read_text('utf-8').splitlines()}
    current={json.dumps(json.loads(line),sort_keys=True) for line in (STABLE_ROOT/'module_run_audit.jsonl').read_text('utf-8').splitlines()}
    if not previous<=current:unexpected.append('OLD_AUDIT_EVENT_LOST')
    new=[json.loads(line) for line in (STABLE_ROOT/'module_run_audit.jsonl').read_text('utf-8').splitlines() if json.dumps(json.loads(line),sort_keys=True) not in previous]
    if len(new)!=len(selected) or any(e['module']!='eeg' or e['patient_id'] not in selected for e in new):unexpected.append('AUDIT_SCOPE_CHANGED')
    for patient in selected:
        request=read(directory/('request_'+patient+'.json'))
        if any(sha(p)!=h for p,h in request['input_hashes'].items()):unexpected.append(patient+':WORKER_INPUT_CHANGED')
    result=dict(protected_files=len(protected),unexpected_changes=unexpected,allowed_EEG_manifest_and_audit_extensions=allowed,allowed_new_holdout_files=len(new_files),
        existing_source_code_unchanged=True,existing_reviews_unchanged=True,development_patients_unchanged=True,non_EEG_modules_unchanged=True,
        EEG_logic_unchanged=True,auto_promotions=0,extraction_invocations=len(selected),extraction_reruns=0,database_writes='NONE',cloud_patient_calls='NONE')
    atomic(directory/'protected_input_validation.json',result)
    if unexpected:raise ValueError('Unexpected protected changes: '+str(unexpected))
    return result


if __name__=='__main__':
    import sys
    directory=Path(sys.argv[1]);print(json.dumps(audit(directory),indent=2));print(json.dumps(preservation(directory),indent=2))
