"""Read-only smoke test over all published same-cohort v0.2 patient views."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from caa_pdf_extractor.workbench_store import ROOT,sha
from review_app.tfne_repository import load_artifact

IDS=json.loads((ROOT/'data/debug/tfne_pilot_v0_1_20261101T130202_586729Z/cohort_freeze.json').read_text(encoding='utf-8'))['patient_ids']


@pytest.mark.parametrize('patient',IDS)
def test_same_patient_both_versions_load_and_v02_evidence_table_renders(patient,tmp_path):
    path=ROOT/'data/patient_artifacts'/patient/'tfne_neurological_deficit_v0_2.json'
    if not path.exists():pytest.skip('v0.2 not yet published')
    before=sha(path)
    old,_=load_artifact(patient);new,_=load_artifact(patient,version='0.2')
    assert old['patient_id']==new['patient_id']==patient
    app=AppTest.from_string('from review_app.views.tfne import render\n'+f'render({patient!r},[],review_root={str(tmp_path)!r})',default_timeout=30).run()
    next(s for s in app.selectbox if s.label=='TFNE extraction version').select('0.2').run()
    assert not app.exception,[e.message for e in app.exception]
    if new['episodes']:
        assert any({'Value','Evidence span','Validation status'}<=set(d.value.columns) for d in app.dataframe)
        assert any('Changed from v0.1' in w.value for w in app.warning)
    else:assert any('No grounded episodes' in info.value for info in app.info)
    assert sha(path)==before and not list(tmp_path.rglob('events.json'))
