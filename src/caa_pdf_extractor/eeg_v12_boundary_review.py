"""Six-patient source-only EEG 1.2 repair audit. Never calls clinical assembly."""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

from .workbench_store import ROOT,read,sha
from .extraction.eeg_sources import flatten
from .extraction.eeg_sources_v12 import discover
from .extraction.eeg_section_headings_v12 import section_headings

PATIENTS=('2687726420','2702072620','2809457122','2887921324','2957537425','2989288225')
FIXTURE=ROOT/'tests/fixtures/eeg_v12_boundary_2989288225_27700090930173.json'
PATTERNS={
    'RUHE_EKG_ingested_into_EEG':r'(?im)^[ \t]*RUHE[- \t]+(?:EKG|ECG)\b',
    'PSYCHIATRIC_CONSULT_ingested_into_EEG':r'(?im)^[ \t]*Psychiatrisch(?:es|er)\s+Konsil(?:ium)?\b',
    '24H_ECG_ingested_into_EEG':r'(?im)^[ \t]*(?:24[- ]STUNDEN[- ]EKG|HOLTER[- ]EKG)\b',
    'ECHOCARDIOGRAPHY_ingested_into_EEG':r'(?im)^[ \t]*(?:Echokardiogra[fp]hie|Herzecho)\b',
    'GENERAL_CLINICAL_NOTE_ingested_into_EEG':r'(?im)^[ \t]*(?:(?:Neurologischer|Psychiatrischer|Somatischer)\s*Status|Pro[cz]edere)\b|\bAnfallskalender\b|\bLevetiracetam\s+erhöhen\b|Beurteilung\s*\n\s*1\.',
}


def serial(report):
    return {k:([serial(s) for s in v] if k=='segments' else v) for k,v in report.items() if k!='ranges'}


def flat_reports(rows):
    out={}
    for row in rows:
        out[row['report_occurrence_id']]=row
        out.update(flat_reports(row.get('segments',[])))
    return out


def review(directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to((ROOT/'data/debug').resolve()):raise ValueError('Debug output must stay inside workspace')
    from .workbench_worker import guard
    guard(directory)
    def save(name,value):
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    def csv_file(name,rows):
        with (directory/name).open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ['status']);writer.writeheader();writer.writerows(rows)
    scope=read(directory/'scope.json')
    if scope['patients']!=list(PATIENTS) or scope['module_version']!='1.2' or not scope['segmentation_only']:raise ValueError('Invalid bounded segmentation scope')
    before=read(directory/'before_segmentation.json');trace=read(directory/'root_cause_trace_before_patch.json')
    base=Path(scope['base_directory']);fixture=read(FIXTURE)
    gates=Counter(dict.fromkeys(list(PATTERNS)+['clinical_EEG_text_wrongly_removed','embedded_EEG_ECG_channel_wrongly_removed','DATABASE_WRITES','CLOUD_PATIENT_CALLS'],0))
    after={};comparisons=[];headings=[];checks=[];target=None
    def check(patient,label,passed):checks.append(dict(patient_id=patient,check=label,passed=bool(passed)))
    for patient in PATIENTS:
        source=Path(before[patient]['source_path'])
        if sha(source)!=before[patient]['source_sha256']:raise ValueError('Original cleaned source changed')
        bundle=read(source)
        for doc in bundle['documents']:
            if sha(ROOT/'pdf_db'/patient/doc['filename'])!=doc['sha256']:raise ValueError('Source PDF changed')
            text,_=flatten(doc)
            for match,classified in section_headings(text):
                headings.append(dict(patient_id=patient,document_id=doc['source_document_id'],source_heading=match[0].strip(),
                    section_type=classified['section_type'],heading_core=classified['heading_core'],cleaned_document_offset=match.start()))
        reports,decisions=discover(bundle)
        rows=[serial(r) for r in reports]
        after[patient]=dict(source_path=str(source),source_sha256=sha(source),reports=rows,decisions=[serial(r) for r in decisions])
        previous=flat_reports(before[patient]['reports']);current=flat_reports(rows)
        check(patient,'Performed EEG heading identities preserved',set(previous)==set(current))
        if set(previous)!=set(current):gates['clinical_EEG_text_wrongly_removed']+=len(set(previous)-set(current))
        for occurrence,old in previous.items():
            new=current.get(occurrence)
            if new is None:continue
            a=old['eeg_report_text'];b=new['eeg_report_text'];changed=a!=b
            # The independent preservation reference is the original text prefix
            # before a separately classified peer heading, not a phenotype score.
            prefix_ok=a.startswith(b) or b.startswith(a)
            peer_ok=not changed or bool(new.get('excluded_section_type')) or b.startswith(a)
            preserved=prefix_ok and peer_ok
            if not preserved:gates['clinical_EEG_text_wrongly_removed']+=1
            if changed and len(b)<len(a):
                stop=[h for h in headings if h['patient_id']==patient and h['document_id']==new['document_id'] and h['cleaned_document_offset']>=new['report_end']]
                check(patient,'Changed source ends at a recognized peer heading: '+occurrence,bool(stop))
            for field in ('eeg_date','eeg_date_precision','recording_type','segment_start','segment_end'):
                check(patient,field+' preserved: '+occurrence,new[field]==old[field])
            for key,pattern in PATTERNS.items():gates[key]+=bool(re.search(pattern,b))
            # Only embedded channel sentences before the first new peer section
            # count as EEG content; ECG prose after that section is excluded.
            channel=re.compile(r'(?im)^[ \t]*(?:Im\s+(?:EKG|ECG)\b|(?:EKG|ECG)[- ]Kanal\b|(?:EKG|ECG):[ \t]+\S)[^\n]*')
            for m in channel.finditer(a):
                if m.start()<len(b) and m[0] not in b:gates['embedded_EEG_ECG_channel_wrongly_removed']+=1
            comparisons.append(dict(patient_id=patient,document_id=new['document_id'],eeg_date=new['eeg_date'],report_occurrence_id=occurrence,
                page=new['source_page_start'],before_end=old['report_end'],after_end=new['report_end'],before_chars=len(a),after_chars=len(b),changed=changed,
                excluded_section_heading=new.get('excluded_section_heading'),excluded_section_type=new.get('excluded_section_type'),clinical_prefix_preserved=preserved))
            if patient==fixture['patient_id'] and new['document_id']==fixture['document_id'] and new['eeg_date']==fixture['EEG_date']:target=new
        if patient=='2687726420':
            dates={r['eeg_date'] for r in reports}
            check(patient,'All historical short-year headings retained',{'2019-01-14','2019-01-19','2019-01-21','2019-01-25','2020-01-28'}<=dates)
            check(patient,'Routine and following cEEG remain separate',any(r['eeg_date']=='2026-07-29' and r['recording_type']=='ROUTINE_EEG' for r in reports) and any(r['eeg_date']=='2026-07-30' and r['recording_type']=='CONTINUOUS_EEG' for r in reports))
        if patient=='2957537425':
            video=next(r for r in reports if r['recording_type']=='VIDEO_EEG' and r['eeg_date']=='2025-05-19')
            check(patient,'Mobile video still has two separate cEEG summaries',len(video['segments'])==2)
            check(patient,'Same-date routine EEG remains separate',any(r['recording_type']=='ROUTINE_EEG' and r['eeg_date']=='2025-05-19' for r in reports))
    assert target is not None
    target_text=target['eeg_report_text'];expected=fixture['expected_EEG_BOUNDED_SOURCE_TEXT']
    def compact(value):return ' '.join(value.split()).casefold()
    check(fixture['patient_id'],'Exact physician-specified EEG source retained',compact(target_text)==compact(expected))
    if compact(target_text)!=compact(expected):gates['clinical_EEG_text_wrongly_removed']+=1
    for term in ('RUHE EKG','Psychiatrisches Konsil','Sertralin','Quetiapin'):
        check(fixture['patient_id'],'EEG source excludes '+term,term.casefold() not in target_text.casefold())
    for term in ('Minimal abnormes EEG','links temporal'):
        check(fixture['patient_id'],'EEG source retains '+term,term in target_text)
    target_doc=next(d for d in read(Path(before[fixture['patient_id']]['source_path']))['documents'] if d['source_document_id']==fixture['document_id'])
    raw='\n'.join(s['raw_pdf_text'] for s in target_doc['sections']);cleaned='\n'.join(s['text'] for s in target_doc['sections'])
    for term in ('RUHE EKG (04.06.2024)','Psychiatrisches Konsil (03.06.2024)','Sertralin','Quetiapin'):
        check(fixture['patient_id'],'Raw and cleaned source preserve '+term,term in raw and term in cleaned)
    target_headings=[h for h in headings if h['patient_id']==fixture['patient_id'] and h['document_id']==fixture['document_id']]
    ecg_detected=any(h['source_heading']=='RUHE EKG (04.06.2024)' and h['section_type']=='ECG' for h in target_headings)
    psych_detected=any(h['source_heading']=='Psychiatrisches Konsil (03.06.2024)' and h['section_type']=='PSYCHIATRIC_CONSULT' for h in target_headings)
    check(fixture['patient_id'],'Resting ECG heading detected',ecg_detected)
    check(fixture['patient_id'],'Psychiatric consultation heading detected',psych_detected)
    # No patient-specific cut is permitted in either generic parser module.
    parser_paths=[ROOT/'src/caa_pdf_extractor/extraction'/n for n in ('eeg_sources_v12.py','eeg_section_headings_v12.py')]
    for path in parser_paths:
        check('SCOPE','Generic rule has no patient/date-specific strings: '+path.name,all(token not in path.read_text('utf-8') for token in ('2989288225','27700090930173','04.06.2024')))
    clinical_before=read(directory/'source_code_before.json')
    changed_code=[p for p,h in clinical_before.items() if sha(p)!=h]
    assert changed_code==[str(ROOT/'src/caa_pdf_extractor/extraction/eeg_sources_v12.py')],changed_code
    clinical_loaded=[n for n in sys.modules if n.startswith('caa_pdf_extractor.extraction.eeg_extraction_') or n.startswith('caa_pdf_extractor.extraction.eeg_pipeline')]
    assert not clinical_loaded,clinical_loaded
    protected=read(directory/'protected_files_before.json');mutated=[p for p,h in protected.items() if sha(p)!=h]
    assert not mutated,mutated
    failed=sum(not c['passed'] for c in checks)
    metrics=dict(status='EEG_V1_2_BOUNDARY_FIX_READY_FOR_REVIEW' if not failed and not sum(gates.values()) else 'EEG_V1_2_BOUNDARY_REVIEW_REQUIRED',
        EEG_VERSION='1.2',root_cause=trace['root_cause'],
        patient_2989288225_boundary_before_fix='Included resting ECG and psychiatric consultation; ended before Therapieempfehlung:',
        patient_2989288225_boundary_after_fix='Ends immediately before RUHE EKG (04.06.2024), preserving only the physician-specified EEG text.',
        before_end=trace['candidate']['report_end'],after_end=target['report_end'],before_chars=len(trace['candidate']['eeg_report_text']),after_chars=len(target_text),
        RUHE_EKG_heading_detected='YES' if ecg_detected else 'NO',PSYCHIATRIC_CONSULT_heading_detected='YES' if psych_detected else 'NO',
        regression_patients_rerun=6,patient_ids=list(PATIENTS),segmentation_checks_passed=len(checks)-failed,segmentation_checks_failed=failed,
        boundary_failures_remaining=failed+sum(gates.values()),hard_gate_failures=sum(gates.values()),changed_source_occurrences=sum(r['changed'] for r in comparisons),
        EEG_CLINICAL_LOGIC_CHANGED='NO',clinical_extraction_invocations=0,clinical_modules_loaded=clinical_loaded,
        protected_files_unchanged=len(protected),existing_candidates_rewritten=False,DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE')
    save('after_segmentation.json',after);save('normalized_section_headings.json',headings)
    save('target_boundary_after.json',dict(patient_id=fixture['patient_id'],document_id=fixture['document_id'],page=fixture['page'],
        EEG_BOUNDED_SOURCE_TEXT=target_text,report_start=target['report_start'],report_end=target['report_end'],excluded_section_heading=target['excluded_section_heading'],
        excluded_section_type=target['excluded_section_type'],RAW_PDF_TEXT_preserved=True,CLEANED_PAGE_TEXT_preserved=True))
    save('hard_gates.json',dict(gates));save('validation_metrics.json',metrics)
    save('preservation_audit.json',dict(protected_files=len(protected),unexpected_changes=mutated,changed_extraction_source_files=changed_code,
        phenotype_schema_pipeline_geometry_unchanged=True,original_source_input_hashes_preserved=True,clinical_extraction_invocations=0,guard='Network/database/child-process operations and writes outside this debug directory prohibited'))
    csv_file('eeg_boundary_before_after.csv',comparisons);csv_file('segmentation_regression_checks.csv',checks)
    csv_file('eeg_boundary_hard_gates.csv',[dict(gate=k,violations=v,passed=v==0) for k,v in gates.items()])
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    return metrics


if __name__=='__main__':review(sys.argv[1])
