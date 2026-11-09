"""Nine affected diagnosis/event candidates and read-only controls; no medication execution."""
import argparse
import csv
import json
from pathlib import Path
from collections import defaultdict

from .workbench_store import ROOT,WorkbenchStore,read,atomic,sha,integrity,candidate_entities
from .workbench_runner import run_module,implementation_hashes
from .workbench_deferred import enqueue_medication
from .extraction import diagnosis_events_v4 as dx
from .extraction.patient_diagnoses import build,FAMILIES
from .extraction.admission_summary import materialize
from .extraction.admission_event_linkage import apply as apply_admission_dates

AFFECTED=('2183012310','2435608415','2536647917','2586499918','2608083318','2695942320','2788388022','2888149624','2990288425')
CONTROLS=('2459345815','2552817617','2684469520','2826947422','2953158925')


def csvwrite(path,rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)) or ['status']);writer.writeheader()
        writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)


def layer(entry,name):
    path=next((a['path'] for a in entry['artifacts'] if Path(a['path']).suffix=='.json' and Path(a['path']).name.startswith(name)),None)
    return read(path) if path else []


def preferred(manifest):
    group=manifest['modules']['diagnoses']
    return group['versions'].get(group['active_validated_version']) or max(group['versions'].values(),key=lambda e:e['created_at'])


def validate_patient(pid,r):
    names={d['canonical_name'] for d in r['canonical_disease_identities']}
    expected={'2183012310':{'RECTAL_CARCINOMA'},'2435608415':{'HYDROCEPHALUS'},'2536647917':{'CORONARY_ARTERY_DISEASE','CAROTID_ARTERY_STENOSIS'},
        '2586499918':{'PARKINSONISM'},'2695942320':{'PERIPHERAL_ARTERIAL_DISEASE','OBSTRUCTIVE_SLEEP_APNEA'},'2888149624':{'PULMONARY_SARCOIDOSIS'}}
    assert expected.get(pid,set())<=names,(pid,expected.get(pid,set())-names)
    assert r['structured_diagnosis_reconciliation']['lost']==0
    assert len({e['event_id'] for e in r['canonical_clinical_events']})==len(r['canonical_clinical_events'])
    if pid=='2536647917':
        es=[e for e in r['canonical_clinical_events'] if e['event_type']=='INTRACEREBRAL_HEMORRHAGE']
        assert len(es)==2 and {(e['event_date'],e['side'],e['location']) for e in es}=={('2023-03-22','LEFT','PARIETO_OCCIPITAL'),('2017-03-04','RIGHT','PARIETAL')}
        assert all(e['etiology']=='CEREBRAL_AMYLOID_ANGIOPATHY' and e['etiology_certainty']=='PROBABLE' for e in es)
        measurements=[a for d in r['canonical_disease_identities'] if d['canonical_name']=='CAROTID_ARTERY_STENOSIS' for a in d['source_attributes']]
        assert any({(x['side'],x['stenosis_percent'],x['measurement_method']) for x in a.get('measurements',[])}=={('LEFT',20,'ECST'),('RIGHT',40,'ECST')} for a in measurements)
    if pid=='2608083318':
        assert any(c['event_type']=='ISCHEMIC_STROKE' and c['side']=='BILATERAL' and c['territory']=='MCA' and c['multiplicity']=='MULTIPLE' for c in r['clinical_event_candidates'])
        assert any(c['event_date']=='2018-07' and c['assertion_status']=='UNCERTAIN' and c['event_date_precision']=='MONTH' for c in r['clinical_event_candidates'])
    if pid=='2435608415':assert any('Multiinfarkt' in c['raw_text'] or 'multiinfarkt' in c['raw_text'] for c in r['diagnosis_mentions'])
    if pid=='2695942320':assert any(e['event_type']=='ISCHEMIC_STROKE' and e['location']=='RIGHT_BASAL_GANGLIA' and e['event_date']=='2022-10-29' for e in r['canonical_clinical_events'])
    if pid=='2888149624':
        assert any(e['event_type']=='INTRACEREBRAL_HEMORRHAGE' and e['location']=='RIGHT_BASAL_GANGLIA' and e['event_date']=='2024-03-08' for e in r['canonical_clinical_events'])
        assert any(a.get('first_diagnosis_year')==1985 for d in r['canonical_disease_identities'] if d['canonical_name']=='PULMONARY_SARCOIDOSIS' for a in d['source_attributes'])
    if pid=='2990288425':assert not r['af_state']['confirmed_af']
    if pid=='2788388022':
        assert 'CEREBRAL_AMYLOID_ANGIOPATHY' not in names
        assert any(a['probable_admission_event_type']=='ISCHEMIC_STROKE' and a['probable_admission_diagnosis'] for a in r['admission_clinical_summary'])


def run(output):
    output=Path(output).resolve();original=read(output/'original_manifests.json')
    if set(original)!=set(AFFECTED+CONTROLS):raise ValueError('Validation manifest snapshot must contain exactly nine affected patients and five controls')
    cache={read(f)['source']['encounter_id']:read(f) for f in (output/'admission_cache_final').glob('*.json')}
    store=WorkbenchStore();preflight={};control_rows=[];control_details=[]
    for pid in AFFECTED+CONTROLS:
        source=Path(original[pid]['source_context']['directory']);bundle=read(source/'source_bundle.json')
        result=dx.assemble(pid,bundle,read(source/'source_mentions.json'))
        if pid in AFFECTED:
            model={k:v for k,v in cache.items() if v['source']['patient_id']==pid}
            encounters=read(source/'encounters.json')
            apply_admission_dates(pid,bundle,encounters,model,result)
            result['admission_clinical_summary']=materialize(pid,bundle,encounters,model,result['canonical_clinical_events'])
            result['patient_diagnoses']=build(pid,result);validate_patient(pid,result);preflight[pid]=result
        else:
            old=preferred(original[pid]);old_diseases=layer(old,'canonical_disease_identities')
            old_af=layer(old,'af_state');old_timeline=layer(old,'diagnostic_assertion_timeline')
            missing={d['canonical_name'] for d in old_diseases}-{d['canonical_name'] for d in result['canonical_disease_identities']}
            # Verify every lost AF baseline label against its actual source, rather than
            # reinstating a false-positive to make a control comparison green.
            discrepancy=bool(missing)
            if missing-{'ATRIAL_FIBRILLATION'}:raise ValueError(f'Unexpected lost control diagnosis: {pid} {missing}')
            if missing:
                assert not any(o.get('explicit_positive') for o in result['af_observations'])
                control_details.extend(dict(a,patient_id=pid) for a in result['diagnostic_assertion_timeline'] if a['canonical_name']=='ATRIAL_FIBRILLATION')
            control_rows.append(dict(patient_id=pid,artifact_changes=0,active_pointer_changed=False,
                old_diagnoses=len(old_diseases),new_diagnoses_in_memory=len(result['canonical_disease_identities']),
                old_event_count=len(layer(old,'canonical_clinical_events')),new_event_count_in_memory=len(result['canonical_clinical_events']),
                old_AF_state=old_af.get('AF_state','LEGACY_NO_STATE') if old_af else 'LEGACY_NO_STATE',new_AF_state_in_memory=result['af_state']['AF_state'],
                lost_diagnoses=sorted(missing),baseline_discrepancy=discrepancy,
                explanation='Stored AF positive derives only from a future contact-if-AF-occurs statement; current classifier returns CONDITIONAL. Active artifacts are preserved.' if discrepancy else 'Supported diagnosis identities retained; only in-memory regression checks performed.'))
    csvwrite(output/'regression_controls.csv',control_rows);atomic(output/'control_AF_source_audit.json',control_details)

    candidates=read(output/'published_candidates.json') if (output/'published_candidates.json').exists() else {}
    for pid in AFFECTED:
        if pid in candidates:
            if read(candidates[pid]['metadata_path'])['implementation_hashes']==implementation_hashes('diagnoses'):continue
            history=read(output/'candidate_history.json') if (output/'candidate_history.json').exists() else []
            if not any(e['version']==candidates[pid]['version'] for e in history):history.append(candidates[pid]);atomic(output/'candidate_history.json',history)
        model={k:v for k,v in cache.items() if v['source']['patient_id']==pid}
        e=run_module(pid,'diagnoses',store=store,expected_active=original[pid]['modules']['diagnoses']['active_validated_version'],admission_results=model)
        candidates[pid]=e;atomic(output/'published_candidates.json',candidates)
        meta=read(e['metadata_path'])
        if meta.get('fatal_errors') or not all(meta['validation_test_results']['hard_gates'].values()):raise ValueError(f'Candidate failed hard gates: {pid}')
        print(pid,e['version'],flush=True)

    summary=[];admissions=[];events=[];registry=[];gates=[];reconciliation=[];af_trace=[];caa_trace=[];results={}
    for pid,e in candidates.items():
        names=['canonical_disease_identities','canonical_clinical_events','clinical_event_candidates','diagnosis_mentions',
            'diagnostic_assertion_timeline','structured_diagnosis_reconciliation','structured_diagnosis_items','patient_diagnoses','admission_clinical_summary','af_state','af_observations']
        result={n:layer(e,n) for n in names};results[pid]=result;validate_patient(pid,result)
        assert integrity(e)
        entities=candidate_entities(e,'diagnoses');assert len({r['entity_id'] for r in entities})==len(entities)
        before=preferred(original[pid]);old_names={FAMILIES.get(d['canonical_name'],d['canonical_name']) for d in layer(before,'canonical_disease_identities')}
        old_event_keys={(d['event_type'],d.get('event_date'),d.get('side'),d.get('location')) for d in layer(before,'canonical_clinical_events')}
        new_events=[d['event_id'] for d in result['canonical_clinical_events'] if (d['event_type'],d.get('event_date'),d.get('side'),d.get('location')) not in old_event_keys]
        primary=max(result['admission_clinical_summary'],key=lambda a:a.get('admission_date') or '')
        summary.append(dict(patient_id=pid,new_candidate_version=e['version'],new_diagnoses_added=sorted({d['canonical_name'] for d in result['patient_diagnoses']}-old_names),
            new_events_added=new_events,admission_date=primary['admission_date'],admission_diagnosis=primary['probable_admission_diagnosis'],
            admission_confidence=primary['confidence'],AF_state=result['af_state']['AF_state'],review_required_count=sum(r.get('review_required',False) for r in entities),
            admission_count=len(result['admission_clinical_summary']),event_count=len(result['canonical_clinical_events']),event_candidate_count=len(result['clinical_event_candidates'])))
        admissions+=result['admission_clinical_summary'];events+=result['canonical_clinical_events'];registry+=result['patient_diagnoses']
        for a in result['diagnostic_assertion_timeline']:
            if a['canonical_name']=='ATRIAL_FIBRILLATION':af_trace.append(a)
            if a['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY':caa_trace.append(a)
        grouped=defaultdict(list)
        for r in result['structured_diagnosis_items']:grouped[(r['document_id'],r['section_id'])].append(r)
        for (document,section),items in grouped.items():
            counts={name:sum(r['disposition']==name for r in items) for name in ('MAPPED_DISEASE_OR_EVENT','NON_DISEASE_EVENT','UNMAPPED_DIAGNOSIS_CANDIDATE')}
            reconciliation.append(dict(patient_id=pid,document_id=document,section_id=section,source_item_count=len(items),**counts,lost=len(items)-sum(counts.values())))
        meta=read(e['metadata_path'])
        gates+=[dict(patient_id=pid,gate=k,passed=v) for k,v in meta['validation_test_results']['hard_gates'].items()]
    csvwrite(output/'patient_summary.csv',summary);csvwrite(output/'admissions.csv',admissions)
    csvwrite(output/'patient_diagnoses.csv',registry);csvwrite(output/'clinical_events.csv',events)
    csvwrite(output/'AF_assertion_regression.csv',af_trace);csvwrite(output/'CAA_assertion_regression.csv',caa_trace)
    csvwrite(output/'structured_diagnosis_reconciliation.csv',reconciliation);csvwrite(output/'hard_gates.csv',gates)
    csvwrite(output/'clinical_event_candidates.csv',[c for r in results.values() for c in r['clinical_event_candidates']])

    queue=enqueue_medication(dict(patient_id='2608083318',drug='PHENOBARBITAL',priority='HIGH',reason='ANTISEIZURE_MEDICATION',
        source_document='3.pdf',document_id=122294902536868,page=2,section_id='doc122294902536868_sec05',
        source_text='Phenobarbital 60 mg',task_status='DEFERRED_NO_MEDICATION_RERUN',reported_in='DIAGNOSIS_ARCHITECTURE_VNEXT'))
    atomic(output/'deferred_medication_regression.json',queue)
    allowed={str(store.patient_dir(pid)/'module_manifest.json') for pid in AFFECTED}|{str(store.root/'module_run_audit.jsonl')}
    protected=read(output/'protected_files.json')
    changed=[p for p,h in protected.items() if p not in allowed and (not Path(p).exists() or sha(p)!=h)]
    unchanged_pointers=all(store.manifest(pid)['modules'][module]['active_validated_version']==g['active_validated_version'] for pid,m in original.items() for module,g in m['modules'].items())
    old_entries_preserved=all(store.manifest(pid)['modules'][module]['versions'][v]==entry for pid,m in original.items() for module,g in m['modules'].items() for v,entry in g['versions'].items())
    assert not changed and unchanged_pointers and old_entries_preserved
    atomic(output/'protected_input_validation.json',dict(protected_files=len(protected),unexpected_changed_files=changed,
        all_active_pointers_preserved=unchanged_pointers,all_prior_version_entries_preserved=old_entries_preserved,
        medication_module_runs=0,control_candidate_publications=0,diagnosis_candidate_publications=len(candidates)))
    basal=next(e for e in results['2695942320']['canonical_clinical_events'] if e.get('location')=='RIGHT_BASAL_GANGLIA' and e.get('event_date')=='2022-10-29')
    latest278=max(results['2788388022']['admission_clinical_summary'],key=lambda a:a.get('admission_date') or '')
    status=dict(PATIENT_2990288425_CONFIRMED_AF='YES' if results['2990288425']['af_state']['confirmed_af'] else 'NO',
        PATIENT_2536647917_ICH_EVENT_COUNT=sum(e['event_type']=='INTRACEREBRAL_HEMORRHAGE' for e in results['2536647917']['canonical_clinical_events']),
        PATIENT_2788388022_ADMISSION_DIAGNOSIS=latest278['probable_admission_diagnosis'],
        PATIENT_2695942320_ADMISSION_EVENT_DATE_SOURCE=basal['date_source'],
        STRUCTURED_DIAGNOSIS_ITEM_LOSS=sum(r['lost'] for r in reconciliation),
        CONDITIONAL_AF_FALSE_POSITIVES=sum(a['assertion_status']=='CONDITIONAL' and a.get('explicit_positive') for a in af_trace),
        HARD_GATE_FAILURES=sum(not g['passed'] for g in gates),DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE',
        diagnosis_candidates=len(candidates),encounters_inferred=len(admissions),model_abstentions=sum(a['probable_admission_diagnosis'] is None for a in admissions),
        published_inference_semantic_calls=sum(len(x['attempts']) for x in cache.values()),
        total_local_semantic_calls_this_task=sum(len(read(f)['attempts']) for folder in ('admission_cache','admission_cache_final') for f in (output/folder).glob('*.json'))+read(output/'rejected_grounding_attempts.json')['semantic_calls'],
        control_baseline_discrepancies=sum(r['baseline_discrepancy'] for r in control_rows))
    status['total_diagnosis_revisions_published']=sum(len(store.manifest(pid)['modules']['diagnoses']['versions'])-len(original[pid]['modules']['diagnoses']['versions']) for pid in AFFECTED)
    atomic(output/'final_status.json',status)
    assert not status['HARD_GATE_FAILURES'] and not status['STRUCTURED_DIAGNOSIS_ITEM_LOSS'] and not status['CONDITIONAL_AF_FALSE_POSITIVES']
    text=['# Diagnosis architecture vNext ready for physician review','',
        'DIAGNOSIS_ARCHITECTURE_VNEXT_READY_FOR_PHYSICIAN_REVIEW','',
        'Nine new diagnosis/event 4.3 candidate revisions were published. No 4.2 artifact was overwritten, no candidate was auto-promoted, and every active pointer is preserved. No medication module, computed CAA, clinical scale, risk score, neurosonology or OAC module ran. Local Granite inferred probable admission reasons for all 34 saved verified encounters from their complete summary narratives.','',
        '```text']+[f'{k} = {v}' for k,v in status.items() if k.isupper()]+['```','',
        '## Per-patient summary','',
        '| Patient | Newly represented diagnosis families | New event rows | Latest admission diagnosis | Confidence | AF state | Review items |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for row in summary:text.append('| '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in [row['patient_id'],', '.join(row['new_diagnoses_added']),len(row['new_events_added']),row['admission_diagnosis'],row['admission_confidence'],row['AF_state'],row['review_required_count']])+' |')
    text+=['','`patient_summary.csv` contains the complete per-patient fields and added IDs. The displayed admission is the latest admission by saved admission date; `admissions.csv` contains every encounter. New event-row counts include changed representation/type names relative to historical artifacts and are not a count of newly occurring clinical events.','',
        '## Architecture and safety','',
        '- `patient_diagnoses` is the unified physician-facing registry, with no acute/chronic split. Epilepsy is one family with source concepts retained. Disease/event history rows link to distinct chronological event IDs. Legacy canonical disease/event layers remain for compatibility, and unmapped structured items remain reviewable.',
        '- Clinical events retain source dates, anatomy, etiologic assertions and mention counts. Undated/ambiguous references and the July 2018 suspected infarct are retained separately as reviewable event candidates. Uncertain events never establish AF or a final disease assertion.',
        '- `admission_clinical_summary` is a separate physician-adjudicated overlay entity. It is labelled PROBABLE_ADMISSION_DIAGNOSIS / LLM_SUMMARY_INFERENCE. ACCEPT, EDIT, REJECT and UNCERTAIN use the existing local audit repository. Source quotations are retained with exact offsets; complete summaries and raw model responses remain in the local evidence/cache files.',
        '- Admission inference links existing events where a source-supported date or clear transfer context identifies one episode. Low-confidence inference does not auto-link. Dates are not copied onto historical events. A conservative admission-date fallback applies only to a pre-existing deterministic event candidate plus explicitly acute admission wording; no event is created merely from an LLM label.',
        '- 2695942320 has explicit narrative onset evidence for 29 October 2022, preceding the later November admission/transfer. The narrative date takes priority. No admission-date substitution was necessary for the reported event.',
        '- 2536647917 has two distinct ICH events (left parieto-occipital 22 March 2023; right parietal 4 March 2017), with source-reported probable CAA etiology and the hemorrhagic-infarction differential retained for 2017.',
        '- Conditional, negative, suspected, nondiagnostic and differential source assertions cannot establish AF. CAA workup/phenotype/non-assessable safety regressions remain covered. HISTORICAL is a temporal attribute rather than a substitute for assertion certainty.',
        '', '## Granite provenance and limitations','',
        f"The published inferences use {status['published_inference_semantic_calls']} local semantic calls and model granite4.2:latest, digest recorded in local_model.json. The task made {status['total_local_semantic_calls_this_task']} local semantic calls in total, including development/grounding attempts. Four early failed grounding/schema attempts were rejected before publication; raw bytes for those four attempts were not retained, and this limitation is recorded in rejected_grounding_attempts.json. Final prompts, full input narratives, exact supporting spans, and raw final responses are retained. No cloud endpoint, model download or proxy was used.",
        f"Granite abstained on {status['model_abstentions']} summaries. Confidence is model-reported, not calibrated. Low-confidence labels, inferred admission event types, unresolved event links and the source-backed but ambiguous historical references require physician review; inferred labels are not final confirmed diagnoses.",
        '', '## Regression controls','',
        'The five controls were evaluated in memory only. Their source/candidate files and active pointers are unchanged. Supported diagnosis families remain represented. Partial-date/event anatomy enrichment can change an in-memory event representation without modifying the stored control outputs.',
        '2953158925 has a pre-existing baseline AF false positive: its only stored positive assertion says the patient/GP will be contacted if AF occurs. The current guard classifies that statement CONDITIONAL; the other AF evidence is suspected or negative. Its stored CONFIRMED_AF would become SUSPECTED_AF under a future rerun. This is reported as a control baseline discrepancy, not silently restored or auto-published. See regression_controls.csv and control_AF_source_audit.json.',
        '', '## Deferred medication issue','',
        'PHENOBARBITAL for 2608083318 remains OPEN / HIGH / ANTISEIZURE_MEDICATION in data/review/deferred_medication_regressions.json. It is visible in the medication panel as a deferred issue. No medication artifact or dictionary was changed.',
        '', '## Review App and evidence','',
        'Refresh the artifact index and select the newest 4.3 diagnosis candidate. The view contains Patient diagnoses, Clinical events and Admissions. Probable admission diagnoses have their own review identities and source navigation. Promotion still requires explicit physician adjudication of every required entity.',
        'Evidence: patient_summary.csv, patient_diagnoses.csv, clinical_events.csv, clinical_event_candidates.csv, admissions.csv, AF_assertion_regression.csv, CAA_assertion_regression.csv, structured_diagnosis_reconciliation.csv, hard_gates.csv, regression_controls.csv and protected_input_validation.json. Test and UI smoke evidence are stored beside this report.']
    if (output/'candidate_history.json').exists():text+=['',f"The selected review set has nine latest candidates. {status['total_diagnosis_revisions_published']} diagnosis revisions were published during this task; earlier immutable 4.3 revisions are recorded in candidate_history.json. Choose the latest version listed in published_candidates.json. No historical revision was overwritten."]
    (output/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf8')
    return status


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run(args.output),ensure_ascii=False,indent=2))
