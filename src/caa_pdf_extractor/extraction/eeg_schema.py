"""EEG finding schema. None is undocumented/unknown, never explicit absence.

Exam date metadata (eeg_date/eeg_date_precision) is assembled by the pipeline;
recording-end metadata is omitted from newly serialized exams.
"""
VERSION='1.0'
PRIMARY=('NORMAL','FOCAL_SLOWING','GENERALIZED_SLOWING','EPILEPTIFORM_DISCHARGES','RHYTHMIC_PERIODIC_PATTERN','ELECTROGRAPHIC_SEIZURE','OTHER_ABNORMALITY','UNINTERPRETABLE','UNKNOWN')
BOOL_FIELDS=tuple('focal_slowing_present generalized_slowing_present epileptiform_discharge_present periodic_discharge_present rhythmic_delta_activity_present rhythmic_periodic_pattern_present seizure_recorded electrographic_status_epilepticus encephalopathy_pattern lateralized_abnormality reduced_vigilance_phases alpha_dropout subvigil_beta_activity sleep_architecture_present hyperventilation_performed photic_stimulation_performed ventricular_extrasystoles supraventricular_extrasystoles eeg_clinical_event_captured artifact_limited'.split())
SIDES=('LEFT','RIGHT','BILATERAL','NONE','UNKNOWN')
REGIONS=('FRONTAL','TEMPORAL','PARIETAL','OCCIPITAL','CENTRAL','FRONTOCENTRAL','FRONTOTEMPORAL','TEMPOROPARIETAL','HEMISPHERIC','MULTIFOCAL','GENERALIZED','OTHER','UNKNOWN')
ENUMS={
 'assessability':('ESTABLISHED','UNCERTAIN','NOT_ASSESSABLE','UNKNOWN'),
 'recording_type':('ROUTINE_EEG','SLEEP_EEG','VIDEO_EEG','LONG_TERM_EEG','AMBULATORY_EEG','ICU_EEG','OTHER','UNKNOWN'),
 'clinical_indication_category':('EPILEPSY','SEIZURE','TFNE','ALTERED_CONSCIOUSNESS','ENCEPHALOPATHY','STROKE','ICH','CAA','SYNCOPE','OTHER','UNKNOWN'),
 'frequency_stability':('STABLE','SUFFICIENTLY_STABLE','MODERATELY_STABLE','UNSTABLE','OTHER','UNKNOWN'),
 'modulation':('GOOD','SUFFICIENT','MODERATE','LOW','ABSENT','UNKNOWN'),
 'amplitude':('LOW','LOW_MEDIUM','MEDIUM','MEDIUM_HIGH','HIGH','UNKNOWN'),
 'predominant_location':('POSTERIOR','FRONTAL','FRONTOCENTRAL','CENTRAL','TEMPORAL','DIFFUSE','OTHER','UNKNOWN'),
 'symmetry':('SYMMETRIC','ASYMMETRIC','UNKNOWN'),
 'background_continuity':('CONTINUOUS','NEAR_CONTINUOUS','DISCONTINUOUS','BURST_SUPPRESSION','SUPPRESSED','UNKNOWN'),
 'eye_opening_blocking':('GOOD','MODERATE','LOW','ABSENT','NOT_PERFORMED','UNKNOWN'),
 'reactivity_to_external_stimuli':('PRESENT','ABSENT','NOT_TESTED','UNKNOWN'),
 'background_reactivity':('PRESENT','ABSENT','UNKNOWN'),
 'slow_activity_amount':('ABSENT','LITTLE','MODERATE','ABUNDANT','UNKNOWN'),
 'focal_slowing_side':SIDES,'focal_slowing_region':REGIONS,
 'fast_activity_13_18hz':('PRESENT','ABSENT','UNKNOWN'),'fast_activity_18_25hz':('PRESENT','ABSENT','UNKNOWN'),
 'fast_activity_amount':('ABSENT','LITTLE','MODERATE','ABUNDANT','UNKNOWN'),
 'fast_activity_distribution':('FRONTOCENTRAL','DIFFUSE','FOCAL','OTHER','UNKNOWN'),'fast_activity_side':SIDES,
 'epileptiform_discharge_type':('SPIKE','SHARP_WAVE','SPIKE_WAVE','POLYSPIKE','POLYSPIKE_WAVE','OTHER','UNKNOWN'),
 'epileptiform_distribution':('FOCAL','MULTIFOCAL','GENERALIZED','LATERALIZED','OTHER','UNKNOWN'),
 'epileptiform_side':SIDES+('GENERALIZED',),'epileptiform_region':REGIONS,
 'periodic_discharge_type':('LPD','GPD','BIPD','OTHER','UNKNOWN'),
 'rhythmic_delta_type':('LRDA','GRDA','OTHER','UNKNOWN'),'pattern_side':SIDES,'pattern_region':REGIONS,
 'seizure_type_eeg':('ELECTROGRAPHIC','ELECTROCLINICAL','CLINICAL_ONLY','OTHER','UNKNOWN'),
 'encephalopathy_pattern_type':('GENERALIZED_SLOWING','TRIPHASIC_MORPHOLOGY','BURST_SUPPRESSION','LOW_VOLTAGE','DISCONTINUOUS','OTHER','UNKNOWN'),
 'lateralized_side':SIDES,'lateralized_abnormality_type':('SLOWING','EPILEPTIFORM','ATTENUATION','AMPLITUDE_ASYMMETRY','OTHER','UNKNOWN'),
 'vertex_transients':('PRESENT','ABSENT','UNKNOWN'),'k_complexes':('PRESENT','ABSENT','UNKNOWN'),
 'sleep_spindles':('PRESENT','ABSENT','UNKNOWN'),'high_slow_waves':('PRESENT','ABSENT','UNKNOWN'),
 'hyperventilation_response':('NO_CHANGE','INCREASE_SLOW_ACTIVITY','OTHER','NOT_PERFORMED','UNKNOWN'),
 'photic_driving':('GOOD','MODERATE','LOW','ABSENT','NOT_PERFORMED','UNKNOWN'),
 'ecg_rhythm':('SINUS','ARRHYTHMIA','OTHER','UNKNOWN'),'heart_rate_category':('NORMAL','TACHYCARDIC','BRADYCARDIC','UNKNOWN'),
 'source_reported_abnormality_grade':('NORMAL','BROADLY_NORMAL','MINIMALLY_ABNORMAL','MILDLY_ABNORMAL','MODERATELY_ABNORMAL','ABNORMAL','SEVERELY_ABNORMAL','UNKNOWN'),
 'eeg_tfne_timing':('DURING_EPISODE','POST_EPISODE','INTERICTAL','UNKNOWN'),
}
TEXT_FIELDS=tuple('clinical_indication_text background_description_text reactivity_description_text slow_activity_description_text fast_activity_description_text epileptiform_description_text status_epilepticus_description_text encephalopathy_description_text sleep_description_text activation_description_text other_ecg_findings event_description clinical_eeg_correlation original_interpretation_text'.split())
FREQUENCIES=('background_frequency_hz','basic_rhythm_hz','diffuse_theta_frequency_hz','pattern_frequency_hz')
GROUPS={
 'Indication':['clinical_indication_text','clinical_indication_category'],
 'Background':['background_frequency_hz','basic_rhythm_hz','frequency_stability','modulation','amplitude','predominant_location','symmetry','background_continuity','background_description_text'],
 'Slowing':['focal_slowing_present','generalized_slowing_present','slow_activity_amount','focal_slowing_side','focal_slowing_region','diffuse_theta_frequency_hz','slow_activity_description_text'],
 'Fast activity':['fast_activity_13_18hz','fast_activity_18_25hz','fast_activity_amount','fast_activity_distribution','fast_activity_side','fast_activity_description_text'],
 'Epileptiform findings':['epileptiform_discharge_present','epileptiform_discharge_type','epileptiform_distribution','epileptiform_side','epileptiform_region','epileptiform_description_text'],
 'Periodic / rhythmic patterns':['periodic_discharge_present','periodic_discharge_type','rhythmic_delta_activity_present','rhythmic_delta_type','pattern_side','pattern_region','pattern_frequency_hz'],
 'Seizure / status':['seizure_recorded','seizure_type_eeg','electrographic_status_epilepticus','status_epilepticus_description_text'],
 'Reactivity':['eye_opening_blocking','reactivity_to_external_stimuli','background_reactivity','reactivity_description_text'],
 'Encephalopathy / lateralized abnormality':['encephalopathy_pattern','encephalopathy_pattern_type','lateralized_abnormality','lateralized_side','lateralized_abnormality_type','encephalopathy_description_text'],
 'Sleep / vigilance':['reduced_vigilance_phases','alpha_dropout','subvigil_beta_activity','vertex_transients','k_complexes','sleep_spindles','high_slow_waves','sleep_architecture_present','sleep_description_text'],
 'Activation':['hyperventilation_performed','hyperventilation_response','photic_stimulation_performed','photic_driving','activation_description_text'],
 'ECG':['ecg_rhythm','heart_rate_category','ventricular_extrasystoles','supraventricular_extrasystoles','other_ecg_findings'],
 'TFNE context':['eeg_tfne_timing','eeg_clinical_event_captured','event_description','clinical_eeg_correlation'],
}


def defaults():return {**dict.fromkeys(BOOL_FIELDS,None),**dict.fromkeys(FREQUENCIES,None),**dict.fromkeys(TEXT_FIELDS,None),**{k:'UNKNOWN' for k in ENUMS}}


def validate_value(field,value):
    if field in BOOL_FIELDS:
        if value is not None and type(value) is not bool:raise ValueError(f'{field} requires true/false/null')
    elif field in ENUMS:
        if value not in ENUMS[field]:raise ValueError(f'Invalid {field} value')
    elif field in FREQUENCIES:
        if value is not None:
            values=list(value.values()) if isinstance(value,dict) and set(value)=={'min','max'} else [value]
            if any(type(v) not in (int,float) or not 0<float(v)<=200 for v in values):raise ValueError('Invalid explicit EEG frequency')
            if len(values)==2 and value['min']>value['max']:raise ValueError('Reversed frequency range')
    elif field in TEXT_FIELDS:
        if value is not None and not isinstance(value,str):raise ValueError(f'{field} requires source text')
    else:raise ValueError(f'Unknown EEG field: {field}')
    return value
