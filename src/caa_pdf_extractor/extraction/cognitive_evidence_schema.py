"""Source evidence constraints, separate from the validated extraction policy."""
EVIDENCE_ROLES = ('CURRENT_PERFORMED', 'HISTORICAL_REFERENCE', 'EXTERNAL_REPORT',
                  'COPIED_FORWARD', 'UNKNOWN_MENTION')
SOURCE_SHAPE = """page > 0 AND source_start >= 0 AND source_end > source_start
 AND length(evidence_text)=source_end-source_start
 AND score_start >= source_start AND score_end > score_start AND score_end <= source_end"""
ROLE_SHAPE = """((cognitive_assessment_id IS NULL AND evidence_role='UNKNOWN_MENTION') OR
 (cognitive_assessment_id IS NOT NULL AND evidence_role<>'UNKNOWN_MENTION')) IS TRUE"""
