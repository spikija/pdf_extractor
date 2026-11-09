"""Diagnosis/event-only V4 regression runner; medication artifacts are read-only."""
import argparse
import csv
import html
import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .module_artifacts import ModuleStore, digest, invalidate, now, sha, write_new
from .extraction import diagnosis_events_v4 as extraction

ROOT = Path(__file__).resolve().parents[2]
PATIENTS = ('2459345815', '2684469520')
DEFAULT_SOURCE = ROOT / 'data/debug/five_patient_integration_20261025T203648+0100'
DEFAULT_PREVIOUS = ROOT / 'data/debug/five_patient_diagnosis_medication_v3_20261025T214201_791289+0100'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def csv_text(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)) or ['status'])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return stream.getvalue()


def display(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value) if value is not None else 'UNKNOWN'


def md_table(rows, fields):
    lines = ['| ' + ' | '.join(fields) + ' |', '| ' + ' | '.join('---' for _ in fields) + ' |']
    return '\n'.join(lines + ['| ' + ' | '.join(display(r.get(f)).replace('|', '\\|').replace('\n', ' ') for f in fields) + ' |' for r in rows])


def html_table(rows, fields):
    return '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>'+html.escape(f.replace('_',' '))+'</th>' for f in fields) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(display(r.get(f)))+'</td>' for f in fields)+'</tr>' for r in rows) + '</tbody></table></div>'


def guard(output):
    sys.dont_write_bytecode = True
    output = output.resolve()
    def audit(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'sqlite3.connect', 'sqlite3.connect/handle'):
            raise RuntimeError('V4_DATABASE_AND_NETWORK_ACCESS_FORBIDDEN')
        if event == 'open':
            name, mode, flags = args
            writing = isinstance(mode, str) and any(c in mode for c in 'wax+')
            writing |= isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and isinstance(name, (str, bytes, os.PathLike)) and not Path(name).resolve().is_relative_to(output):
                raise RuntimeError('V4_WRITE_OUTSIDE_NEW_ARTIFACT_TREE')
        if event in ('os.remove', 'os.rename', 'os.rmdir'):
            raise RuntimeError('V4_DELETION_AND_REPLACEMENT_FORBIDDEN')
    sys.addaudithook(audit)


def regression(results, *, patient_id=None):
    """Shared V4 gates; a single-patient run evaluates only applicable regressions."""
    if patient_id is not None and set(results) != {str(patient_id)}:
        raise ValueError('Single-patient regression scope mismatch')
    empty = dict(canonical_clinical_events=[], canonical_disease_identities=[],
                 diagnosis_mentions=[], patient_clinical_diagnoses=[])
    first, second = (results.get(pid, empty) for pid in PATIENTS)
    strokes = [e for e in first['canonical_clinical_events'] if e['event_type']=='ISCHEMIC_STROKE']
    current = [e for e in strokes if e['event_date']=='2015-08-29']
    historical = [e for e in strokes if e['event_date']=='2006']
    hemorrhage = [e for e in first['canonical_clinical_events'] if e['event_type']=='TREATMENT_ASSOCIATED_ICH' and e['side']=='LEFT' and e['location']=='PARIETO_OCCIPITAL']
    lesions = [e for e in second['canonical_clinical_events'] if e['event_type']=='ISCHEMIC_STROKE' and e['event_date']=='2021-03-08' and e['location']=='MESIOTEMPORAL' and e['side']=='LEFT']
    by_name = {d['canonical_name']: d for d in second['canonical_disease_identities']}
    core = {'CNS_VASCULITIS', 'DIABETES_MELLITUS_TYPE_2', 'HYPOTHYROIDISM', 'RESTLESS_LEGS_SYNDROME',
            'LICHEN_PLANUS', 'OBSTRUCTIVE_SLEEP_APNEA', 'POLYNEUROPATHY', 'LIPID_DISORDER', 'CEREBRAL_AMYLOID_ANGIOPATHY'}
    excluded = ('Internistischer Befund', 'Kardio-pulmonal kompensiert', 'Untersuchungstechnik',
                'Diffusionsstudie', 'Rechtes Herz:', 'Vena cava inferior normal weit',
                'Keine epileptiformen Potenziale', 'Thrombophilie-Screening', 'PET')
    gates = dict(
        ischemic_stroke_source_mention_found=any(m['canonical_candidate']=='ISCHEMIC_STROKE' for m in first['diagnosis_mentions']),
        exactly_one_2015_stroke=len(current)==1 and current[0]['mention_count']>=3 and len(current[0]['source_document_ids'])==3,
        historical_2006_stroke_distinct=len(historical)==1 and len(strokes)==2,
        stroke_2015_anatomy_and_etiology=len(current)==1 and current[0]['side']=='RIGHT' and current[0]['territory']=='MCA' and current[0]['etiology']=='CARDIOEMBOLIC' and current[0]['etiology_context']=='ATRIAL_FIBRILLATION',
        stroke_2015_iv_thrombolysis=len(current)==1 and current[0]['acute_treatment']['IV_THROMBOLYSIS'] is True,
        atrial_fibrillation_one_chronic_identity=sum(d['canonical_name']=='ATRIAL_FIBRILLATION' for d in first['canonical_disease_identities'])==1,
        separate_post_lysis_hemorrhage=len(hemorrhage)==1 and not any(e['event_type']=='PRIMARY_ICH' for e in first['canonical_clinical_events']),
        physician_view_includes_stroke=any(r['record_type']=='CLINICAL_EVENT' and r['canonical_name']=='ISCHEMIC_STROKE' for r in first['patient_clinical_diagnoses']),
        core_structured_diseases_preserved=core.issubset(by_name),
        junk_not_promoted=not any(m['raw_text'].strip().rstrip(':').casefold()==t.strip().rstrip(':').casefold() or m['raw_text'].startswith('Diffusionsstudie') for m in second['diagnosis_mentions'] for t in excluded),
        no_negated_candidates=not any(m['semantic_class']=='NEGATED' for r in results.values() for m in r['diagnosis_mentions']),
        recent_mesiotemporal_lesion=len(lesions)==1 and lesions[0]['source_type']=='IMAGING_CONFIRMED',
        caa_assertion_chronology='CEREBRAL_AMYLOID_ANGIOPATHY' in by_name and bool(by_name['CEREBRAL_AMYLOID_ANGIOPATHY']['first_suspicion_date']) and by_name['CEREBRAL_AMYLOID_ANGIOPATHY']['first_suspicion_date'] < by_name['CEREBRAL_AMYLOID_ANGIOPATHY']['first_explicit_diagnosis_date'],
        vasculitis_assertion_chronology='CNS_VASCULITIS' in by_name and bool(by_name['CNS_VASCULITIS']['first_suspicion_date']) and by_name['CNS_VASCULITIS']['first_suspicion_date'] < by_name['CNS_VASCULITIS']['first_explicit_diagnosis_date'],
        raw_spans_accounted_for=all(r['counts']['promoted_source_spans']+r['counts']['evidence_only_spans_rejected']==r['counts']['raw_medical_spans_detected'] for r in results.values()),
        no_duplicate_chronic_identity=all(len({d['canonical_name'] for d in r['canonical_disease_identities']})==len(r['canonical_disease_identities']) for r in results.values()),
        structured_core_diagnoses_promoted=all(any(m['canonical_candidate']==name and m['source_priority']=='STRUCTURED_DIAGNOSIS' for m in second['diagnosis_mentions']) for name in core),
    )
    if patient_id is not None:
        first_gates = {'ischemic_stroke_source_mention_found', 'exactly_one_2015_stroke',
                       'historical_2006_stroke_distinct', 'stroke_2015_anatomy_and_etiology',
                       'stroke_2015_iv_thrombolysis', 'atrial_fibrillation_one_chronic_identity',
                       'separate_post_lysis_hemorrhage', 'physician_view_includes_stroke'}
        second_gates = {'core_structured_diseases_preserved', 'junk_not_promoted',
                        'recent_mesiotemporal_lesion', 'caa_assertion_chronology',
                        'vasculitis_assertion_chronology', 'structured_core_diagnoses_promoted'}
        gates = {k: v for k, v in gates.items()
                 if (k not in first_gates or patient_id == PATIENTS[0])
                 and (k not in second_gates or patient_id == PATIENTS[1])}
    return gates


def render(results):
    intro = ('Only the two regression patients were processed. Chronic disease identities and clinical events are separate, '
             'and both appear in the physician diagnosis view. All results require physician review. '
             'A source documentation date is not an inferred onset date. Historical suspicion periods retain their precision. '
             'Approximate event dates derived from investigation dates are labeled in the event artifacts. '
             'Suspected conditions remain in the assertion timeline and do not establish a disease identity. '
             'Unresolved references are retained; they are not counted as additional events.')
    md = ['# Diagnosis V4 regression review', '', intro]
    body = ['<h1>Diagnosis V4 regression review</h1><p>'+html.escape(intro)+'</p>']
    for pid, result in results.items():
        md += ['', '## Patient '+pid, '', md_table([result['counts']], list(result['counts']))]
        body += ['<section><h2>Patient '+pid+'</h2>',html_table([result['counts']], list(result['counts']))]
        diseases = result['canonical_disease_identities']
        events = result['canonical_clinical_events']
        timelines = result['diagnostic_assertion_timeline']
        # Group duplicate unresolved quotes for readable review, retaining all details in JSON/CSV.
        grouped = {}
        for row in result['unresolved_candidates']:
            identity = (extraction.key(row['raw_text']), row['reason'])
            if identity not in grouped:
                grouped[identity] = dict(raw_text=row['raw_text'], reason=row['reason'], evidence_id=row.get('evidence_id') or row.get('span_id'), source_count=0)
            grouped[identity]['source_count'] += 1
        unresolved = list(grouped.values())
        chronology = [dict(canonical_name=d['canonical_name'], first_suspicion_date=d['first_suspicion_date'],
                           first_explicit_diagnosis_date=d['first_explicit_diagnosis_date'], review_required=True) for d in diseases]
        examples = []
        categories = set()
        for r in result['diagnosis_rejected_spans']:
            if r['semantic_class'] not in categories or len(examples)<5:
                examples.append(r);categories.add(r['semantic_class'])
            if len(examples)>=10:
                break
        tables = [
            ('A. Canonical chronic diseases', diseases, ['canonical_name','first_suspicion_date','first_explicit_diagnosis_date','domain','category','mention_count','document_count','review_required']),
            ('B. Canonical clinical events', events, ['event_type','event_date','event_date_precision','event_date_basis','side','territory','location','etiology','etiology_context','acute_treatment','complications','mention_count','source_document_ids','review_required']),
            ('C. Diagnostic assertion chronology', chronology, ['canonical_name','first_suspicion_date','first_explicit_diagnosis_date','review_required']),
            ('D. Unresolved candidates', unresolved, ['raw_text','reason','source_count','evidence_id']),
            ('E. Rejected evidence-only examples', examples, ['raw_text','semantic_class','reason','span_id']),
        ]
        for title, records, fields in tables:
            md += ['', '### '+title, '', md_table(records, fields)]
            body += ['<h3>'+html.escape(title)+'</h3>', html_table(records, fields)]
        fields = ['canonical_name','assertion_status','assertion_date','assertion_effective_date','effective_date_basis','referenced_historical_period','raw_text','evidence_id']
        md += ['', '<details><summary>Full source assertion timeline</summary>\n',md_table(timelines,fields),'\n</details>',
               '', f'[Combined physician diagnosis table](patient_artifacts/{pid}/patient_clinical_diagnoses_v4.csv)',
               '', f'[All rejected spans](patient_artifacts/{pid}/diagnosis_rejected_spans_v4.csv)']
        body += ['<details><summary>Full source assertion timeline</summary>',html_table(timelines,fields),'</details>',
                 f'<p><a href="patient_artifacts/{pid}/patient_clinical_diagnoses_v4.csv">Combined physician diagnosis table</a> · <a href="patient_artifacts/{pid}/diagnosis_rejected_spans_v4.csv">All rejected spans</a></p></section>']
    css = 'body{font:15px/1.5 system-ui,sans-serif;color:#172c3b;background:#f4f7fa;margin:0;padding:28px}section{background:white;padding:24px;margin:24px 0;border-radius:12px}h1,h2,h3{color:#153b59}p{max-width:1100px}.table-wrap{overflow:auto;margin:14px 0}table{border-collapse:collapse;font-size:13px;width:100%}th,td{border:1px solid #d9e1e7;padding:9px;text-align:left;vertical-align:top}th{background:#e9f1f7}tr:nth-child(even){background:#f7f9fb}td{max-width:540px;overflow-wrap:anywhere}summary{cursor:pointer;font-weight:600;padding:12px}a{color:#075a91}@media print{body{background:white;padding:0}.table-wrap{overflow:visible}table{font-size:9px}section{break-before:page}}'
    return '\n'.join(md), '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Diagnosis V4 regression review</title><style>'+css+'</style><body>'+''.join(body)+'</body></html>'


def run(source, previous, output, protect=True):
    source, previous = Path(source).resolve(), Path(previous).resolve()
    # Hash all previous V3 files, and the source inputs/modules for the two patients.
    original = {str(p):sha(p) for p in previous.rglob('*') if p.is_file()}
    original.update({str(p):sha(p) for pid in PATIENTS for p in (source/pid).rglob('*') if p.is_file()})
    medication_code = ROOT/'src/caa_pdf_extractor/extraction/medication_identity_v3.py'
    original[str(medication_code)] = sha(medication_code)
    code = [Path(__file__), Path(extraction.__file__),
            ROOT/'src/caa_pdf_extractor/extraction/diagnosis_identity_v3.py',
            ROOT/'src/caa_pdf_extractor/module_artifacts.py']
    code_hashes = {str(path):sha(path) for path in code}
    results = {pid: extraction.assemble(pid, load(source/pid/'source_bundle.json'), load(source/pid/'source_mentions.json')) for pid in PATIENTS}
    gates = regression(results)
    store = ModuleStore(output)
    if protect:
        guard(store.root)
    # Report even failed gates, but never publish failures as validated.
    passed = all(gates.values())
    for pid, result in results.items():
        manifest = {name:store.preserve(entry) for name,entry in load(previous/'patient_artifacts'/pid/'module_manifest.json').items()}
        for name, entry in manifest.items():
            if name not in ('diagnoses','events') and entry['status']!='STALE_DEPENDENCY':
                # Preservation is orthogonal to validation: replacing VALIDATED
                # with PRESERVED would hide V3 medication from master selection.
                entry['preservation_status'] = 'PRESERVED'
        artifacts = {'canonical_disease_identities_v4.json':result['canonical_disease_identities']}
        for name, rows in result.items():
            if name=='counts':
                continue
            artifacts[name+'_v4.json'] = rows
            if isinstance(rows, list):
                artifacts[name+'_v4.csv'] = csv_text(rows)
        source_hash = digest([sha(source/pid/f) for f in ('source_bundle.json','source_mentions.json')] + list(code_hashes.values()))
        manifest['diagnoses'] = store.publish(pid,'diagnoses','4.0',artifacts,source_hash,
            status='VALIDATED' if passed else 'FAILED_VALIDATION', validation={'hard_gates':gates,'physician_approved':False})
        manifest['events'] = dict(manifest['diagnoses'],module='events',artifact=str(store.root/'patient_artifacts'/pid/'canonical_clinical_events_v4.json'))
        manifest = invalidate(manifest,['diagnoses','events'])
        store.save_manifest(pid,manifest)
        write_new(store.root/'patient_artifacts'/pid/'candidate_count_qa_v4.json',result['counts'])
    preserved = all(Path(path).is_file() and sha(path)==value for path,value in original.items())
    if not preserved:
        raise RuntimeError('ORIGINAL_ARTIFACT_INTEGRITY_FAILURE')
    md, webpage = render(results)
    write_new(store.root/'diagnosis_v4_regression_review.md',md)
    write_new(store.root/'diagnosis_v4_regression_review.html',webpage)
    write_new(store.root/'diagnosis_rejected_spans_v4.csv',csv_text([dict(patient_id=pid,**r) for pid,result in results.items() for r in result['diagnosis_rejected_spans']]))
    report = dict(
        PATIENT_2459345815_STROKE_FOUND_IN_SOURCE='YES' if gates['ischemic_stroke_source_mention_found'] else 'NO',
        PATIENT_2459345815_STROKE_CANONICAL_EVENT='YES' if gates['exactly_one_2015_stroke'] and gates['historical_2006_stroke_distinct'] else 'NO',
        PATIENT_2684469520_PDF_TEXT_EXTRACTION_PROBLEM='NO',
        PATIENT_2684469520_CANDIDATE_FILTERING_PROBLEM='YES',
        MEDICATION_V3_PRESERVED='YES' if preserved else 'NO',UNRELATED_MODULES_PRESERVED='YES' if preserved else 'NO',
        DIAGNOSIS_V4_READY_FOR_PHYSICIAN_REVIEW='YES' if passed else 'NO',
        created_at=now(),source=str(source),previous=str(previous),patients=list(PATIENTS),
        hard_gates=gates,candidate_count_qa={pid:r['counts'] for pid,r in results.items()},
        original_artifact_hashes=original,implementation_hashes=code_hashes,clinical_database_write=0,medication_module_runs=0,
        unrelated_module_runs=0,external_service_calls=0,clinical_approval=False,
        source_scope='Existing parsed source text and legacy raw spans for the two named regression patients only.',
        pdf_text_assessment='Required structured diagnoses and MRI lesion are present in readable saved source text; candidate promotion and assembly caused the reported omissions. This is not an audit of all PDF/OCR accuracy.',
        chronology_policy='First explicit diagnosis is earliest established source documentation. Earlier suspected historical periods are retained separately at their original precision. Historical diagnosis claims remain in timeline without silently backdating established assertions.')
    for path in code:
        write_new(store.root/'executed_source'/path.name,path.read_text(encoding='utf-8-sig'))
    write_new(store.root/'validation_report.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot-output',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--previous',type=Path,default=DEFAULT_PREVIOUS)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    output=args.output or ROOT/'data/debug'/('diagnosis_v4_regression_'+datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S_%f%z'))
    result=run(args.pilot_output,args.previous,output)
    print('DIAGNOSIS_V4_READY_FOR_PHYSICIAN_REVIEW = '+result['DIAGNOSIS_V4_READY_FOR_PHYSICIAN_REVIEW'])
    print(output.resolve())
    if result['DIAGNOSIS_V4_READY_FOR_PHYSICIAN_REVIEW']!='YES':
        raise SystemExit(1)


if __name__=='__main__':
    main()
