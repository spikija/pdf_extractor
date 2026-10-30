import pytest

from caa_pdf_extractor import cognitive_backfill as b


def event():
    return dict(case_id=1,source_document_id=2,test_type='MMSE',score=17,max_score=30,
                assessment_date=None,assessment_year=None,assessment_month=None,date_precision='UNKNOWN',
                date_basis='UNKNOWN',context='CURRENT',source_text='MMSE 17/30',page=1,section_id='s',
                source_start=0,source_end=10,reason='not a database column')


def evidence():
    return dict(case_id=1,source_document_id=2,page=1,section_id='s',source_start=0,source_end=10,
                score_start=5,score_end=10,evidence_text='MMSE 17/30',evidence_role='UNKNOWN_MENTION',
                test_type='MMSE',score=17,max_score=30,date_text=None,cognitive_assessment_key=None)


def test_frozen_input_rejects_modified_or_different_artifact():
    with pytest.raises(ValueError,match='UNAUTHORIZED_OR_MODIFIED_ARTIFACT'):
        b.FrozenMMSE(b'{}')


def test_frozen_bytes_cannot_be_mutable():
    with pytest.raises(ValueError,match='ARTIFACT_BYTES_MUST_BE_IMMUTABLE'):
        b.FrozenMMSE(bytearray(b'{}'))


def test_scalar_projection_preserves_unknown_dates_and_exact_source():
    row=event()
    projected=b.event_projection(row)
    assert projected=={**{k:row[k] for k in b.EVENT_FIELDS},'deleted':False}
    assert projected['assessment_date'] is projected['assessment_year'] is projected['assessment_month'] is None


@pytest.mark.parametrize('precision,year,month', [('YEAR',2018,None),('MONTH',2015,1)])
def test_partial_date_projection_never_pads(precision,year,month):
    row=event()
    row.update(date_precision=precision,assessment_year=year,assessment_month=month)
    result=b.event_projection(row)
    assert result['assessment_date'] is None
    assert (result['date_precision'],result['assessment_year'],result['assessment_month'])==(precision,year,month)


def test_unresolved_projection_never_uses_available_equal_score_parent():
    result=b.evidence_projection(evidence(),{'same_score':99})
    assert result['cognitive_assessment_id'] is None
    assert result['evidence_role']=='UNKNOWN_MENTION'


def test_supported_link_uses_only_frozen_key():
    row=evidence()
    row.update(cognitive_assessment_key='frozen',evidence_role='COPIED_FORWARD')
    result=b.evidence_projection(row,{'frozen':88,'same_score':99})
    assert result['cognitive_assessment_id']==88


def test_missing_frozen_parent_cannot_fall_back_to_equal_score():
    row=evidence()
    row.update(cognitive_assessment_key='frozen',evidence_role='COPIED_FORWARD')
    with pytest.raises(ValueError,match='MISSING_OR_UNSUPPORTED_PARENT'):
        b.evidence_projection(row,{'same_score':99})


@pytest.mark.parametrize('key,role',[(None,'CURRENT_PERFORMED'),('frozen','UNKNOWN_MENTION')])
def test_unresolved_role_link_conflicts_rejected(key,role):
    row=evidence()
    row.update(cognitive_assessment_key=key,evidence_role=role)
    with pytest.raises(ValueError,match='UNRESOLVED_ROLE_MISMATCH|MISSING_OR_UNSUPPORTED_PARENT'):
        b.evidence_projection(row,{'frozen':88})


def test_existing_exact_match_requires_no_insert():
    expected=b.event_projection(event())
    assert b.classify(expected,{**expected,'id':9})=='EXACT_MATCH_EXISTING'
    assert b.classify(expected,None)=='INSERT_MISSING'


@pytest.mark.parametrize('field,value',[('score',18),('deleted',True),('assessment_date','2018-01-01'),('source_text','changed'),('case_id',2)])
def test_conflicting_stored_value_is_never_overwritten(field,value):
    expected=b.event_projection(event())
    with pytest.raises(ValueError,match='CONFLICT_EXISTING_NO_OVERWRITE'):
        b.classify(expected,{**expected,'id':9,field:value})


def test_rollback_guards_entire_rows_and_xmin_and_deletes_evidence_first():
    parent={**b.event_projection(event()),'id':9}
    child={**b.evidence_projection(evidence(),{}),'id':12}
    receipt={'inserted_ids':{b.EVENT_TABLE:[9],b.EVIDENCE_TABLE:[12]},
             'after':{b.EVENT_TABLE:[{'row':parent,'xmin':'123'}],b.EVIDENCE_TABLE:[{'row':child,'xmin':'123'}]}}
    sql=b.rollback_sql(receipt)
    assert "xmin='123'::xid" in sql and 'to_jsonb(t)=' in sql
    assert sql.index('DELETE FROM public."cognitive_assessment_evidence"')<sql.index('DELETE FROM public."cognitive_assessment"')
    assert sql.startswith('-- Exact guarded rollback') and sql.endswith('COMMIT;\n')
    assert 'UPDATE ' not in sql and 'setval' not in sql
