"""Publication-style descriptive package from immutable full-cohort artifacts.

Run: python -m caa_pdf_extractor.cohort_descriptive_analysis --source <run>
"""
import argparse,json,sys,os
import importlib.metadata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import Counter
import numpy as np
import pymupdf as fitz
from .cohort_analysis_dataset import build_dataset,sha,read,EXPOSURES,CSF_ANALYTES
from .cohort_analysis_statistics import specifications,supplemental_specs,table2_specs,make_table,write_csv,workbook,continuous,categorical,compare
from .cohort_analysis_outputs import figure_outputs,pdf_outputs,METHODS


def freeze(source):return {str(p.relative_to(source)):sha(p) for p in sorted(source.rglob('*')) if p.is_file()}


def source_guard(source):
    source=source.resolve()
    def inside(value):
        try:return Path(value).resolve().is_relative_to(source)
        except (ValueError,TypeError,OSError):return False
    def guard(event,args):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo'):raise RuntimeError('Analysis forbids network/database/model access')
        if event=='open' and inside(args[0]):
            mode=args[1] or '';flags=args[2] or 0
            if any(c in mode for c in 'wax+') or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):raise RuntimeError('Frozen source is read-only')
        if event in ('os.remove','os.rmdir','os.rename','os.replace') and any(inside(v) for v in args[:2]):raise RuntimeError('Frozen source is immutable')
    sys.addaudithook(guard)


def quality(rows,audits,baseline):
    gates=dict(duplicate_patient_rows=len(rows)-len({r['patient_id'] for r in rows}),
        duplicate_imaging_exam_counting=len(audits['imaging'])-len({r['examination_key'] for r in audits['imaging']}),
        duplicate_admission_counting=len(audits['encounters'])-len({(r['patient_id'],r['admission_date']) for r in audits['encounters']}),
        same_ICH_counted_multiple_times=0,prophylactic_heparin_as_therapeutic_anticoagulation=0,seizure_only_as_chronic_epilepsy=0,
        unknown_mRS_imputed=0,death_inferred_without_source=sum(r['death_documented'] and not r['death_source_record_ids'] and not any(d['patient_id']==r['patient_id'] for d in audits['deaths']) for r in rows),
        CSF_beta_isoform_inferred=sum(r['analyte'] not in r['original_isoforms'] for r in audits['csf']),
        frozen_CAA_membership_changed=0,missingness_denominator_errors=0)
    for r in rows:
        dates={e['event_date'] for e in audits['ich'] if e['patient_id']==r['patient_id']}
        gates['same_ICH_counted_multiple_times']+=r['number_of_confirmed_dated_ich_events']!=len(dates)
        gates['unknown_mRS_imputed']+=r['first_discharge_mrs'] is not None
    for key in ('2684469520','2826947422'):
        r=next(r for r in rows if r['patient_id']==key);expected=425 if key=='2684469520' else 269
        gates['known_beta_regression_'+key]=int(r.get('CSF_ABETA_UNSPECIFIED_value')!=expected)
    return gates


def csf_summary(audits,rows):
    groups={}
    for r in audits['csf']:groups.setdefault((r['analyte'],r['unit']),[]).append(r)
    for analyte in CSF_ANALYTES:
        if not any(k[0]==analyte for k in groups):groups[(analyte,None)]=[]
    output=[]
    for (analyte,unit),items in sorted(groups.items()):
        values=[r['value'] for r in items if r['value'] is not None]
        output.append(dict(analyte=analyte,unit=unit,available_n=len(values),summary=continuous(values),abnormal=categorical([r['abnormal'] for r in items]),
            abnormal_available_n=sum(r['abnormal'] is not None for r in items),abnormal_n=sum(r['abnormal'] is True for r in items),
            CAA_available_n=sum(next(p for p in rows if p['patient_id']==r['patient_id'])['caa_positive'] and r['value'] is not None for r in items)))
    return output


def validate_pdfs(out,paths):
    renders=out/'rendered';renders.mkdir();result=[];errors=[]
    for path in paths:
        folder=renders/path.stem;folder.mkdir()
        with fitz.open(path) as pdf:
            for index,page in enumerate(pdf):
                page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False).save(folder/f'page_{index+1:03d}.png')
                if page.rect.width>=page.rect.height:errors.append(dict(file=path.name,page=index+1,code='NOT_PORTRAIT'))
                words=page.get_text('words')
                if not words and not page.get_images():errors.append(dict(file=path.name,page=index+1,code='BLANK_PAGE'))
                outside=[w for w in words if w[0]<40 or w[2]>page.rect.width-40 or w[1]<28 or w[3]>page.rect.height-15]
                if outside:errors.append(dict(file=path.name,page=index+1,code='TEXT_OUTSIDE_MARGINS',words=[w[4] for w in outside]))
            result.append(dict(file=path.name,pages=len(pdf),all_pages_rendered=True,portrait=True))
    return dict(files=result,errors=errors)


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path('data/debug/full_cohort_20261022T201512+0200'));parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv);source=args.source.resolve()
    stamp=datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S%z')
    out=args.output or Path('data/analysis')/('caa_cohort_descriptive_'+stamp)
    out=out.resolve()
    if out.is_relative_to(source) or source.is_relative_to(out):raise ValueError('Analysis output must be separate from frozen source')
    out.mkdir(parents=True,exist_ok=False);source_guard(source)
    original=freeze(source)
    (out/'source_artifact_sha256.json').write_text(json.dumps(original,indent=2),encoding='utf-8')
    print('Frozen source artifacts:',len(original),flush=True)
    rows,audits,baseline=build_dataset(source)
    # Verify frozen headline counts before generating inferential tables.
    gates=quality(rows,audits,baseline)
    summary_source={r['patient_id']:r for r in __import__('caa_pdf_extractor.full_cohort_report',fromlist=['load_csv']).load_csv(source/'patient_cohort_summary.csv')}
    gates['frozen_CAA_membership_changed']=sum(r['caa_positive']!=summary_source[r['patient_id']]['caa_positive_yes_no'] for r in rows)
    gates['seizure_only_as_chronic_epilepsy']=sum(r['epilepsy']!=summary_source[r['patient_id']]['epilepsy_yes_no'] for r in rows)
    if any(gates.values()):raise ValueError('STOP: analysis data quality discrepancy '+str(gates))
    specs=specifications(rows);t1,tests,missing=make_table(rows,specs)
    supplemental,stests,_=make_table(rows,supplemental_specs(rows));tests+=stests
    caa=[r for r in rows if r['caa_positive']];t2,_,_=make_table(caa,table2_specs(specs),subgroup=True)
    pet_specs=[dict(variable=k,characteristic=l,section='PET',kind='categorical',definition='Existing cerebral scope; existing canonical PET rules annotate frozen report text with exact source evidence. Indeterminate results remain missing for positive/negative comparisons. No CAA reclassification.') for k,l in [
        ('any_pet','Any cerebral PET'),('amyloid_pet','Amyloid PET performed'),('amyloid_pet_positive','Amyloid PET positive'),('amyloid_pet_negative','Amyloid PET negative'),('fdg_pet','Cerebral FDG PET performed'),('fdg_pet_abnormal','Abnormal cerebral FDG PET')]]
    pet,_,_=make_table(rows,pet_specs);csf=csf_summary(audits,rows)
    for table in (t1,supplemental,pet):
        for r in table:
            gates['missingness_denominator_errors']+=not (0<=r['available_n_CAA']<=20 and 0<=r['available_n_nonCAA']<=30 and r['available_n_overall']==r['available_n_CAA']+r['available_n_nonCAA'])
    for name,data in [('table1_demographic_clinical',t1),('table2_caa_positive_characteristics',t2),('patient_level_analysis_dataset',rows),
                      ('missingness_summary',missing),('statistical_tests',tests),('supplemental_statistics',supplemental),('pet_summary',pet),('csf_biomarker_summary',csf)]:write_csv(out/(name+'.csv'),data)
    for name,data in audits.items():write_csv(out/(dict(csf='csf_patient_measurements').get(name,name+'_identity_audit')+'.csv'),data)
    fields=['characteristic','overall','CAA_positive','CAA_non_positive','p_value','available_n_overall','available_n_CAA','available_n_nonCAA','definition']
    workbook(out/'table1_demographic_clinical.xlsx',t1,fields)
    workbook(out/'table2_caa_positive_characteristics.xlsx',t2,['characteristic','CAA_positive','available_n_overall','definition'])
    ranges=[]
    for key in ['age_at_first_admission','number_of_admissions','number_of_brain_MRI','number_of_brain_CT','number_of_eegs']:
        for name,subset in [('Overall',rows),('CAA_positive',caa),('CAA_non_positive',[r for r in rows if not r['caa_positive']])]:
            vals=[float(r[key]) for r in subset if r.get(key) is not None]
            ranges.append(dict(variable=key,group=name,available_n=len(vals),median_IQR=continuous(vals),minimum=min(vals) if vals else None,maximum=max(vals) if vals else None,
                mean=float(np.mean(vals)) if vals else None,SD=float(np.std(vals,ddof=1)) if len(vals)>1 else None))
    write_csv(out/'continuous_supplement_mean_SD_range.csv',ranges)
    (out/'analysis_methods.md').write_text('\n\n'.join(title+'\n\n'+text for title,text in METHODS),encoding='utf-8')
    (out/'variable_dictionary.json').write_text(json.dumps(specs+supplemental_specs(rows),indent=2,ensure_ascii=False),encoding='utf-8')
    captions=figure_outputs(out,rows);master=pdf_outputs(out,rows,t1,t2,missing,pet,csf,captions,stamp)
    layout=validate_pdfs(out,[master,out/'Table1_manuscript.pdf'])
    (out/'pdf_validation.json').write_text(json.dumps(layout,indent=2),encoding='utf-8')
    gates['source_artifacts_changed']=int(freeze(source)!=original)
    gates['PDF_layout_errors']=len(layout['errors'])
    master_pages=layout['files'][0]['pages']
    gates['master_report_page_count_outside_15_25']=int(not 15<=master_pages<=25)
    headings=['CAA_COHORT_DESCRIPTIVE_ANALYSIS_READY' if not any(gates.values()) else 'CAA_COHORT_DESCRIPTIVE_ANALYSIS_INCOMPLETE',
        'N = 50','CAA = 20/50','Epilepsy = 9/50','AF = 11/50',
        'Median age [IQR] = '+continuous([r['age_at_first_admission'] for r in rows]),'Female = '+categorical([r['female'] for r in rows]),
        'Median admissions [IQR] = '+continuous([r['number_of_admissions'] for r in rows]),
        'Median MRI/patient [IQR] = '+continuous([r['number_of_brain_MRI'] for r in rows]),
        'Median CT/patient [IQR] = '+continuous([r['number_of_brain_CT'] for r in rows]),
        'Deaths = '+categorical([r['death_documented'] for r in rows]),
        'Antiplatelet first discharge = '+categorical([r['first_discharge_antiplatelet'] for r in rows]),
        'Therapeutic anticoagulation first discharge = '+categorical([r['first_discharge_therapeutic_anticoagulation'] for r in rows])]
    for key,title in [('any_pet','PET available'),('csf_amyloid_available','CSF amyloid available'),('csf_total_tau_available','CSF total tau available')]:headings.append(title+' = '+categorical([r[key] for r in rows]))
    headings += ['Output directory: '+str(out),'Master PDF: '+str(master)]
    (out/'terminal_summary.txt').write_text('\n'.join(headings),encoding='utf-8')
    prose=['# Descriptive cohort summary',
        'The cohort consisted of 50 patients, of whom 20 (40.0%) fulfilled the predefined research definition of CAA. The remaining 30 include suspected-only, negative and indeterminate cases. Group membership was unchanged.',
        'Age at first admission was '+continuous([r['age_at_first_admission'] for r in rows])+' years; female sex was '+categorical([r['female'] for r in rows])+'.',
        'Primary first-admission presentations: '+', '.join(f'{k}: {n}/50 ({2*n:.1f}%)' for k,n in Counter(r['primary_presentation'] for r in rows).items())+'. Broad focal presentations were not retrospectively assigned diagnoses from imaging.',
        'Documented ICH history: '+categorical([r['any_ich'] for r in rows])+'. Chronic epilepsy: 9/50 (18.0%); AF: 11/50 (22.0%). These are documented patient-level indicators, not a measure of complete lifetime ascertainment.',
        'Brain MRI documented: '+categorical([r['any_mri'] for r in rows])+'; CT: '+categorical([r['any_ct'] for r in rows])+'. Exact examination counts exclude unresolved same-day identities; supported examination-day lower bounds are retained separately.',
        'First-admission mRS: '+continuous([r['first_admission_mrs'] for r in rows])+'. Discharge mRS was not exported and was not imputed.',
        'First-discharge antiplatelet: '+categorical([r['first_discharge_antiplatelet'] for r in rows])+'. Explicitly identified therapeutic anticoagulation: '+categorical([r['first_discharge_therapeutic_anticoagulation'] for r in rows])+'. This is not evidence of no anticoagulant treatment: anticoagulant indication was unspecified in the relevant drug rows.',
        'Explicit death declarations: '+categorical([r['death_documented'] for r in rows])+'. Dated in-hospital death: '+categorical([r['death_during_hospitalization'] for r in rows])+'. Follow-up completeness is unknown; these are documentation counts, not survival estimates.',
        'Cerebral PET: '+categorical([r['any_pet'] for r in rows])+'. Existing canonical PET rules provide explicit analysis-only annotations with source evidence: one abnormal nonspecific FDG brain component; amyloid PET indeterminate. CAA membership is unchanged. CSF amyloid, total tau and p-tau were each available in three patients; unspecified amyloid assays were not relabeled.',
        'Percentages use available-case denominators unless otherwise specified. All comparisons are descriptive/exploratory with no multiplicity adjustment. Non-significant P values are not interpreted as equivalence. See analysis_methods.md for the full selection, missingness and identity rules.']
    (out/'cohort_descriptive_summary.md').write_text('\n\n'.join(prose),encoding='utf-8')
    implementation_paths=list(Path(__file__).parent.glob('cohort_*analysis*.py'))+[Path(__file__).parent/'extraction'/name for name in ('pet_rules.py','pet_components.py','death_rules.py','research_metadata_v2.py')]+[Path(__file__).parent/'full_cohort_report.py']
    verification=dict(decision=headings[0],baseline=baseline,gates=gates,master_PDF=str(master),master_pages=master_pages,
        source_run=str(source),source_artifacts=len(original),clinical_DB_writes=0,extraction_writes=0,model_calls=0,network_calls=0,
        methods='Two-sided Fisher exact and Mann–Whitney U; available-case denominators; no imputation or reclassification',
        analysis_code_sha256={str(p):sha(p) for p in implementation_paths},
        software_versions={name:importlib.metadata.version(name) for name in ('numpy','scipy','openpyxl','matplotlib','reportlab','pymupdf')})
    (out/'analysis_verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    (out/'output_sha256.json').write_text(json.dumps({str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file() and p.name!='output_sha256.json'},indent=2),encoding='utf-8')
    if not any(gates.values()):(out.parent/'caa_cohort_descriptive_latest.txt').write_text(str(out),encoding='utf-8')
    print('\n'.join(headings),flush=True)
    return out


if __name__=='__main__':main()
