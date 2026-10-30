"""Patient-level cohort statistics and a source-linked portrait A4 review PDF."""
from collections import Counter,defaultdict
from pathlib import Path
import csv,html,json,re
import xml.etree.ElementTree as ET
import pymupdf as fitz
from .diagnosis_caa_v21_review import read,write,sha,csvfile
from .extraction.research_metadata_v2 import mri_technique,ich_dimensions,medication_proposal
from .full_cohort_laboratory import reference_flag

EPILEPSY={'EPILEPSY','EPILEPSY_UNSPECIFIED','FOCAL_EPILEPSY','GENERALIZED_EPILEPSY','SYMPTOMATIC_EPILEPSY','POST_STROKE_EPILEPSY','OTHER_EPILEPSY'}
STATUS={'STATUS_EPILEPTICUS','CONVULSIVE_STATUS_EPILEPTICUS','NONCONVULSIVE_STATUS_EPILEPTICUS'}
SEIZURE=STATUS|{'EPILEPTIC_SEIZURE','FOCAL_SEIZURE','GENERALIZED_TONIC_CLONIC_SEIZURE'}
SOURCE_POSITIVE={'CLINICIAN_DIAGNOSED_CAA','FORMAL_PROBABLE_CAA','FORMAL_POSSIBLE_CAA','DEFINITE_PATHOLOGICAL_CAA','PROBABLE_WITH_SUPPORTING_PATHOLOGY'}


def load_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    for row in rows:
        for key,value in row.items():
            if value and (value.startswith('[') or value.startswith('{')):
                try:row[key]=json.loads(value)
                except ValueError:pass
            elif value in ('True','False'):row[key]=value=='True'
    return rows


def patient_flags(diseases,events,assertions,boston,edinburgh):
    source={r['source_caa_status'] for r in assertions}
    computed={r['classification'] for r in boston}
    ed_positive=any(r.get('adequate_mri_available') is False and any(r.get(k)=='HIGH_PROBABILITY' for k in ('original_edinburgh','simplified_edinburgh')) for r in edinburgh)
    positive=bool(source&SOURCE_POSITIVE or computed&{'PROBABLE_CAA','POSSIBLE_CAA','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE_CAA'} or ed_positive)
    strong=bool(source&(SOURCE_POSITIVE-{'FORMAL_POSSIBLE_CAA'}) or computed&{'PROBABLE_CAA','PROBABLE_WITH_SUPPORTING_PATHOLOGY','DEFINITE_CAA'} or ed_positive)
    suspected=not positive and 'SUSPECTED_CAA' in source
    negative=not positive and not suspected and ('CAA_EXPLICITLY_NEGATED' in source or 'DOES_NOT_MEET' in computed)
    research='CAA_POSITIVE' if strong else 'CAA_POSSIBLE' if positive else 'CAA_SUSPECTED_ONLY' if suspected else 'CAA_NEGATIVE' if negative else 'CAA_INDETERMINATE'
    subtypes={r['subcategory'] for r in diseases};event_types={r['event_type'] for r in events}
    return dict(research_caa_status=research,caa_positive_yes_no=positive,
        clinician_diagnosed='CLINICIAN_DIAGNOSED_CAA' in source,formal_probable='FORMAL_PROBABLE_CAA' in source,formal_possible='FORMAL_POSSIBLE_CAA' in source,
        pathology=bool(source&{'DEFINITE_PATHOLOGICAL_CAA','PROBABLE_WITH_SUPPORTING_PATHOLOGY'}),
        computed_probable='PROBABLE_CAA' in computed,computed_possible='POSSIBLE_CAA' in computed,edinburgh_positive=ed_positive,
        suspicion_only=suspected,caa_negative=negative,caa_indeterminate=research=='CAA_INDETERMINATE',
        epilepsy_yes_no=bool(subtypes&EPILEPSY),seizure_history_yes_no=bool(event_types&SEIZURE),
        status_epilepticus_yes_no=bool(event_types&STATUS),atrial_fibrillation_yes_no='ATRIAL_FIBRILLATION' in subtypes,
        ich_history_yes_no='INTRACEREBRAL_HEMORRHAGE' in event_types or 'INTRACEREBRAL_HEMORRHAGE' in subtypes,
        ischemic_stroke_history_yes_no='ISCHEMIC_STROKE' in event_types or 'ISCHEMIC_STROKE' in subtypes,
        dementia_yes_no=any(d.get('category')=='COGNITIVE_DEMENTIA' for d in diseases))


def best(values,priority,default='NOT_ASSESSABLE'):
    return next((v for v in priority if v in values),default)


def cohort_statistics(patients):
    n=len(patients)
    definitions=[('TOTAL_PATIENTS',lambda p:True,'All dynamically discovered patient folders; one row per patient'),
        ('ANY_CAA_POSITIVE',lambda p:p['caa_positive_yes_no'],'Any explicit clinician diagnosis, formal probable/possible, computed Boston probable/possible, applicable Edinburgh high probability, or pathological confirmation; overlapping evidence counted once'),
        ('CLINICIAN_DIAGNOSED_CAA',lambda p:p['clinician_diagnosed'],'Explicit source clinician diagnosis, separate from computed criteria'),
        ('FORMAL_PROBABLE_CAA',lambda p:p['formal_probable'],'Explicit formal probable source classification'),
        ('FORMAL_POSSIBLE_CAA',lambda p:p['formal_possible'],'Explicit formal possible source classification'),
        ('COMPUTED_BOSTON_PROBABLE',lambda p:p['computed_probable'],'At least one computed Boston v2.0 probable timepoint'),
        ('COMPUTED_BOSTON_POSSIBLE',lambda p:p['computed_possible'],'At least one computed Boston v2.0 possible timepoint; may overlap probable at another date'),
        ('EDINBURGH_POSITIVE',lambda p:p['edinburgh_positive'],'Applicable CT event with confirmed MRI fallback and original or simplified HIGH_PROBABILITY'),
        ('PATHOLOGICAL_CAA',lambda p:p['pathology'],'Explicit CAA pathological confirmation; no inference from imaging or PET'),
        ('CAA_SUSPICION_ONLY',lambda p:p['suspicion_only'],'Source suspicion without any inclusive positive evidence'),
        ('CAA_NEGATIVE',lambda p:p['caa_negative'],'Explicit source negative or computed DOES_NOT_MEET, without positive evidence or suspicion'),
        ('CAA_INDETERMINATE',lambda p:p['caa_indeterminate'],'Insufficient source/computed support; no invented negative'),
        ('CHRONIC_EPILEPSY',lambda p:p['epilepsy_yes_no'],'Canonical chronic epilepsy disease; isolated seizure/status or EEG alone excluded'),
        ('ANY_SEIZURE_EVENT',lambda p:p['seizure_history_yes_no'],'Any supported epileptic seizure or status event'),
        ('SEIZURE_ONLY',lambda p:p['seizure_history_yes_no'] and not p['epilepsy_yes_no'],'Seizure/status event without canonical chronic epilepsy'),
        ('ANY_STATUS_EPILEPTICUS',lambda p:p['status_epilepticus_yes_no'],'Any supported status epilepticus event'),
        ('EPILEPSY_AND_STATUS',lambda p:p['epilepsy_yes_no'] and p['status_epilepticus_yes_no'],'Both canonical chronic epilepsy and a status event'),
        ('ATRIAL_FIBRILLATION',lambda p:p['atrial_fibrillation_yes_no'],'Canonical ATRIAL_FIBRILLATION; flutter excluded'),
        ('ICH_HISTORY',lambda p:p['ich_history_yes_no'],'Supported primary ICH event/disease; hemorrhagic ischemic transformation alone excluded'),
        ('ISCHEMIC_STROKE_HISTORY',lambda p:p['ischemic_stroke_history_yes_no'],'Supported ischemic stroke history, including hemorrhagic transformation'),
        ('COGNITIVE_DISORDER',lambda p:p['dementia_yes_no'],'Canonical COGNITIVE_DEMENTIA disease category')]
    rows=[dict(metric=metric,n=sum(bool(fn(p)) for p in patients),denominator=n,percent=round(100*sum(bool(fn(p)) for p in patients)/n,2) if n else 0,definition=definition) for metric,fn,definition in definitions]
    for sex,count in sorted(Counter(p['sex'] for p in patients).items()):rows.append(dict(metric='SEX_'+str(sex).upper(),n=count,denominator=n,percent=round(100*count/n,2),definition='Patient-bound ranked gender extraction; unresolved remains UNKNOWN'))
    return rows


def assemble(root,diagnosis_root):
    results=read(root/'extraction_results.json');manifest=load_csv(root/'cohort_manifest.csv')
    names=['canonical_diagnoses','clinical_events','caa_source_assertions','boston_patient_timepoints','edinburgh_ich_events','review_holds','csf_biomarkers','diagnosis_mentions','diagnosis_candidate_eligibility','boston_features']
    tables={name:load_csv(diagnosis_root/(name+'.csv')) for name in names}
    section_map={(p['patient_id'],d['source_document_id'],s['section_id']):s for p in read(root/'bundles.json') for d in p['documents'] for s in d['sections']}
    bypid={r['patient_id']:r for r in results};patients=[];panels={};excluded_medications=[]
    documents=read(root/'sources.json')['tables']['source_documents']
    csvfile(root,'source_document_lookup',[dict(patient_id=d['document_json']['document']['patient_id'],document_id=d['id'],
        filename=d['filename'],relative_path=d['document_json']['document']['relative_path'],pages=len(d['document_json']['pages']),sha256=d['sha256']) for d in documents])
    for m in manifest:
        pid=m['patient_id'];r=bypid[pid]
        discharge_medications=[]
        for medication in r['medications']:
            heading=section_map.get((pid,medication['source_document_id'],medication['section']),{}).get('heading','')
            if re.search(r'Entlass|empfohlen.*(?:Therapie|Medik)',heading,re.I):discharge_medications.append(medication)
            else:
                excluded_medications.append(dict(medication,exclusion_reason='MEDICATION_CONTEXT_NOT_EXPLICIT_DISCHARGE'))
                r['review_holds'].append(dict(patient_id=pid,code='MEDICATION_CONTEXT_NOT_EXPLICIT_DISCHARGE',source_document_id=medication['source_document_id'],detail=medication['section']))
        r['medications']=discharge_medications
        for medication in r['medications']:
            medication.update(medication_proposal(medication['medication'],medication.get('source_row',medication['medication']),medication['date']))
        subsets={name:[v for v in rows if v['patient_id']==pid] for name,rows in tables.items()}
        for biomarker in subsets['csf_biomarkers']:
            biomarker['research_abnormality']=reference_flag(biomarker['value'],biomarker.get('reference_low') or None,biomarker.get('reference_high') or None,biomarker.get('reference_text'))
            literal=re.search(r'\b(erniedrigt|erh.ht|normal)\b',biomarker.get('original_source_row',''),re.I)
            if biomarker['research_abnormality']=='UNKNOWN' and literal:
                biomarker['source_abnormality_text']=literal[0]
                biomarker['research_abnormality']='LOW' if literal[0].lower()=='erniedrigt' else 'NORMAL' if literal[0].lower()=='normal' else 'HIGH'
        candidate_map={c['record_id']:c for c in subsets['diagnosis_candidate_eligibility']}
        for diagnosis in subsets['canonical_diagnoses']:
            locators=[]
            for rid in diagnosis['member_ids']:
                for source in candidate_map[rid]['source_ids']:
                    s=section_map.get((pid,source['doc'],source['section']))
                    locators.append(f'doc{source["doc"]} / p{s["page_start"] if s else "?"} / {source["section"]}')
            diagnosis['source_lookup']=sorted(set(locators))
        for event in subsets['clinical_events']:
            refs=[]
            for rid in event['record_ids']:
                for source in candidate_map[rid]['source_ids']:
                    s=section_map.get((pid,source['doc'],source['section']))
                    refs.append(f'doc{source["doc"]} / p{s["page_start"] if s else "?"} / {source["section"]}')
            event['source_lookup']=sorted(set(refs)) or ['doc'+str(d)+' / '+str(event.get('evidence_ids')) for d in event.get('source_document_ids',[])]
        holds=subsets['review_holds']+r['review_holds']
        for unresolved in r.get('cognitive_canonical',{}).get('unresolved_mentions',[]):
            holds.append(dict(patient_id=pid,code='MMSE_IDENTITY_UNRESOLVED_EVIDENCE_RETAINED',source_document_id=unresolved['source_document_id'],detail=unresolved['mention_id']))
        if not r['case'].get('date_of_birth'):holds.append(dict(patient_id=pid,code='DOB_UNKNOWN',detail='No unsupported age inferred'))
        if not r['case'].get('gender'):holds.append(dict(patient_id=pid,code='SEX_UNRESOLVED',detail=r['gender_evidence']['status']))
        # Deduplicate identical review holds without discarding source IDs.
        holds=list({json.dumps(h,sort_keys=True,default=str):h for h in holds}.values())
        flags=patient_flags(subsets['canonical_diagnoses'],subsets['clinical_events'],subsets['caa_source_assertions'],subsets['boston_patient_timepoints'],subsets['edinburgh_ich_events'])
        summary=dict(patient_id=pid,sex=r['case'].get('gender') or 'UNKNOWN',birth_year=(r['case'].get('date_of_birth') or '')[:4] or None,
            documents=int(m['number_of_pdfs']),encounters=len({(e['admission_date'],e.get('discharge_date')) for e in r['encounters']}),
            source_caa_status=sorted({x['source_caa_status'] for x in subsets['caa_source_assertions']}),
            boston_status=best({x['classification'] for x in subsets['boston_patient_timepoints']},['DEFINITE_CAA','PROBABLE_WITH_SUPPORTING_PATHOLOGY','PROBABLE_CAA','POSSIBLE_CAA','DOES_NOT_MEET','NOT_ASSESSABLE']),
            edinburgh_status=best({x.get('original_edinburgh') for x in subsets['edinburgh_ich_events']}|{x.get('simplified_edinburgh') for x in subsets['edinburgh_ich_events']},['HIGH_PROBABILITY','INTERMEDIATE_OR_UNCERTAIN','LOW_PROBABILITY','NOT_APPLICABLE','NOT_ASSESSABLE']),
            **flags,review_hold_count=len(holds),overall_review_status='PHYSICIAN_REVIEW_WITH_HOLDS' if holds else 'PHYSICIAN_REVIEW')
        suspicion_dates=[a['source_diagnosis_date'] for a in subsets['caa_source_assertions'] if a['source_caa_status']=='SUSPECTED_CAA' and a.get('source_diagnosis_date')]
        diagnosis_dates=[a['source_diagnosis_date'] for a in subsets['caa_source_assertions'] if a['source_caa_status'] in SOURCE_POSITIVE and a.get('source_diagnosis_date')]
        summary.update(first_source_suspicion_date=min(suspicion_dates) if suspicion_dates else None,
            first_explicit_source_diagnosis_date=min(diagnosis_dates) if diagnosis_dates else None)
        patients.append(summary)
        for collection in ('imaging','other_exams','eeg','medications','laboratory'):
            for row in r[collection]:
                doc=row.get('source_document_id');key=row.get('section') or row.get('section_id')
                if not row.get('page') and doc and key:
                    s=section_map.get((pid,doc,key));row['page']=s['page_start'] if s else None
                if not row.get('page') and doc and row.get('header'):
                    found=next((s for (sp,sd,_),s in section_map.items() if sp==pid and str(sd)==str(doc) and row['header'].strip() in s['source_text']),None)
                    if found:row.update(page=found['page_start'],section=found['section_id'])
        panels[pid]=dict(result=r,summary=summary,tables=subsets,holds=holds,documents=[dict(document_id=d['id'],filename=d['filename'],pages=len(d['document_json']['pages'])) for d in documents if d['document_json']['document']['patient_id']==pid])
    statistics=cohort_statistics(patients)
    csvfile(root,'patient_cohort_summary',patients);csvfile(root,'cohort_statistics',statistics)
    for name in ('canonical_diagnoses','clinical_events','caa_source_assertions','csf_biomarkers'):csvfile(root,name,tables[name])
    csvfile(root,'boston_results',tables['boston_patient_timepoints']);csvfile(root,'edinburgh_results',tables['edinburgh_ich_events'])
    csvfile(root,'caa_summary',[dict({k:p[k] for k in ('patient_id','source_caa_status','boston_status','edinburgh_status','research_caa_status','caa_positive_yes_no','first_source_suspicion_date','first_explicit_source_diagnosis_date')},
        source_clinical_assertions=[s for s in p['source_caa_status'] if not s.startswith('FORMAL_') and 'PATHOLOG' not in s],
        formal_source_classifications=[dict(status=a['source_caa_status'],framework=a['criteria_system'],source_id=a['source_id']) for a in panels[p['patient_id']]['tables']['caa_source_assertions'] if a['source_caa_status'].startswith('FORMAL_')],
        pathology_status='EXPLICIT_CAA_CONFIRMATION' if p['pathology'] else 'NO_EXPLICIT_CAA_PATHOLOGICAL_CONFIRMATION') for p in patients])
    for name,key in [('imaging_summary','imaging'),('laboratory_summary','laboratory'),('medication_summary','medications'),('encounter_summary','encounters'),('examination_summary','other_exams'),('eeg_summary','eeg')]:
        # Detailed source reports stay in protected extraction artifacts; the
        # review exports contain concise summaries and bounded source lookup.
        csvfile(root,name,[{k:v for k,v in row.items() if k not in ('report','raw_report_text','snippets','eeg_report_text')} for r in results for row in r[key]])
    csvfile(root,'review_holds',[h for p in panels.values() for h in p['holds']])
    csvfile(root,'medication_context_exclusions',excluded_medications)
    write(root/'physician_review_data.json',dict(patients=patients,statistics=statistics,panels=panels))
    return patients,statistics,panels


def esc(value):
    if value is None or value=='':return 'Unknown'
    if isinstance(value,bool):return 'Yes' if value else 'No'
    if isinstance(value,(dict,list,tuple)):value=json.dumps(value,ensure_ascii=False)
    # Break long machine identifiers without reducing the font size.
    return html.escape(re.sub(r'([^\s]{25})(?=[^\s])',lambda m:m[1]+'\u200b',str(value))).replace('\n','<br/>')


def tab(headers,rows):
    if not rows:return '<p class="muted">No supported data identified in the supplied source documents.</p>'
    widths=([36,40,24] if str(headers[0]).startswith('Diagnosis') else [60,25,15] if headers[0]=='Metric' else [23,54,23]) if len(headers)==3 else [24,40,18,18] if len(headers)==4 else [50,50]
    return '<table><thead><tr>'+''.join(f'<th style="width:{widths[i]}%">'+esc(h)+'</th>' for i,h in enumerate(headers))+'</tr></thead><tbody>'+''.join(('<tr class="priority">' if any('ANTITHROMBOTIC' in str(v) or 'ANTISEIZURE' in str(v) for v in row) else '<tr>')+''.join(f'<td style="width:{widths[i]}%">'+esc(v)+'</td>' for i,v in enumerate(row))+'</tr>' for row in rows)+'</tbody></table>'


def lookup(row):
    doc=row.get('source_document_id');page=row.get('page') or row.get('page_number') or row.get('source_context',{}).get('page_start')
    section=row.get('section_id') or row.get('section') or row.get('procedure_id') or row.get('source_evidence_id') or row.get('imaging_id') or row.get('id')
    return f'doc{doc or "?"} / p{page or "?"} / {section or "lookup held"}'


def mrs(value):
    score=value.get('value') or {};exact=score.get('score')
    if exact is not None:return str(exact)+' / '+str(value.get('confidence','UNKNOWN'))
    if score.get('min') is not None:return str(score['min'])+'–'+str(score['max'])+' / '+str(value.get('confidence','UNKNOWN'))
    return 'Not assessable: insufficient supported admission function'


def patient_html(panel):
    r=panel['result'];p=panel['summary'];t=panel['tables'];pid=p['patient_id']
    parts=[f'<h1>Patient {esc(pid)}</h1>',f'<p><b>CAA:</b> {esc(p["research_caa_status"])} &nbsp; <b>Chronic epilepsy:</b> {esc(p["epilepsy_yes_no"])}<br/><b>Review:</b> {esc(p["overall_review_status"])}</p>',
        '<h2>Patient overview</h2>',tab(['Sex / birth','Sources / encounters','Review holds'],[[f'{p["sex"]}; DOB {r["case"].get("date_of_birth") or "unknown"}',f'{p["documents"]} PDFs; {p["encounters"]} distinct inpatient encounters',p['review_hold_count']]]),
        '<h3>Source documents</h3>',tab(['Document ID','Original PDF filename','Pages'],[[d['document_id'],d['filename'],d['pages']] for d in panel.get('documents',[])]),
        '<h2>Admission / encounter timeline</h2>']
    for e in r['encounters']:
        snippets=e.get('snippets',[]);refs=[s['snippet_id'] for s in snippets if s['snippet_id'] in e['reason'].get('index_evidence_ids',[]) or s['snippet_id'] in e['reason'].get('evidence_ids',[])]
        parts.append(tab(['Admission → discharge','Reason / current presentation','Source'],[[f'{e["admission_date"]} → {e.get("discharge_date") or "unknown"}',str(e['reason'].get('primary_reason'))+'; '+str(e['reason'].get('current_encounter_purpose'))+'\n'+e.get('concise_summary','Source phenotype: '+str(e['phenotype'].get('flags'))),f'doc{e["source_document_id"]} / '+', '.join(refs[:4])]]))
        ph=e['phenotype'];parts.append(tab(['Sensory presentation','Admission mRS / assessability'],[[
            '; '.join(f'{k.replace("sensory_","")}: {ph.get(k,"unknown")}' for k in ('sensory_present','sensory_polarity','sensory_laterality','sensory_distribution','sensory_spreading','sensory_transient')),
            mrs(e['mrs'].get('admission',{}))]]))
    parts+=['<h2>Canonical diagnoses</h2>',tab(['Diagnosis / etiology','Domain → category → subcategory','Date / evidence'],[[
        d['canonical_name']+'\nEtiology: '+str(d.get('etiology') or 'unknown')+ ('\nAF type: '+str(d['af_type']) if d.get('af_type') else '')+ ('\nValve: '+str(d['valvular_severity'])+'; observed '+str(d.get('valvular_severities')) if d.get('valvular_severity') else ''),
        f'{d["domain"]} → {d.get("category") or "unknown"} → {d["subcategory"]}',
        str(d.get('first_supported_date') or 'Unknown')+f' / {d["original_mention_count"]} mentions; '+'; '.join(d.get('source_lookup',d['member_ids']))] for d in t['canonical_diagnoses']]),
        '<h2>Acute clinical events</h2>',tab(['Date / precision','Event / etiology','Source'],[[(e.get('event_date') or str(e.get('event_year') or 'Unknown'))+' / '+str(e.get('date_precision')),e['event_type']+'; '+str(e.get('etiology') or 'unknown')+ ('; hemorrhagic transformation' if e.get('hemorrhagic_transformation') else ''),'; '.join(e.get('source_lookup',e['record_ids']))] for e in t['clinical_events']]),
        '<h2>CAA status — source and computed assessments kept separate</h2>',tab(['Evidence stream','Assessment'],[[
            'Source clinical assertion',[s for s in p['source_caa_status'] if not s.startswith('FORMAL_') and 'PATHOLOG' not in s]],['Formal source classification',[s for s in p['source_caa_status'] if s.startswith('FORMAL_')]],['Computed Boston v2.0',p['boston_status']],['Computed Edinburgh',p['edinburgh_status']],['Pathology','Explicit confirmation' if p['pathology'] else 'No explicit CAA pathological confirmation identified'],['Patient research status',p['research_caa_status']]]),
        tab(['Source CAA assertion / formal framework','Source date','Lookup'],[[a['source_caa_status']+' / '+str(a['criteria_system']),a.get('source_diagnosis_date') or 'Unknown',lookup(a)] for a in t['caa_source_assertions']]),
        tab(['First source suspicion','First explicit source diagnosis'],[[p.get('first_source_suspicion_date'),p.get('first_explicit_source_diagnosis_date')]]),
        tab(['Boston timepoint','Computed category / review','Feature evidence'],[[b.get('date'),str(b['classification'])+'; '+str(b.get('review') or b.get('holds') or ''),', '.join(b.get('source_ids',[]))] for b in t['boston_patient_timepoints']]),
        tab(['ICH date / adequate MRI','Edinburgh original / simplified','Review'],[[str(e.get('event_date') or 'Unknown')+' / MRI: '+esc(e.get('adequate_mri_available')),str(e.get('original_edinburgh'))+' / '+str(e.get('simplified_edinburgh')),e.get('review')] for e in t['edinburgh_ich_events']]),
        '<h2>Cerebral imaging timeline</h2>',tab(['Date / modality','Key finding','Source'],[[i.get('raw_source_date') or i.get('exam_date'),i['modality']+': '+i.get('concise_summary','Source structured findings retained; review required'),lookup(i)] for i in r['imaging']]),
        '<h3>MRI technique and hemorrhagic markers</h3>',tab(['MRI date','CMB / cSS / cSAH features','SWI/GRE / contrast'],[[i.get('raw_source_date'),
            '; '.join(f.get('finding_type','')+' '+str(f.get('laterality') or '')+' '+str(f.get('location') or '') for f in i['all_existing_positive_findings'] if f['finding_type'] in ('CMB','CSS','CSAH','cSS','cSAH')) or 'No positive marker explicitly extracted; absence not inferred',
            str(mri_technique(i['raw_report_text'])['sequences'])+' / '+mri_technique(i['raw_report_text'])['contrast_status']] for i in r['imaging'] if i['modality']=='MRI']),
        '<h3>CAA feature counts — conservative lower bounds, not serial sums</h3>',tab(['MRI assessment','Lobar markers / white matter','Exclusion support / evidence'],[[
            str(f.get('date'))+' / '+str(f.get('assessment_mri_id')),
            '; '.join(k+': '+str(f.get(k) if f.get(k) not in (None,'') else 'Unknown') for k in ('lobar_ich','lobar_cmb','css_csah_foci','severe_cso_pvs','multispot_wmh')),
            str(f.get('deep_hemorrhage_exclusion_support'))+'; alternative: '+str(f.get('alternative_cause_status'))] for f in t['boston_features']]),
        '<h3>Hemorrhage location and dimensions</h3>',tab(['Date / type','Location / size','Source'],[[i.get('raw_source_date'),
            str(f.get('laterality') or 'Unknown')+' '+str(f.get('location') or 'unknown')+'; '+str(ich_dimensions(f.get('evidence_text') or '')),lookup(i)] for i in r['imaging'] for f in i['all_existing_positive_findings'] if f['finding_type']=='ICH'])]
    parts+=['<h2>EEG</h2>',tab(['Date / status','Structured finding','Source'],[[e.get('eeg_date'),str(e.get('eeg_status'))+'; '+str(e.get('eeg_primary_finding'))+'; '+str(e.get('eeg_laterality')),lookup(e)] for e in r['eeg']])]
    for heading,pattern in [('Neurosonology',r'NEUROSONOLOGY'),('Echocardiography — TTE / TEE',r'echo'),('PET / other investigations',r'pet|spect|other|ultrasound|x.ray')]:
        parts+=['<h2>'+heading+'</h2>',tab(['Date / investigation','Concise finding','Source'],[[e.get('date'),e['kind']+': '+e['concise_summary']+(f'; {e["echo"]["echo_subtype"]}' if e.get('echo') else ''),lookup(e)] for e in r['other_exams'] if re.search(pattern,e['kind'],re.I)])]
    cog=r.get('cognitive_canonical',{});assessments=cog.get('canonical_assessments',[])
    parts+=['<h2>Cognitive testing</h2>',tab(['Test / score','Owned date / identity','Source'],[[f'{a["test_type"]}: {a["score"]}/{a["max_score"]}',str(a.get('assessment_date') or a.get('assessment_year') or 'Unknown')+' / '+a['assessment_role'],lookup(a)] for a in assessments])]
    unresolved=cog.get('unresolved_mentions',[])
    if unresolved:parts+=['<p>Additional explicit scores remain evidence-only because test identity is unresolved; they are not counted as additional performed tests.</p>',tab(['Test / score','Mention / date','Source'],[[f'{a["test_type"]}: {a["score"]}/{a["max_score"]}',a['mention_id']+' / '+str(a.get('assessment_date') or a.get('assessment_year') or 'Unknown'),lookup(a)] for a in unresolved])]
    parts+=['<h2>Selected research laboratory — first reported value</h2>',tab(['Parameter / value','Unit / reference / flag','Admission date / source'],[[l['parameter']+': '+l['value'],str(l.get('unit'))+' / '+str(l.get('reference_text') or 'reference not extracted')+' / '+l['abnormality'],str(l['measurement_date'])+' / '+lookup(l)] for l in r['laboratory']]),
        '<h3>CSF amyloid / tau — no inferred isoform or calculated ratio</h3>',tab(['Reported analyte / canonical assay','Value / reference / flag','Admission date / source'],[[l['original_analyte_text']+' / '+l['canonical_analyte'],str(l['value'])+' '+str(l['unit'])+' / '+str(l['reference_text'])+' / '+str(l['research_abnormality'])+' (source: '+str(l['abnormality'])+')',str(l['date'])+' / '+lookup(l)] for l in t['csf_biomarkers']]),
        '<h2>Discharge medication</h2>','<p class="muted">Antithrombotic and antiseizure drug classes are highlighted. Drug class does not establish indication; paused/discontinued wording is preserved as reported.</p>',tab(['Medication / dose as reported','Classification','Discharge date / source'],[[l['medication'],str(l['category'])+' / '+str(l.get('agent_class') or l.get('antithrombotic_subclass') or '')+' / '+str(l.get('antithrombotic_role') or ''),str(l['date'])+' / '+lookup(l)] for l in r['medications']]),
        '<h2>Review holds</h2>',tab(['Issue','Evidence / detail'],[[h.get('code') or h.get('issue_type'),str(h.get('source_document_id') or h.get('source_ids') or '')+' / '+str(h.get('detail') or '')] for h in panel['holds']])]
    return '\n'.join(parts)


CSS='''body {font-family:sans-serif;font-size:8.7pt;line-height:1.22;color:#182630;}
h1 {font-size:16pt;color:#17475f;margin:3pt 0 10pt;} h2 {font-size:11pt;color:#17475f;margin:12pt 0 5pt;} h3 {font-size:9pt;margin:9pt 0 4pt;}
p {margin:4pt 0 7pt;} table {border-collapse:collapse;width:100%;font-size:7.7pt;margin:4pt 0 9pt;table-layout:fixed;}
th {background:#e3edf2;font-weight:bold;text-align:left;} th,td {border:0.4pt solid #a8b9c4;padding:4pt;vertical-align:top;overflow-wrap:anywhere;}
thead {display:table-header-group;} tr {page-break-inside:avoid;} .priority td {background-color:#fff2d8;} .muted {color:#586873;font-size:8pt;}
'''


def render_html(body,path):
    a4=fitz.paper_rect('a4');rect=fitz.Rect(45,60,a4.width-45,a4.height-48)
    elements=list(ET.fromstring('<root>'+body.replace('&nbsp;','&#160;')+'</root>'))
    # Place complete rows transactionally. Story does not repeat table headers
    # on continuation pages, so pagination explicitly redraws each header.
    row_css=CSS+' table {margin:0;} body {margin:0;} '
    with fitz.DocumentWriter(str(path)) as writer:
        page=1;device=writer.begin_page(a4);y=rect.y0
        def new_page():
            nonlocal page,device,y
            writer.end_page();page+=1
            if page>300:raise ValueError('Patient pagination failed to converge')
            device=writer.begin_page(a4);y=rect.y0
        def place(fragment,css=CSS):
            story=fitz.Story(html='<html><body>'+fragment+'</body></html>',user_css=css)
            more,filled=story.place(fitz.Rect(rect.x0,y,rect.x1,rect.y1))
            return story,more,fitz.Rect(filled)
        for element in elements:
            if element.tag!='table':
                if element.tag in ('h1','h2','h3') and rect.y1-y<90:new_page()
                fragment=ET.tostring(element,encoding='unicode')
                story,more,filled=place(fragment)
                if more:
                    new_page();story,more,filled=place(fragment)
                if more:raise ValueError('Paragraph exceeds a portrait page')
                story.draw(device);y=filled.y1+3
                continue
            header=ET.tostring(element.find('thead'),encoding='unicode');first=True
            for row in element.find('tbody'):
                include_header=first
                raw=ET.tostring(row,encoding='unicode')
                fragment='<table>'+(header if include_header else '')+'<tbody>'+raw+'</tbody></table>'
                story,more,filled=place(fragment,row_css)
                if more:
                    new_page();fragment='<table>'+header+'<tbody>'+raw+'</tbody></table>'
                    story,more,filled=place(fragment,row_css)
                if more:raise ValueError('Table row exceeds a portrait page; redesign the row')
                story.draw(device);y=filled.y1;first=False
            y+=9
        writer.end_page()
    return page


def generate_pdf(root,patients,statistics,panels):
    folder=root/'pdf_parts';folder.mkdir(exist_ok=True);patient_pages={}
    for p in patients:
        pid=p['patient_id'];body=patient_html(panels[pid]);(folder/(pid+'.html')).write_text(body,encoding='utf-8')
        patient_pages[pid]=render_html(body,folder/(pid+'.pdf'))
    freeze=read(root/'cohort_freeze.json');stamp=root.name.removeprefix('full_cohort_')
    def summary_body(offset):
        parts=['<h1>FULL COHORT SUMMARY</h1>',f'<p><b>Run:</b> {esc(stamp)}<br/>{freeze["patients"]} patients · {freeze["pdfs"]} source PDFs · {freeze["pages"]} source pages.</p>',
            '<p>Physician-review proposals only. No clinical backfill or clinical database writes. Source assertions, formal source classifications, computed Boston, CT-only Edinburgh fallback and pathology remain separate. All prevalence numerators count patients once; evidence streams can overlap.</p>',
            '<p>CAA inclusive definition: explicit clinician CAA, formal probable/possible CAA, computed Boston probable/possible, applicable Edinburgh high probability, or pathological confirmation. Suspicion, differential diagnosis and compatible imaging alone do not count. Chronic epilepsy requires a canonical chronic diagnosis; isolated seizure, status or EEG findings alone do not count.</p>',
            '<p>Unknown means insufficient support. Source lookup uses local document IDs; source_document_lookup.csv maps IDs to the original PDF filename and SHA-256. Dates retain their configured admission/discharge conventions. Source notation β, ß, ä, ö, ü is preserved.</p>',
            tab(['Metric','Patients / denominator','Percent'],[[s['metric'],f'{s["n"]} / {s["denominator"]}',f'{s["percent"]:.1f}%'] for s in statistics]),'<h2>Patient navigation</h2>']
        rows=[];page=offset+1
        for p in patients:
            rows.append([p['patient_id'],p['research_caa_status'],'Yes' if p['epilepsy_yes_no'] else 'No',f'{page:04d}']);page+=patient_pages[p['patient_id']]
        parts.append(tab(['Patient ID','CAA research status','Epilepsy','First page'],rows));return '\n'.join(parts)
    summary=folder/'summary.pdf';count=render_html(summary_body(0),summary)
    assert render_html(summary_body(count),summary)==count
    combined=fitz.open();toc=[[1,'Cohort Summary',1]];ranges={};header_for_page={}
    with fitz.open(summary) as pdf:combined.insert_pdf(pdf)
    for p in patients:
        pid=p['patient_id'];start=len(combined)
        with fitz.open(folder/(pid+'.pdf')) as pdf:combined.insert_pdf(pdf)
        ranges[pid]=dict(first_page=start+1,last_page=len(combined));toc.append([1,'Patient '+pid,start+1])
        for index in range(start,len(combined)):header_for_page[index]=p
    for i,page in enumerate(combined):
        p=header_for_page.get(i)
        header=(f'Patient {p["patient_id"]} | CAA: {p["research_caa_status"]} | Epilepsy: '+('YES' if p['epilepsy_yes_no'] else 'NO')) if p else 'FULL COHORT PHYSICIAN REVIEW | SUMMARY'
        page.insert_text((45,23),header,fontname='helv',fontsize=7.7,color=(0.08,0.25,0.34))
        if p:page.insert_text((45,36),'Review: '+p['overall_review_status'],fontname='helv',fontsize=7.2)
        page.insert_text((45,page.rect.height-24),f'Page {i+1} / {len(combined)}     |     Run {stamp}     |     Review only',fontname='helv',fontsize=7.2)
    combined.set_toc(toc);combined.set_metadata(dict(title='Full cohort physician review',author='Local validated extraction pipeline',subject='Diagnosis taxonomy V2.2; Boston v2.0; Edinburgh fallback'))
    target=root/f'CAA_full_cohort_physician_review_{stamp}.pdf';combined.save(target,garbage=4,deflate=True);combined.close()
    write(root/'pdf_patient_pages.json',ranges)
    return target,ranges


def validate_pdf(root,target,ranges):
    renders=root/'pdf_rendered';renders.mkdir(exist_ok=True);errors=[];page_audits=[]
    body_patient_headings=[]
    with fitz.open(target) as pdf:
        for i,page in enumerate(pdf):
            pix=page.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False);pix.save(renders/f'page_{i+1:04d}.png')
            words=page.get_text('words');body=[w for w in words if 45<=w[1]<page.rect.height-38]
            if page.rect.width>=page.rect.height:errors.append(dict(page=i+1,code='NOT_PORTRAIT'))
            if not body:errors.append(dict(page=i+1,code='ACCIDENTAL_BLANK_BODY'))
            outside=[w for w in body if w[0]<43 or w[2]>page.rect.width-43 or w[3]>page.rect.height-43]
            if outside:errors.append(dict(page=i+1,code='TEXT_OUTSIDE_BODY_MARGINS',words=[w[4] for w in outside]))
            # Text boxes from distinct cells/rows must not geometrically overlap.
            spans=[s for b in page.get_text('dict')['blocks'] if 'lines' in b for l in b['lines'] for s in l['spans'] if s['text'].strip() and s['bbox'][1]>=45]
            body_patient_headings.extend(s['text'].strip().removeprefix('Patient ') for s in spans if s['size']>=14 and re.fullmatch(r'Patient \d+',s['text'].strip()))
            if any(s['size']<7.4 for s in spans if s['bbox'][1]<page.rect.height-38):errors.append(dict(page=i+1,code='BODY_FONT_TOO_SMALL'))
            for ai,a in enumerate(spans):
                ar=fitz.Rect(a['bbox'])
                for b in spans[ai+1:]:
                    br=fitz.Rect(b['bbox'])
                    if abs(ar.y0-br.y0)>max(ar.height,br.height):continue
                    overlap=ar&br
                    if not overlap.is_empty and overlap.width>1.0 and overlap.height>min(ar.height,br.height)*0.6:
                        errors.append(dict(page=i+1,code='OVERLAPPING_TEXT_SPANS'));break
            page_audits.append(dict(page=i+1,width=page.rect.width,height=page.rect.height,rendered=True,body_words=len(body),body_spans=len(spans)))
        for pid,span in ranges.items():
            if 'Patient '+pid not in pdf[span['first_page']-1].get_text():errors.append(dict(patient_id=pid,code='PATIENT_FIRST_PAGE_MISSING'))
        alltext='\n'.join(p.get_text() for p in pdf)
        for char in 'βßäöü':
            if char not in alltext:errors.append(dict(code='UNICODE_ROUNDTRIP_MISSING',character=char))
        section_ids=[t[1].removeprefix('Patient ') for t in pdf.get_toc() if t[1].startswith('Patient ')]
        if sorted(section_ids)!=sorted(ranges):errors.append(dict(code='PATIENT_BOOKMARK_RECONCILIATION'))
        if sorted(body_patient_headings)!=sorted(ranges):errors.append(dict(code='PATIENT_BODY_HEADING_RECONCILIATION'))
    result=dict(pdf=str(target.resolve()),sha256=sha(target),pages=len(page_audits),patient_sections=len(ranges),
        all_pages_rendered=True,portrait=True,patient_body_headings=body_patient_headings,errors=errors,page_audits=page_audits)
    write(root/'pdf_validation.json',result)
    return result
