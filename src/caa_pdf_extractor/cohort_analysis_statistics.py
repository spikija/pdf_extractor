"""Available-case descriptive tables and predeclared exploratory tests."""
import csv,json,math
from pathlib import Path
import numpy as np
from scipy.stats import fisher_exact,mannwhitneyu
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment
from .cohort_analysis_dataset import PRIMARY,COMPONENTS,EXPOSURES


def write_csv(path,rows,fields=None):
    fields=fields or list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def numeric(values):return [float(x) for x in values if x is not None and math.isfinite(float(x))]


def continuous(values):
    values=numeric(values)
    if not values:return 'NA'
    q=np.percentile(values,[25,50,75],method='linear')
    return f'{q[1]:.1f} [{q[0]:.1f}–{q[2]:.1f}] (N={len(values)})'


def categorical(values):
    values=[v for v in values if v is not None]
    return f'{sum(v is True for v in values)}/{len(values)} ({100*sum(v is True for v in values)/len(values):.1f}%)' if values else 'NA'


def pformat(value):return 'NA' if value is None else '<0.001' if value<0.001 else f'{value:.3f}'


def compare(caa,noncaa,kind):
    a=[x for x in caa if x is not None];b=[x for x in noncaa if x is not None]
    method='Mann–Whitney U, two-sided, asymptotic, continuity and tie correction' if kind=='continuous' else 'Fisher exact, two-sided'
    reason=None;p=None;stat=None
    if min(len(a),len(b))<5:reason='Fewer than five available patients in either group'
    elif len(set(a+b))<2:reason='No variation in available values'
    elif kind=='categorical' and min(sum(x is True for x in a+b),sum(x is False for x in a+b))<2:
        reason='Fewer than two patients in a pooled outcome category'
    elif kind=='continuous':
        result=mannwhitneyu(a,b,alternative='two-sided',method='asymptotic',use_continuity=True)
        stat=float(result.statistic);p=float(result.pvalue)
    else:
        table=[[sum(x is True for x in a),sum(x is False for x in a)],[sum(x is True for x in b),sum(x is False for x in b)]]
        result=fisher_exact(table,alternative='two-sided');p=float(result.pvalue)
        stat=float(result.statistic) if math.isfinite(result.statistic) else None
    return dict(test=method,statistic=stat,p_value_raw=p,p_value=pformat(p),not_tested_reason=reason,available_n_CAA=len(a),available_n_nonCAA=len(b))


def specifications(rows):
    specs=[]
    def add(section,key,label,kind='categorical',definition=None):specs.append(dict(section=section,variable=key,characteristic=label,kind=kind,definition=definition or label))
    add('Demographics and admissions','age_at_first_admission','Age at first admission, years','continuous','Exact DOB and earliest validated admission; elapsed days / 365.2425. Approximate ages excluded.')
    add('Demographics and admissions','female','Female sex');add('Demographics and admissions','male','Male sex')
    add('Demographics and admissions','number_of_admissions','Unique admissions per patient','continuous','Unique validated admission dates; identical encounter documents merged. Discharge conflicts retained.')
    for k,l in [('admissions_1','One admission'),('admissions_2_3','Two to three admissions'),('admissions_ge4','Four or more admissions')]:add('Demographics and admissions',k,l)
    labels=['Spontaneous ICH','Ischemic stroke','TIA','Seizure or status','TFNE','Cognitive/behavioral','Headache','Other focal neurological deficit','Other','Unknown']
    for c,l in zip(PRIMARY,labels):
        key='primary_'+c
        for r in rows:r[key]=r['primary_presentation']==c
        add('First-admission primary presentation',key,l,definition='Mutually exclusive; validated reason, refined only by admission-linked clinical events. Broad focal labels remain broad; zero denotes no specific validated primary classification, not absence of that clinical event.')
    clabels=['Motor deficit','Sensory deficit','Language/speech deficit','Visual deficit','Cognitive/behavioral symptoms','Seizure','Status epilepticus','Headache','Altered consciousness','TFNE']
    for c,l in zip(COMPONENTS,clabels):add('First-admission presentation components','presentation_'+c,l,definition='Multiple response; only validated structured admission fields or linked clinical events. Omission is missing. Percentages may total over 100%.')
    history=[('any_ich','Clinical ICH event history'),('spontaneous_ich','Spontaneous ICH: linked clinical evidence'),('lobar_ich','Lobar ICH: source radiology marker'),('multiple_ich','At least two dated ICH events documented'),('ischemic_stroke','Ischemic stroke history'),('any_tia','TIA history'),('csah','Convexity SAH'),('css','Cortical superficial siderosis'),('cmb','Cerebral microbleeds'),('lobar_cmb','Lobar microbleeds'),('deep_cmb','Deep microbleeds')]
    for k,l in history:add('Cerebrovascular characteristics',k,l,definition='Source-supported documentation indicator where complete; hemorrhagic markers are positive/explicit negative/unknown. Generic SAH and combined cSS/cSAH counts do not establish cSAH. Multiple ICH uses distinct validated event dates; undated evidence excluded.')
    for k,l in [('any_mri','At least one brain MRI'),('any_ct','At least one brain CT'),('any_swi_gre','Explicit SWI or GRE available'),('number_of_brain_MRI','Brain MRIs per patient'),('number_of_brain_CT','Brain CTs per patient')]:
        add('Imaging utilization',k,l,'continuous' if k.startswith('number') else 'categorical',
            'Observed cerebral examinations only. Copies merge by family/date and identical or nested report. Ambiguous different same-day reports make exact counts missing; separate supported examination-day lower bounds are supplied. CTA is separate from plain CT.')
    for k,l in [('any_eeg','EEG performed'),('abnormal_eeg','Any abnormal EEG'),('epileptiform_activity','Epileptiform EEG activity')]:add('EEG',k,l,definition='Existing structured EEG fields; no epilepsy inference from slowing. Test abnormalities use assessed-case denominators; no EEG is missing for abnormality.')
    for k,l in [('epilepsy','Chronic epilepsy'),('any_seizure','Any seizure history'),('focal_seizure','Focal seizure documented'),('generalized_seizure','Generalized seizure documented'),('any_status','Status epilepticus'),('convulsive_status','Convulsive status'),('nonconvulsive_status','Nonconvulsive status')]:add('Epilepsy and seizures',k,l,definition='Frozen patient-level chronic epilepsy indicator or existing canonical clinical event types; no EEG/drug-based chronic epilepsy classification.')
    for when,label in [('admission','Admission'),('discharge','Discharge')]:
        add('Modified Rankin Scale','first_'+when+'_mrs',label+' mRS','continuous','First validated hospitalization; exact previously validated score only. Bounds not midpoint-imputed. Absent discharge fields stay missing.')
        for suffix,text in [('0_2','0–2'),('3_5','3–5'),('6','6')]:add('Modified Rankin Scale',f'mrs_{when}_{suffix}',f'{label} mRS {text}')
    add('Cardiovascular and treatment','af','Atrial fibrillation',definition='Frozen canonical ATRIAL_FIBRILLATION indicator; atrial flutter excluded.')
    add('Cardiovascular and treatment','first_discharge_antiplatelet','Antiplatelet at first discharge')
    add('Cardiovascular and treatment','first_discharge_therapeutic_anticoagulation','Therapeutic anticoagulation at first discharge',definition='Explicit therapeutic role required. Prophylaxis excluded; unknown anticoagulant role remains missing, including oral agents whose indication was not encoded. No dose-based inference.')
    add('Other characteristics','cognitive_impairment','Cognitive impairment/dementia')
    add('Other characteristics','death_documented','Death explicitly documented',definition='Explicit patient death declaration in retained source candidate, regardless of disease-eligibility tag. No death inferred from absent follow-up. Not a survival endpoint.')
    add('Other characteristics','death_during_hospitalization','Death during a verified hospitalization',definition='Explicit death date inside a validated admission/discharge interval. Undated documented deaths remain missing; no death documentation is recorded as no documented in-hospital death.')
    for k,l in [('any_pet','Cerebral PET available'),('amyloid_pet','Amyloid PET performed'),('fdg_pet','Cerebral FDG PET performed'),('csf_amyloid_available','CSF amyloid measured'),('csf_total_tau_available','CSF total tau measured'),('csf_ptau_available','CSF p-tau measured')]:add('Other characteristics',k,l)
    return specs


def make_table(rows,specs,subgroup=False):
    output=[];tests=[];missing=[]
    for s in specs:
        key=s['variable'];allv=[r.get(key) for r in rows];a=[r.get(key) for r in rows if r['caa_positive']];b=[r.get(key) for r in rows if not r['caa_positive']]
        fmt=continuous if s['kind']=='continuous' else categorical
        av=[sum(v is not None for v in x) for x in (allv,a,b)]
        test=compare(a,b,s['kind']) if not subgroup else dict(p_value='NA',p_value_raw=None,test='Not applicable: CAA-positive subgroup only',statistic=None,not_tested_reason='Subgroup descriptive table')
        record=dict(section=s['section'],variable=key,characteristic=s['characteristic'],overall=fmt(allv),
            CAA_positive=fmt(a),CAA_non_positive=fmt(b),p_value=test['p_value'],available_N=f'{av[0]} / {av[1]} / {av[2]}',
            available_n_overall=av[0],available_n_CAA=av[1],available_n_nonCAA=av[2],definition=s['definition'])
        output.append(record);tests.append(dict(variable=key,characteristic=s['characteristic'],**test))
        missing.append(dict(variable=key,characteristic=s['characteristic'],available_n_overall=av[0],available_n_CAA=av[1],available_n_nonCAA=av[2],
            missing_n_overall=len(rows)-av[0],missing_n_CAA=sum(r['caa_positive'] for r in rows)-av[1],missing_n_nonCAA=sum(not r['caa_positive'] for r in rows)-av[2]))
    return output,tests,missing


def supplemental_specs(rows):
    specs=[]
    def add(k,label,kind='categorical',section='Supplemental characteristics'):specs.append(dict(variable=k,characteristic=label,kind=kind,section=section,definition=label))
    for k,l in [('number_of_eegs','EEGs per patient'),('mri_exam_days_lower_bound','MRI examination-days: supported lower bound'),('ct_exam_days_lower_bound','CT examination-days: supported lower bound'),('last_available_mrs','Last available exact mRS')]:add(k,l,'continuous')
    for k in ['normal_eeg','focal_slowing','generalized_slowing']:add(k,k.replace('_',' ').capitalize())
    for t in ['paroxysmal','persistent','long_standing_persistent','permanent','unspecified']:
        add('af_'+t,'AF subtype among AF patients: '+t.replace('_',' '))
    for prefix,title in [('first_discharge_','First discharge'),('ever_discharge_','Any available discharge')]:
        for k in EXPOSURES:add(prefix+k,title+': '+k.replace('_',' '))
    for k in ('clinician_diagnosed','formal_probable','formal_possible','computed_probable','computed_possible','edinburgh_positive','pathology','suspicion_only','caa_negative','caa_indeterminate'):
        add(k,'CAA evidence: '+k.replace('_',' '),section='CAA status streams (may overlap)')
    return specs


def table2_specs(specs):
    labels=[('clinician_diagnosed','Source clinician-diagnosed CAA'),('formal_probable','Formal probable source CAA'),('formal_possible','Formal possible source CAA'),
        ('computed_probable','Computed Boston probable'),('computed_possible','Computed Boston possible'),('edinburgh_positive','Applicable Edinburgh positive'),('pathology','Pathological confirmation'),
        ('suspicion_preceding_diagnosis','Dated suspicion preceding dated diagnosis'),('any_ich','Clinical ICH event history'),('multiple_ich','At least two dated ICH events'),('lobar_ich','Lobar ICH: source radiology marker'),
        ('cmb','Cerebral microbleeds'),('lobar_cmb','Lobar microbleeds'),('css','Cortical superficial siderosis'),('csah','Convexity SAH'),
        ('cognitive_impairment','Cognitive impairment/dementia'),('any_tfne','Validated TFNE'),('epilepsy','Chronic epilepsy'),('af','Atrial fibrillation'),
        ('first_discharge_antiplatelet','Antiplatelet at first discharge'),('first_discharge_therapeutic_anticoagulation','Therapeutic anticoagulation at first discharge'),
        ('death_documented','Documented death'),('any_pet','Cerebral PET'),('csf_amyloid_available','CSF amyloid available'),('csf_total_tau_available','CSF total tau available'),('csf_ptau_available','CSF p-tau available')]
    return [dict(variable=k,characteristic=l,section='CAA-positive subgroup',kind='categorical',definition=next((s['definition'] for s in specs if s['variable']==k),l)) for k,l in labels]


def workbook(path,rows,fields):
    wb=Workbook();ws=wb.active;ws.title='Descriptive table';ws.append(fields)
    for r in rows:ws.append([r.get(k) for k in fields])
    for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='254B62')
    for row in ws:
        for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
    for i,k in enumerate(fields,1):ws.column_dimensions[ws.cell(1,i).column_letter].width=55 if k in ('characteristic','definition') else 24
    ws.freeze_panes='B2';ws.auto_filter.ref=ws.dimensions
    ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.page_setup.orientation='portrait';ws.page_setup.paperSize=ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0;ws.print_title_rows='1:1';wb.save(path)
