"""Audit completed pilot artifacts without re-inference or clinical-value edits.

Patient-specific expectations live here as post-inference regression assertions,
not in extraction, numeric validators or model prompts.
"""
import argparse
import csv
import json
from pathlib import Path

from .integration_pilot import FIXED, ROOT, MATRIX, csvfile, sha, table, write
from .extraction.neurologic_taxonomy import classify_neurologic, validate_taxonomy
from .extraction import caa_evidence_v21 as ce
from .extraction.caa_report_adapter import attach_source_report
from .extraction.neurosonology_report_adapter import extract_bundle as extract_neuro_reports
from .preparation.patient_bundle import prepare_document
from .preparation.models import PatientDocumentBundle


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def cfs_regression(assessment):
    cfs = assessment.get('cfs') or {}
    raw = assessment.get('raw_cfs_candidate') or {}
    phenotype = (assessment.get('phenotype') or {}).get('functional_phenotype', {})
    accepted = cfs.get('validator_accepted') is True
    return dict(test='INDEPENDENT_RETROSPECTIVE_CFS', patient_id='2435608415',
        status='PASS' if accepted and cfs.get('validated_best_cfs') in (6, 7)
            and cfs.get('validated_plausible_range') == [6, 7] and raw.get('confidence') in ('LOW', 'MODERATE')
            else 'REVIEW_REQUIRED',
        raw_best_cfs=raw.get('best_cfs'), raw_range=[raw.get('plausible_range_min'), raw.get('plausible_range_max')],
        validated_best_cfs=cfs.get('validated_best_cfs'), validated_range=cfs.get('validated_plausible_range'),
        confidence=raw.get('confidence'), source=cfs.get('source_type'),
        functional_phenotype=phenotype, validator_errors=cfs.get('validator_errors', []),
        physician_value_copied=False, score_repaired_in_python=False,
        interpretation='Rejected inconsistent model candidates remain rejected. No additional score-seeking retry.')


def annotate_diagnosis_rows(diagnoses):
    """Use current source-label neurological taxonomy after semantic eligibility.

    Preserve the already applied common/cardio V2.3 mappings. Acute occurrences
    retain their separate event eligibility and never enter disease identity.
    """
    output = []
    for original in diagnoses:
        row = dict(original)
        if row['candidate_type'] == 'DIAGNOSIS_DISEASE' and not row.get('category'):
            annotation = classify_neurologic(row['raw_candidate'], domain=row.get('domain') or 'OTHER',
                legacy_subcategory=row.get('subcategory') or 'OTHER')
            validate_taxonomy(annotation)
            row.update(annotation)
            if annotation['subcategory'] == 'CEREBRAL_AMYLOID_ANGIOPATHY':
                row['canonical_name'] = 'CAA'
            elif annotation['subcategory'] in ('CAA_RELATED_INFLAMMATION', 'ABETA_RELATED_ANGIITIS'):
                row['canonical_name'] = annotation['subcategory']
        # Earlier adapter explicitly annotated ordinary CAA; check its source
        # subtype as well so inflammatory variants cannot inherit that identity.
        elif row['candidate_type'] == 'DIAGNOSIS_DISEASE' and row.get('canonical_name') == 'CAA':
            annotation = classify_neurologic(row['raw_candidate'], domain='NEUROLOGIC')
            validate_taxonomy(annotation)
            row.update(annotation)
            if annotation['subcategory'] in ('CAA_RELATED_INFLAMMATION', 'ABETA_RELATED_ANGIITIS'):
                row['canonical_name'] = annotation['subcategory']
        output.append(row)
    return output


def assemble_caa_reports(pid, folder, model_folder, dob):
    """Connect report text and normalized source findings to CAA chronology.

    The shared assembler needs literal comparison/continuity text to join serial
    CT observations. Raw semantic feature candidates remain in the model ledger.
    """
    current = read(folder / 'caa_summary.json')
    reports = {r['id']: r for r in rows(folder / 'imaging.csv')}
    episodes, scans, holds = {}, [], []
    for original in current.get('raw_model_scan_features', current.get('scan_features', [])):
        scan = dict(original)
        scan['features'] = ce.ScanEvidence.model_validate(scan['features'])
        report = reports[scan['id']]
        scan = attach_source_report(dict(scan, patient_id=pid), dict(report,
            findings=json.loads(report['findings']), negative_findings=json.loads(report['negative_findings']),
            source_context=json.loads(report['source_context'])))
        did = report['source_document_id']
        accepted = sorted(model_folder.glob(f'{pid}_{did}_caa_features_attempt*_validated.json'))
        if not accepted:
            raise ValueError('Missing accepted CAA episode proposal for ' + str(did))
        episodes[scan['episode_id']] = ce.EpisodeEvidence.model_validate(read(accepted[-1])['episode'])
        scan, disagreements = ce.reconcile_validated_scan(scan)
        scans.append(scan)
        holds.extend(disagreements)
    result = ce.assemble_patient(pid, dob, scans, episodes)
    result['review_holds'].extend(holds)
    for key in ('source_assertions', 'channels', 'source_chronology'):
        result[key] = current.get(key)
    result['raw_model_scan_features'] = current.get('raw_model_scan_features', current.get('scan_features', []))
    result['scan_features'] = [dict(s, features=s['features'].model_dump()) for s in scans]
    dates = [r['date'] for r in result['boston_patient_timepoints'] if r.get('assessable')]
    dates += [r['event_date'] for r in result['edinburgh_ich_events'] if r.get('event_date') and
        any(r.get(k) not in (None, 'NOT_APPLICABLE', 'NOT_ASSESSABLE') for k in ('original_edinburgh', 'simplified_edinburgh'))]
    result['first_computed_criteria_date'] = min(dates, default=None)
    result['source_report_continuity_adapter_applied'] = True
    return result


def audit(output):
    output = Path(output).resolve()
    if output.parent != (ROOT / 'data/debug').resolve() or not output.name.startswith('five_patient_integration_'):
        raise ValueError('Only a newly generated five-patient pilot directory is accepted')
    status = read(output / 'status.json')
    if status['status'] not in ('FIVE_PATIENT_INTEGRATION_PARTIAL', 'FIVE_PATIENT_INTEGRATION_READY_FOR_PHYSICIAN_REVIEW'):
        raise ValueError('Complete core assembly before reviewing artifacts')
    selected = status['selected_patient_ids']
    if len(selected) != 5 or len(set(selected)) != 5 or not set(FIXED) <= set(selected):
        raise ValueError('Invalid pilot cohort')
    manifests = read(output / 'pilot_source_manifest.json')
    if set(m['patient_id'] for m in manifests) != set(selected):
        raise ValueError('Unexpected manifest patient')
    if any(sha(ROOT / 'pdf_db' / m['relative_pdf_path']) != m['file_hash'] for m in manifests):
        raise ValueError('Source changed after extraction')
    gates = read(output / 'hard_gate_results.json')
    if gates['patients_processed'] != 5 or any(v for k, v in gates.items() if k != 'patients_processed'):
        raise ValueError('Do not finalize a pilot with hard-gate failures')
    matrix = rows(output / 'five_patient_module_matrix.csv')
    gaps = rows(output / 'pipeline_gap_audit.csv')
    regressions, notes = [], []
    for pid, value in [('2684469520', '425'), ('2826947422', '269')]:
        matches = [r for r in rows(output / pid / 'labs.csv') if r.get('canonical_analyte') == 'CSF_ABETA_UNSPECIFIED' and r.get('value') == value]
        regressions.append(dict(test='UNICODE_AMYLOID_SOURCE_CELL', patient_id=pid,
            status='PASS' if matches and any(r.get('reference_text') == '>600' for r in matches) else 'FAIL',
            expected_value=value, expected_analyte='CSF_ABETA_UNSPECIFIED', found_source_rows=matches))
    cha = read(output / '2459345815/cha2ds2_va.json')
    parts = cha.get('components', {})
    score_ok = (parts.get('H') == 1 and parts.get('A2') == 2 and parts.get('S2') == 2 and parts.get('A') == 0
        and (cha.get('cha2ds2_va_minimum_confirmed') or 0) >= 5)
    regressions.append(dict(test='SOURCE_RETEST_CHA_ESTABLISHED_COMPONENTS', patient_id='2459345815',
        status='PASS' if score_ok else 'REVIEW_REQUIRED', index_date=cha.get('score_index_date'), components=parts,
        exact=cha.get('cha2ds2_va_exact'), minimum=cha.get('cha2ds2_va_minimum_confirmed'),
        maximum=cha.get('cha2ds2_va_maximum_possible'), recommendation=cha.get('esc_oac_recommendation'),
        no_components_changed_by_regression=True))
    for pid in selected:
        summary = read(output / pid / 'patient_summary.json')
        docs = []
        for manifest in manifests:
            if manifest['patient_id'] != pid:
                continue
            parsed = read(output / 'parsed' / f"{manifest['source_document_id']}.json")
            if parsed['document']['sha256'] != manifest['file_hash'] or parsed['document']['patient_id'] != pid:
                raise ValueError('Cached pilot source provenance mismatch')
            docs.append(prepare_document(pid, manifest['source_document_id'], Path(manifest['relative_pdf_path']).name, manifest['file_hash'], parsed))
        neuro, other_exams = extract_neuro_reports(PatientDocumentBundle(pid, docs))
        csvfile(output / pid / 'neurosonology.csv', neuro, ['patient_id'])
        write(output / pid / 'other_instrumental_exams.json', other_exams)
        next(r for r in matrix if r['patient_id'] == pid)['neurosonology'] = ('REVIEW_REQUIRED' if any(r['review_required'] for r in neuro)
            else 'EXECUTED') if neuro else 'NO_SOURCE_DATA'
        caa = assemble_caa_reports(pid, output / pid, output / 'model', summary.get('date_of_birth'))
        write(output / pid / 'caa_summary.json', caa)
        diagnoses = annotate_diagnosis_rows(rows(output / pid / 'diagnoses.csv'))
        csvfile(output / pid / 'diagnoses.csv', diagnoses, ['patient_id'])
        functions = summary.get('functional_assessments', [])
        additional = []
        orbit = read(output / pid / 'orbit.json')
        if 'antiplatelet' in orbit.get('missing_components', []):
            additional.append(dict(module='ORBIT_index_medication_ascertainment', patient_id=pid,
                failure_type='The current dated fact adapter does not assemble index-specific antiplatelet status from the medication inventory; UNKNOWN retained.',
                missing_adapter='INDEX_SPECIFIC_MEDICATION_TO_RISK_COMPONENT', temporary_fallback_used='NONE', production_fix_required=True))
        if orbit.get('missing_components') and 'CONFIRMED_AF_INDEX' not in orbit['missing_components'] and any(
            orbit.get('labs_used', {}).get(k) is None for k in ('HEMOGLOBIN', 'HEMATOCRIT', 'EGFR')):
            additional.append(dict(module='ORBIT_blood_laboratory_layout', patient_id=pid,
                failure_type='Geometry extraction currently targets six other biomarkers. Hb/Hct/eGFR use the explicit same-line date/value adapter; complete dated blood-table coverage is unverified.',
                missing_adapter='DATED_BLOOD_TABLE_CELL_TO_ORBIT_LAB', temporary_fallback_used='NARRATIVE_OR_SAME_LINE_DATED_LABS_ONLY', production_fix_required=True))
        own_cha = read(output / pid / 'cha2ds2_va.json')
        if not own_cha.get('af_evidence') and any(r.get('canonical_name') == 'ATRIAL_FIBRILLATION' and
            r['candidate_type'] == 'DIAGNOSIS_DISEASE' for r in diagnoses):
            additional.append(dict(module='AF_index_date_ownership', patient_id=pid,
                failure_type='Source AF diagnosis retained but no dated index is assembled; current score applicability must not be interpreted as source absence of AF.',
                missing_adapter='SOURCE_CONFIRMED_AF_TO_DATED_INDEX', temporary_fallback_used='NONE', production_fix_required=True))
            own_matrix = next(r for r in matrix if r['patient_id'] == pid)
            for key in ('AF', 'CHA2DS2_VA', 'ORBIT'):
                own_matrix[key] = 'REVIEW_REQUIRED'
        for result in functions:
            cfs = result.get('cfs') or {}
            if cfs.get('validator_errors'):
                additional.append(dict(module='retrospective_CFS_model_consistency', patient_id=pid,
                    failure_type=json.dumps(cfs['validator_errors'], ensure_ascii=False),
                    missing_adapter='VALIDATED_INTERNALLY_CONSISTENT_CFS_PROPOSAL', temporary_fallback_used='NONE', production_fix_required=True))
                next(r for r in matrix if r['patient_id'] == pid)['CFS'] = 'REVIEW_REQUIRED'
            if pid == '2435608415' and result.get('index_date') == '2015-03-18':
                regressions.append(cfs_regression(result))
                mrs = result.get('mrs') or {}
                notes.append(dict(patient_id=pid, item='Independent PRE_EVENT mRS candidate',
                    value=mrs.get('validated_pre_event_mrs'), range=mrs.get('candidate_range'),
                    interpretation='Source/temporal and numeric validation only. Physician anchor review remains necessary; previous 4-5 was not copied.'))
        # A failed attempt with baseline sources must not masquerade as no data.
        for g in [*gaps, *additional]:
            if g['patient_id'] == pid and g['module'] == 'retrospective_function':
                for name in ('CFS', 'mRS'):
                    next(r for r in matrix if r['patient_id'] == pid)[name] = 'REVIEW_REQUIRED'
        existing = {(g['module'], g['patient_id'], g['failure_type']) for g in gaps}
        additional = [g for g in additional if (g['module'], g['patient_id'], g['failure_type']) not in existing]
        gaps.extend(additional)
        summary['module_matrix'] = next(r for r in matrix if r['patient_id'] == pid)
        summary['gaps'].extend(additional)
        summary['canonical_diagnosis_count'] = len({r['canonical_name'] for r in diagnoses if r['candidate_type'] == 'DIAGNOSIS_DISEASE' and r.get('canonical_name')})
        summary['source_label_neurologic_taxonomy_applied'] = True
        summary['neurosonology_report_ownership_adapter_applied'] = True
        write(output / pid / 'patient_summary.json', summary)
        review = output / pid / f'patient_{pid}_integrated_review.md'
        import re
        updated = re.sub(r'## D\. Diagnoses\n[\s\S]*?(?=## E\. Medications)',
            '## D. Diagnoses\n\n' + table(diagnoses, ['record_id', 'raw_candidate', 'candidate_type', 'canonical_name', 'domain', 'category', 'subcategory', 'evidence_ids']) +
            '\n[All diagnoses](diagnoses.csv). Current shared neurological source-label rules were applied after semantic eligibility; common/cardio V2.3 mappings are preserved.\n\n', review.read_text(encoding='utf-8'))
        write(review, updated)
        updated = re.sub(r'## G\. Neurosonology\n[\s\S]*?(?=## H\. EEG)', lambda _:
            '## G. Neurosonology\n\n' + table(neuro, ['exam_id', 'vessel', 'side', 'stenosis_category', 'nascet_percent', 'psv_cm_s', 'edv_cm_s', 'evidence_quote']) +
            '\n[All vessel observations](neurosonology.csv). Unexamined vessels remain unknown. '
            'Arm/leg venous duplex, cardiac and other examinations are kept separately in [other_instrumental_exams.json](other_instrumental_exams.json).\n\n',
            review.read_text(encoding='utf-8'))
        write(review, updated)
        caa_note = ('\n## CAA source-report reconciliation\n\nLiteral report text and source-normalized findings were connected to the shared CAA assembler. '
            'This enables explicit serial-CT continuity checks. Raw model features remain separate in caa_summary.json; disagreements require physician review.\n\n' +
            table(caa.get('boston_patient_timepoints', []), ['date', 'classification', 'mri_adequate', 'why']) + '\n' +
            table(caa.get('edinburgh_ich_events', []), ['event_date', 'adequate_mri_available', 'original_edinburgh', 'simplified_edinburgh', 'review']))
        updated = review.read_text(encoding='utf-8')
        updated = re.sub(r'## F\. Imaging and CAA\n[\s\S]*?(?=## G\. Neurosonology)',
            lambda _: '## F. Imaging and CAA\n\n' + table(rows(output / pid / 'imaging.csv'), ['id', 'date', 'modality', 'source_document_id']) +
                caa_note + '\n[CAA channels, chronology, raw and reconciled feature traces](caa_summary.json); [labs and CSF](labs.csv).\n\n', updated)
        write(review, updated)
        cfs_errors = [g for g in additional if g['module'] == 'retrospective_CFS_model_consistency']
        if cfs_errors:
            write(review, review.read_text(encoding='utf-8').split('\n## Rejected model estimates\n')[0] + '\n## Rejected model estimates\n\n' +
                'CFS had baseline evidence but no internally consistent accepted numeric proposal. This is a model/validation limitation, not absence of source data. '
                'Raw candidates and the single consistency retry remain in the local model audit. No physician value was substituted.\n\n' +
                table(cfs_errors, ['module', 'failure_type']))
        if any(g['module'] == 'ORBIT_index_medication_ascertainment' for g in additional):
            write(review, review.read_text(encoding='utf-8') + '\nORBIT integration limitation: index-specific antiplatelet status is not assembled from the medication inventory. '
                'That component remains UNKNOWN; no medication-list omission is converted to zero.\n')
    csvfile(output / 'five_patient_module_matrix.csv', matrix, ['patient_id', *MATRIX])
    csvfile(output / 'pipeline_gap_audit.csv', gaps)
    write(output / 'pilot_regression_audit.json', dict(regressions=regressions, interpretation_notes=notes,
        scope='POST_INFERENCE_ONLY; current source-label taxonomy and CAA report-continuity adapters; no model calls or CFS/mRS/risk-score repair', hard_gates=gates))
    status['modules_still_disconnected'] = sorted({r['module'] for r in gaps})
    status['modules_successfully_executed'] = sorted({k for r in matrix for k in MATRIX if r[k] in ('EXECUTED', 'REVIEW_REQUIRED')})
    if gaps or any(r['status'] != 'PASS' for r in regressions):
        status['status'] = 'FIVE_PATIENT_INTEGRATION_PARTIAL'
    status['post_inference_regression_audit'] = str(output / 'pilot_regression_audit.json')
    status['review_finalizer_sha256'] = sha(Path(__file__))
    validation_xml = output / 'pytest_validation.xml'
    if validation_xml.exists():
        from xml.etree import ElementTree
        suites = ElementTree.parse(validation_xml).getroot().findall('.//testsuite')
        status['repository_tests'] = {key: sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
        status['repository_test_report'] = str(validation_xml)
    status['neurologic_taxonomy_sha256'] = sha(ROOT / 'src/caa_pdf_extractor/extraction/neurologic_taxonomy.py')
    # Recheck the hierarchy after source-based CAA reconciliation, as well as
    # the original extraction gate. A postprocessing failure cannot be hidden.
    mri_edinburgh_failures = sum(e.get('adequate_mri_available') is True and
        any(e.get(k) not in (None, 'NOT_APPLICABLE') for k in ('original_edinburgh', 'simplified_edinburgh'))
        for pid in selected for e in read(output / pid / 'caa_summary.json').get('edinburgh_ich_events', []))
    if mri_edinburgh_failures:
        raise ValueError('CAA_MRI_plus_Edinburgh_both_computed after report reconciliation')
    status['postprocessing_hard_gates_rechecked'] = True
    status['modules_using_temporary_adapter'] = sorted(set(status['modules_using_temporary_adapter']) | {'POST_INFERENCE_SOURCE_LABEL_TAXONOMY_AND_REGRESSION_AUDIT', 'CAA_LITERAL_REPORT_CONTINUITY_ADAPTER', 'NEUROSONOLOGY_REPORT_OWNERSHIP_ADAPTER'})
    write(output / 'status.json', status)
    report = '# Five-patient integration pilot\n\n' + status['status'] + '\n\n' + \
        'Fresh PDF sources; exactly five selected patients. No clinical database writes or cloud patient calls. Full-cohort execution remains disabled.\n\n' + \
        table(matrix, ['patient_id', *MATRIX], 5) + '\n\n' + \
        'Hard gates: [hard_gate_results.json](hard_gate_results.json). Detailed source regressions: [pilot_regression_audit.json](pilot_regression_audit.json).\n\n' + \
        table(regressions, ['test', 'patient_id', 'status', 'validated_best_cfs', 'validated_range', 'exact', 'minimum', 'maximum']) + '\n\n' + \
        'Integration gaps: [pipeline_gap_audit.csv](pipeline_gap_audit.csv). An EXECUTED module may still yield clinically unknown values. ' \
        'REVIEW_REQUIRED includes rejected model estimates and incomplete source information; it is not an approved clinical classification.\n\n'
    import re
    for pid in selected:
        own = (output / pid / f'patient_{pid}_integrated_review.md').read_text(encoding='utf-8')
        report += re.sub(r'\]\(([^):]+)\)', lambda m: '](' + pid + '/' + m[1] + ')', own) + '\n\n---\n\n'
    write(output / 'five_patient_integration_review.md', report)
    return dict(status=status['status'], regressions=regressions, hard_gates=gates)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pilot-output', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.pilot_output)
    print(json.dumps(dict(status=result['status'], regression_statuses=[dict(test=r['test'], patient_id=r['patient_id'], status=r['status']) for r in result['regressions']], hard_gates=result['hard_gates']), indent=2))
