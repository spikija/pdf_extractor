"""Storage constraints for encounter phenotypes and separately owned mRS."""
REASONS = ('FOCAL_NEUROLOGICAL_DEFICIT', 'EPILEPTIC_EVENT', 'COGNITIVE_DECLINE', 'OTHER', 'UNKNOWN')
MRS_SOURCE_TYPES = ('EXPLICIT', 'ESTIMATED')
MRS_CONFIDENCES = ('HIGH', 'MODERATE', 'LOW')


def mrs_shape(prefix):
    return f"""(({prefix} IS NULL AND {prefix}_min IS NULL AND {prefix}_max IS NULL) OR
      ({prefix} BETWEEN 0 AND 5 AND {prefix}_min IS NULL AND {prefix}_max IS NULL) OR
      ({prefix} IS NULL AND {prefix}_min BETWEEN 0 AND 5 AND {prefix}_max BETWEEN 0 AND 5
       AND {prefix}_min < {prefix}_max)) IS TRUE"""
