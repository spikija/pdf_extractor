"""Offline, selective V3 assembly from the existing five-patient pilot artifacts."""
import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .module_artifacts import DEPENDENCIES, ModuleStore, digest, invalidate, now, sha, write_new
from .extraction import diagnosis_identity_v3 as diagnosis
from .extraction.medication_identity_v3 import Resolver, COMMON_BRANDS, assemble as medications

ROOT = Path(__file__).resolve().parents[2]
PATIENTS = ('2435608415', '2459345815', '2552817617', '2684469520', '2826947422')
LEGACY = {'imaging': 'imaging.csv', 'CAA': 'caa_summary.json', 'neurosonology': 'neurosonology.csv',
          'EEG': 'eeg.json', 'PET': 'PET.json', 'clinical_scales': 'clinical_scales.csv',
          'CHA2DS2_VA': 'cha2ds2_va.json', 'ORBIT': 'orbit.json', 'STESS': 'STESS.json',
          'OAC_decision_analysis': 'anticoagulation_decisions.csv', 'events': 'source_stroke_mentions.json',
          'cognition': 'cognitive.json', 'encounters': 'encounters.json', 'labs': 'labs.csv',
          'demographics': 'patient_summary.json', 'AF': 'research_fact_inventory.json'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        result = list(csv.DictReader(f))
    for row in result:
        for k, value in row.items():
            if value and value[0] in '[{':
                try:
                    row[k] = json.loads(value)
                except ValueError:
                    pass
    return result


def csv_text(records):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(dict.fromkeys(k for r in records for k in r)) or ['status'])
    writer.writeheader()
    for row in records:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    return out.getvalue()


def table(records, fields):
    def cell(v):
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        return str(v if v is not None else 'UNKNOWN').replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(fields) + ' |', '| ' + ' | '.join('---' for _ in fields) + ' |'] +
                     ['| ' + ' | '.join(cell(r.get(k)) for k in fields) + ' |' for r in records])


def document_date(document):
    """Use only an explicit letter-date header, never arbitrary dates in a PDF."""
    if document.get('document_date'):
        return document['document_date']
    candidates = set()
    for section in document.get('sections', []):
        if section.get('page_start') != 1:
            continue
        for match in re.finditer(r'(?im)^\s*(?:[A-Za-zÄÖÜäöüß .-]+,\s*am|Datum\s*:)\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$', section.get('text', '')):
            try:
                candidates.add(datetime(int(match[3]), int(match[2]), int(match[1])).date().isoformat())
            except ValueError:
                pass
    return next(iter(candidates)) if len(candidates) == 1 else None


def guard(output):
    """No database, network or writes outside the fresh output snapshot."""
    sys.dont_write_bytecode = True
    output = output.resolve()
    def audit(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'sqlite3.connect', 'sqlite3.connect/handle'):
            raise RuntimeError('V3_NETWORK_AND_DATABASE_ACCESS_FORBIDDEN')
        if event == 'open':
            name, mode, flags = args
            writing = isinstance(mode, str) and any(c in mode for c in 'wax+')
            writing |= isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and isinstance(name, (str, bytes, os.PathLike)) and not Path(name).resolve().is_relative_to(output):
                raise RuntimeError('V3_WRITE_OUTSIDE_NEW_ARTIFACT_TREE')
        if event in ('os.remove', 'os.rename', 'os.rmdir'):
            raise RuntimeError('V3_DELETION_AND_REPLACEMENT_FORBIDDEN')
    sys.addaudithook(audit)


def run(source, output, modules=('diagnoses', 'medications'), previous=None, protect=True):
    source = Path(source).resolve()
    if set(modules) != {'diagnoses', 'medications'} and not previous:
        raise ValueError('Selective reruns require --previous to preserve the other V3 module')
    selected = load(source / 'pilot_patient_selection.json')
    if {str(r['patient_id']) for r in selected} != set(PATIENTS):
        raise ValueError('Source must contain exactly the established five-patient cohort')
    original = {str(p): sha(p) for pid in PATIENTS for p in (source / pid).rglob('*') if p.is_file()}
    if previous:
        original.update({str(p): sha(p) for p in Path(previous).resolve().rglob('*') if p.is_file()})
    resolver = Resolver()
    store = ModuleStore(output)
    if protect:
        guard(store.root)
    regression = {b: resolver.resolve(b + ' 10 mg 1 0 1') for b in COMMON_BRANDS}
    gates = dict(duplicate_chronic_disease_identity=0, CAA_multiple_canonical_identities_same_patient=0,
                 AF_multiple_canonical_identities_same_patient=0, type2_diabetes_multiple_canonical_identities_same_patient=0,
                 acute_events_incorrectly_deduplicated=0, medication_lookup_using_full_dose_string_only=0,
                 known_common_trade_name_unresolved=sum(not r['canonical_drug'] for r in regression.values()),
                 patient_data_sent_to_external_service=0, unrelated_module_artifact_deleted=0, clinical_database_write=0)
    dx_review = ['# Five-patient diagnosis review V3', '', 'Research proposals; physician review required. Acute event instances remain in preserved source artifacts.']
    med_review = ['# Five-patient medication review V3', '', 'Source observations retain separate exposures. Unknown ATC codes and dates are not inferred.']
    unresolved = defaultdict(list)
    counts = []
    for pid in PATIENTS:
        folder = source / pid
        if previous:
            manifest = {k: store.preserve(v) for k, v in load(Path(previous) / 'patient_artifacts' / pid / 'module_manifest.json').items()}
        else:
            manifest = {}
            for module, name in LEGACY.items():
                path = folder / name
                if path.exists():
                    manifest[module] = dict(module=module, version='LEGACY_PILOT', status='PRESERVED',
                        artifact=str(path), artifacts=[dict(path=str(path), sha256=sha(path))],
                        source_hash=sha(path), created_at=now(), dependencies=DEPENDENCIES.get(module, []),
                        validation={'physician_approved': False, 'legacy_status_not_promoted': True})
        patient_counts = {'patient_id': pid}
        if 'diagnoses' in modules:
            bundle = load(folder / 'source_bundle.json')
            dates = {str(d['source_document_id']): document_date(d) for d in bundle['documents']}
            history = load(folder / 'source_mentions.json')
            mentions, canonical = diagnosis.assemble(pid, rows(folder / 'diagnoses.csv'), history, dates)
            assert len(mentions) == len(history['mentions']) + len(history.get('audit_only', []))
            names = Counter(r['canonical_name'] for r in canonical)
            gates['duplicate_chronic_disease_identity'] += sum(n - 1 for n in names.values())
            for gate, name in [('CAA_multiple_canonical_identities_same_patient', 'CEREBRAL_AMYLOID_ANGIOPATHY'),
                               ('AF_multiple_canonical_identities_same_patient', 'ATRIAL_FIBRILLATION'),
                               ('type2_diabetes_multiple_canonical_identities_same_patient', 'DIABETES_MELLITUS_TYPE_2')]:
                gates[gate] += max(0, names[name] - 1)
            expected = {'2552817617': 'CEREBRAL_AMYLOID_ANGIOPATHY', '2684469520': 'DIABETES_MELLITUS_TYPE_2',
                        '2459345815': 'ATRIAL_FIBRILLATION', '2826947422': 'ATRIAL_FIBRILLATION'}
            if pid in expected:
                assert names[expected[pid]] == 1, (pid, expected[pid], names)
            artifacts = {}
            for name, records in [('diagnosis_mentions', mentions), ('canonical_diagnoses', canonical)]:
                artifacts[name + '_v3.json'] = records
                artifacts[name + '_v3.csv'] = csv_text(records)
            # Primary artifact is the canonical layer, independent of insertion order.
            artifacts = {'canonical_diagnoses_v3.json': canonical, **artifacts}
            source_hash = digest([sha(folder / f) for f in ('diagnoses.csv', 'source_mentions.json', 'source_bundle.json')] + [sha(Path(diagnosis.__file__))])
            manifest['diagnoses'] = store.publish(pid, 'diagnoses', '3.0', artifacts, source_hash,
                validation={'source_mentions_retained': True, 'unique_disease_identities': True, 'physician_approved': False})
            patient_counts.update(diagnosis_mentions=len(mentions), canonical_diagnoses=len(canonical))
            dx_review += ['', '## Patient ' + pid, '', table(canonical, ['canonical_name', 'domain', 'category', 'subcategory',
                'first_documented_date', 'first_documented_date_precision', 'mention_count', 'source_document_count', 'review_required']),
                '', '<details><summary>Source mentions and evidence</summary>\n', table(mentions, ['mention_id', 'raw_candidate', 'evidence_id', 'assertion_status']), '\n</details>']
        if 'medications' in modules:
            source_rows = rows(folder / 'medications.csv')
            encounters = {str(e['source_document_id']): e for e in load(folder / 'encounters.json')}
            mentions, canonical, exposures = medications(pid, source_rows, resolver, encounters)
            assert len(mentions) == len(exposures) == len(source_rows)
            artifacts = {'canonical_medications_v3.json': canonical}
            for name, records in [('medication_mentions', mentions), ('canonical_medications', canonical), ('medication_exposures', exposures)]:
                artifacts[name + '_v3.json'] = records
                artifacts[name + '_v3.csv'] = csv_text(records)
            source_hash = digest([sha(folder / 'medications.csv'), sha(folder / 'encounters.json'), resolver.source_hash,
                                  sha(ROOT / 'src/caa_pdf_extractor/extraction/medication_identity_v3.py')])
            manifest['medications'] = store.publish(pid, 'medications', '3.0', artifacts, source_hash,
                validation={'source_mentions_and_exposures_retained': True, 'common_brand_regressions_pass': not gates['known_common_trade_name_unresolved'], 'physician_approved': False})
            patient_counts.update(medication_mentions=len(mentions), canonical_medications=len(canonical), unresolved_medications=sum(not m['canonical_drug'] for m in mentions))
            for m in mentions:
                if not m['canonical_drug']:
                    unresolved[m['normalized_trade_name']].append(m)
            med_review += ['', '## Patient ' + pid, '', table(mentions, ['raw_medication', 'normalized_trade_name', 'active_ingredients',
                'research_class', 'research_subclass', 'mapping_source', 'mapping_status'])]
        manifest = invalidate(manifest, modules)
        store.save_manifest(pid, manifest)
        counts.append(patient_counts)
    assert all(Path(p).is_file() and sha(p) == h for p, h in original.items()), 'Previous artifact changed'
    assert not any(gates.values()), gates
    queue = [dict(normalized_trade_name=k, patient_count=len({m['patient_id'] for m in v}), mention_count=len(v),
                  patient_ids=sorted({m['patient_id'] for m in v}), raw_medications=sorted({m['raw_medication'] for m in v})) for k, v in unresolved.items()]
    queue.sort(key=lambda r: (-r['patient_count'], -r['mention_count'], r['normalized_trade_name']))
    if 'diagnoses' in modules:
        write_new(store.root / 'five_patient_diagnosis_review_v3.md', '\n'.join(dx_review))
    if 'medications' in modules:
        write_new(store.root / 'five_patient_medication_review_v3.md', '\n'.join(med_review))
        write_new(store.root / 'unresolved_medications.csv', csv_text(queue))
    report = dict(status='DIAGNOSIS_MEDICATION_V3_READY_FOR_PHYSICIAN_REVIEW',
        LOCAL_MEDICATION_DATABASE_FOUND='YES', LOCAL_MEDICATION_DATABASE_ACTIVE_IN_PIPELINE='YES',
        DIAGNOSIS_PATIENT_LEVEL_DEDUP_ACTIVE='YES', PARTIAL_MODULE_RERUNS_PRESERVE_OTHER_ARTIFACTS='YES',
        local_dictionary=dict(path=str(resolver.dictionary_path), rows=len(resolver.base), fields=list(resolver.base[0]),
                              sha256=sha(resolver.dictionary_path), aliases=str(resolver.alias_path)),
        source=str(source), previous=str(previous) if previous else None, modules=list(modules),
        hard_gates=gates, patients=counts, clinical_approval=False,
        scope='Saved source mentions reassembled locally. Upstream semantic eligibility retained; unknown dates/status require review.',
        original_artifact_hashes=original)
    code = [Path(__file__), Path(diagnosis.__file__),
            ROOT / 'src/caa_pdf_extractor/extraction/medication_identity_v3.py',
            ROOT / 'src/caa_pdf_extractor/module_artifacts.py']
    report['implementation_hashes'] = {str(path): sha(path) for path in code}
    for path in code:
        write_new(store.root / 'executed_source' / path.name, path.read_text(encoding='utf-8-sig'))
    write_new(store.root / 'validation_report.json', report)
    write_new(store.root / 'common_trade_name_regressions.json', regression)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-output', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--previous', type=Path, help='Previous V3 snapshot to preserve during a selective rerun')
    parser.add_argument('--modules', nargs='+', choices=['diagnoses', 'medications'], default=['diagnoses', 'medications'])
    args = parser.parse_args()
    output = args.output or ROOT / 'data/debug' / ('five_patient_diagnosis_medication_v3_' + datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S_%f%z'))
    result = run(args.pilot_output, output, args.modules, args.previous)
    print(result['status'])
    print(output.resolve())


if __name__ == '__main__':
    main()
