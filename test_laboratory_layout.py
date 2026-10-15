"""Synthetic PDF word geometry: values, units, headers and provenance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
from decimal import Decimal
import unittest
from unittest.mock import patch
from caa_pdf_extractor.extraction.laboratory_layout import Token, page_tokens, extract_page, validate_candidate, inspect_patient
from caa_pdf_extractor.extraction.laboratory_models import parse_decimal
from caa_pdf_extractor.extraction.laboratory import extract_patient
from test_laboratory import bundle


def word(text, x, y, width=25):
    return (x, y, x + width, y + 8, text, 0, 0, 0)


def fixture(label='CRP', unit='mg/dL', values=('0.01', '0.42'), dates=False):
    words = [word('Parameter', 10, 10), word('Einheit', 120, 10)]
    words += [word('2015-12-11' if dates else 'Erster', 190, 10, 40),
              word('2015-12-14' if dates else 'Letzter', 290, 10, 40)]
    words += [word(label, 10, 40, 60), word(unit, 120, 40)]
    words += [word(value, 200 + index * 100, 40) for index, value in enumerate(values)]
    return page_tokens(words, 1)


class LayoutTests(unittest.TestCase):
    def test_same_row_association(self):
        result = extract_page(fixture(), 1)
        self.assertEqual([c['decimal_value'] for c in result], ['0.01', '0.42'])
        self.assertTrue(all(c['validation_status'] == 'VALID' for c in result))
        self.assertEqual([c['column_role'] for c in result], ['first', 'last'])

    def test_multiple_crp_retained_separate_provenance(self):
        result = extract_page(fixture(), 1)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['candidate_id'], result[1]['candidate_id'])

    def test_multiple_hba1c_retained(self):
        result = extract_page(fixture('HbA1c', '%', ('6.00', '5.90')), 1)
        self.assertEqual([c['decimal_value'] for c in result], ['6.00', '5.90'])

    def test_x_date_header_association(self):
        result = extract_page(fixture(dates=True), 1)
        self.assertEqual([c['date_of_exam'] for c in result], ['2015-12-11', '2015-12-14'])
        self.assertTrue(all(c['date_header_bbox'] for c in result))

    def test_no_date_null(self):
        result = extract_page(fixture(), 1)
        self.assertTrue(all(c['date_of_exam'] is None for c in result))

    def test_date_range_and_birth_date_ignored(self):
        tokens = fixture()
        raw = [(t.x0, t.y0 + 30, t.x1, t.y1 + 30, t.text) for t in tokens]
        raw += [word('Laborwerte', 10, 20), word('von', 50, 20), word('2015-12-11', 190, 20, 40),
                word('bis', 250, 20), word('2016-01-05', 290, 20, 40)]
        result = extract_page(page_tokens(raw, 1), 1)
        self.assertTrue(all(c['date_of_exam'] is None for c in result))

    def test_decimal_comma(self):
        self.assertEqual(extract_page(fixture(values=('0,42',)), 1)[0]['decimal_value'], '0.42')

    def test_decimal_point_and_no_float_value_parse(self):
        self.assertIsInstance(parse_decimal('0.01'), Decimal)
        with self.assertRaises(ValueError): parse_decimal(0.01)
        self.assertEqual(extract_page(fixture(values=('5.90',)), 1)[0]['decimal_value'], '5.90')

    def test_crp_mgdl_accepted(self):
        self.assertEqual(extract_page(fixture(unit='mg/dl'), 1)[0]['validation_status'], 'VALID')

    def test_crp_mgl_rejected(self):
        self.assertIn('UNIT_MISMATCH', extract_page(fixture(unit='mg/L'), 1)[0]['issues'])

    def test_hba1c_percent_accepted(self):
        result = extract_page(fixture('HbA1c', '%', ('5.90',)), 1)[0]
        self.assertEqual(result['validation_status'], 'VALID')
        self.assertFalse(result['audit_only'])

    def test_ifcc_audit_only(self):
        result = extract_page(fixture('HbA1c (IFCC)', 'mmol/mol', ('41',)), 1)[0]
        self.assertTrue(result['audit_only'])
        self.assertEqual(result['decimal_value'], '41')

    def test_wrapped_ifcc_unit_has_exact_fragment_tokens(self):
        tokens = fixture('HbA1c (IFCC)', 'mmol/m', ('41',))
        raw = [(t.x0, t.y0, t.x1, t.y1, t.text) for t in tokens] + [word('ol', 120, 50)]
        result = extract_page(page_tokens(raw, 1), 1)[0]
        self.assertEqual(result['unit_text'], 'mmol/mol')
        self.assertEqual(len(result['unit_fragments']), 2)
        self.assertTrue(result['audit_only'])

    def test_competing_row_labels_rejected(self):
        result = extract_page(fixture('CRP HbA1c'), 1)
        self.assertTrue(all('AMBIGUOUS_LAYOUT' in c['issues'] for c in result))

    def test_ambiguous_column_rejected(self):
        tokens = fixture()
        raw = [(t.x0, t.y0, t.x1, t.y1, t.text) for t in tokens if not t.text.startswith('0.')]
        raw += [word('0.01', 255, 40, 10)]
        result = extract_page(page_tokens(raw, 1), 1)
        self.assertIn('AMBIGUOUS_LAYOUT', result[0]['issues'])

    def test_two_values_same_column_rejected(self):
        tokens = fixture(values=('0.01',))
        raw = [(t.x0, t.y0, t.x1, t.y1, t.text) for t in tokens] + [word('0.42', 225, 40)]
        self.assertTrue(all('AMBIGUOUS_LAYOUT' in c['issues'] for c in extract_page(page_tokens(raw, 1), 1)))

    def test_competing_date_headers_rejected(self):
        tokens = fixture()
        raw = [(t.x0, t.y0 + 20, t.x1, t.y1 + 20, t.text) for t in tokens]
        raw += [word('2015-12-11', 195, 20, 40), word('2015-12-14', 205, 20, 40)]
        result = extract_page(page_tokens(raw, 1), 1)
        self.assertIn('AMBIGUOUS_DATE_COLUMN', result[0]['issues'])
        self.assertIsNone(result[0]['date_of_exam'])

    def test_token_bbox_provenance_required(self):
        tokens = fixture(); candidate = extract_page(tokens, 1)[0]
        self.assertEqual(validate_candidate(candidate, tokens), 'VALID')
        for field, replacement in (('numeric_value_bbox', [1, 2, 3, 4]), ('raw_numeric_text', '9.99'),
                                   ('source_tokens', []), ('date_of_exam', '2026-10-14')):
            changed = deepcopy(candidate); changed[field] = replacement
            with self.assertRaises(ValueError): validate_candidate(changed, tokens)

    def test_relevant_tokens_suffice_to_replay_geometry(self):
        tokens = fixture(); candidates = extract_page(tokens, 1)
        relevant = {t['token_index']: Token(**t) for c in candidates for t in c['source_tokens']}
        for candidate in candidates:
            self.assertEqual(validate_candidate(candidate, list(relevant.values())), 'VALID')

    def test_missing_table_header_rejected(self):
        tokens = page_tokens([word('CRP', 10, 40), word('mg/dL', 120, 40), word('0.01', 200, 40)], 1)
        self.assertEqual(extract_page(tokens, 1), [])

    def test_reference_cells_excluded(self):
        tokens = fixture(values=('0.01',))
        raw = [(t.x0, t.y0, t.x1, t.y1, t.text) for t in tokens] + [word('50', 90, 40, 10)]
        result = extract_page(page_tokens(raw, 1), 1)
        self.assertEqual([c['raw_numeric_text'] for c in result], ['0.01'])

    def test_six_target_aliases(self):
        for label, unit, marker in (('Abeta42', 'pg/mL', 'csf_beta42'), ('Aβ42/40', '', 'csf_beta4240_ratio'),
            ('total tau', 'pg/mL', 'csf_tau_total'), ('p-Tau', 'pg/mL', 'csf_ptau_total')):
            tokens = fixture(label, unit or 'ratio', ('0.42',))
            self.assertEqual(extract_page(tokens, 1)[0]['biomarker'], marker)

    def test_no_targets_no_candidates(self):
        self.assertEqual(extract_page(fixture('Natrium', 'mg/dL'), 1), [])

    def test_minimum_maximum_repetitions_preserved_as_audit_candidates(self):
        tokens = fixture('HbA1c', '%', ('6.00', '5.90'))
        raw = [(t.x0, t.y0, t.x1, t.y1, t.text) for t in tokens]
        raw += [word('Min.', 390, 10, 40), word('Max.', 490, 10, 40),
                word('5.90', 400, 40), word('6.00', 500, 40)]
        result = extract_page(page_tokens(raw, 1), 1)
        self.assertEqual([c['column_role'] for c in result], ['first', 'last', 'minimum', 'maximum'])
        self.assertEqual([c['audit_only'] for c in result], [False, False, True, True])
        self.assertEqual(len({c['candidate_id'] for c in result}), 4)

    def test_invalid_coordinate_provenance_rejected(self):
        with self.assertRaises(ValueError): page_tokens([word('CRP', float('nan'), 40)], 1)

    def test_commit_path_stops_without_database_or_granite(self):
        with tempfile.TemporaryDirectory() as root:
            self._assert_missing_artifact(root)

    def _assert_missing_artifact(self, root):
        with patch('caa_pdf_extractor.extraction.laboratory.get_client', side_effect=AssertionError), \
             patch('caa_pdf_extractor.extraction.laboratory.SessionLocal', side_effect=AssertionError):
            result = extract_patient('123', dry_run=False, debug_root=root)
        self.assertEqual(result['status'], 'NO_VERIFIED_EXTRACTION')
        self.assertFalse(result['granite_called'])
        self.assertEqual(result['new_rows_inserted'], 0)

    def test_original_pdf_hash_checked_and_debug_contains_only_relevant_tokens(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); pdf_path = root / '123/synthetic.pdf'
            pdf_path.parent.mkdir()
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                for x, y, text in ((10, 30, 'Parameter'), (120, 30, 'Einheit'), (200, 30, 'Erster'),
                                   (300, 30, 'Letzter'), (10, 60, 'CRP'), (120, 60, 'mg/dL'),
                                   (200, 60, '0.01'), (300, 60, '0.42'), (10, 200, 'Unrelated text')):
                    page.insert_text((x, y), text)
                pdf.save(pdf_path)
            prepared = bundle()
            prepared.documents[0].sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            prepared.documents[0].sections[0].page_start = 1
            prepared.documents[0].sections[0].page_end = 1
            factory = MagicMock()
            factory.begin.return_value.__enter__.return_value.execute.return_value.all.return_value = [(42,)]
            with patch('caa_pdf_extractor.extraction.laboratory_layout.build_patient_bundle', return_value=prepared):
                result = inspect_patient('123', session_factory=factory, debug_root=root / 'debug', pdf_root=root)
                self.assertEqual(result['status'], 'SUCCESS')
                self.assertEqual(result['candidate_count'], 2)
                output = json.loads(Path(result['layout_debug_artifact']).read_text())
                self.assertNotIn('Unrelated', str(output))
                self.assertFalse(result['granite_called'])
                self.assertFalse(result['verified_artifact_created'])
                self.assertFalse(result['clinical_commit_performed'])
                prepared.documents[0].sha256 = 'stale'
                self.assertEqual(inspect_patient('123', session_factory=factory, debug_root=root / 'debug', pdf_root=root)['status'], 'FAILED_LAYOUT')


if __name__ == '__main__':
    unittest.main()
