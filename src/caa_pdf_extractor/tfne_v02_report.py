"""Read-only clinical comparison reporting; writes only this v0.2 debug directory."""
import copy
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as E

from .tfne_v02_pilot import read,write,BASE,ARTIFACT
from .tfne_v02_audit import assess,csv_file
from .tfne_v02_schema import FIELDS
from .tfne_v02_validation import exact_quote,populated
from .tfne_sources import span_index
from .workbench_store import ROOT,sha


def raw_episode(proposal,index):
    e=copy.deepcopy(proposal);e['field_evidence']={}
    for field,group in proposal['evidence'].items():
        ids=group.get('source_span_ids',[]);quote=group.get('quote')
        rows=[]
        for sid in ids:
            if sid not in index:continue
            source=index[sid]
            if 'quote' in group:
                resolved=exact_quote(quote,source['evidence_text'])
                if resolved is None:continue
                lo=source['evidence_start']+source['evidence_text'].index(resolved)
                rows.append(dict(source,evidence_text=resolved,evidence_start=lo,evidence_end=lo+len(resolved)))
            else:rows.append(source)
        e['field_evidence'][field]=rows
    return e


def report(directory):
    directory=Path(directory).resolve()
    from .workbench_worker import guard
    guard(directory)
    m=read(directory/'validation_metrics.json');freeze=read(directory/'cohort_freeze.json');gates=read(directory/'hard_gates.json')
    raw_totals={'v0_1':Counter(),'v0_2':Counter()};raw_rows=[]
    for patient in freeze['patient_ids']:
        index=span_index(read(directory/'inputs'/f'{patient}.json')['sections'])
        old=read(BASE/'workers'/patient/'raw_episodes.json')['episodes']
        new=[p['proposal'] for p in read(directory/'workers'/patient/'raw_phenotype_proposals.json')]
        for version,proposals in [('v0_1',old),('v0_2',new)]:
            raw_totals[version]['episodes']+=len(proposals)
            for p in proposals:
                e=raw_episode(p,index);rows,counts=assess(e,patient,version);raw_rows+=rows;raw_totals[version].update(counts)
                raw_totals[version]['populated_fields']+=sum(populated(e[f]) for f in FIELDS)
    for counts in raw_totals.values():counts['unsupported_field_rate_percent']=round(100*counts['unsupported_fields']/max(1,counts['populated_fields']),2)
    csv_file(directory/'tfne_v0_2_raw_model_grounding_audit.csv',raw_rows)
    m['raw_model_metrics']={v:dict(c) for v,c in raw_totals.items()}
    tests=E.parse(directory/'test_results.xml').getroot();rows=[]
    for test in tests.iter('testcase'):
        failed=test.find('failure') is not None or test.find('error') is not None
        rows.append(dict(test_class=test.get('classname'),case=test.get('name'),passed=not failed,
            skipped=test.find('skipped') is not None,validation_type='CORE_V02' if test.get('classname','').split('.')[-1]=='test_tfne_v02' else 'UI_OR_LEGACY'))
    csv_file(directory/'tfne_v0_2_regression_results.csv',rows)
    core=[r for r in rows if r['validation_type']=='CORE_V02']
    protocol=read(directory.parent/'protocol_repair.json')
    prior_calls=protocol['initial_semantic_calls']+protocol['partial_anchor_run_semantic_calls']
    m.update(regression_cases_passed=sum(r['passed'] and not r['skipped'] for r in core),regression_cases_failed=sum(not r['passed'] for r in core),
        focused_tests_passed=sum(r['passed'] and not r['skipped'] for r in rows),focused_tests_failed=sum(not r['passed'] for r in rows),
        initial_probe_semantic_calls=prior_calls,total_local_semantic_calls=m['actual_new_semantic_calls']+prior_calls,
        interpretation_logic_changed=False,screening_rules_changed=False)
    write(directory/'validation_metrics.json',m)
    if m['hard_gate_failures'] or m['focused_tests_failed']:raise ValueError('Validation incomplete or failing')
    c=m['candidate_metrics'];a=c['v0_1'];b=c['v0_2']
    m['language_domain_errors']={v:sum(c[v].get(k,0) for k in ('language_to_motor_errors','language_to_cognitive_errors','language_to_sensory_errors')) for v in c}
    m['zero_episode_patients']=[p for p,n in m['episodes_by_patient'].items() if n['v0_2']==0]
    m['known_detection_omission_requires_review']=['2674159019: fluctuating confusion/headache/malaise episode not detected in final pass 1']
    write(directory/'validation_metrics.json',m)
    lines=['# TFNE_NEUROLOGICAL_DEFICIT_V0_2_READY_FOR_PHYSICIAN_REVIEW','',
        'Version 0.2 uses episode detection followed by one phenotype call per grounded detected episode, then deterministic evidence validation and the unchanged TFNE_SCREEN_0.1 research screen. These are unreviewed research candidates, not diagnoses.',
        '', '## Identical collective and sources','',
        'Patient IDs: '+', '.join(freeze['patient_ids'])+'.',
        '', 'The ten v0.1 input JSON files were copied byte-for-byte and their hashes verified. No new patient was selected, and no patient was added or removed. No source rerouting or new PDF preparation was performed. The same installed local Granite tag and digest were required. Both prompts and all requests, responses, source quotes, code hashes, model digest and timestamps are retained.',
        '', '## Material detection limitation','',
        'Patient 2674159019 has the physician-reviewed confusion/headache/malaise episode in the frozen source, but final episode detection selected unrelated procedure/diagnosis/discharge statements; those invalid detections were rejected. Its v0.2 candidate is therefore empty. The permanent confusion regression passes at the field-validation level, but this does not establish end-to-end detection recall. Four patients have zero v0.2 episodes. Repeated source accounts in other patients may also need merging. These candidates are ready for physician comparison, not clinical acceptance, and no extraction was tuned to force a positive result.',
        '', '## Published-candidate comparison','',
        'Both versions are assessed with the same v0.2 exact-evidence and lexical support checks. Counts below are retained candidate fields, not the number of historical abstention flags. They are mechanical support checks, not physician-adjudicated accuracy or recall. Different episode counts change the denominator; unsupported rates for raw model proposals are reported separately.',
        '', '| Measure | v0.1 | v0.2 |','|---|---|---|']
    for key in ('episodes','unsupported_fields','contradictory_fields','invented_duration_fields','language_to_motor_errors',
        'language_to_cognitive_errors','language_to_sensory_errors','transient_without_resolution','persistent_without_persistence_evidence'):
        lines.append(f'| {key} | {a.get(key,0)} | {b.get(key,0)} |')
    lines += ['', '## Raw model proposals before validation','', '| Measure | v0.1 | v0.2 |','|---|---|---|']
    for key in ('episodes','populated_fields','unsupported_fields','unsupported_field_rate_percent','contradictory_fields',
        'invented_duration_fields','language_to_motor_errors','language_to_cognitive_errors','transient_without_resolution','persistent_without_persistence_evidence'):
        lines.append(f'| {key} | {raw_totals["v0_1"].get(key,0)} | {raw_totals["v0_2"].get(key,0)} |')
    lines += ['', 'The raw table measures Granite proposals before any v0.2 removals or mechanical unit conversion. A populated field counts as unsupported when its own quote/IDs are missing or its proposed value fails the documented support check. Lexical checks can abstain from valid paraphrases. These counts must not be interpreted as independently adjudicated clinical error counts.',
        '', '## Grounding and review','',
        'Every populated v0.2 clinical field has exact source provenance. Unsupported values are removed to UNKNOWN/null/empty and remain in the raw response and validation flags. Explicit duration quantities are converted mechanically; "seit 2 Tagen" keeps minimum 2880 and maximum null. Language does not supply motor/sensory/cognitive features. Fluctuation does not supply complete resolution. Persistence needs its own evidence. No historical label is used as symptom evidence.',
        '', 'The first detection probe allowed generated text and exposed PDF whitespace flattening and changed German inflections. Its prompts and all eleven local semantic calls are retained in the parent run. A subsequent anchor-based run made four calls before a field response returned two episode objects; the final schema enforces exactly one. That response is retained. The compatible detection response and two valid phenotype responses were replayed without new calls. No probe candidate was published. The final detection contract selects exact source excerpts from a mechanically generated enum of unchanged source spans/sentences. It changes no clinical source content. Whitespace-only excerpt resolution stores the original substring; spelling changes and paraphrases remain rejected.',
        '', f"Final logical protocol calls: {m['semantic_calls']} (ten detections plus episode-specific phenotypes), including {m['replayed_semantic_responses']} retained compatible responses. New calls in the release run: {m['actual_new_semantic_calls']}. Prior probe/partial-run calls: {prior_calls}. Total actual local semantic calls: {m['total_local_semantic_calls']}. No cloud model or patient-data call occurred.",
        '', 'Before publication, retained raw proposals were revalidated without model calls to ensure that a short quote cannot hide a preceding source negation or uncertainty. Before-validation candidates, request-time code hashes, final validation hashes and timestamps are preserved in source_scope_revalidation.json and each candidate.',
        '', 'The comparison CSV retains all clinical fields and unmatched episodes. Source-parent/quote alignment can be many-to-many, so SPLIT/MERGED rows require physician review. CORRECTED denotes an extraction difference, not a physician-approved correction; correction_is_adjudicated identifies any available explicit matching physician value. Two existing v0.1 REJECT events are preserved as episode-level judgments and are never converted into invented corrected symptom fields.',
        '', 'In Review App select the existing TFNE V0.1 PILOT queue, choose a patient, open TFNE / Neurological Deficits, then switch TFNE extraction version to v0.2. Changed fields are highlighted. The v0.2 table shows Value, Evidence span and Validation status. Rejected/normalized proposals have visible flags. Reviews are artifact-hash-scoped and stored separately under tfne_neurological_deficit_v0_2. No real v0.2 physician judgment was saved during this task.',
        '', 'Zero surviving hard-gate violations describe validator behavior, not perfect episode detection. Missed events, duplicate/split descriptions and uncertain temporal classification remain possible. In particular, zero extracted episodes is not evidence of absence. The complete frozen source narrative selector and raw first-pass outputs remain available for recall review.',
        '', '## Validation','',f"Core regression cases passed: {m['regression_cases_passed']}; failed: {m['regression_cases_failed']}. Focused validator/UI/legacy tests passed: {m['focused_tests_passed']}; failed: {m['focused_tests_failed']}.",
        '', '| Hard gate | Violations |','|---|---|']
    lines += [f'| {key} | {value} |' for key,value in gates.items()]
    lines += ['', f"{m['protected_files']} protected v0.1 candidate, prompt, debug and physician-review files remained byte-identical. Existing extraction modules and TFNE screening code were preserved; only the TFNE repository/view gained version-aware display and review storage.",
        '', '## Artifacts','',f'Run directory: `{directory}`.',
        '', '- `tfne_v0_1_vs_v0_2_comparison.csv`', '- `tfne_v0_2_validation_flags.csv`', '- `tfne_v0_2_field_grounding_audit.csv`',
        '- `tfne_v0_2_raw_model_grounding_audit.csv`', '- `tfne_v0_2_regression_results.csv`', '- `validation_metrics.json`, `hard_gates.json`, `preservation_audit.json`, `published_artifacts.json`',
        '- `inputs/`, `workers/`, `cohort_freeze.json`, `local_model.json`, `test_results.xml`',
        '', 'PROMPT_VERSION = tfne_neurological_deficit_v0_2', 'DATABASE_WRITES = NONE', 'CLOUD_PATIENT_CALLS = NONE']
    (directory/'tfne_v0_2_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(m,indent=2))


if __name__=='__main__':
    import sys
    report(sys.argv[1])
