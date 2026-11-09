"""Recording-end removal: synthetic artifacts, legacy overlays and the real EEG view."""
import copy
import importlib
import json

import pytest

from caa_pdf_extractor.workbench_eeg import reviewed_exam, validate_edits
from review_app.artifact_repository import layer, sha


def synthetic_result(version, header='EEG Befund (26.07.2019)'):
    module='eeg_pipeline_v11' if version=='1.1' else 'eeg_pipeline'
    bundle=dict(patient_id='123', documents=[dict(
        source_document_id=10, filename='synthetic.pdf', sections=[dict(
            section_id='p1', page_start=1, page_end=1,
            text=header+'\nAbnormes EEG. Fokale Verlangsamung links temporal. Keine epilepsietypischen Potentiale.')])])
    return importlib.import_module('caa_pdf_extractor.extraction.'+module).assemble(bundle)


@pytest.mark.parametrize('version', ['1.0', '1.1'])
@pytest.mark.parametrize('header,expected,precision', [
    ('EEG Befund (26.07.2019)', '2019-07-26', 'DAY'),
    ('VIDEO-EEG (29.09. - 04.10.2024)', '2024-09-29', 'DAY'),
    ('VIDEO-EEG (17. - 21.05.2021)', '2021-05-17', 'DAY'),
    ('EEG 07/2019:', '2019-07', 'MONTH'),
    ('EEG 2001:', '2001', 'YEAR'),
])
def test_new_serialized_artifacts_omit_end_and_preserve_dates(version, header, expected, precision):
    result=json.loads(json.dumps(synthetic_result(version, header)))
    exam,=result['eeg_exams']
    assert exam['eeg_date']==expected and exam['eeg_date_precision']==precision
    assert exam['focal_slowing_present'] is True and exam['focal_slowing_side']=='LEFT'
    assert exam['epileptiform_discharge_present'] is False
    assert exam['evidence'] and result['eeg_findings'] and result['eeg_provenance']
    def check(value):
        if isinstance(value, dict):
            assert not set(value)&{'eeg_date_end', 'recording_end', 'recording_end_time', 'eeg_recording_end', 'end_time'}
            for child in value.values():check(child)
        elif isinstance(value, list):
            for child in value:check(child)
    check(result)


def write_entry(tmp_path, version, result):
    files=[]
    for name in ('eeg_exams', 'eeg_findings'):
        path=tmp_path/(name+'.json')
        path.write_text(json.dumps(result[name]), encoding='utf-8')
        files.append(dict(path=str(path), sha256=sha(path)))
    return dict(version=version+'+synthetic', module_version=version, status='CANDIDATE', artifacts=files)


@pytest.mark.parametrize('version', ['1.0', '1.1'])
@pytest.mark.parametrize('legacy_value', [None, 'UNKNOWN', '2024-10-04'])
def test_legacy_json_loads_and_review_projection_ignores_end(tmp_path, version, legacy_value):
    result=synthetic_result(version)
    result['eeg_exams'][0]['eeg_date_end']=legacy_value
    entry=write_entry(tmp_path, version, result)
    raw,=layer(entry, 'eeg_exams')
    original=copy.deepcopy(raw)
    projected=reviewed_exam(raw, layer(entry, 'eeg_findings'), {}, 'hash')
    assert projected=={k:v for k,v in original.items() if k!='eeg_date_end'}
    assert raw==original
    assert all(sha(f['path'])==f['sha256'] for f in entry['artifacts'])


@pytest.mark.parametrize('version', ['1.0', '1.1'])
def test_legacy_saved_end_edit_is_ignored_and_other_edits_survive(version):
    result=synthetic_result(version)
    exam=result['eeg_exams'][0]
    exam['eeg_date_end']='2019-07-27'
    original=copy.deepcopy(exam)
    record=dict(entity_id=exam['eeg_exam_id'], action='EDIT', source_artifact_hash='hash',
        updated_at='2026-11-01T10:00:00Z', physician_value=dict(eeg_date_end='2019-07-28', eeg_date='2019-07-25'))
    projected=reviewed_exam(exam, result['eeg_findings'], {record['entity_id']:record}, 'hash')
    assert projected=={**{k:v for k,v in original.items() if k!='eeg_date_end'}, 'eeg_date':'2019-07-25'}
    assert exam==original
    with pytest.raises(ValueError, match='Unknown EEG field'):
        validate_edits(exam, {'eeg_date_end':'2019-07-29'})


@pytest.mark.parametrize('version', ['1.0', '1.1'])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('header,suffix', [
    ('EEG Befund (26.07.2019)', ''),
    ('Routine-EEG (26.07.2019)', ' · ROUTINE_EEG'),
])
def test_review_app_header_preserves_date_without_recording_end(tmp_path, monkeypatch, version, legacy, header, suffix):
    from streamlit.testing.v1 import AppTest
    from caa_pdf_extractor.workbench_eeg import EEGWorkbenchStore
    from review_app import eeg_holdout
    from review_app.review_repository import ReviewRepository

    result=synthetic_result(version, header)
    if legacy:result['eeg_exams'][0]['eeg_date_end']=None
    entry=write_entry(tmp_path, version, result)
    monkeypatch.setattr(EEGWorkbenchStore, 'versions', lambda *a:{})
    monkeypatch.setattr(ReviewRepository, 'load', lambda *a:{})
    monkeypatch.setattr(eeg_holdout, 'guidance', lambda *a:None)
    app=AppTest.from_string(
        'from review_app.views.eeg import render\n'
        'from review_app.review_repository import ReviewRepository\n'
        f'render({entry!r}, ReviewRepository({str(tmp_path / "reviews")!r}), "123", [])\n'
    ).run()
    assert not app.exception, [e.message for e in app.exception]
    assert any(m.value=='EEG 2019-07-26'+suffix for m in app.markdown)
    assert any(c.value=='Date precision: DAY' for c in app.caption)
    rendered='\n'.join(str(e.value) for kind in ('markdown', 'caption', 'text', 'json') for e in getattr(app, kind))
    assert 'Recording end' not in rendered and 'eeg_date_end' not in rendered
    assert all(sha(f['path'])==f['sha256'] for f in entry['artifacts'])
