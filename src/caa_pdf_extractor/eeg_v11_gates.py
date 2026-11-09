"""EEG 1.1 gate counts over source-linked structures and physician regressions."""
import re
from collections import Counter
from .extraction.eeg_sources_v11 import discover,MODALITY,INLINE_OTHER_MODALITY
from .extraction.eeg_extraction_v11 import clauses

GATES='multi_region_localization_lost dominant_focus_collapsed_to_bilateral_due_to_spread independent_secondary_focus_lost spread_misclassified_as_independent_focus asymmetric_bilateral_pattern_loses_predominance asymmetric_FIRDA_creates_false_focal_slowing true_FIRDA_plus_focal_slowing_loses_either_pattern explicit_reactivity_missed limited_assessability_left_unknown NO_DEFINITE_EVIDENCE_converted_to_UNKNOWN modifier_scope_error explicit_sharp_wave_type_left_unknown clear_generalized_slowing_classified_OTHER separate_24h_ECG_ingested_into_EEG secondary_bilateral_synchrony_misclassified_generalized_epileptiform epileptiform_discharge_equated_with_seizure database_writes cloud_patient_calls'.split()


def gates(result,bundle):
    counts=Counter(dict.fromkeys(GATES,0))
    counts.update(source_provenance_missing=0,EEG_exam_silently_missed=0,clinical_indication_promoted_to_EEG_finding=0,unknown_converted_to_false_without_evidence=0)
    performed,_=discover(bundle);reports={r['report_occurrence_id']:r for r in performed}
    counts['EEG_exam_silently_missed']=len(set(reports)-{i for e in result['eeg_exams'] for i in e['source_occurrence_ids']})
    sources={str(d['source_document_id']):d for d in bundle['documents']}
    evidence={f['evidence_id']:f for f in result['eeg_findings']}
    for e in result['eeg_exams']:
        foci=e['focal_abnormalities'];main=[f for f in foci if f['relationship'] in ('PRIMARY','SECONDARY_INDEPENDENT')]
        for f in foci:
            text=f['source_text_short']
            if re.search(r'fronto[- ]*(?:zentro|centro)[- ]*temporal',text,re.I) and not {'FRONTAL','CENTRAL','TEMPORAL'}<=set(f['regions']):counts['multi_region_localization_lost']+=1
            if f['relationship']=='SECONDARY_INDEPENDENT' and re.search(r'Ausbreitung|breiten\s+sich|breitet\s+sich',text,re.I):counts['spread_misclassified_as_independent_focus']+=1
            if f['side']=='BILATERAL' and re.search(r'links\s*>\s*rechts|rechts\s*>\s*links|mehr\s+als\s+(?:links|rechts)',text,re.I) and f['asymmetry']=='UNKNOWN':counts['asymmetric_bilateral_pattern_loses_predominance']+=1
            if 'FIRDA' in text and re.search(r'generalisier',text,re.I) and f['relationship']!='SPREAD':counts['asymmetric_FIRDA_creates_false_focal_slowing']+=1
        if len({f['side'] for f in main if f['side']!='UNKNOWN'})==1 and main and main[0]['side'] in ('LEFT','RIGHT') and e['dominant_focus_side']=='BILATERAL':counts['dominant_focus_collapsed_to_bilateral_due_to_spread']+=1
        if MODALITY.search(e['eeg_report_text']) or INLINE_OTHER_MODALITY.search(e['eeg_report_text']):counts['separate_24h_ECG_ingested_into_EEG']+=1
        if e['epileptiform_distribution']=='GENERALIZED' and e['secondary_bilateral_synchrony'] is True:
            independent=any(f['field']=='epileptiform_distribution' and f['value']=='GENERALIZED' and re.search(r'generalisier',f['source_clause_text'],re.I) and not re.search(r'sekundär',f['source_clause_text'],re.I) for f in result['eeg_findings'] if f['eeg_exam_id']==e['eeg_exam_id'])
            if not independent:counts['secondary_bilateral_synchrony_misclassified_generalized_epileptiform']+=1
        own=[f for f in result['eeg_findings'] if f['eeg_exam_id']==e['eeg_exam_id']]
        for q in clauses(next(r for r in performed if r['report_occurrence_id']==e['source_occurrence_ids'][0])):
            if q.role=='INDICATION':continue
            text=q.text
            if re.match(r'(?:Im\s+)?(?:EKG|ECG)\b',text,re.I):continue
            if re.search(r'soweit[^.;]{0,100}beurteilbar|bei\s+stark\w*[^.;]{0,70}Artefakt',text,re.I) and e['assessability']=='UNKNOWN':counts['limited_assessability_left_unknown']+=1
            if re.search(r'keine?\w*\s+eindeutig\w*[^.;]{0,30}(?:epileptiform|epilepsietypisch)',text,re.I) and e['epileptiform_evidence_status']=='UNKNOWN':counts['NO_DEFINITE_EVIDENCE_converted_to_UNKNOWN']+=1
            if re.search(r'keine?\w*\s+eindeutig\w*[^.;]{0,30}iktal',text,re.I) and e['ictal_activity_status']=='UNKNOWN':counts['NO_DEFINITE_EVIDENCE_converted_to_UNKNOWN']+=1
            if re.search(r'fehlend\w*\s+Reagibilit\w*\s+auf\s+Ansprache',text,re.I) and not any(r['stimulus']=='VERBAL' and r['response']=='ABSENT' for r in e['reactivity_findings']):counts['explicit_reactivity_missed']+=1
            if re.search(r'erhalten\w*\s+Reaktion\s+auf\s+taktil',text,re.I) and not any(r['stimulus']=='TACTILE' and r['response']=='PRESENT' for r in e['reactivity_findings']):counts['explicit_reactivity_missed']+=1
            if re.search(r'broad\s+sharp[- ]waves?',text,re.I) and e['epileptiform_discharge_type']=='UNKNOWN':counts['explicit_sharp_wave_type_left_unknown']+=1
            if re.search(r'unabhängig\s+(?:davon|hiervon)',text,re.I) and re.search(r'Theta|Delta|sharp|lokal\w*\s+Funktions',text,re.I) and not re.search(r'fraglich|möglich|nicht\s+unabhängig',text,re.I):
                if not any(f['relationship']=='SECONDARY_INDEPENDENT' for f in foci):counts['independent_secondary_focus_lost']+=1
        if e['background_continuity']!='UNKNOWN':
            supporting=[f for f in own if f['field']=='background_continuity']
            if not supporting:counts['modifier_scope_error']+=1
    for finding in result['eeg_findings']:
        doc=sources.get(str(finding['document_id']))
        section=next((s for s in doc['sections'] if s['section_id']==finding['section_id']),None) if doc else None
        if not section or section['page_start']!=finding['page'] or not finding.get('source_text_short'):counts['source_provenance_missing']+=1
        report=next((r for key,r in reports.items() if finding['evidence_id'].startswith(key+':')),None)
        if report:
            relative=finding['source_start']-report['report_start']
            roles=[q.role for q in clauses(report) if q.positions and q.positions[0]<=relative<=q.positions[-1]]
            if 'INDICATION' in roles and finding['field'] not in ('clinical_indication_text','clinical_indication_category'):counts['clinical_indication_promoted_to_EEG_finding']+=1
        if finding['source_type']!='DERIVED' and finding['value'] is False and finding['assertion']!='NEGATED':
            if not (finding['field']=='epileptiform_confirmed' and re.search(r'kein|ohne',finding['source_text_short'],re.I)):counts['unknown_converted_to_false_without_evidence']+=1
        if finding['field']=='seizure_recorded' and finding['value'] is True and not re.search(r'\biktal|Anfallsmuster|elektro(?:gra[fp]h|klin)|(?:Anfall|Ereignis|Episode|Attacke)[^.;]*(?:aufgezeichnet|registriert|erfasst)',finding['source_text_short'],re.I):counts['epileptiform_discharge_equated_with_seizure']+=1
        for obj in finding['value'] if finding['field'] in ('focal_abnormalities','slow_patterns','reactivity_findings') else []:
            if any(eid not in evidence for eid in obj['evidence_ids']):counts['source_provenance_missing']+=1
    return dict(counts)


def physician_gate_counts(regressions):
    counts=dict.fromkeys(GATES,0)
    mapping={'A':['multi_region_localization_lost','explicit_reactivity_missed','limited_assessability_left_unknown','NO_DEFINITE_EVIDENCE_converted_to_UNKNOWN'],
      'B':['asymmetric_FIRDA_creates_false_focal_slowing'],'C':['true_FIRDA_plus_focal_slowing_loses_either_pattern'],
      'D':['dominant_focus_collapsed_to_bilateral_due_to_spread','spread_misclassified_as_independent_focus'],
      'E':['independent_secondary_focus_lost','modifier_scope_error'],'F':['clear_generalized_slowing_classified_OTHER'],
      'G':['asymmetric_bilateral_pattern_loses_predominance'],'H':['explicit_sharp_wave_type_left_unknown','multi_region_localization_lost'],
      'I':['separate_24h_ECG_ingested_into_EEG','secondary_bilateral_synchrony_misclassified_generalized_epileptiform']}
    for check in regressions['checks']:
        if not check['passed']:
            for gate in mapping.get(check['case_id'],[]):counts[gate]+=1
    return counts
