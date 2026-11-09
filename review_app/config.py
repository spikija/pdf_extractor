from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = ROOT / 'data' / 'review'
PDF_ROOT = ROOT / 'pdf_db'
STABLE_ROOT = ROOT / 'data' / 'patient_artifacts'
ARTIFACT_ROOTS = (STABLE_ROOT, ROOT / 'patient_artifacts', ROOT / 'data' / 'debug')
MODULES = {'diagnoses': 'Diagnoses', 'events': 'Clinical events',
           'medications': 'Medications', 'eeg': 'EEG', 'CAA': 'CAA', 'neurosonology': 'Neurosonology',
           'clinical_scales': 'Clinical scales', 'CHA2DS2_VA': 'CHA2DS2-VA',
           'ORBIT': 'ORBIT', 'OAC_decision_analysis': 'OAC decision'}
