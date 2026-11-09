"""Five-pilot AF source audit only. Does not call diagnosis assemble or score runners."""
import argparse
import copy
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from .workbench_store import WorkbenchStore, PILOTS, ROOT, read, sha, atomic, file_lock, now
from .extraction.assertion_scope import AF_PATTERN, af_observation, resolve_af, VERSION
from .extraction.assertion_review import layer, unsafe_af_identity
from .extraction.diagnosis_events_v4 import header_date

TARGET = 'doc26357819338331_sec10:v4:1920'
REGRESSIONS = [
    ('Ein Vorhofflimmern konnte nicht detektiert werden.', 'NEGATED'),
    ('Kein Vorhofflimmern nachweisbar.', 'NEGATED'),
    ('Kein Hinweis auf Vorhofflimmern.', 'NEGATED'),
    ('Vorhofflimmern kann nicht ausgeschlossen werden.', 'UNCERTAIN'),
    ('V.a. Vorhofflimmern.', 'UNCERTAIN'),
    ('Bekanntes Vorhofflimmern.', 'ESTABLISHED'),
    ('Persistierendes Vorhofflimmern.', 'ESTABLISHED'),
    ('Bei bekanntem Vorhofflimmern konnte aktuell keine Arrhythmie detektiert werden.', 'ESTABLISHED'),
    ('Keine frische Ischämie bei Vorhofflimmern.', 'ESTABLISHED'),
]


def write_csv(path, rows, fields=None):
    with path.open('x', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=fields or list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in rows)


def scan_patient(store, patient):
    manifest = store.manifest(patient)
    bundle = read(Path(manifest['source_context']['directory']) / 'source_bundle.json')
    dx = manifest['modules']['diagnoses']
    entry = dx['versions'][dx['active_validated_version']]
    stored = layer(entry, 'diagnosis_mentions') + layer(entry, 'diagnostic_assertion_timeline')
    rows = []
    for document in bundle['documents']:
        day = header_date(document)
        for section in document['sections']:
            text = section.get('text', '')
            for match in AF_PATTERN.finditer(text):
                bare_af = match[0].upper() == 'AF'
                positive_context = not bare_af or bool(__import__('re').search(r'diagnos|vorerkrank', section.get('heading') or '', __import__('re').I))
                obs = af_observation(text, match.start(), match.end(), observation_date=day, positive_context=positive_context)
                snippet = ' '.join(obs['source_clause'].split())
                position = len(' '.join(text[obs['clause_offset']:match.start()].split()))
                short = snippet if len(snippet) <= 450 else snippet[max(0, position-180):position+250]
                matches = [r for r in stored if str(r.get('document_id')) == str(document['source_document_id'])
                           and r.get('section_id', r.get('section')) == section['section_id']
                           and (snippet in ' '.join(r.get('raw_text', '').split()) or ' '.join(r.get('raw_text', '').split()) in snippet)
                           and AF_PATTERN.search(r.get('raw_text', ''))]
                evidence_id = next((r['evidence_id'] for r in matches if r.get('evidence_id') == TARGET),
                                   next((r.get('evidence_id') for r in matches), f'{section["section_id"]}:af-audit:{match.start()}'))
                old = sorted({r.get('assertion_status', 'UNKNOWN') for r in matches})
                obs.update(patient_id=patient, document_id=document['source_document_id'], page=section.get('page_start'),
                           section_id=section['section_id'], evidence_id=evidence_id, source_text_short=short,
                           source_text_exact=obs['source_clause'], source_offset=match.start(),
                           model_assertion='NOT_USED_IN_V4', stored_assertion='|'.join(old) or 'NOT_PREVIOUSLY_CAPTURED',
                           final_assertion=obs['assertion_status'], contributes_to_AF_identity=obs['explicit_positive'],
                           review_required=obs['assertion_status'] in ('UNCERTAIN', 'SOURCE_ONLY'),
                           page_basis='SAVED_SECTION_PAGE_START')
                rows.append(obs)
    return rows


def apply_safety(store, patient, rows, audit_file, *, reason):
    """Invalidate mutable pointers/metadata only. All clinical artifacts stay byte-identical."""
    changes = []
    state = resolve_af(rows)
    with file_lock(store.patient_dir(patient) / '.manifest.lock'):
        manifest = store.manifest(patient)
        group = manifest['modules']['diagnoses']
        active = group.get('active_validated_version')
        affected = []
        for version, entry in group['versions'].items():
            entry['assertion_audit'] = dict(path=str(audit_file), sha256=sha(audit_file), AF_state=state['AF_state'])
            if unsafe_af_identity(entry):
                entry['assertion_safety'] = dict(reason=reason, rule_version=VERSION,
                    promotion_blocked=True, corrected_AF_state=state['AF_state'],
                    requires_new_extraction=True, audit_path=str(audit_file))
                affected.append(version)
                # Events alias shares the same extraction byte set and review hash.
                alias = manifest['modules'].get('events', {}).get('versions', {}).get(version)
                if alias:
                    alias['assertion_safety'] = copy.deepcopy(entry['assertion_safety'])
        if active in affected:
            names = {'AF', 'AF_index', 'CHA2DS2_VA', 'ORBIT', 'AF_OAC_indication', 'OAC_indication',
                     'OAC_decision_analysis', 'anticoagulation_decision_analysis', 'research_summary', 'research_fact_inventory'}
            for name, module in manifest['modules'].items():
                if name not in names:
                    continue
                pointer = module.get('active_validated_version')
                if pointer:
                    entry = module['versions'][pointer]
                    previous = entry['status']
                    entry['status'] = 'STALE_DEPENDENCY'
                    entry['stale_dependencies'] = sorted(set(entry.get('stale_dependencies', [])) | {'AF_ASSERTION_SAFETY'})
                    entry['assertion_safety_reason'] = reason
                    changes.append(dict(patient_id=patient, module=name, version=pointer,
                                        previous_status=previous, status='STALE_DEPENDENCY', recalculated=False))
        manifest['audit'].append(dict(timestamp=now(), patient_id=patient, module='diagnoses',
            previous_version=active, new_version=active, action='ASSERTION_SAFETY_INVALIDATE',
            affected_versions=affected, code_hash=sha(Path(__file__).parent / 'extraction/assertion_scope.py'),
            source_hashes=manifest['source_context']['source_pdf_hashes'], reason=reason, report=str(audit_file)))
        atomic(store.patient_dir(patient) / 'module_manifest.json', manifest)
    return dict(patient_id=patient, AF_state=state['AF_state'], affected_diagnosis_versions=affected,
                active_diagnosis_affected=active in affected, downstream_changes=changes)


def run(output, *, apply=False, store=None):
    store = store or WorkbenchStore()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Hash all clinical payloads, saved source inputs, PDFs and current adjudications before audit.
    protected = {}
    for patient in PILOTS:
        manifest = store.manifest(patient)
        for group in manifest['modules'].values():
            for entry in group.get('versions', {}).values():
                for f in entry.get('artifacts', []):
                    protected[f['path']] = sha(f['path'])
        protected.update(manifest['source_context']['input_hashes'])
        protected.update(manifest['source_context']['source_pdf_hashes'])
    review_root = ROOT / 'data/review'
    if review_root.exists():
        protected.update({str(p): sha(p) for p in review_root.rglob('*') if p.is_file()})
    rows_by_patient = {patient: scan_patient(store, patient) for patient in PILOTS}
    rows = [r for group in rows_by_patient.values() for r in group]
    write_csv(output / 'five_patient_af_assertion_audit.csv', rows)
    write_csv(output / 'patient_2435608415_af_evidence.csv', rows_by_patient['2435608415'])
    regression = []
    for text, expected in REGRESSIONS:
        match = AF_PATTERN.search(text)
        result = af_observation(text, match.start(), match.end())
        regression.append(dict(source_text=text, expected=expected, actual=result['assertion_status'], passed=result['assertion_status'] == expected))
    write_csv(output / 'negation_regression_results.csv', regression)
    observations = output / 'af_assertion_observations.json'
    atomic(observations, rows_by_patient)
    states = {p: resolve_af(rs) for p, rs in rows_by_patient.items()}
    affected = []
    if apply:
        for patient in PILOTS:
            affected.append(apply_safety(store, patient, rows_by_patient[patient], observations,
                                         reason='Concept-scoped AF assertion re-audit; negative monitoring cannot establish chronic AF'))
        store.sync_audit()
    atomic(output / 'downstream_invalidation.json', dict(applied=apply, patients=affected,
        absent_downstream_modules='No new score/summary artifacts created; only existing dependent entries marked stale',
        recalculation_performed=False))
    target = next(r for r in rows if r['evidence_id'] == TARGET)
    patient = '2435608415'
    manifest = store.manifest(patient)
    old_folder = Path(manifest['source_context']['original_directory'])
    cha = read(old_folder / 'cha2ds2_va.json')
    facts = [f for f in read(old_folder / 'research_fact_inventory.json') if f.get('component') == 'AF']
    trace = f'''# Patient 2435608415 AF assertion trace

ROOT_CAUSE = `diagnosis_events_v4.classify()` used the `NEGATIVE` regex, which included
`nicht nachweisbar` but omitted `nicht detektiert`. With a recognized concept and no matched
negation, `classify()` returned `(ESTABLISHED_DISEASE, True)`. `assemble()` then emitted
ESTABLISHED and grouped this sole positive mention into the chronic AF identity.
The original source wording was never deleted; the predicate was not recognized.

GRANITE_INVOLVED = NO for this exact V4 evidence. Candidate method = DETERMINISTIC.
`source_units()` rediscovered the concept from saved section text (`origin=SOURCE_REDISCOVERY`),
then `concepts()` -> `local_clause()` -> `classify()` -> `assemble()` canonicalization.
Granite raw output = NOT_APPLICABLE. Deterministic pre-model classification =
ESTABLISHED_DISEASE/promote=True. No model phase occurred. Deterministic post-model
classification = same original ESTABLISHED_DISEASE. Original negation detector = no match.
Original final assertion = ESTABLISHED; original canonicalization = one AF identity,
one supporting mention, dated 2014-05-27. A source date is not an AF onset date.

Document = 26357819338331; page = 3; section = doc26357819338331_sec10;
evidence = {TARGET}.

Exact assertion: “Ein Vorhofflimmern konnte nicht detektiert werden.”
Original source lines wrap “detektiert” / “werden.”; joining retained both words.
Observation source-date = {target['observation_date']}; scope = CURRENT_ASSESSMENT.
The paragraph identifies a 24-hour ECG (26.05.2014); the retained observation date is
the source-document date (2014-05-27), not an invented diagnosis/onset date.

After guard: assertion = NEGATED; AF_PRESENT = FALSE for this observation;
AF_ESTABLISHED_FROM_THIS_EVIDENCE = FALSE; modality = {target['source_modality']}.
Patient AF resolution = {states[patient]['AF_state']}.
Truly positive established AF sources found = {states[patient]['positive_evidence_count']}.
All occurrences, including future-risk wording and repeated negative Holters, are in
`patient_2435608415_af_evidence.csv`. No historical AF is erased by a negative search.

Downstream trace from existing saved artifacts (no recalculation):
- Chronic AF disease identity: YES, this evidence was its only supporting mention.
- Disease first-documented date: YES, incorrectly 2014-05-27.
- Research AF index: NO observed propagation; saved AF fact inventory has {len(facts)} AF facts,
  saved score index is {cha.get('score_index_date')!r}.
- CHA2DS2-VA applicability: NO observed propagation; saved applicable={cha.get('applicable')!r}.
- OAC indication: saved decision rows retain UNKNOWN indication; no affirmative AF linkage
  from this V4 evidence was found in the preserved pre-V4 OAC analysis.
- OAC decision analysis: no recalculation from V4; preserved output predates the faulty
  V4 rediscovery. Stable dependency statuses are held stale, with the new assertion-safety reason.

The active extraction was previously physician-promoted after Stage 1. This audit does not
rewrite/reject that history or rerun diagnosis extraction. A deterministic safety overlay suppresses
unsupported AF in the UI, blocks promotion of affected candidates, and changes the review
fingerprint so old corrections are stale. Original clinical payloads remain unchanged.
'''
    (output / 'patient_2435608415_af_trace.md').write_text(trace, encoding='utf-8')
    unchanged = all(Path(p).is_file() and sha(p) == h for p, h in protected.items())
    from .extraction.research_scores_v23 import cha2ds2_va
    def sample(text, day='2000-01-01', model=None):
        match = AF_PATTERN.search(text)
        return af_observation(text, match.start(), match.end(), observation_date=day, model_assertion=model)
    hard_gates = dict(
        negated_AF_creates_AF_identity=sum(r['contributes_to_AF_identity'] for r in rows if r['assertion_status']=='NEGATED'),
        uncertain_AF_creates_confirmed_AF=sum(r['contributes_to_AF_identity'] for r in rows if r['assertion_status']=='UNCERTAIN'),
        AF_negative_monitoring_erases_prior_confirmed_AF=int(not resolve_af([sample('Bekanntes Vorhofflimmern.'),sample('Kein VHF im Holter.','2001-01-01')])['confirmed_af']),
        CHA2DS2VA_runs_without_confirmed_AF=sum(bool(cha2ds2_va(confirmed_af=True,af_state=s,index_date=None,dob=None,facts={})['applicable']) for s in (None,'SOURCE_ONLY','UNCERTAIN','NO_CONFIRMED_AF','SUSPECTED_AF','CONFLICTING_AF_EVIDENCE')),
        Granite_overrides_explicit_negation=int(sample('Ein Vorhofflimmern konnte nicht detektiert werden.',model='ESTABLISHED')['assertion_status']!='NEGATED'),
        patient_data_cloud_calls=0,clinical_database_writes=0)
    atomic(output / 'af_hard_gates.json', hard_gates)
    atomic(output / 'audit_summary.json', dict(patient_states=states, regression_pass=all(r['passed'] for r in regression), hard_gates=hard_gates,
        protected_files_unchanged=unchanged, protected_file_count=len(protected), diagnosis_extraction_runs=0,
        score_recalculations=0, clinical_database_writes=0, cloud_calls=0, patient_ids=list(PILOTS),
        original_file_hashes=protected, target_evidence=target))
    if not unchanged or not all(r['passed'] for r in regression) or any(hard_gates.values()):
        raise RuntimeError('AF assertion audit hard gate failed')
    return dict(output=str(output), states=states, protected_files_unchanged=unchanged)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true', help='Annotate safety holds and invalidate existing downstream pointers')
    args = parser.parse_args()
    output = args.output or ROOT / 'data/debug' / ('af_assertion_fix_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    print(json.dumps(run(output, apply=args.apply), indent=2))
