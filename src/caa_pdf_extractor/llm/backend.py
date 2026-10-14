"""Explicit local backend selection; injected clients bypass this factory."""
import json
import os
from ..config import load_environment


class MockClient:
    """No clinical interpretation: return a valid empty extraction for development."""
    runtime = "mock"
    model_name = "mock-empty"
    model_version = "1"

    def generate_json(self, prompt):
        prefix, source = prompt.split("\nSource data:\n", 1)
        data = json.loads(source)
        if 'laboratory_candidates' in data:
            return json.dumps({'patient_id': data['patient_id'], 'measurements': []})
        if 'family_history_passages' in data:
            return json.dumps({'patient_id': data['patient_id'], 'family_history': []})
        if 'examination' in data:
            return json.dumps({'patient_id': data['patient_id'],
                               'examination_id': data['examination']['examination_id'], 'records': []})
        if 'procedure' in data:
            return json.dumps({'patient_id': data['patient_id'],
                               'procedure_id': data['procedure']['procedure_id'], 'findings': [],
                               'additional_findings': {'caa_suspicion_explicit': None, 'caa_evidence': None,
                                   'microbleed_description': None, 'other_findings': []}})
        target = "medications" if "medications" in prefix else "clinical_history"
        return json.dumps({"patient_id": data["patient_id"], target: []})


def get_client():
    load_environment()
    backend = os.getenv("LLM_BACKEND", "granite").strip().lower()
    if backend == "mock":
        return MockClient()
    if backend == "granite":
        from .granite import GraniteClient
        return GraniteClient()
    raise ValueError("LLM_BACKEND must be mock or granite")
