"""Physician-specified source regression expectations; independent of extraction rules."""
import ast
import csv
from pathlib import Path
from .workbench_store import read

CASES=[
 dict(case_id='A',patient_id='2345733313',date='2023-03-02',expected=dict(generalized_slowing_present=True,focal_slowing_present=True,dominant_focus_side='LEFT',dominant_focus_regions=['FRONTAL','CENTRAL','TEMPORAL'],reactivity_to_external_stimuli='ABSENT',reactivity_stimulus='VERBAL',background_reactivity='UNKNOWN',assessability='LIMITED',artifact_type=['MOVEMENT','MUSCLE'],epileptiform_confirmed=False,epileptiform_discharge_present=None,epileptiform_evidence_status='NO_DEFINITE_EVIDENCE',ictal_activity_status='NO_DEFINITE_EVIDENCE',seizure_recorded=None)),
 dict(case_id='B',patient_id='2345733313',date='2023-02-17',expected=dict(rhythmic_delta_type='FIRDA',rhythmic_delta_activity_present=True,focal_slowing_present=None,pattern_asymmetry='LEFT_PREDOMINANT'),patterns=[dict(distribution='GENERALIZED',maximum='FRONTAL',asymmetry='LEFT_PREDOMINANT',rhythmic_delta_type='FIRDA')],no_foci=True),
 dict(case_id='C',patient_id='2520723716',date='2016-11-11',expected=dict(rhythmic_delta_type='FIRDA',rhythmic_delta_activity_present=True,focal_slowing_present=True,dominant_focus_side='LEFT',dominant_focus_regions=['TEMPORAL'],anterior_posterior_extent='MID_TO_ANTERIOR')),
 dict(case_id='D',patient_id='2520723716',date='2017-02-03',expected=dict(focal_slowing_present=True,focal_slowing_side='RIGHT',dominant_focus_side='RIGHT',dominant_focus_regions=['FRONTAL','TEMPORAL'],spread_present=True,spread_side='LEFT',spread_regions=['FRONTAL','TEMPORAL'],background_continuity='UNKNOWN'),foci=[dict(relationship='PRIMARY',side='RIGHT',regions=['FRONTAL','TEMPORAL']),dict(relationship='SPREAD',side='LEFT',regions=['FRONTAL','TEMPORAL'])],no_secondary=True),
 dict(case_id='E',patient_id='2529565017',date='2017-02-02',expected=dict(dominant_focus_side='RIGHT',dominant_focus_regions=['FRONTAL','CENTRAL'],focal_slowing_continuity='NEAR_CONTINUOUS',background_continuity='UNKNOWN',epileptiform_discharge_present=True,epileptiform_discharge_type='SHARP_WAVE',epileptiform_distribution='MULTIFOCAL',clinical_signs_of_NCSE='ABSENT',electrographic_status_epilepticus=None,reactivity_to_external_stimuli='PRESENT',reactivity_stimulus='TACTILE',background_reactivity='UNKNOWN'),foci=[dict(relationship='PRIMARY',side='RIGHT',regions=['FRONTAL','CENTRAL'],epileptiform_component=True),dict(relationship='SECONDARY_INDEPENDENT',side='LEFT',frequency='OCCASIONAL'),dict(relationship='SPREAD',side='BILATERAL',regions=['FRONTAL','CENTRAL'])]),
 dict(case_id='F',patient_id='2648066819',date='2015-08-05',expected=dict(generalized_slowing_present=True,eeg_primary_finding='GENERALIZED_SLOWING',focal_slowing_present=None,generalized_slowing_maximum='BIFRONTAL',source_reported_abnormality_grade='MILDLY_ABNORMAL'),patterns=[dict(kind='GENERALIZED_SLOWING',frequency_hz=dict(min=4.0,max=5.0),maximum='BIFRONTAL')]),
 dict(case_id='G',patient_id='2661437519',date='2019-07-26',expected=dict(focal_slowing_present=True,focal_slowing_side='BILATERAL',dominant_focus_regions=['TEMPORAL'],focal_slowing_asymmetry='LEFT_PREDOMINANT',generalized_slowing_present=True),foci=[dict(side='BILATERAL',regions=['TEMPORAL'],asymmetry='LEFT_PREDOMINANT')],patterns=[dict(distribution='BILATERAL',kind='RHYTHMIC_THETA_DELTA'),dict(distribution='GENERALIZED',maximum='FRONTAL')]),
 dict(case_id='H',patient_id='2674159019',date='2019-12-04',expected=dict(dominant_focus_side='LEFT',dominant_focus_regions=['FRONTAL','CENTRAL','TEMPORAL'],anterior_posterior_extent='ANTERIOR',epileptiform_discharge_type='SHARP_WAVE',morphology_modifier='BROAD',epileptiform_frequency='OCCASIONAL',epileptiform_distribution='FOCAL',generalized_slowing_present=True,assessability='LIMITED',background_slowing_continuity='CONTINUOUS',background_continuity='UNKNOWN')),
 dict(case_id='I',patient_id='2520723716',date='2012-01-16',expected=dict(secondary_bilateral_synchrony=True,epileptiform_discharge_present=False,ecg_rhythm='UNKNOWN',ventricular_extrasystoles=None,supraventricular_extrasystoles=None,heart_rate_category='UNKNOWN'),report_excludes=['24-STUNDEN-EKG','200 ventrikuläre','Vorhofflimmern'],foci=[dict(relationship='PRIMARY',side='LEFT',secondary_bilateral_synchrony=True)]),
]


def match(row,expected):return all(row.get(key)==value for key,value in expected.items())


def evaluate(results,baseline_directory=None,physician_inventory=None):
    checks=[];source_cases=set()
    def add(case,patient,date,label,passed,expected=None,actual=None):
        checks.append(dict(case_id=case,patient_id=patient,eeg_date=date,check=label,passed=bool(passed),expected=expected,actual=actual))
        source_cases.add((patient,date))
    for case in CASES:
        pid=case['patient_id'];date=case['date'];exam=next(e for e in results[pid]['eeg_exams'] if e['eeg_date']==date)
        for field,value in case['expected'].items():add(case['case_id'],pid,date,field,exam[field]==value,value,exam[field])
        for collection,key in [('foci','focal_abnormalities'),('patterns','slow_patterns')]:
            for expected in case.get(collection,[]):add(case['case_id'],pid,date,key+':'+str(expected),any(match(row,expected) for row in exam[key]),expected,exam[key])
        if case.get('no_foci'):add(case['case_id'],pid,date,'No focal abnormalities',not exam['focal_abnormalities'])
        if case.get('no_secondary'):add(case['case_id'],pid,date,'No independent secondary focus',not any(f['relationship']=='SECONDARY_INDEPENDENT' for f in exam['focal_abnormalities']))
        for text in case.get('report_excludes',[]):add(case['case_id'],pid,date,'Source boundary excludes '+text,text not in exam['eeg_report_text'])
    if baseline_directory:
        with (Path(baseline_directory)/'independent_field_validation.csv').open(encoding='utf-8-sig') as stream:
            for row in csv.DictReader(stream):
                pid=row['patient_id'];date=row['eeg_date'];exam=next(e for e in results[pid]['eeg_exams'] if e['eeg_date']==date)
                try:expected=ast.literal_eval(row['expected'])
                except (ValueError,SyntaxError):expected=row['expected']
                field=row['field']
                # 1.1 explicitly replaces the old UNKNOWN quality state for artifact limitation.
                if field=='assessability' and pid=='2945890325' and date=='2025-03-20':expected='LIMITED'
                add('PREVIOUS_SOURCE_QA',pid,date,field,exam[field]==expected,expected,exam[field])
    if physician_inventory and baseline_directory:
        for review in physician_inventory:
            pid=review['patient_id'];eid=review['entity_id']
            source=read(Path(baseline_directory)/'published_candidates.json')[pid]
            old=next(read(f['path']) for f in source['artifacts'] if Path(f['path']).name=='eeg_exams.json')
            prior=next((e for e in old if e['eeg_exam_id']==eid),None)
            if not prior:continue
            exam=next(e for e in results[pid]['eeg_exams'] if e['eeg_exam_id']==eid)
            for field in ('focal_slowing_present','generalized_slowing_present','epileptiform_discharge_present','seizure_recorded','electrographic_status_epilepticus'):
                add('SAVED_PHYSICIAN_REVIEW',pid,exam['eeg_date'],field,exam[field]==prior[field],prior[field],exam[field])
    return dict(checks=checks,source_cases=len(source_cases),source_cases_passed=sum(all(c['passed'] for c in checks if (c['patient_id'],c['eeg_date'])==case) for case in source_cases),source_cases_failed=sum(any(not c['passed'] for c in checks if (c['patient_id'],c['eeg_date'])==case) for case in source_cases))
