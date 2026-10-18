"""Additive CT, neck-vessel and EEG research enums."""
CT_FINDING_TYPES=('EDEMA','MASS_EFFECT','HYDROCEPHALUS','CALCIFICATION')
CT_NEGATIVE_TYPES=('NO_EDEMA','NO_MASS_EFFECT','NO_HYDROCEPHALUS','NO_CALCIFICATION','NO_TERRITORIAL_INFARCT')
NECK_SUMMARIES=('NO_STENOSIS','STENOSIS_LT_50','STENOSIS_GE_50','OCCLUSION','INDETERMINATE')
EEG_STATUSES=('not_performed','performed','unknown')
EEG_PRIMARY=('normal','focal_slowing','generalized_slowing','epileptiform_discharges','rhythmic_periodic_pattern','electrographic_seizure','other_abnormality','uninterpretable')
EEG_LATERALITY=('left','right','bilateral','generalized','not_localizable','unknown')
EEG_TIMING=('during_episode','post_episode','interictal','unknown')
