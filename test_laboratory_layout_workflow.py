"""Layout artifacts: NULL-safe rows and provenance-backed duplicate identity."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy.sql.elements import Null
from caa_pdf_extractor.database.models import ExtractionRun, Laboratory
from caa_pdf_extractor.extraction.laboratory_layout import extract_page, page_tokens, METHOD_VERSION
from caa_pdf_extractor.extraction.laboratory_layout_workflow import extract_layout_patient, observation_fingerprint
from caa_pdf_extractor.extraction.laboratory_models import FIELDS
from caa_pdf_extractor.extraction.radiology_artifacts import digest
from test_laboratory_layout import word


def synthetic_snapshot():
    raw = [word('Parameter', 10, 10), word('Einheit', 120, 10)]
    raw += [word(role, 190 + index * 100, 10, 40) for index, role in enumerate(('Erster', 'Letzter', 'Min.', 'Max.'))]
    for y, label, unit, values in ((40, 'CRP', 'mg/dl', ('0.01', '0.42')),
          (80, 'HbA1c', '%', ('6.00', '5.90', '5.90', '6.00')),
          (120, 'HbA1c (IFCC)', 'mmol/mol', ('42.00', '41.00', '41.00', '42.00'))):
        raw += [word(label, 10, y, 70), word(unit, 120, y)]
        raw += [word(v, 200 + index * 100, y) for index, v in enumerate(values)]
    candidates = extract_page(page_tokens(raw, 6), 1)
    other = raw[:6] + [word('CRP', 10, 40), word('mg/dl', 120, 40), word('0.03', 200, 40)]
    candidates += extract_page(page_tokens(other, 3), 2)
    return {'patient_id': '123', 'resolved_case_id': 42, 'method_version': METHOD_VERSION,
        'candidates': candidates, 'source_pages': [{'source_document_id': 1, 'page_number': 6, 'pdf_sha256': 'synthetic'}],
        'prepared_source_manifest': 'synthetic-manifest'}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.current = synthetic_snapshot(); self.rows = []; self.runs = {}
        self.session = MagicMock()
        def add(row):
            if isinstance(row, ExtractionRun):
                row.id = len(self.runs) + 1; self.runs[row.id] = row
            else:
                row.id = len(self.rows) + 1; self.rows.append(row)
        self.session.add.side_effect = add
        self.session.get.side_effect = lambda cls, key: self.runs.get(key)
        self.session.execute.return_value.all.return_value = [(42,)]
        self.session.scalars.side_effect = lambda statement: (list(self.runs.values()) if statement.column_descriptions[0]['entity'] is ExtractionRun
            else [r for r in self.rows if r.deleted is False])
        owner = self
        class Transaction:
            def __enter__(self):
                self.rows = list(owner.rows); self.runs = dict(owner.runs)
                return owner.session
            def __exit__(self, typ, value, tb):
                if typ:
                    owner.rows[:] = self.rows; owner.runs.clear(); owner.runs.update(self.runs)
                else:
                    for row in owner.rows:
                        for name in (*FIELDS, 'date_of_exam'):
                            if isinstance(getattr(row, name), Null): setattr(row, name, None)
                return False
        self.factory = MagicMock(); self.factory.begin.side_effect = Transaction

    def extract(self, dry_run=True):
        with patch('caa_pdf_extractor.extraction.laboratory_layout_workflow.snapshot', return_value=deepcopy(self.current)), \
             patch('caa_pdf_extractor.extraction.laboratory.get_client', side_effect=AssertionError('No Granite')):
            return extract_layout_patient('123', dry_run=dry_run, session_factory=self.factory, debug_root=self.temp.name)

    def artifact(self):
        return sorted(Path(self.temp.name, '123', 'verified').glob('laboratory_layout_*.json'))[-1]

    def test_verified_layout_commit_and_idempotent_second_commit(self):
        dry = self.extract()
        self.assertEqual(dry['validation_status'], 'VERIFIED')
        self.assertEqual(dry['persistable_observations'], 5)
        self.assertEqual(dry['audit_only_observations'], 6)
        self.assertEqual(self.rows, [])
        before = self.artifact().read_bytes()
        first, second = self.extract(False), self.extract(False)
        self.assertEqual((first['new_rows_inserted'], first['duplicates_skipped'], first['ambiguous_blocked']), (5, 0, 0))
        self.assertEqual((second['new_rows_inserted'], second['duplicates_skipped'], second['ambiguous_blocked']), (0, 5, 0))
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(self.artifact().read_bytes(), before)
        self.assertFalse(second['granite_called'])
        self.assertEqual(len(second['observation_links']), 5)

    def test_crp_and_hba1c_null_date_one_observation_per_row(self):
        self.extract(); self.extract(False)
        self.assertEqual([r.crp for r in self.rows], [Decimal('0.01'), Decimal('0.42'), None, None, Decimal('0.03')])
        self.assertEqual([r.hba1c for r in self.rows], [None, None, Decimal('6.00'), Decimal('5.90'), None])
        for row in self.rows:
            self.assertIsNone(row.date_of_exam)
            self.assertEqual(sum(getattr(row, name) is not None for name in FIELDS), 1)
            self.assertTrue(all(getattr(row, name) is None for name in FIELDS if name not in ('crp', 'hba1c')))

    def test_ifcc_minimum_maximum_audit_only(self):
        self.extract()
        data = json.loads(self.artifact().read_text())
        self.assertEqual(len(data['source_snapshot']['candidates']), 11)
        self.assertTrue(all(not c['audit_only'] and c['column_role'] in ('first', 'last') for c in data['observations']))

    def test_distinct_provenance_same_value_null_date_blocked(self):
        self.extract(); self.extract(False)
        candidate = deepcopy(self.current['candidates'][0])
        candidate['candidate_id'] = 'different-source-cell'
        candidate['numeric_value_bbox'][0] += 0.1
        self.current['candidates'] = [candidate]
        self.extract()
        result = self.extract(False)
        self.assertEqual(result['status'], 'DUPLICATE_IDENTITY_AMBIGUOUS')
        self.assertEqual(result['ambiguous_blocked'], 1)
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(len(self.rows), 5)

    def test_two_equal_values_in_new_artifact_not_silently_collapsed(self):
        first = deepcopy(self.current['candidates'][0]); other = deepcopy(first)
        other['candidate_id'] = 'another-cell'; other['numeric_value_bbox'][0] += 1
        self.current['candidates'] = [first, other]
        self.extract(); result = self.extract(False)
        self.assertEqual(result['ambiguous_blocked'], 2)
        self.assertEqual(self.rows, [])

    def test_unlinked_legacy_match_blocks_reuse(self):
        self.rows.append(Laboratory(id=1, case_id=42, date_of_exam=None, crp=Decimal('0.01'), deleted=False))
        self.extract(); result = self.extract(False)
        self.assertEqual((result['new_rows_inserted'], result['ambiguous_blocked']), (4, 1))

    def test_audited_row_changed_blocks_duplicate_reinsertion(self):
        self.extract(); self.extract(False)
        self.rows[0].crp = Decimal('9.99')
        result = self.extract(False)
        self.assertEqual(result['ambiguous_blocked'], 1)
        self.assertEqual(result['new_rows_inserted'], 0)
        self.assertEqual(self.rows[0].crp, Decimal('9.99'))

    def test_source_fingerprint_mismatch_blocks_all_writes(self):
        self.extract(); self.current['prepared_source_manifest'] = 'changed'
        self.assertEqual(self.extract(False)['status'], 'VERIFIED_EXTRACTION_STALE')
        self.assertEqual(self.rows, [])

    def test_rehashed_tampering_rejected_by_original_audit(self):
        self.extract(); path = self.artifact(); data = json.loads(path.read_text())
        data.pop('artifact_sha256'); data['source_run_id'] = 999
        data['artifact_sha256'] = digest(data); path.write_text(json.dumps(data))
        self.assertEqual(self.extract(False)['status'], 'VERIFIED_EXTRACTION_INVALID')
        self.assertEqual(self.rows, [])

    def test_observation_fingerprint_uses_coordinates_not_evidence(self):
        candidate = self.current['candidates'][0]
        fingerprint = observation_fingerprint('123', candidate)
        changed = deepcopy(candidate); changed['reconstructed_row_text'] = 'representation changed'
        self.assertEqual(observation_fingerprint('123', changed), fingerprint)
        changed['numeric_value_bbox'][0] += 0.1
        self.assertNotEqual(observation_fingerprint('123', changed), fingerprint)

    def test_transaction_failure_does_not_leave_clinical_rows(self):
        self.extract(); self.session.flush.side_effect = RuntimeError('failure')
        self.assertEqual(self.extract(False)['status'], 'FAILED_LAYOUT_WORKFLOW')
        self.assertEqual(self.rows, [])


if __name__ == '__main__': unittest.main()
