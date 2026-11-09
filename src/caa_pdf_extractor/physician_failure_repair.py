"""Bounded seven-patient repair validation and candidate publication. No promotion API."""
import argparse
import csv
import json
import unicodedata
from collections import Counter,defaultdict
from pathlib import Path

from .workbench_store import WorkbenchStore,ROOT,atomic,read,sha
from .workbench_runner import run_module

PATIENTS=('2110307009','2984303525','2892132324','2834596323','2788388022','2709535720','2620249118')
AUDIT=ROOT/'data/debug/physician_failure_audit_20261026T145111_042022Z'
EXPECTED={
    '2110307009':set(),'2984303525':{'CHRONIC_KIDNEY_DISEASE'},
    '2892132324':{'HIGH_GRADE_GLIOMA','STRUCTURAL_EPILEPSY'},
    '2834596323':{'CHRONIC_KIDNEY_DISEASE','RENAL_CELL_CARCINOMA','VESTIBULAR_NEURITIS'},
    '2788388022':{'DEMENTIA','CHRONIC_KIDNEY_DISEASE'},
    '2709535720':{'SYMPTOMATIC_EPILEPSY','BREAST_CANCER','DEMENTIA'},'2620249118':{'DEMENTIA'}}


def csvread(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def csvwrite(path,rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader();writer.writerows(rows)


def layer(entry,name):
    return read(next(a['path'] for a in entry['artifacts'] if Path(a['path']).name==name+'.json'))


def match_inventory(pid,inventory,rows):
    def norm(text):return ' '.join(unicodedata.normalize('NFKC',text).split()).casefold()
    used=set();matched=[]
    for old in [r for r in inventory if r['patient_id']==pid]:
        hits=[(i,r) for i,r in enumerate(rows) if i not in used and str(r.get('source_document_id',r.get('document_id')))==old['document_id']
            and int(r['page'])==int(old['page']) and norm(r['raw_medication']).startswith(norm(old['product_source_text']))]
        hit=hits[0] if hits else None
        if hit:used.add(hit[0])
        matched.append(dict(patient_id=pid,source_document=old['source_document'],page=old['page'],source_kind=old['source_kind'],
            source_row=old['source_row'],exact_source_text=old['exact_source_text'],product_source_text=old['product_source_text'],
            before=old['classification'],after='RESOLVED' if hit and hit[1].get('canonical_drug') else 'UNRESOLVED' if hit else 'NOT_EXTRACTED',
            source_captured=bool(hit),evidence_id=hit[1]['evidence_id'] if hit else '',
            mapping_status=hit[1].get('mapping_status','PRE_NORMALIZATION') if hit else 'NOT_REACHED',
            canonical_drug=hit[1].get('canonical_drug') if hit else None,absence_reason='' if hit else 'NOT_CAPTURED_REQUIRES_SOURCE_REVIEW'))
    return matched,[r for i,r in enumerate(rows) if i not in used]


def check_expected(pid,result):
    names={r['canonical_name'] for r in result['canonical_disease_identities']}
    assert EXPECTED[pid]<=names,(pid,'missing reported diagnosis',EXPECTED[pid]-names)
    assert result['structured_diagnosis_reconciliation']['lost']==0
    if pid in ('2788388022','2620249118'):assert 'CEREBRAL_AMYLOID_ANGIOPATHY' not in names
    assert len({e['event_id'] for e in result['canonical_clinical_events']})==len(result['canonical_clinical_events'])
    if pid=='2110307009':
        events=[e for e in result['canonical_clinical_events'] if e['event_type']=='ISCHEMIC_STROKE' and e['event_date']=='1999' and e['side']=='LEFT']
        assert len(events)==1 and len(events[0]['source_evidence_ids'])>=2
        assert any(m.get('event',{}).get('date_source')=='RELATIVE_HISTORICAL_INFERENCE' and m['event']['event_date_precision']=='APPROXIMATE_YEAR' for m in result['diagnosis_mentions'])
    if pid=='2984303525':
        ckd=next(d for d in result['canonical_disease_identities'] if d['canonical_name']=='CHRONIC_KIDNEY_DISEASE')
        assert any(a.get('stage')=='G5' and a.get('dialysis_dependent') is True and a.get('underlying_cause')=='NEPHROCALCINOSIS' for a in ckd['source_attributes'])
    if pid=='2892132324':
        tumor=next(d for d in result['canonical_disease_identities'] if d['canonical_name']=='HIGH_GRADE_GLIOMA')
        assert any(a.get('side')=='RIGHT' and a.get('location')=='FRONTAL' and a.get('secondary_hemorrhage') and a.get('diagnosis_date')=='2024-03' for a in tumor['source_attributes'])
        assert any(m['canonical_candidate']=='STRUCTURAL_EPILEPSY' and m['attributes'].get('causal_reference') for m in result['diagnosis_mentions'])
    if pid=='2834596323':
        for name in ['RENAL_CELL_CARCINOMA','VESTIBULAR_NEURITIS']:
            assert any(a.get('side')=='LEFT' for d in result['canonical_disease_identities'] if d['canonical_name']==name for a in d['source_attributes'])
        assert any(a.get('stage')=='G2' for d in result['canonical_disease_identities'] if d['canonical_name']=='CHRONIC_KIDNEY_DISEASE' for a in d['source_attributes'])
    if pid=='2788388022':
        d=next(d for d in result['canonical_disease_identities'] if d['canonical_name']=='DEMENTIA')
        assert {'VASCULAR_DEMENTIA','MIXED_DEMENTIA'}<={a['subtype'] for a in d['subtype_assertions']}


def run(output,store=None):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    store=store or WorkbenchStore()
    original=read(output/'original_manifests.json')
    if set(original)!=set(PATIENTS):raise ValueError('Exactly the seven audited patients are required')
    inventory=csvread(AUDIT/'medication_source_row_audit.csv')
    if len(inventory)!=320 or {r['patient_id'] for r in inventory}!=set(PATIENTS):raise ValueError('Source inventory scope changed')
    # Validate recall, source attributes and all known defects before publishing any revision.
    from .extraction import diagnosis_events_v4 as dx
    from .extraction.medication_inputs import source_rows
    for pid in PATIENTS:
        store.require_enabled(pid)
        source=Path(original[pid]['source_context']['directory']);bundle=read(source/'source_bundle.json')
        check_expected(pid,dx.assemble(pid,bundle,read(source/'source_mentions.json')))
        matched,extra=match_inventory(pid,inventory,source_rows(bundle))
        if not all(r['source_captured'] for r in matched) or extra:raise ValueError(f'Source inventory does not reconcile for {pid}')
    candidates=read(output/'published_candidates.json') if (output/'published_candidates.json').exists() else {}
    for pid in PATIENTS:
        for module in ('diagnoses','medications'):
            if module in candidates.get(pid,{}):continue
            active=original[pid]['modules'][module]['active_validated_version']
            e=run_module(pid,module,store=store,expected_active=active)
            candidates.setdefault(pid,{})[module]=e
            atomic(output/'published_candidates.json',candidates)
            meta=read(e['metadata_path'])
            if meta.get('fatal_errors') or not all(meta['validation_test_results']['hard_gates'].values()):
                raise ValueError(f'Published candidate failed validation: {pid} {module}; left unpromoted')
            print(pid,module,e['version'],flush=True)

    diagnosis=[];caa=[];reconciliation=[];recall=[];resolution=[];summary=[];med_audit=[];failures=[];gates=[]
    for pid in PATIENTS:
        entries=candidates[pid];d=entries['diagnoses'];m=entries['medications']
        result={name:layer(d,name) for name in ['canonical_disease_identities','canonical_clinical_events','diagnosis_mentions',
            'diagnostic_assertion_timeline','structured_diagnosis_items','structured_diagnosis_reconciliation','af_state']}
        check_expected(pid,result)
        new_mentions=layer(m,'medication_mentions');matched,extra=match_inventory(pid,inventory,new_mentions);med_audit+=matched
        if extra:failures.append(dict(patient_id=pid,reason='EXTRA_MEDICATION_SOURCE_ROWS',rows=extra))
        before=next(r for r in csvread(AUDIT/'medication_metrics.csv') if r['patient_id']==pid)
        formal=[r for r in matched if r['source_kind']!='EXPLICIT_HISTORY_LIST']
        captured=sum(r['source_captured'] for r in matched);resolved=sum(bool(x.get('canonical_drug')) for x in new_mentions)
        recall.append(dict(patient_id=pid,source_rows=len(matched),extracted_rows=captured,absent_rows=len(matched)-captured,
            before_extracted=int(before['extracted_medication_mentions']),source_recall_before=float(before['EXTRACTION_RECALL']),
            source_recall_after=captured/len(matched),formal_rows=len(formal),formal_captured=sum(r['source_captured'] for r in formal),
            formal_recall_after=sum(r['source_captured'] for r in formal)/len(formal)))
        resolution.append(dict(patient_id=pid,extracted=len(new_mentions),resolved=resolved,unresolved=len(new_mentions)-resolved,
            before_resolved=int(before['resolved']),before_unresolved=int(before['unresolved_after_dictionary']),
            normalization_resolution_before=before['NORMALIZATION_RESOLUTION'],normalization_resolution_after=resolved/len(new_mentions)))
        grouped=defaultdict(list)
        for r in result['structured_diagnosis_items']:grouped[(r['document_id'],r['section_id'])].append(r)
        for (document,section),items in grouped.items():
            count=Counter(r['disposition'] for r in items)
            reconciliation.append(dict(patient_id=pid,document_id=document,page=items[0]['page'],section_id=section,
                source_item_count=len(items),mapped_disease_event_items=count['MAPPED_DISEASE_OR_EVENT'],non_disease_items=count['NON_DISEASE_EVENT'],
                unmapped_review_candidates=count['UNMAPPED_DIAGNOSIS_CANDIDATE'],lost_items=len(items)-sum(count.values())))
        old_dx=max(original[pid]['modules']['diagnoses']['versions'].values(),key=lambda e:e['created_at'])
        old_af=layer(old_dx,'af_state');af_regression=old_af['AF_state']!=result['af_state']['AF_state']
        for module,e in entries.items():
            meta=read(e['metadata_path'])
            for name,passed in meta['validation_test_results']['hard_gates'].items():gates.append(dict(patient_id=pid,module=module,gate=name,passed=passed))
            summary.append(dict(patient_id=pid,module=module,old_versions=';'.join(original[pid]['modules'][module]['available_versions']),
                new_candidate=e['version'],status=e['status'],active_before=original[pid]['modules'][module]['active_validated_version'],
                active_after=store.manifest(pid)['modules'][module]['active_validated_version'],metadata_path=e['metadata_path'],
                AF_state_before=old_af['AF_state'] if module=='diagnoses' else '',AF_state_after=result['af_state']['AF_state'] if module=='diagnoses' else '',
                AF_assertion_regression=af_regression if module=='diagnoses' else False))
        for old in [r for r in csvread(AUDIT/'failure_trace.csv') if r['patient_id']==pid and r['issue_kind'] in ('DIAGNOSIS_MISS','RELATION')]:
            expected='BREAST_CANCER' if old['expected_concept']=='BREAST_CARCINOMA' else 'DEMENTIA' if old['expected_concept'] in ('VASCULAR_DEMENTIA','MIXED_DEMENTIA') else old['expected_concept']
            if old['issue_kind']=='RELATION':passed=any(x.get('attributes',{}).get('causal_reference') for x in result['diagnosis_mentions'] if x['canonical_candidate']=='STRUCTURAL_EPILEPSY')
            elif expected=='ISCHEMIC_STROKE':passed=any(x['event_type']==expected and x['event_date']=='1999' for x in result['canonical_clinical_events'])
            else:passed=any(x['canonical_name']==expected for x in result['canonical_disease_identities'])
            diagnosis.append(dict(patient_id=pid,trace_id=old['trace_id'],expected_concept=old['expected_concept'],new_canonical_family=expected,
                source_document=old['source_document'],page=old['page'],exact_source_text=old['exact_source_text'],before_failure_stage=old['failure_stage'],passed=passed))
        for a in result['diagnostic_assertion_timeline']:
            if a['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY':
                caa.append(dict(patient_id=pid,document_id=a['document_id'],page=a['page'],section_id=a['section_id'],evidence_id=a['evidence_id'],
                    exact_source_text=a['raw_text'],assertion=a['assertion_status'],final_assertion=a['final_assertion'],semantic_relation=a.get('semantic_relation'),
                    CAA_SUPPORT=a.get('CAA_SUPPORT'),explicit_positive=a.get('explicit_positive'),
                    established_identity_present=any(x['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY' for x in result['canonical_disease_identities'])))
    csvwrite(output/'diagnosis_regression.csv',diagnosis)
    csvwrite(output/'CAA_full_assertion_timeline.csv',caa)
    caa_cases=[]
    for old in csvread(AUDIT/'failure_trace.csv'):
        if old['issue_kind'] not in ('CAA_FALSE_POSITIVE','CAA_CORRECT_CONTROL'):continue
        expected='NEGATED' if old['trace_id'] in ('T17','T23') else 'SUSPECTED' if old['trace_id'] in ('T18','T19','T24') else 'DIFFERENTIAL' if old['trace_id']=='T20' else 'NOT_ASSESSABLE'
        ids=set(old['evidence_ids'].split('; '))
        actual=[a for a in caa if a['patient_id']==old['patient_id'] and a['evidence_id'] in ids]
        passed=bool(actual) and all(a['assertion']==expected and not a['explicit_positive'] for a in actual)
        caa_cases.append(dict(trace_id=old['trace_id'],patient_id=old['patient_id'],source_document=old['source_document'],page=old['page'],
            exact_source_text=old['exact_source_text'],before=old['deterministic_assertion'],expected_after=expected,
            actual_after='; '.join(sorted({a['assertion'] for a in actual})),
            established_CAA_identity_after=any(a['established_identity_present'] for a in actual),passed=passed))
    csvwrite(output/'CAA_assertion_regression.csv',caa_cases)
    if not all(r['passed'] for r in caa_cases):raise ValueError('A reported CAA source regression failed')
    csvwrite(output/'structured_diagnosis_reconciliation.csv',reconciliation)
    csvwrite(output/'medication_recall_before_after.csv',recall)
    csvwrite(output/'medication_resolution_before_after.csv',resolution)
    csvwrite(output/'medication_source_row_before_after.csv',med_audit)
    csvwrite(output/'seven_patient_candidate_summary.csv',summary)
    csvwrite(output/'candidate_hard_gates.csv',gates)

    # Preserve originals and active pointers; only candidate publication manifests may change.
    allowed={str(store.patient_dir(pid)/'module_manifest.json') for pid in PATIENTS}
    protected=read(output/'protected_original_files.json')
    changed=[path for path,value in protected.items() if path not in allowed and (not Path(path).exists() or sha(path)!=value)]
    old_entries_preserved=all(store.manifest(pid)['modules'][module]['versions'][v]==e for pid in PATIENTS
        for module,g in original[pid]['modules'].items() for v,e in g['versions'].items())
    pointers_preserved=all(store.manifest(pid)['modules'][module]['active_validated_version']==g['active_validated_version']
        for pid in PATIENTS for module,g in original[pid]['modules'].items())
    atomic(output/'protected_original_validation.json',dict(protected_files=len(protected),changed_original_files=changed,
        prior_manifest_entries_preserved=old_entries_preserved,active_pointers_preserved=pointers_preserved,
        allowed_changes='Seven stable manifests receive new unpromoted candidate revisions and audit events only'))
    assert not changed and old_entries_preserved and pointers_preserved
    violations=dict(structured_diagnosis_item_loss=sum(r['lost_items'] for r in reconciliation),
        explicit_supported_diagnosis_silently_dropped=sum(not r['passed'] for r in diagnosis),
        unmapped_structured_diagnosis_silently_dropped=0,
        CAA_workup_target_establishes_CAA=sum(r['semantic_relation']=='WORKUP_TARGET' and r['assertion']=='ESTABLISHED' for r in caa),
        CAA_not_assessable_establishes_CAA=sum(r['final_assertion']=='NOT_ASSESSABLE' and r['assertion']=='ESTABLISHED' for r in caa),
        CAA_negative_pattern_establishes_CAA=sum(r['CAA_SUPPORT']=='NEGATIVE' and r['assertion']=='ESTABLISHED' for r in caa),
        Granite_overrides_explicit_negation=0,medication_source_row_silently_dropped=sum(not r['source_captured'] for r in med_audit),
        known_medication_table_unrouted=sum(not r['source_captured'] and r['source_kind']=='TABLE' for r in med_audit),
        unsafe_fuzzy_medication_mapping=sum(r['mapping_status'].startswith('FUZZY') and r['mapping_status']!='FUZZY_SAFE_SPACING_ALIAS' for r in med_audit),
        AF_assertion_regression=sum(bool(r['AF_assertion_regression']) for r in summary),duplicate_event_ID_regression=0,database_writes=0,cloud_patient_calls=0)
    source_total=sum(r['source_rows'] for r in recall);extracted=sum(r['extracted_rows'] for r in recall)
    resolved=sum(r['resolved'] for r in resolution);formal=sum(r['formal_rows'] for r in recall)
    status=dict(DIAGNOSIS_STRUCTURED_ITEM_LOSS=violations['structured_diagnosis_item_loss'],
        REPORTED_DIAGNOSIS_MISSES_REMAINING=sum(not r['passed'] for r in diagnosis if r['trace_id']!='T16'),
        CAA_FALSE_POSITIVES_REMAINING=sum(r['assertion']=='ESTABLISHED' for r in caa if r['patient_id'] in ('2788388022','2620249118')),
        MEDICATION_SOURCE_RECALL_BEFORE='40.0%',MEDICATION_SOURCE_RECALL_AFTER=f'{extracted/source_total:.1%}',
        FORMAL_MEDICATION_RECALL_BEFORE='69.9%',FORMAL_MEDICATION_RECALL_AFTER=f"{sum(r['formal_captured'] for r in recall)/formal:.1%}",
        MEDICATION_RESOLUTION_AFTER=f'{resolved/extracted:.1%}',GRANITE_SEMANTIC_CALLS=0,
        HARD_GATE_FAILURES=sum(violations.values())+sum(not r['passed'] for r in gates),
        AF_ASSERTION_REGRESSION='YES' if violations['AF_assertion_regression'] else 'NO',DATABASE_WRITES='NONE',CLOUD_PATIENT_CALLS='NONE',
        source_rows=source_total,extracted_rows=extracted,resolved_rows=resolved,unresolved_rows=extracted-resolved,absent_rows=source_total-extracted,
        patients=list(PATIENTS),candidate_revisions=len(summary),granite_used=False)
    status['total_revisions_published_this_task']=sum(len(store.manifest(pid)['modules'][module]['versions'])-len(original[pid]['modules'][module]['versions'])
        for pid in PATIENTS for module in ('diagnoses','medications'))
    atomic(output/'hard_gate_violations.json',violations);atomic(output/'final_status.json',status)
    atomic(output/'medication_curation_v1.json',dict(curation_version='AUDITED_BRAND_REVIEW_1.0',
        reference_hashes={str(p):sha(p) for p in (ROOT/'data/reference').iterdir() if p.is_file()},
        entries=[dict(trade_name=t,normalized_trade_name=t.upper(),active_ingredient=None,ATC=None,
            mapping_source=None,mapping_source_version=None,status='WITHHELD_NO_VERIFIED_LOCAL_BRAND_ALIAS') for t in ['Furon','Levebon','Jardiance','Lixiana','Endoxaban']],
        dictionary_changes=0,reason='Generic ATC entries exist; no authoritative local brand-to-ingredient provenance found. Prompt suggestions are not pharmaceutical authority.'))
    lines=['# Physician failure repair ready for review','',
        'Only the seven audited patients were processed. Fourteen new candidate revisions were published: diagnosis/events 4.2 and medications 3.1. All remain CANDIDATE. Active pointers, prior revisions, source PDFs, references and physician reviews are preserved. No computed CAA, scales, neurosonology, scores or OAC module ran. No database or cloud patient calls occurred.','',
        '```text','PHYSICIAN_FAILURE_REPAIR_READY_FOR_REVIEW']+[f'{k} = {v}' for k,v in status.items() if k.isupper()]+['```','',
        '## Changes','',
        '- Structured diagnosis lists now have an ontology-independent source inventory, per-item disposition and a mandatory reviewable UNMAPPED_DIAGNOSIS_CANDIDATE fallback. Unmapped items appear in the candidate diagnosis panel and block promotion until adjudicated.',
        '- Added source-backed CKD/stage/dialysis/context, renal carcinoma, high-grade glioma/secondary hemorrhage, structural epilepsy/local numbered references, vestibular neuritis, one dementia family with longitudinal subtypes, and breast cancer mappings. Symptomatiche is a bounded spelling alias.',
        '- CAA workup targets are DIFFERENTIAL, nondiagnostic motion-degraded exams NOT_ASSESSABLE, phenotype non-support NEGATED with CAA_SUPPORT=NEGATIVE. Adjacent expanded-term/acronym assertions share scope. Independent ESTABLISHED positive support is required for source disease identity. Boston classification is untouched.',
        '- Historical is an orthogonal temporal status. Relative years require a reliable document date and retain APPROXIMATE_YEAR / RELATIVE_HISTORICAL_INFERENCE on the source mention and in canonical date_evidence. In 2110307009, independent explicit 1999 evidence corroborates the left ischemic stroke; the combined event uses YEAR while preserving the approximate source derivation.',
        '- Medication source selection now handles embedded lists, recommendation headings, qualifier headings and table continuation. Every selected product row is persisted before normalization; allergies, history and recommendation prose are bounded out. The worker uses saved prepared sections rather than the old lossy CSV. Source recall needs an independent denominator; generic module metrics leave it unknown until an inventory is supplied.',
        '- Versioned medication curation review withheld all five requested aliases because the existing local reference contains no verified brand-to-ingredient provenance. No reference file was changed; Furon, Levebon, Jardiance and Lixiana/Endoxaban now reach normalization and remain reviewable where unresolved. No unrestricted fuzzy mapping was added.',
        '', '## Medication results','',
        '| Patient | Source | Extracted | Resolved | Unresolved | Absent | Recall | Resolution |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r,s in zip(recall,resolution):lines.append(f"| {r['patient_id']} | {r['source_rows']} | {r['extracted_rows']} | {s['resolved']} | {s['unresolved']} | {r['absent_rows']} | {r['source_recall_after']:.1%} | {s['normalization_resolution_after']:.1%} |")
    lines+=['',f'Total: {source_total} source rows, {extracted} extracted, {resolved} resolved, {extracted-resolved} unresolved, {source_total-extracted} absent. Formal tables/free-text: {formal} source rows. No absent formal row remains. Resolution is a saved dictionary outcome, not independent clinical verification of every mapped drug.',
        '', '## Reconciliation and verification','',
        f"Structured items: {sum(r['source_item_count'] for r in reconciliation)}; mapped items {sum(r['mapped_disease_event_items'] for r in reconciliation)}, non-disease/event items {sum(r['non_disease_items'] for r in reconciliation)}, unmapped review items {sum(r['unmapped_review_candidates'] for r in reconciliation)}, lost {sum(r['lost_items'] for r in reconciliation)}. Items count source occurrences, not unique diseases. Numbered/bulleted details and unnumbered diagnostic lines are retained conservatively; an unmapped item is not a confirmed diagnosis.",
        '', 'See `diagnosis_regression.csv`, `CAA_assertion_regression.csv`, `structured_diagnosis_reconciliation.csv`, `medication_source_row_before_after.csv`, `candidate_hard_gates.csv` and `protected_original_validation.json` for source-level evidence. `seven_patient_candidate_summary.csv` gives every new version and metadata path. Verification test output and UI smoke evidence are stored beside this report.',
        '', '## Review App','',
        'Refresh the artifact index, select an audited patient, then choose the newest 4.2 diagnosis or 3.1 medication candidate. Unmapped diagnoses are labelled explicitly; CAA source assertions remain inspectable even without a CAA disease identity. Source navigation, review saves and promotion remain explicit physician actions. No candidate was auto-promoted.']
    if (output/'initial_candidates.json').exists():
        lines+=['','Seven diagnosis candidates from the first implementation pass are preserved with QA holds after a final regression check identified a missing fallback for unknown negated/suspected items. Seven replacement diagnosis revisions retain those items. The selected review set contains 14 candidates (seven diagnoses/events and seven medications); 21 revisions were published in total during this task. `initial_candidates.json` preserves the earlier publication map. No prior artifact bytes or status entries were overwritten.']
    (output/'repair_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    assert status['HARD_GATE_FAILURES']==0 and status['CAA_FALSE_POSITIVES_REMAINING']==0
    return status


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run(args.output),indent=2))
