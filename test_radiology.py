"""Synthetic MRI phenotype tests: no patient PDFs, PostgreSQL or Ollama."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import ExtractionRun, Radiology
from caa_pdf_extractor.extraction.radiology import extract_patient
from caa_pdf_extractor.extraction.radiology_inputs import report_text
from caa_pdf_extractor.extraction.radiology_models import RadiologyExtraction
from caa_pdf_extractor.extraction.radiology_validators import validate_radiology
from caa_pdf_extractor.llm.backend import get_client
from caa_pdf_extractor.llm.radiology_prompts import radiology_prompt
from caa_pdf_extractor.preparation.models import DiagnosticProcedure, PatientDocumentBundle, PreparedDocument

LOBAR = 'Akutes links frontales intrazerebrales Hämatom.'
CMB = 'Chronische Mikroblutungen frontoparietal.'
CAA = 'Verdacht auf Amyloidangiopathie.'
EDEMA = 'Umgebendes Ödem.'


def procedure(**changes):
    p = DiagnosticProcedure('doc1_proc01', '123', 1, 3, 4, 'MRI', '2015-12-14',
                            ['Schädel nativ', 'Diffusion'], [],
                            '\n'.join([LOBAR, CMB, CAA, EDEMA]), 'Original source')
    return replace(p, **changes)


def payload(p=None):
    p = p or procedure()
    return {'patient_id': p.patient_id, 'procedure_id': p.procedure_id,
            'findings': [{'type': 'lobar', 'acute_lesion': True, 'evidence': LOBAR},
                         {'type': 'cmb', 'acute_lesion': False, 'evidence': CMB}],
            'additional_findings': {'caa_suspicion_explicit': True, 'caa_evidence': CAA,
                                    'microbleed_description': CMB, 'other_findings': [EDEMA]}}


def empty_payload(p):
    return {'patient_id': p.patient_id, 'procedure_id': p.procedure_id, 'findings': [],
            'additional_findings': {'caa_suspicion_explicit': None, 'caa_evidence': None,
                                   'microbleed_description': None, 'other_findings': []}}


def bundle(*procedures):
    return PatientDocumentBundle('123', [PreparedDocument(1, 'synthetic.pdf', '123/synthetic.pdf',
                                                        'synthetic', 'unknown', None, [],
                                                        diagnostic_procedures=list(procedures))])


class ValidationTests(unittest.TestCase):
    def validate(self, data, p=None):
        return validate_radiology(json.dumps(data), '123', p or procedure())

    def test_one_mri_multiple_lobar_and_cmb_phenotypes(self):
        output = self.validate(payload())
        self.assertEqual([x['type'] for x in output['findings']], ['lobar', 'cmb'])
        self.assertEqual([x['acute_lesion'] for x in output['findings']], [True, False])

    def test_css(self):
        quote = 'Chronische kortikale oberflächliche Siderose.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'css', 'acute_lesion': False, 'evidence': quote}]
        self.assertEqual(self.validate(data, p)['findings'][0]['type'], 'css')

    def test_deterministic_date_and_metadata_win_over_historical_ct_date(self):
        data = payload()
        data.update(mri_date='2015-12-11', modality='CT', source_document_id=99,
                    page_start=1, page_end=2, studies=['CTA'])
        output = self.validate(data)
        self.assertEqual(output['mri_date'], '2015-12-14')
        self.assertEqual(output['modality'], 'MRI')
        self.assertEqual(output['source_document_id'], 1)
        self.assertEqual(output['page_start'], 3)
        self.assertEqual(output['studies'], procedure().studies)
        self.assertIn('mri_date', output['metadata_reconciliation'])

    def test_unknown_mri_date_never_filled_from_model(self):
        p = procedure(date=None)
        data = payload(p)
        data['mri_date'] = '2015-12-11'
        self.assertIsNone(self.validate(data, p)['mri_date'])

    def test_absent_findings_stay_empty(self):
        p = procedure(result_text='Kein Befund erstellt.')
        self.assertEqual(self.validate(empty_payload(p), p)['findings'], [])

    def test_current_ich_without_acute_is_true(self):
        quote = 'Links frontales intrazerebrales Hämatom.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'lobar', 'acute_lesion': None, 'evidence': quote}]
        output = self.validate(data, p)
        self.assertIs(output['findings'][0]['acute_lesion'], True)
        self.assertEqual(output['acuity_reconciliation'][0]['normalized_value'], True)

    def test_old_chronic_residual_ich_is_false(self):
        for marker in ('Altes', 'Chronisches', 'Residuales', 'Remote', 'Previous'):
            quote = marker + ' links frontales intrazerebrales Hämatom.'
            p = procedure(result_text=quote)
            data = empty_payload(p)
            data['findings'] = [{'type': 'lobar', 'acute_lesion': True, 'evidence': quote}]
            self.assertIs(self.validate(data, p)['findings'][0]['acute_lesion'], False)

    def test_cropped_quote_keeps_chronicity_from_its_source_sentence(self):
        quote = 'links frontales intrazerebrales Hämatom'
        p = procedure(result_text='Altes ' + quote + '.')
        data = empty_payload(p)
        data['findings'] = [{'type': 'lobar', 'acute_lesion': True, 'evidence': quote}]
        self.assertIs(self.validate(data, p)['findings'][0]['acute_lesion'], False)

    def test_historical_comparison_does_not_make_current_ich_chronic(self):
        quote = 'Links frontales intrazerebrales Hämatom.'
        p = procedure(result_text='Alte CT-Untersuchung vom 11.12.2015 zum Vergleich. ' + quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'lobar', 'acute_lesion': False, 'evidence': quote}]
        output = self.validate(data, p)
        self.assertIs(output['findings'][0]['acute_lesion'], True)
        self.assertEqual(output['mri_date'], '2015-12-14')

    def test_old_comparison_ct_in_same_sentence_does_not_set_chronicity(self):
        quote = 'Im Vergleich zum alten CT zeigt sich ein links frontales intrazerebrales Hämatom.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'lobar', 'acute_lesion': None, 'evidence': quote}]
        self.assertIs(self.validate(data, p)['findings'][0]['acute_lesion'], True)

    def test_cmb_always_normalized_false_without_chronic_word(self):
        quote = 'Mikroblutungen frontoparietal.'
        p = procedure(result_text=quote)
        for acuity in (True, False, None):
            data = empty_payload(p)
            data['findings'] = [{'type': 'cmb', 'acute_lesion': acuity, 'evidence': quote}]
            self.assertIs(self.validate(data, p)['findings'][0]['acute_lesion'], False)

    def test_css_normalized_false_without_chronic_word(self):
        quote = 'Kortikale oberflächliche Siderose.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'css', 'acute_lesion': True, 'evidence': quote}]
        self.assertIs(self.validate(data, p)['findings'][0]['acute_lesion'], False)

    def test_ambiguous_supported_phenotype_has_null_acuity(self):
        quote = 'Unbekannter Phänotyp.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'unknown', 'acute_lesion': True, 'evidence': quote}]
        self.assertIsNone(self.validate(data, p)['findings'][0]['acute_lesion'])

    def test_unsupported_finding_is_rejected_not_rescued_by_acuity_rules(self):
        quote = 'Mikroblutungen frontoparietal.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'lobar', 'acute_lesion': None, 'evidence': quote}]
        with self.assertRaises(ValueError): self.validate(data, p)

    def test_ct_and_rtg_cannot_validate_as_mri(self):
        for modality in ('CT', 'RTG'):
            p = procedure(modality=modality)
            with self.assertRaises(ValueError): self.validate(payload(p), p)

    def test_caa_and_descriptions_preserved_only_in_additional_findings(self):
        output = self.validate(payload())
        self.assertTrue(output['additional_findings']['caa_suspicion_explicit'])
        self.assertEqual(output['additional_findings']['microbleed_description'], CMB)
        self.assertEqual(output['additional_findings']['other_findings'], [EDEMA])
        data = payload()
        data['findings'].append({'type': 'other', 'acute_lesion': None, 'evidence': CAA})
        with self.assertRaises(ValueError): self.validate(data)

    def test_inferred_caa_or_invented_quote_rejected(self):
        data = payload()
        data['additional_findings']['caa_evidence'] = CMB
        with self.assertRaises(ValueError): self.validate(data)

    def test_explicit_caa_suspicion_cannot_be_omitted(self):
        for value in (None, False):
            data = payload()
            data['additional_findings']['caa_suspicion_explicit'] = value
            data['additional_findings']['caa_evidence'] = None
            with self.assertRaises(ValueError): self.validate(data)
        data = payload()
        data['additional_findings']['other_findings'] = ['Invented tumor']
        with self.assertRaises(ValueError): self.validate(data)

    def test_negated_phenotype_rejected(self):
        quote = 'Keine Mikroblutungen.'
        p = procedure(result_text=quote)
        data = empty_payload(p)
        data['findings'] = [{'type': 'cmb', 'acute_lesion': None, 'evidence': quote}]
        with self.assertRaises(ValueError): self.validate(data, p)

    def test_strict_schema_and_provenance(self):
        for changes in ({'patient_id': 'other'}, {'procedure_id': 'other'}, {'unexpected': True}):
            data = payload(); data.update(changes)
            with self.assertRaises(ValueError): self.validate(data)
        for changes in ({'type': 'CAA'}, {'acute_lesion': 'true'}, {'evidence': 'not in source'}):
            data = payload(); data['findings'][0].update(changes)
            with self.assertRaises(ValueError): self.validate(data)

    def test_report_boundary_excludes_unrelated_consultation(self):
        quote = 'Es wurde kein Befund im Röntgen erstellt.'
        p = procedure(result_text=quote + '\nInternistisches Konsil (15.12.2015)\n' + LOBAR)
        self.assertEqual(report_text(p), quote)
        with self.assertRaises(ValueError): self.validate(payload(p), p)

    def test_mock_backend_without_granite(self):
        with patch.dict(os.environ, {'LLM_BACKEND': 'mock'}): client = get_client()
        p = procedure()
        raw = client.generate_json(radiology_prompt(p, RadiologyExtraction.model_json_schema()))
        self.assertEqual(validate_radiology(raw, '123', p)['findings'], [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runs, self.rows = {}, []
        self.session = MagicMock()
        def add(row):
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1
                self.runs[row.id] = row
            else:
                self.rows.append(row)
        self.session.add.side_effect = add
        self.session.get.side_effect = lambda cls, key: self.runs[key]
        self.session.execute.return_value.all.return_value = [(42,)]
        self.session.scalars.side_effect = lambda stmt: [r for r in self.rows if r.deleted is False]
        owner = self
        class Transaction:
            def __enter__(self):
                self.before = list(owner.rows)
                self.before_runs = dict(owner.runs)
                return owner.session
            def __exit__(self, typ, value, tb):
                if typ:
                    owner.rows[:] = self.before
                    owner.runs.clear()
                    owner.runs.update(self.before_runs)
                else:
                    for row in owner.rows:
                        for field in ('mri_date', 'acute_lesion'):
                            if isinstance(getattr(row, field), Null): setattr(row, field, None)
                return False
        self.factory = MagicMock()
        self.factory.begin.side_effect = Transaction

    def extract(self, procedures=None, responses=None, *, dry_run=True):
        procedures = procedures or [procedure()]
        if not dry_run:
            # Arrange a verified dry-run, then exercise the inference-free path.
            prepared = self.extract(procedures, responses, dry_run=True)
            if prepared['status'] != 'SUCCESS':
                return prepared
            self.client.generate_json.reset_mock()
            with patch('caa_pdf_extractor.extraction.radiology.build_patient_bundle', return_value=bundle(*procedures)), \
                 patch('caa_pdf_extractor.extraction.radiology.get_client', side_effect=AssertionError('Commit must not initialize a backend')):
                return extract_patient('123', dry_run=False, client=self.client,
                                       session_factory=self.factory, debug_root=self.temp.name)
        responses = responses or [json.dumps(payload(p)) for p in procedures if p.modality == 'MRI']
        self.client = SimpleNamespace(runtime='fake', model_name='fake', model_version='1',
                                      generate_json=MagicMock(side_effect=responses))
        with patch('caa_pdf_extractor.extraction.radiology.build_patient_bundle', return_value=bundle(*procedures)):
            return extract_patient('123', dry_run=dry_run, client=self.client,
                                   session_factory=self.factory, debug_root=self.temp.name)

    def debug(self):
        return json.loads(Path(self.temp.name, '123', 'radiology_radiology_v2_source_evidence.json').read_text(encoding='utf-8'))

    def commit_saved(self, procedures=None, *, use_client=True):
        self.client.generate_json.reset_mock()
        self.client.generate_json.side_effect = AssertionError('Commit must not call inference')
        with patch('caa_pdf_extractor.extraction.radiology.build_patient_bundle', return_value=bundle(*(procedures or [procedure()]))), \
             patch('caa_pdf_extractor.extraction.radiology.get_client', side_effect=AssertionError('Commit must not initialize Granite')):
            result = extract_patient('123', dry_run=False, client=self.client if use_client else None,
                                     session_factory=self.factory, debug_root=self.temp.name)
        self.client.generate_json.assert_not_called()
        return result

    def artifact_path(self):
        return sorted(Path(self.temp.name, '123', 'verified').glob('radiology_*.json'))[-1]

    def edit_artifact(self, change):
        from caa_pdf_extractor.extraction.radiology_artifacts import digest
        path = self.artifact_path()
        data = json.loads(path.read_text(encoding='utf-8'))
        data.pop('artifact_sha256')
        change(data)
        data['artifact_sha256'] = digest(data)
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_saved_verified_artifact_commits_without_any_model_call(self):
        dry = self.extract()
        source_run = self.runs[1]
        before = json.loads(json.dumps(source_run.raw_output))
        normalized = self.debug()['normalized_output']
        first = self.commit_saved()
        second = self.commit_saved()
        self.assertTrue(first['verified_artifact_used'])
        self.assertFalse(first['granite_called'])
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped']), (2, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped']), (0, 2))
        self.assertEqual(self.debug()['normalized_output'], normalized)
        self.assertEqual(source_run.raw_output, before)
        commits = [r for r in self.runs.values() if r.raw_output.get('operation') == 'COMMIT_VERIFIED_EXTRACTION']
        self.assertEqual(len(commits), 2)
        self.assertTrue(all(r.raw_output['source_run_id'] == source_run.id and not r.raw_output['inference_performed'] for r in commits))

    def test_default_commit_does_not_initialize_granite(self):
        self.extract()
        with patch.dict(os.environ, {'LLM_BACKEND': 'granite', 'GRANITE_MODEL': 'fake'}):
            result = self.commit_saved(use_client=False)
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertFalse(result['granite_called'])

    def test_stale_source_fingerprint_blocks_commit(self):
        self.extract()
        result = self.commit_saved([procedure(result_text=procedure().result_text + '\nSource changed')])
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_changed_source_text_blocks_commit(self):
        self.extract()
        result = self.commit_saved([procedure(source_text='Source provenance changed')])
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_changed_prompt_version_blocks_reuse(self):
        self.extract()
        with patch('caa_pdf_extractor.extraction.radiology_artifacts.PROMPT_VERSION', 'radiology_v2'):
            result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_changed_prompt_text_with_same_version_blocks_reuse(self):
        self.extract()
        with patch('caa_pdf_extractor.llm.radiology_prompts.INSTRUCTIONS', 'Changed instructions'):
            result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_unverified_artifact_blocks_commit_even_with_valid_checksum(self):
        self.extract()
        self.edit_artifact(lambda data: data.update(validation_status='UNVERIFIED'))
        result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_edited_evidence_blocks_commit_even_with_valid_checksum(self):
        self.extract()
        self.edit_artifact(lambda data: data['procedures'][0]['normalized_output']['findings'][0].update(evidence='Invented quotation'))
        result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_missing_artifact_never_falls_back_to_granite(self):
        self.extract()
        self.artifact_path().unlink()
        result = self.commit_saved()
        self.assertEqual(result['status'], 'NO_VERIFIED_EXTRACTION')
        self.assertIn('--dry-run', result['next_step'])
        self.assertEqual(self.rows, [])

    def test_invalid_json_artifact_blocks_commit(self):
        self.extract()
        self.artifact_path().write_text('not JSON', encoding='utf-8')
        result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_verified_empty_extraction_commits_successfully_without_inference(self):
        p = procedure(result_text='Es wurde kein Befund im Röntgen erstellt.')
        self.extract([p], [json.dumps(empty_payload(p))])
        result = self.commit_saved([p])
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertTrue(result['verified_artifact_used'])
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(self.rows, [])

    def test_changed_case_resolution_blocks_artifact(self):
        self.extract()
        self.session.execute.return_value.all.return_value = [(43,)]
        result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_model_identity_mismatch_blocks_artifact(self):
        self.extract()
        self.client.model_name = 'different-model'
        result = self.commit_saved()
        self.assertEqual(result['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_previous_verified_debug_can_be_promoted_without_inference(self):
        from caa_pdf_extractor.extraction.radiology_artifacts import verify_debug_artifact
        self.extract()
        original = self.debug()['normalized_output']
        self.client.generate_json.reset_mock()
        with patch('caa_pdf_extractor.preparation.build_patient_bundle', return_value=bundle(procedure())):
            path = verify_debug_artifact('123', Path(self.temp.name, '123', 'radiology_radiology_v2_source_evidence.json'),
                                         session_factory=self.factory, debug_root=self.temp.name)
        self.assertEqual(json.loads(path.read_text())['validation_status'], 'VERIFIED')
        result = self.commit_saved()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(self.debug()['normalized_output'], original)

    def test_dry_run_no_radiology_inserts_and_complete_audit(self):
        result = self.extract()
        self.assertEqual(result['case_id'], 42)
        self.assertEqual(result['radiology_records_validated'], 2)
        self.assertEqual(self.rows, [])
        debug = self.debug()
        self.assertEqual(debug['normalized_output'][0]['case_id'], 42)
        self.assertEqual(debug['normalized_output'][0]['additional_findings']['caa_evidence'], CAA)
        self.assertIsNotNone(debug['runs'][0]['completed_at'])

    def test_successful_multirow_insert_and_mapping(self):
        result = self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'], result['duplicates_skipped']), (2, 0))
        self.assertEqual([r.type for r in self.rows], ['lobar', 'cmb'])
        self.assertEqual([r.acute_lesion for r in self.rows], [True, False])
        self.assertTrue(all(r.case_id == 42 and r.pid == '123' and not r.deleted and not r.saved_on_hdd for r in self.rows))
        self.assertEqual(self.rows[0].mri_date.isoformat(), '2015-12-14')
        self.assertFalse(any(r.type == 'other' for r in self.rows))

    def test_duplicate_phenotypes_skipped_second_commit(self):
        self.extract(dry_run=False)
        result = self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'], result['duplicates_skipped']), (0, 2))
        self.assertEqual(len(self.rows), 2)

    def test_different_examination_date_allowed(self):
        self.extract(dry_run=False)
        result = self.extract([procedure(date='2016-01-07')], dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 2)
        self.assertEqual(len(self.rows), 4)

    def test_deleted_rows_do_not_block_new_insert(self):
        self.extract(dry_run=False)
        for row in self.rows: row.deleted = True
        result = self.extract(dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 2)

    def test_ct_rtg_and_composite_cta_audited_but_never_written(self):
        ct = procedure(procedure_id='ct', modality='CT', date='2015-12-11', studies=['Schädel nativ', 'CTA intracranielle Gefäße', 'CTA Halsgefäße'])
        rtg = procedure(procedure_id='rtg', modality='RTG', date='2015-12-11')
        result = self.extract([ct, rtg, procedure()], dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 2)
        self.assertEqual(self.client.generate_json.call_count, 0)
        self.assertEqual(len(self.runs), 6)
        self.assertEqual([self.runs[x].status for x in (1, 2)], ['SKIPPED_NON_MRI'] * 2)
        self.assertEqual(self.runs[1].raw_output['procedure']['studies'], ct.studies)
        self.assertTrue(all(r.mri_date.isoformat() == '2015-12-14' for r in self.rows))

    def test_no_report_mri_creates_no_unknown_placeholder(self):
        p = procedure(date='2016-01-07', result_text='Es wurde kein Befund im Röntgen erstellt.')
        result = self.extract([p], [json.dumps(empty_payload(p))], dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(result['status'], 'SUCCESS')

    def test_unknown_date_and_acuity_explicit_null(self):
        quote = 'Unbekannter Phänotyp.'
        p = procedure(date=None, result_text=quote)
        data = empty_payload(p); data['findings'] = [{'type': 'unknown', 'acute_lesion': None, 'evidence': quote}]
        result = self.extract([p], [json.dumps(data)], dry_run=False)
        self.assertEqual(result['new_rows_inserted'], 1)
        self.assertIsNone(self.rows[0].mri_date)
        self.assertIsNone(self.rows[0].acute_lesion)

    def test_case_not_found_and_ambiguous(self):
        for matches, status in ([], 'CASE_NOT_FOUND'), ([(42,), (43,)], 'CASE_AMBIGUOUS'):
            self.session.execute.return_value.all.return_value = matches
            result = self.extract(dry_run=False)
            self.assertEqual(result['status'], status)
            self.assertEqual(self.rows, [])

    def test_malformed_json_retained(self):
        result = self.extract(responses=['not JSON'], dry_run=False)
        self.assertEqual(result['status'], 'FAILED_PARSE')
        self.assertEqual(self.rows, [])
        self.assertEqual(self.runs[1].raw_output['response'], 'not JSON')
        self.assertIsNotNone(self.runs[1].validation_errors)

    def test_independent_bad_phenotype_preserves_valid_siblings(self):
        p2 = procedure(procedure_id='doc1_proc02', date='2016-01-07')
        data = payload(p2); data['findings'][0]['type'] = 'invalid'
        result = self.extract([procedure(), p2], [json.dumps(payload()), json.dumps(data)], dry_run=False)
        self.assertEqual(result['status'], 'PARTIAL_VALID')
        self.assertEqual(self.rows, [])
        self.assertGreater(result['radiology_records_validated'], 0)

    def test_insert_failure_rolls_back_entire_batch_and_preserves_source_audit(self):
        add = self.session.add.side_effect
        def fail_second(row):
            if isinstance(row, Radiology) and self.rows: raise RuntimeError('synthetic insert failure')
            add(row)
        self.session.add.side_effect = fail_second
        result = self.extract(dry_run=False)
        self.assertEqual(result['status'], 'FAILED_INSERT')
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(self.rows, [])
        self.assertEqual(self.runs[1].raw_output['insertion_status'], 'SUCCESS')


if __name__ == '__main__':
    unittest.main()
