"""Configuration and import checks independent of the local database/runtime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from caa_pdf_extractor import config
from caa_pdf_extractor.llm.backend import get_client
from caa_pdf_extractor.llm.prompts import clinical_history_prompt
from caa_pdf_extractor.llm.medication_prompts import medication_prompt
from caa_pdf_extractor.extraction.models import ClinicalHistoryExtraction
from caa_pdf_extractor.extraction.medication_models import MedicationExtraction


class PortabilityTests(unittest.TestCase):
    def test_model_imports_without_configuration_or_engine(self):
        environment = dict(os.environ, DATABASE_URL='')
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-c',
            'import caa_pdf_extractor.models, caa_pdf_extractor.base; '
            'from caa_pdf_extractor.database.connection import get_engine; '
            'assert get_engine.cache_info().currsize == 0'],
            env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_environment_precedence_and_optional_dotenv(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env').write_text('DATABASE_URL=dotenv-placeholder\n')
            with patch.object(config, 'PROJECT_ROOT', root), patch.dict(os.environ, {'DATABASE_URL': 'environment-placeholder'}):
                self.assertEqual(config.database_url(), 'environment-placeholder')
            with patch.object(config, 'PROJECT_ROOT', root / 'absent'), patch.dict(os.environ, {'DATABASE_URL': 'environment-placeholder'}):
                self.assertEqual(config.database_url(), 'environment-placeholder')

    def test_missing_url_error(self):
        with patch('caa_pdf_extractor.config.load_environment'), patch.dict(os.environ, {'DATABASE_URL': ''}):
            with self.assertRaisesRegex(RuntimeError, 'DATABASE_URL is required'):
                config.database_url()

    def test_mock_backend_uses_no_runtime_and_handles_both_schemas(self):
        with patch.dict(os.environ, {'LLM_BACKEND': 'mock'}):
            client = get_client()
        clinical = clinical_history_prompt('synthetic', [], ClinicalHistoryExtraction.model_json_schema())
        medication = medication_prompt('synthetic', [], MedicationExtraction.model_json_schema())
        self.assertEqual(ClinicalHistoryExtraction.model_validate_json(client.generate_json(clinical)).clinical_history, [])
        self.assertEqual(MedicationExtraction.model_validate_json(client.generate_json(medication)).medications, [])

    def test_backend_configuration_rejects_unknown_backend(self):
        with patch.dict(os.environ, {'LLM_BACKEND': 'external'}):
            with self.assertRaisesRegex(ValueError, 'mock or granite'):
                get_client()


if __name__ == '__main__':
    unittest.main()
