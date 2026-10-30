"""Additive storage shapes; no extraction policy or clinical writer."""
DATE_SHAPE="""((date_precision='DAY' AND event_date IS NOT NULL
 AND event_year=extract(year from event_date) AND event_month=extract(month from event_date)
 AND date_basis IN ('EXPLICIT_DIAGNOSIS_DATE','CURRENT_DIAGNOSTIC_ENCOUNTER')) OR
 (date_precision='MONTH' AND event_date IS NULL AND event_year BETWEEN 1000 AND 9999
 AND event_month BETWEEN 1 AND 12 AND date_basis='EXPLICIT_MONTH_YEAR') OR
 (date_precision='YEAR' AND event_date IS NULL AND event_year BETWEEN 1000 AND 9999
 AND event_month IS NULL AND date_basis='EXPLICIT_YEAR') OR
 (date_precision='UNKNOWN' AND event_date IS NULL AND event_year IS NULL
 AND event_month IS NULL AND date_basis='UNKNOWN')) IS TRUE"""
DURATION_SHAPE="sensory_duration_minutes IS NULL OR (sensory_duration_minutes>=0 AND sensory_duration_minutes::text NOT IN ('NaN','Infinity','-Infinity'))"
STATUS_SHAPE="""((event_type='SUSPICION' AND caa_status IN ('SUSPECTED','UNKNOWN')) OR
 (event_type='CLINICAL_DIAGNOSIS' AND caa_status IN ('DIAGNOSED_UNSPECIFIED','POSSIBLE','PROBABLE','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE','UNKNOWN')) OR
 (event_type='CRITERIA_CLASSIFICATION' AND caa_status IN ('POSSIBLE','PROBABLE','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE','UNKNOWN')) OR
 (event_type='PATHOLOGY_CONFIRMATION' AND caa_status IN ('PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE'))) IS TRUE"""
