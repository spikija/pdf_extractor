"""Additive longitudinal cognitive source-mention schema."""
TEST_TYPES = ('MMSE', 'MOCA', 'OTHER')
DATE_PRECISIONS = ('DAY', 'MONTH', 'YEAR', 'UNKNOWN')
DATE_BASES = ('EXPLICIT_TEST_DATE', 'EXPLICIT_MONTH_YEAR', 'EXPLICIT_YEAR', 'CURRENT_SECTION_DATE', 'UNKNOWN')
CONTEXTS = ('CURRENT', 'HISTORICAL', 'EXTERNAL_REPORT', 'UNKNOWN')

DATE_SHAPE = """(
 (date_precision='DAY' AND assessment_date IS NOT NULL
  AND assessment_year=extract(year from assessment_date) AND assessment_month=extract(month from assessment_date)
  AND date_basis IN ('EXPLICIT_TEST_DATE','CURRENT_SECTION_DATE')) OR
 (date_precision='MONTH' AND assessment_date IS NULL AND assessment_year BETWEEN 1000 AND 9999
  AND assessment_month BETWEEN 1 AND 12 AND date_basis='EXPLICIT_MONTH_YEAR') OR
 (date_precision='YEAR' AND assessment_date IS NULL AND assessment_year BETWEEN 1000 AND 9999
  AND assessment_month IS NULL AND date_basis='EXPLICIT_YEAR') OR
 (date_precision='UNKNOWN' AND assessment_date IS NULL AND assessment_year IS NULL
  AND assessment_month IS NULL AND date_basis='UNKNOWN')
) IS TRUE"""

SCORE_SHAPE = """(score >= 0 AND max_score > 0 AND score <= max_score
 AND (test_type NOT IN ('MMSE','MOCA') OR (max_score=30 AND score BETWEEN 0 AND 30))) IS TRUE"""
