"""Offline source-only validation. No clinical assembler, model, database or network."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from .section_dictionary import (CONFIG, build_section_tree, eeg_action,
    heading_matches, load_dictionary)
from .section_dictionary_tools import known_heading_results, statistics
from .page_boilerplate import clean_pages
from .extraction.eeg_sources_v12 import discover
from .tfne_sources import model_sections, select_sections, source_folder
from .workbench_store import ROOT

GATES = ('KNOWN_SECTION_HEADING_MISSED','DATE_SUFFIX_BREAKS_HEADING_MATCH',
    'PREFIX_SECTION_WITH_CONTINUATION_MISSED','PATIENT_PAGE_HEADER_RETAINED_AS_CLINICAL_TEXT',
    'KNOWN_INSTITUTIONAL_FOOTER_RETAINED_AS_CLINICAL_TEXT','CLINICAL_BODY_TEXT_FALSELY_REMOVED_AS_FOOTER',
    'EEG_CROSSES_HARD_NON_EEG_BOUNDARY','TFNE_MODULE_INGESTS_NON_ANAMNESE_NON_SUMMARY_SECTION',
    'RAW_TEXT_MUTATED','DATABASE_WRITES','CLOUD_PATIENT_CALLS')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate(output):
    output=Path(output).resolve()
    if not output.is_relative_to((ROOT/'data/debug').resolve()) or not (output/'preservation_before.json').exists():
        raise ValueError('An existing new debug audit directory and preservation snapshot are required')
    from .workbench_worker import guard
    guard(output)  # blocks network, database imports, subprocess and outside writes
    def save(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    dictionary=load_dictionary(); gates=Counter(dict.fromkeys(GATES+('EEG_RECORDING_IDENTITIES_CHANGED','EEG_SOURCE_GREW_BEYOND_PREVIOUS_BOUNDARY'),0))
    known=known_heading_results(dictionary)
    for r in known:
        if not r['passed']:
            gates['KNOWN_SECTION_HEADING_MISSED']+=1
            gates['DATE_SUFFIX_BREAKS_HEADING_MATCH']+=r['test_heading']!=r['source_variant'].rstrip('*')
            gates['PREFIX_SECTION_WITH_CONTINUATION_MISSED']+=r['source_variant'].endswith('*')
    save('known_heading_results.json',known)
    boundary_reference=read(ROOT/'data/debug/eeg_v1_2_boundary_fix_20261101T124730_912442Z/after_segmentation.json')
    source_inventory=[]; changes=[]; trees=[]; observed=0
    for patient,reference in boundary_reference.items():
        path=Path(reference['source_path'])
        if sha(path)!=reference['source_sha256']:raise ValueError('Saved source changed')
        bundle=read(path); before=json.dumps(bundle,sort_keys=True)
        reports,_=discover(bundle)
        def flat(rows):
            out={}
            for row in rows:
                out[row['report_occurrence_id']]=row;out.update(flat(row.get('segments',[])))
            return out
        current=flat(reports);previous=flat(reference['reports']);observed+=len(current)
        gates['EEG_RECORDING_IDENTITIES_CHANGED']+=len(set(current)^set(previous))
        for identity in set(current)&set(previous):
            gates['EEG_SOURCE_GREW_BEYOND_PREVIOUS_BOUNDARY']+=len(current[identity]['eeg_report_text'])>len(previous[identity]['eeg_report_text'])
        for identity in sorted(set(current)|set(previous)):
            a=previous.get(identity);b=current.get(identity)
            if a is None or b is None or a['eeg_report_text']!=b['eeg_report_text']:
                changes.append(dict(patient_id=patient,report_occurrence_id=identity,
                    before_present=a is not None,after_present=b is not None,
                    before_heading=a['header'] if a else None,after_heading=b['header'] if b else None,
                    before_text=a['eeg_report_text'] if a else None,after_text=b['eeg_report_text'] if b else None,
                    excluded_heading=b.get('excluded_section_heading') if b else None))
        for report in current.values():
            body=report['eeg_report_text']
            for heading in heading_matches(body,dictionary):
                if heading['boundary_strength']=='HARD' and eeg_action(heading,body[:heading['start']],body[heading['end']:],
                    structured=bool(report.get('recording_type') in ('VIDEO_EEG','CONTINUOUS_EEG')))=='STOP':
                    gates['EEG_CROSSES_HARD_NON_EEG_BOUNDARY']+=1
        for d in bundle['documents']:
            tree=build_section_tree(d['sections'],d['source_document_id'],dictionary)
            trees.append(dict(patient_id=patient,document_id=d['source_document_id'],sections=tree))
            source_inventory.append(dict(patient_id=patient,path=str(path),sha256=sha(path),document_id=d['source_document_id']))
        gates['RAW_TEXT_MUTATED']+=before!=json.dumps(bundle,sort_keys=True)
        print('EEG source-only patient checked',flush=True)
    save('section_trees.json',trees);save('eeg_boundary_changes.json',changes)
    tfne_base=ROOT/'data/debug/tfne_pilot_v0_1_20261101T130202_586729Z'
    tfne_patients=read(tfne_base/'cohort_freeze.json')['patient_ids']; tfne_counts=[]
    for patient in tfne_patients:
        manifest=read(ROOT/'data/patient_artifacts'/patient/'module_manifest.json')
        folder=source_folder(manifest)
        if folder is None:raise ValueError('Missing saved source')
        bundle=read(folder/'source_bundle.json');before=json.dumps(bundle,sort_keys=True);sections=[]
        for d in bundle['documents']:
            selected,_=select_sections(patient,d);sections.extend(selected)
        payload=model_sections(sections)  # serialize only; NEVER call a model
        gates['TFNE_MODULE_INGESTS_NON_ANAMNESE_NON_SUMMARY_SECTION']+=sum(s['section_type'] not in
            ('ANAMNESE','ZUSAMMENFASSUNG_DES_AUFENTHALTES') for s in payload)
        gates['RAW_TEXT_MUTATED']+=before!=json.dumps(bundle,sort_keys=True)
        tfne_counts.append(dict(patient_id=patient,sections=len(sections),spans=sum(len(s['spans']) for s in sections)))
        print('TFNE source-only patient checked',flush=True)
    save('tfne_source_routing_counts.json',tfne_counts)
    # Synthetic geometry proves margin gating independently of historical cleaning.
    furniture=['Universitätsklinikum der','Paracelsus Medizinischen','Privatuniversität',
        'Universitätsklinik f. Neurologie','Telefon +43(0)5 7255-34625',
        'Neurologie@salk.at | www.salk.at','Gemeinnützige Salzburger Landeskliniken Betriebsgesellschaft mbH',
        'Christian Doppler-Klinik | A-5020 Salzburg','Ignaz Harrerstraße 79 | UID-Nr. ATU57476234',
        'Landesgericht Salzburg | FN 240832s','Anderer Beispielname, geb.: 23.02.1947; Seite 8']
    for i,text in enumerate(furniture):
        pages=[dict(page=1,height=840,width=600,blocks=[dict(block_id='f',bbox=[40,790,550,810],text=text)])]
        before=json.dumps(pages,sort_keys=True);cleaned,excluded=clean_pages(pages)
        if cleaned[0]['text']:
            gates['PATIENT_PAGE_HEADER_RETAINED_AS_CLINICAL_TEXT' if i==len(furniture)-1 else 'KNOWN_INSTITUTIONAL_FOOTER_RETAINED_AS_CLINICAL_TEXT']+=1
        gates['RAW_TEXT_MUTATED']+=before!=json.dumps(pages,sort_keys=True)
    clinical=['Aphasie seit gestern.','Keine Auffälligkeiten.\nUniversitätsklinikum der',
        'Intermittierende Theta-Aktivität links temporal.','Parameter\nNormbereich\nEinheit']
    for text in clinical:
        pages=[dict(page=n,height=840,width=600,blocks=[dict(block_id=str(n),bbox=[40,790,550,810],text=text)]) for n in (1,2,3)]
        cleaned,excluded=clean_pages(pages)
        gates['CLINICAL_BODY_TEXT_FALSELY_REMOVED_AS_FOOTER']+=len(excluded)
    protected=read(output/'preservation_before.json')
    changed=[p for p,h in protected.items() if not (ROOT/p).exists() or sha(ROOT/p)!=h]
    save('preservation_audit.json',dict(files_verified=len(protected),changed_files=changed))
    if changed:raise ValueError('Protected historical files changed')
    save('source_inventory.json',source_inventory)
    save('hard_gates.json',dict(gates))
    summary=statistics(dictionary)
    summary.update(known_heading_tests_passed=sum(r['passed'] for r in known),
        known_heading_tests_failed=sum(not r['passed'] for r in known),
        source_only_eeg_patients=len(boundary_reference),eeg_source_occurrences_checked=observed,
        eeg_boundary_changes=len(changes),source_only_tfne_patients=len(tfne_patients),
        historical_artifacts_changed=len(changed),protected_files_verified=len(protected),
        hard_gate_failures=sum(gates.values()),model_calls=0,DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE')
    save('validation_metrics.json',summary)
    save('runtime_dictionary_hashes.json',{str(p.relative_to(ROOT)):sha(p) for p in CONFIG.glob('*.yaml')})
    print(json.dumps(summary,indent=2))
    if summary['hard_gate_failures']:raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);validate(p.parse_args().output)
