"""Synthetic family history validation, artifact and transaction regressions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from caa_pdf_extractor.database.models import ExtractionRun, FamilyHistory
from caa_pdf_extractor.extraction.family_history import extract_patient
from caa_pdf_extractor.extraction.family_history_inputs import select_family_history_inputs
from caa_pdf_extractor.extraction.family_history_models import validate_family_history, relative_category
from caa_pdf_extractor.llm.backend import get_client
from caa_pdf_extractor.llm.family_history_prompts import family_history_prompt
from caa_pdf_extractor.preparation.models import PatientDocumentBundle, PreparedDocument, PreparedSection


def bundle(text='Familienanamnese: Mutter hatte Demenz.'):
    section = PreparedSection('doc1_sec01', '123', 1, 'Anamnese', 2, 2, text, text)
    return PatientDocumentBundle('123', [PreparedDocument(1, 'synthetic.pdf', '123/synthetic.pdf',
        'synthetic-hash', 'unknown', None, [section])])


def payload(relative='Mutter', disease='Demenz', category='first grade', evidence=None):
    return {'patient_id': '123', 'family_history': [{'relative': relative, 'disease': disease,
        'relative_category': category, 'source_document_id': 1, 'section_id': 'doc1_sec01',
        'page_number': 2, 'evidence': evidence or f'Familienanamnese: {relative} hatte {disease}.'}]}


class ValidationTests(unittest.TestCase):
    def validate(self, data=None, text=None):
        return validate_family_history(json.dumps(data or payload()), '123',
            select_family_history_inputs(bundle(text or 'Familienanamnese: Mutter hatte Demenz.')))

    def test_mother_first_degree(self):
        self.assertEqual(relative_category('Mutter'), 'first grade')

    def test_father_first_degree(self):
        self.assertEqual(relative_category('Vater'), 'first grade')

    def test_siblings_first_degree(self):
        for relative in ('Bruder', 'Schwester', 'brother', 'sister'):
            self.assertEqual(relative_category(relative), 'first grade')

    def test_children_first_degree(self):
        for relative in ('Tochter', 'Sohn'):
            self.assertEqual(relative_category(relative), 'first grade')

    def test_grandparents_second_degree(self):
        for relative in ('mütterliche Großmutter', 'Großvater', 'grandmother'):
            self.assertEqual(relative_category(relative), 'second grade')

    def test_aunt_uncle_second_degree(self):
        for relative in ('Tante', 'Onkel', 'Nichte', 'Neffe', 'Enkelin'):
            self.assertEqual(relative_category(relative), 'second grade')

    def test_unspecified_familial_disease(self):
        text = 'Familiäre Demenz.'
        result = self.validate(payload(None, 'Demenz', 'not specified', text), text)
        self.assertIsNone(result['family_history'][0]['relative'])
        self.assertEqual(result['family_history'][0]['relative_category'], 'not specified')

    def test_ambiguous_relative_unknown(self):
        text = 'Familienanamnese: Verwandter hatte Demenz.'
        with self.assertRaisesRegex(ValueError, 'Vague relative degree'):
            self.validate(payload('Verwandter', 'Demenz', 'unknown', text), text)

    def test_half_sibling_no_invented_convention(self):
        self.assertEqual(relative_category('Halbbruder'), 'unknown')

    def test_compound_kinship_is_not_incorrectly_first_degree(self):
        with self.assertRaises(ValueError):
            relative_category('Vater der Mutter')

    def test_negative_family_history_no_row_and_audit_preserved(self):
        text = 'Familienanamnese unauffällig.'
        result = self.validate({'patient_id': '123', 'family_history': []}, text)
        self.assertEqual(result['family_history'], [])
        self.assertEqual(result['negative_statements'][0]['text'], text)

    def test_negative_disease_cannot_be_inserted(self):
        text = 'Familienanamnese: Mutter hatte keine Demenz.'
        with self.assertRaises(ValueError):
            self.validate(payload(evidence=text), text)

    def test_patient_disease_is_not_family_history(self):
        text = 'Mutter gesund; Patient hatte Demenz.'
        with self.assertRaises(ValueError):
            self.validate(payload(evidence=text), text)
        self.assertEqual(select_family_history_inputs(bundle('Eigenanamnese: Demenz.')), [])

    def test_genetics_not_family_history(self):
        text = 'Familienanamnese: Mutter APOE Genotyp.'
        with self.assertRaises(ValueError):
            self.validate(payload(disease='APOE Genotyp', evidence=text), text)
        self.assertEqual(select_family_history_inputs(bundle('APOE Genotyp E3/E4.')), [])

    def test_verbatim_required(self):
        with self.assertRaises(ValueError):
            self.validate(payload(evidence='Die Mutter war dement.'))

    def test_disease_not_inferred_or_translated(self):
        for disease in ('dementia', 'Alzheimer-Demenz'):
            with self.assertRaises(ValueError):
                self.validate(payload(disease=disease))

    def test_wrong_provenance_or_patient_rejected(self):
        for field, value in (('source_document_id', 2), ('page_number', 3), ('section_id', 'wrong')):
            data = payload()
            data['family_history'][0][field] = value
            with self.assertRaises(ValueError):
                self.validate(data)
        data = payload(); data['patient_id'] = 'wrong'
        with self.assertRaises(ValueError):
            self.validate(data)

    def test_option_and_conflicting_degree_rejected(self):
        for category in ('Option', 'second grade', 'unknown'):
            with self.assertRaises(ValueError):
                self.validate(payload(category=category))

    def test_null_model_category_derived(self):
        result = self.validate(payload(category=None))
        self.assertEqual(result['family_history'][0]['relative_category'], 'first grade')
        self.assertEqual(len(result['relative_category_reconciliation']), 1)

    def test_whitespace_normalized_evidence(self):
        data = payload(evidence='Familienanamnese:   Mutter hatte Demenz.')
        self.assertEqual(self.validate(data)['family_history'][0]['relative'], 'Mutter')

    def test_selection_focused_not_document(self):
        inputs = select_family_history_inputs(bundle('Medikation: ASS.\nEigenanamnese: Schlaganfall.\nMutter hatte Demenz.\nLabor: Glucose.'))
        self.assertEqual([i.text for i in inputs], ['Mutter hatte Demenz.'])

    def test_hemiparesis_not_half_sibling_and_other_sentences_excluded(self):
        self.assertEqual(select_family_history_inputs(bundle('Halbseitenlähmung rechts mit Sprachstörung.')), [])
        inputs = select_family_history_inputs(bundle('Mutter hatte Demenz. Patient hatte Schlaganfall.'))
        self.assertEqual([i.text for i in inputs], ['Mutter hatte Demenz.'])

    def test_disease_under_family_heading_with_unspecified_relative(self):
        prepared = bundle('Demenz.')
        prepared.documents[0].sections[0].heading = 'Familienanamnese'
        inputs = select_family_history_inputs(prepared)
        result = validate_family_history(json.dumps(payload(None, 'Demenz', 'not specified', 'Demenz.')), '123', inputs)
        self.assertEqual(result['family_history'][0]['relative_category'], 'not specified')

    def test_uncertain_attribution_blocks_record(self):
        text = 'Familienanamnese: Mutter oder Vater hatte Demenz.'
        with self.assertRaises(ValueError):
            self.validate(payload(evidence=text), text)

    def test_social_relation_not_treated_as_relative(self):
        text = 'Familienanamnese: Freund hatte Demenz.'
        with self.assertRaises(ValueError):
            self.validate(payload('Freund', 'Demenz', 'unknown', text), text)

    def test_vague_disease_kept_without_inference(self):
        text = 'Mutter hatte neurologische Erkrankung.'
        result = self.validate(payload('Mutter', 'neurologische Erkrankung', 'first grade', text), text)
        self.assertEqual(result['family_history'][0]['disease'], 'neurologische Erkrankung')

    def test_mock_no_granite(self):
        with patch.dict(os.environ, {'LLM_BACKEND': 'mock'}):
            client = get_client()
        inputs = select_family_history_inputs(bundle())
        result = validate_family_history(client.generate_json(family_history_prompt('123', inputs)), '123', inputs)
        self.assertEqual(result['family_history'], [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.bundle = bundle(); self.rows = []; self.runs = {}
        self.session = MagicMock()
        def add(row):
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1; self.runs[row.id] = row
            else:
                row.id = len(self.rows) + 1; self.rows.append(row)
        self.session.add.side_effect = add
        self.session.get.side_effect = lambda cls, key: self.runs.get(key)
        self.session.scalars.side_effect = lambda stmt: [r for r in self.rows if r.deleted is False]
        self.session.execute.return_value.all.return_value = [(42,)]
        owner = self
        class Transaction:
            def __enter__(self):
                self.rows_before = list(owner.rows); self.runs_before = dict(owner.runs)
                return owner.session
            def __exit__(self, typ, value, tb):
                if typ:
                    owner.rows[:] = self.rows_before
                    owner.runs.clear(); owner.runs.update(self.runs_before)
                return False
        self.factory = MagicMock(); self.factory.begin.side_effect = Transaction
        self.client = SimpleNamespace(runtime='fake', model_name='fake', model_version='1', generate_json=MagicMock())

    def extract(self, *, dry_run=True, response=None):
        self.client.generate_json.reset_mock()
        self.client.generate_json.side_effect = None if dry_run else AssertionError('Commit inference forbidden')
        self.client.generate_json.return_value = response if response is not None else json.dumps(payload())
        with patch('caa_pdf_extractor.extraction.family_history.build_patient_bundle', return_value=self.bundle), \
             patch('caa_pdf_extractor.extraction.family_history.get_client', side_effect=AssertionError('Factory forbidden')):
            result = extract_patient('123', dry_run=dry_run, client=self.client,
                                     session_factory=self.factory, debug_root=self.temp.name)
        if not dry_run:
            self.client.generate_json.assert_not_called()
        return result

    def artifact(self):
        return sorted(Path(self.temp.name, '123', 'verified').glob('family_history_*.json'))[-1]

    def test_dry_run_no_rows_then_commit_without_granite_idempotent(self):
        dry = self.extract()
        self.assertEqual(dry['status'], 'SUCCESS'); self.assertEqual(self.rows, [])
        self.assertEqual(json.loads(self.artifact().read_text())['validation_status'], 'VERIFIED')
        first, second = self.extract(dry_run=False), self.extract(dry_run=False)
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped']), (1, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped']), (0, 1))
        self.assertEqual(self.rows[0].case_id, 42)
        self.assertFalse(second['granite_called'])
        self.assertFalse(second['case_family_history_modified'])

    def test_duplicate_conservative_normalization_preserves_existing(self):
        self.rows.append(FamilyHistory(case_id=42, relative='  mutter ', disease='DEMENZ', relative_category='first grade', deleted=False))
        self.extract(); result = self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'], result['duplicates_skipped']), (0, 1))
        self.assertEqual(self.rows[0].relative, '  mutter ')

    def test_null_relative_duplicate(self):
        self.bundle = bundle('Familiäre Demenz.')
        data = payload(None, 'Demenz', 'not specified', 'Familiäre Demenz.')
        self.extract(response=json.dumps(data)); self.extract(dry_run=False)
        self.assertEqual(self.extract(dry_run=False)['duplicates_skipped'], 1)
        self.assertIsNone(self.rows[0].relative)

    def test_deleted_record_does_not_block_new(self):
        self.rows.append(FamilyHistory(case_id=42, relative='Mutter', disease='Demenz', relative_category='first grade', deleted=True))
        self.extract()
        self.assertEqual(self.extract(dry_run=False)['new_rows_inserted'], 1)

    def test_duplicate_within_artifact(self):
        data = payload(); data['family_history'] *= 2
        self.extract(response=json.dumps(data))
        result = self.extract(dry_run=False)
        self.assertEqual((result['new_rows_inserted'], result['duplicates_skipped']), (1, 1))

    def test_missing_and_stale_artifact(self):
        self.assertEqual(self.extract(dry_run=False)['status'], 'NO_VERIFIED_EXTRACTION')
        self.extract(); self.bundle = bundle('Familienanamnese: Mutter hatte Demenz.\nNew source.')
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_empty_verified_artifact_tracks_unselected_source(self):
        self.bundle = bundle('Eigenanamnese: Schlaganfall.')
        self.extract(response=json.dumps({'patient_id': '123', 'family_history': []}))
        result = self.extract(dry_run=False)
        self.assertEqual(result['status'], 'SUCCESS'); self.assertEqual(result['new_rows_inserted'], 0)
        self.bundle = bundle('Eigenanamnese: Demenz.')
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')

    def test_negative_history_empty_commit(self):
        self.bundle = bundle('Familienanamnese negativ.')
        self.extract(response=json.dumps({'patient_id': '123', 'family_history': []}))
        self.assertEqual(self.extract(dry_run=False)['status'], 'SUCCESS')
        self.assertEqual(self.rows, [])

    def test_case_not_found(self):
        self.session.execute.return_value.all.return_value = []
        self.assertEqual(self.extract()['status'], 'CASE_NOT_FOUND')
        self.client.generate_json.assert_not_called()

    def test_malformed_and_unsupported_output(self):
        for raw, status in (('not json', 'FAILED_PARSE'), (json.dumps({'patient_id': '123'}), 'FAILED_VALIDATION'),
                            (json.dumps(payload(evidence='Invented quotation')), 'FAILED_VALIDATION')):
            self.assertEqual(self.extract(response=raw)['status'], status)
            self.assertEqual(self.runs[len(self.runs)].raw_output['response'], raw)
        self.assertEqual(self.rows, [])
        self.assertEqual(self.extract(dry_run=False)['status'], 'NO_VERIFIED_EXTRACTION')

    def test_audit_anchor_required_even_with_rehashed_artifact(self):
        from caa_pdf_extractor.extraction.radiology_artifacts import digest
        self.extract(); path = self.artifact(); data = json.loads(path.read_text())
        data.pop('artifact_sha256'); data['normalized_output']['family_history'][0]['disease'] = 'Invented'
        data['artifact_sha256'] = digest(data); path.write_text(json.dumps(data))
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_changed_prompt_or_model_blocked(self):
        self.extract()
        with patch('caa_pdf_extractor.extraction.family_history_artifacts.PROMPT_VERSION', 'family_history_v2'):
            self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')
        self.client.model_version = 'changed'
        self.assertEqual(self.extract(dry_run=False)['status'], 'VERIFIED_EXTRACTION_STALE')

    def test_insert_failure_rolls_back_and_preserves_artifact(self):
        self.extract(); before = self.artifact().read_bytes()
        self.session.flush.side_effect = RuntimeError('Insert failure')
        self.assertEqual(self.extract(dry_run=False)['status'], 'FAILED')
        self.assertEqual(self.rows, [])
        self.assertEqual(self.artifact().read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
