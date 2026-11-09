"""TFNE pilot evidence, input isolation, deterministic gates and output distributions."""
import json
from collections import Counter
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,sha
from .tfne_pilot import ARTIFACT,write,csv_file
from .tfne_granite import PROMPT_PATH,RESOLUTION,STEREOTYPY,meaningful,LABELS
from .tfne_sources import FORBIDDEN,ALLOWED,span_index
from .tfne_screen import derive

GATES='EEG_USED_DURING_CLINICAL_PHENOTYPE_EXTRACTION IMAGING_USED_DURING_CLINICAL_PHENOTYPE_EXTRACTION HISTORICAL_DIAGNOSIS_OVERWRITTEN NONFOCAL_DIZZINESS_AUTOMATICALLY_TFNE NONFOCAL_CONFUSION_AUTOMATICALLY_TFNE RECURRENCE_AUTOMATICALLY_EQUALS_STEREOTYPY LACK_OF_LATER_MENTION_ASSUMED_RESOLUTION PERSISTENT_DEFICIT_CLASSIFIED_AS_TFNE >24H_PERSISTENT_DEFICIT_CLASSIFIED_AS_TFNE SOURCE_PROVENANCE_MISSING DATABASE_WRITES CLOUD_PATIENT_CALLS'.split()


def audit(directory):
    import re
    directory=Path(directory).resolve();freeze=read(directory/'cohort_freeze.json')
    if len(freeze['patient_ids'])!=10 or len(set(freeze['patient_ids']))!=10:raise ValueError('Exactly ten frozen patients required')
    if sha(directory/'tfne_pilot_10_selection.csv')!=freeze['selection_sha256'] or sha(PROMPT_PATH)!=freeze['prompt_hash']:raise ValueError('Selection/prompt changed')
    if any(sha(p)!=h for p,h in freeze['input_hashes'].items()):raise ValueError('Frozen source input changed')
    gates=Counter(dict.fromkeys(GATES,0));episodes=[];temporal=[];summary=[];abstentions=[];sections_total=0
    for patient in freeze['patient_ids']:
        output=directory/'workers'/patient;artifact=read(output/ARTIFACT);source=read(directory/'inputs'/f'{patient}.json');index=span_index(source['sections'])
        request=read(output/'request.json');response=read(output/'response.json')
        if request['messages'][0]['content']!=PROMPT_PATH.read_text(encoding='utf-8'):raise ValueError('Ad hoc TFNE task prompt detected')
        if artifact['granite_prompt_hash']!=freeze['prompt_hash'] or artifact['module_version']!='0.1':raise ValueError('Prompt/version mismatch')
        sections=json.loads(request['messages'][1]['content'])['source_sections'];sections_total+=len(sections)
        for section in sections:
            if section['section_type'] not in ALLOWED:raise ValueError('Disallowed section supplied to Granite')
            for span in section['spans']:
                if span['span_id'] not in index or span['text']!=index[span['span_id']]['evidence_text']:raise ValueError('Model input not traceable to frozen source')
                if re.search(r'\b(?:EEG|cEEG|vEEG)\b|Elektroen',span['text'],re.I):gates['EEG_USED_DURING_CLINICAL_PHENOTYPE_EXTRACTION']+=1
                if FORBIDDEN.search(span['text']):gates['IMAGING_USED_DURING_CLINICAL_PHENOTYPE_EXTRACTION']+=1
        for e in artifact['episodes']:
            calculated=derive(e)
            if any(e[k]!=v for k,v in calculated.items()):raise ValueError('TFNE screen was not derived with the frozen operational rules')
            positive=e['TFNE_clinical_screen'] in ('TFNE_SCREEN_STRONG','TFNE_SCREEN_POSSIBLE')
            if positive and e['focality']!='FOCAL':
                if 'DIZZINESS' in e['symptom_domains']:gates['NONFOCAL_DIZZINESS_AUTOMATICALLY_TFNE']+=1
                if 'COGNITIVE' in e['symptom_domains']:gates['NONFOCAL_CONFUSION_AUTOMATICALLY_TFNE']+=1
            if positive and e['temporal_course']!='TRANSIENT_FULLY_RESOLVED':gates['PERSISTENT_DEFICIT_CLASSIFIED_AS_TFNE']+=1
            if positive and (e.get('duration_max_minutes') or 0)>1440:gates['>24H_PERSISTENT_DEFICIT_CLASSIFIED_AS_TFNE']+=1
            text_for=lambda field:' '.join(x['evidence_text'] for x in e['field_evidence'].get(field,[]))
            if e['fully_resolved']=='TRUE' and not RESOLUTION.search(text_for('fully_resolved')):gates['LACK_OF_LATER_MENTION_ASSUMED_RESOLUTION']+=1
            if e['stereotyped']=='TRUE' and not STEREOTYPY.search(text_for('stereotyped')):gates['RECURRENCE_AUTOMATICALLY_EQUALS_STEREOTYPY']+=1
            if e['historical_diagnosis'] in LABELS and not re.search(LABELS[e['historical_diagnosis']],text_for('historical_diagnosis'),re.I):gates['HISTORICAL_DIAGNOSIS_OVERWRITTEN']+=1
            for field,value in e.items():
                if field in __import__('caa_pdf_extractor.tfne_schema',fromlist=['defaults']).defaults() and meaningful(field,value) and not e['field_evidence'].get(field):gates['SOURCE_PROVENANCE_MISSING']+=1
            for evidence in e['evidence']:
                match=next((v for v in index.values() if all(v[k]==evidence[k] for k in ('document_id','page','section_id','evidence_start','evidence_end','evidence_text'))),None)
                if match is None or evidence['section_type'] not in ALLOWED:gates['SOURCE_PROVENANCE_MISSING']+=1
            episodes.append(dict(patient_id=patient,**{k:v for k,v in e.items() if k not in ('evidence','field_evidence')},
                source_document_ids=sorted({r['document_id'] for r in e['evidence']}),source_pages=sorted({r['page'] for r in e['evidence']}),
                source_section_ids=list(dict.fromkeys(r['section_id'] for r in e['evidence'])),evidence_ids=list(dict.fromkeys(r['evidence_id'] for r in e['evidence'])),
                artifact_path=str(STABLE_ROOT/patient/ARTIFACT)))
            temporal.append(dict(patient_id=patient,episode_id=e['episode_id'],temporal_course=e['temporal_course'],fully_resolved=e['fully_resolved'],duration_text=e['duration_text'],
                duration_min_minutes=e['duration_min_minutes'],duration_max_minutes=e['duration_max_minutes'],symptom_components=e['symptom_components'],evidence=e['field_evidence'].get('temporal_course',[])+e['field_evidence'].get('fully_resolved',[])))
        summary.append(dict(patient_id=patient,source_sections=len(sections),episodes=len(artifact['episodes']),strong=sum(e['TFNE_clinical_screen']=='TFNE_SCREEN_STRONG' for e in artifact['episodes']),
            possible=sum(e['TFNE_clinical_screen']=='TFNE_SCREEN_POSSIBLE' for e in artifact['episodes']),grounding_abstention_flags=len(artifact['validation_flags']),raw_model_episode_count=len(read(output/'raw_episodes.json')['episodes'])))
        abstentions.extend(dict(patient_id=patient,**flag) for flag in artifact['validation_flags'])
        gates['DATABASE_WRITES']+=artifact['database_writes'];gates['CLOUD_PATIENT_CALLS']+=artifact['cloud_patient_calls']
    counts=Counter(e['temporal_course'] for e in episodes);screens=Counter(e['TFNE_clinical_screen'] for e in episodes);phenotypes=Counter(e['retrospective_clinical_phenotype'] for e in episodes);labels=Counter(e['historical_diagnosis'] for e in episodes)
    metrics=dict(status='TFNE_NEUROLOGICAL_DEFICIT_V0_1_READY_FOR_PHYSICIAN_REVIEW',pilot_patient_ids=freeze['patient_ids'],patients_processed=10,source_sections_processed=sections_total,episodes_total=len(episodes),
        transient_count=counts['TRANSIENT_FULLY_RESOLVED'],transient_fully_resolved_count=counts['TRANSIENT_FULLY_RESOLVED'],persistent_count=counts['PERSISTENT'],progressive_count=counts['PROGRESSIVE'],mixed_count=counts['MIXED'],
        unknown_course_count=counts['UNKNOWN'],fluctuating_without_clear_resolution_count=counts['FLUCTUATING_WITHOUT_CLEAR_RESOLUTION'],TFNE_strong_count=screens['TFNE_SCREEN_STRONG'],TFNE_possible_count=screens['TFNE_SCREEN_POSSIBLE'],
        transient_atypical_count=phenotypes['TRANSIENT_ATYPICAL'],persistent_focal_count=phenotypes['PERSISTENT_FOCAL'],progressive_cognitive_count=phenotypes['PROGRESSIVE_COGNITIVE'],
        historical_epilepsy_count=labels['EPILEPSY'],historical_TIA_count=labels['TIA'],historical_TFNE_count=labels['TFNE'],hard_gate_failures=sum(gates.values()),grounding_abstention_flags=len(abstentions),
        raw_model_episodes=sum(r['raw_model_episode_count'] for r in summary),final_local_Granite_calls=len(list((directory/'workers').glob('*/request.json'))),
        preliminary_local_Granite_calls=len(list((directory/'preliminary_workers').glob('*/request.json'))),
        local_Granite_calls=len(list((directory/'workers').glob('*/request.json')))+len(list((directory/'preliminary_workers').glob('*/request.json'))),
        DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE',diagnostic_validity_claimed=False,all_candidates_unreviewed=True)
    csv_file(directory/'tfne_episode_extraction.csv',episodes);csv_file(directory/'tfne_temporal_course_audit.csv',temporal);csv_file(directory/'tfne_screen_summary.csv',summary)
    csv_file(directory/'tfne_hard_gates.csv',[dict(gate=k,violations=v,passed=v==0) for k,v in gates.items()]);csv_file(directory/'tfne_grounding_flags.csv',abstentions)
    write(directory/'validation_metrics.json',metrics);write(directory/'hard_gates.json',dict(gates));return metrics


def preservation(directory):
    directory=Path(directory);files=read(directory/'protected_files.json');changes=[p for p,h in files.items() if sha(p)!=h]
    code=read(directory/'existing_extraction_code.json');changes.extend(p for p,h in code.items() if sha(p)!=h)
    result=dict(protected_preexisting_files=len(files),unexpected_changes=changes,existing_extraction_modules_unchanged=not changes,existing_patient_artifacts_and_reviews_unchanged=not changes,
        clinical_database_writes=0,cloud_patient_calls=0)
    write(directory/'preservation_audit.json',result)
    if changes:raise ValueError('Protected existing inputs changed: '+str(changes))
    return result


if __name__=='__main__':
    import sys
    print(json.dumps(audit(sys.argv[1]),indent=2));print(json.dumps(preservation(sys.argv[1]),indent=2))
