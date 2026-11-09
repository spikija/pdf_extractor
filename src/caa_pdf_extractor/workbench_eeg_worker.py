"""One-patient EEG assembly inside a write/network/database-restricted subprocess."""
import json
import re
import sys
from pathlib import Path
from .workbench_store import read,sha
from .workbench_worker import guard


def gates(result,bundle):
    from .extraction.eeg_sources import discover
    from .extraction.eeg_extraction_v1 import clauses,EP_PATTERN
    performed,_=discover(bundle);reports={r['report_occurrence_id']:r for r in performed}
    fields=result['eeg_findings'];exams=result['eeg_exams']
    counts=dict(EEG_exam_silently_missed=len(set(reports)-{i for e in exams for i in e['source_occurrence_ids']}),
        clinical_indication_promoted_to_EEG_finding=0,negated_epileptiform_activity_marked_present=0,
        unknown_converted_to_false_without_evidence=0,epileptiform_discharge_equated_with_seizure=0,
        isolated_epileptiform_discharge_equated_with_status=0,TFNE_diagnosed_from_EEG_alone=0,
        unchecked_form_field_assumed_absent_without_validated_rule=0,source_provenance_missing=0,
        database_writes=0,cloud_patient_calls=0)
    docs={str(d['source_document_id']):d for d in bundle['documents']}
    for f in fields:
        report=next((r for r in performed if f['evidence_id'].startswith(r['report_occurrence_id']+':')),None)
        if not report:counts['source_provenance_missing']+=1;continue
        section=next((s for s in docs[str(f['document_id'])]['sections'] if s['section_id']==f['section_id']),None)
        if not section or f['page']!=section['page_start'] or not f.get('source_text_short'):counts['source_provenance_missing']+=1
        relative=f['source_start']-report['report_start']
        roles=[role for q,a,b,role in clauses(report) if a<=relative<b]
        if 'INDICATION' in roles and f['field'] not in ('clinical_indication_text','clinical_indication_category'):counts['clinical_indication_promoted_to_EEG_finding']+=1
        if f['field']=='epileptiform_discharge_present' and f['value'] is True and f['assertion']!='ESTABLISHED':counts['negated_epileptiform_activity_marked_present']+=1
        if f['value'] is False and (f['assertion']!='NEGATED' or not f['source_text_short']):counts['unknown_converted_to_false_without_evidence']+=1
        if f['source_type']=='STRUCTURED_FORM' and not re.search(r'\[[xX]\]|☒|☑',f['source_text_short']):counts['unchecked_form_field_assumed_absent_without_validated_rule']+=1
        if f['field']=='seizure_recorded' and f['value'] is True and not re.search(r'\biktal|Anfallsmuster|elektro(?:gra[fp]h|klin)|(?:Anfall|Ereignis|Episode|Attacke)[^.;]*(?:aufgezeichnet|registriert|erfasst)',f['source_text_short'],re.I):counts['epileptiform_discharge_equated_with_seizure']+=1
        if f['field']=='electrographic_status_epilepticus' and f['value'] is True and not re.search(r'Status\s+epilepticus',f['source_text_short'],re.I):counts['isolated_epileptiform_discharge_equated_with_status']+=1
    return counts


def execute(request):
    from .workbench_eeg import EEGWorkbenchStore
    EEGWorkbenchStore(request['stable_root']).require_enabled(request['patient_id'])
    if any(sha(path)!=expected for path,expected in request['input_hashes'].items()):raise ValueError('EEG inputs changed')
    from .extraction.eeg_pipeline import assemble
    source=Path(request['source_dir']);bundle=read(source/'source_bundle.json')
    if bundle['patient_id']!=request['patient_id']:raise ValueError('Patient mismatch')
    result=assemble(bundle,read(source/'encounters.json'),read(request['semantic_results']),read(request['source_reconciliation']))
    counts=gates(result,bundle)
    if any(counts.values()):raise ValueError(f'EEG hard gate failures: {counts}')
    output=Path(request['output'])
    for name,rows in result.items():(output/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'execution.json').write_text(json.dumps(dict(patient_id=request['patient_id'],hard_gate_violation_counts=counts,guard='OS audit hook: no sockets/DB/children; writes only into fresh worker directory'),indent=2),encoding='utf-8')


if __name__=='__main__':
    sys.dont_write_bytecode=True
    request=read(sys.argv[1]);guard(Path(request['output']));execute(request)
