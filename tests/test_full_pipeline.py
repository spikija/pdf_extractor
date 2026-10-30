import ast
import inspect
from pathlib import Path
from caa_pdf_extractor import full_pipeline
from caa_pdf_extractor.extraction import functional_status
from caa_pdf_extractor.extraction.research_scores_v23 import cha2ds2_va, component


def test_master_cannot_claim_readiness_for_unconnected_adapters(monkeypatch,tmp_path):
    calls=[]
    def verify(out):
        calls.append(out)
        return dict(status='PIPELINE_CONSOLIDATION_INCOMPLETE',regression_exit_code=0,regression_counts={'PASSED':1})
    monkeypatch.setattr(full_pipeline,'verify',verify)
    assert full_pipeline.main(['--run','--output',str(tmp_path/'report')])==2
    assert calls==[tmp_path/'report']
    assert 'semantic_patient_extraction' in full_pipeline.BLOCKERS


def test_shared_function_logic_has_no_debug_or_patient_dependencies():
    text=Path(functional_status.__file__).read_text(encoding='utf-8')
    tree=ast.parse(text)
    for n in ast.walk(tree):
        if isinstance(n,ast.ImportFrom):
            assert 'debug' not in (n.module or '') and 'single_document' not in (n.module or '')
        if isinstance(n,ast.Constant) and isinstance(n.value,str):
            assert '2435608415' not in n.value


def test_isolated_wrappers_reuse_shared_validator():
    from caa_pdf_extractor.single_document_cfs_anchor_debug import check_estimate
    from caa_pdf_extractor.single_document_function_debug import validate_candidates
    assert check_estimate is functional_status.check_estimate
    assert validate_candidates is functional_status.validate_candidates


def test_shared_score_exposes_minimum_in_actual_calculator():
    r=cha2ds2_va(confirmed_af=True,index_date='2020-01-01',dob='1940-01-01',facts={
        'H':component(True,'2019-01-01',['H']), 'S2':component(True,'2018-01-01',['S'])})
    assert r['score'] is None and r['cha2ds2_va_exact'] is None
    assert r['cha2ds2_va_minimum_confirmed']==5 and r['cha2ds2_va_maximum_possible']==8
    assert r['component_states']['C']['state']=='UNKNOWN'
    assert r['esc_oac_recommendation']=='OAC_RECOMMENDED_FROM_MINIMUM_CONFIRMED_SCORE'


def test_master_preflight_runs_full_suite_and_records_hashes():
    source=inspect.getsource(full_pipeline.verify)
    assert "'pytest','-q'" in source
    m=full_pipeline.manifest(full_pipeline.inventory())
    assert m['production_ready'] is False and m['critical_final_answer']=='NO'
    assert m['medication_dictionary_hash']
    assert m['python_module_hashes'] and m['prompt_hashes']
