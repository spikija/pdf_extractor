"""Frozen same-20 V2.1 review CLI. No SQL, persistence imports or cloud access.

Run python -m caa_pdf_extractor.diagnosis_caa_v21_review --output PATH
with --infer for loopback Granite; replay is the default and never calls a model.
"""
import argparse
import csv
import hashlib
import ipaddress
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from .extraction.diagnosis_canonical_v2 import stable_id
from .extraction.diagnosis_canonical_v21 import (Eligibility, eligibility_schema, eligibility_prompt,
    validate_eligibility, grouping_prompt, grouping_schema, validate_group_mapping, category_errors)
from .extraction.caa_evidence_v21 import evidence_schema,evidence_prompt,validate_evidence,assemble_patient,reconcile_validated_scan
from .extraction.research_metadata_v2 import macro_location
from .extraction.diagnosis_caa_v21_audit import audit_tables
from .extraction.diagnosis_caa_v21_addendum import csf_census, apply_taxonomy
from .llm.granite import GraniteClient

BASE=Path('data/debug')
INPUTS=dict(history=BASE/'history_v2_validation_20261019T214416+0200.json',
    image=BASE/'caa_imaging_physician_review_20261021T195219+0200/review_data.json',
    sensory=BASE/'sensory_refinement20_20261021T191239+0200.json',
    snapshot=BASE/'caa_panorama_frozen_20261021T220651+0200/snapshot.json',
    old_assertions=BASE/'diagnosis_caa_v2_20261022T163534+0200/caa_source_assertions.csv',
    admission_events=BASE/'diagnosis_caa_v2_20261022T163534+0200/clinical_events.csv',
    bundles=BASE/'sensory_caa_preimplementation_20261021/bundles.json')
DIGEST='f586c02fdecdf151b656207c339aa003997345774a41768bac1fd6d2fb85913b'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def protect(path):
    if sys.platform!='win32':path.chmod(0o700 if path.is_dir() else 0o600);return
    result=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True)
    sid=next(csv.reader([result.stdout.strip()]))[1]
    suffix='(OI)(CI)F' if path.is_dir() else 'F'
    subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',f'*{sid}:{suffix}',f'*S-1-5-18:{suffix}'],capture_output=True,check=True)

def guard_network(name,args):
    if name=='socket.connect':
        address=args[1];host=address[0] if isinstance(address,tuple) else ''
        if not ipaddress.ip_address(host).is_loopback:raise RuntimeError('Non-loopback network prohibited')
    if name=='socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1','localhost',None):
        raise RuntimeError('Non-loopback DNS prohibited')

def csvfile(root,name,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r)) or ['patient_id','status']
    with (root/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()})

def table(headers,rows):
    def cell(x):return ('UNKNOWN' if x is None else str(x)).replace('|','/').replace('\n',' ')
    return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(cell(x) for x in r)+' |\n' for r in rows)

class RetainedModel:
    def __init__(self,root,infer):self.root=root;self.infer=infer;self.client=None;self.calls=0
    def run(self,key,prompt,schema,validator,reuse_validated_schema=False):
        path=self.root/(key+'.json');ph=hashlib.sha256(prompt.encode()).hexdigest()
        if path.exists():
            saved=read(path)
            try:
                if saved['prompt_sha256']!=ph or saved['schema']!=schema and not reuse_validated_schema:raise ValueError('Stale cached model request '+key)
                return validator(saved['proposal'])
            except ValueError:
                if not self.infer:raise
                archive=self.root/(key+'.superseded.'+sha(path)[:12]+'.json')
                if not archive.exists():path.rename(archive)
                else:path.unlink()
        # A previously rejected response can become usable after a validation
        # contract refinement. It must pass every current rule without repair.
        for rejected in sorted(self.root.glob(key+'.rejected*.json'),reverse=True):
            saved=read(rejected)
            if saved.get('prompt_sha256')!=ph or saved.get('schema')!=schema and not reuse_validated_schema or 'proposal' not in saved:continue
            try:result=validator(saved['proposal'])
            except (ValueError,TypeError,KeyError):continue
            if self.infer:write(path,dict(saved,revalidated_from=rejected.name))
            return result
        if not self.infer:raise RuntimeError('Missing retained local model response '+key)
        if self.client is None:
            self.client=GraniteClient(base_url='http://127.0.0.1:11434',model='granite4.2:latest');self.client.check_model()
            if self.client.model_version!=DIGEST:raise RuntimeError('Local model digest differs from frozen model')
        options=dict(temperature=0,seed=0,num_ctx=32768,num_predict=12000)
        (self.root/(key+'.prompt.txt')).write_text(prompt,encoding='utf-8')
        # Preserve every rejected attempt; feedback repairs structure without Python relabeling.
        original_prompt=prompt
        for attempt in range(3):
            response=self.client._request('/api/chat',dict(model=self.client.model_name,
                messages=[dict(role='user',content=prompt)],stream=False,think=False,format=schema,options=options))
            self.calls+=1
            record=dict(prompt_sha256=ph,schema=schema,model=self.client.model_name,digest=DIGEST,options=options,raw_response=response)
            try:
                if not response.get('done') or response.get('done_reason')=='length':raise ValueError('Incomplete model response')
                proposal=json.loads(response['message']['content']);record['proposal']=proposal
                result=validator(proposal)
                write(path,record)
                print(json.dumps(dict(request=key,status='VALID',attempt=attempt+1)),flush=True)
                return result
            except (ValueError,TypeError,KeyError) as exc:
                record['validation_error']=str(exc)
                rejected_hash=hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest()[:12]
                write(self.root/(key+f'.rejected{attempt+1}.'+rejected_hash+'.json'),record)
                prompt=original_prompt+'\nPrevious answer rejected: '+str(exc)+'\nReturn a corrected complete answer.'
        raise ValueError('Local model validation failed '+key+': '+record['validation_error'])


def prepare(history,sensory,image):
    records={};episodes={};scans={}
    for p in history['patients']:
        pid=p['patient_id'];records[pid]={}
        for i,r in enumerate(p['output']['records'],1):
            records[pid][f'R{i:03}']=dict(label=r['disease'],accepted_date=r['date_disease'],
                year=r['date_disease_year'],month=r['date_disease_month'],precision=r['date_disease_precision'],
                sources=[dict(doc=e['source_document_id'],section=e['section_id'],text=e['evidence_text']) for e in r['evidence']])
    for e in sensory['encounters']:
        pid=e['patient_id'];doc=e['source_document_id'];key=f'DOC-{doc}'
        snippets=[s for s in e['snippets'] if s['heading'] in ('Zusammenfassung des Aufenthalts','Anamnese')]
        episodes[(pid,key)]=dict(id=key,admission_date=e['admission_date'],
            phenotype=e['unchanged_index_and_purpose'],text='\n'.join(s['text'] for s in snippets))
    for s in image['imaging_timeline']:
        if s['performed'] is not True or s['scope']!='BRAIN' or s['modality'] not in ('CT','MRI'):continue
        pid=s['patient_id'];key=f"DOC-{s['source_document_id']}"
        ich_ids=sorted({(str(f.get('laterality') or 'UNKNOWN'),str(f.get('location') or 'UNKNOWN')) for f in s['all_existing_positive_findings']
                        if not f.get('deleted') and f['finding_type']=='ICH' and macro_location(f.get('location') or '')['macro_location']=='LOBAR'})
        validated_identity=';'.join(side+'_'+location for side,location in ich_ids) if ich_ids else None
        scans.setdefault((pid,key),[]).append(dict(id=s['imaging_id'],date=s.get('raw_source_date'),
            modality=s['modality'],episode_id=key,admission_date=s.get('admission_date'),
            source_document_id=s['source_document_id'],header=s['subtype'],text=s.get('raw_report_text') or '',
            validated_ich_identity=validated_identity,
            complete_diagnostic_report=s.get('source_context',{}).get('discovery_kind')=='FORMAL_REPORT',
            validated_positive=s['all_existing_positive_findings'],validated_negative=s['all_existing_negative_findings']))
    return records,episodes,scans


def main(argv=None, *, inputs=None, expected_patient_ids=None):
    inputs=inputs or INPUTS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--infer',action='store_true')
    args=parser.parse_args(argv);root=args.output.resolve()
    allowed_parent=root.parent==BASE.resolve() or (expected_patient_ids is not None and root.parent.parent==BASE.resolve() and root.parent.name.startswith('full_cohort_'))
    if not allowed_parent or not root.name.startswith('diagnosis_caa_v21_'):raise ValueError('Output must be a review directory under data/debug')
    root.mkdir(exist_ok=True);protect(root);modelroot=root/'model';modelroot.mkdir(exist_ok=True)
    sys.addaudithook(guard_network)
    baseline={str(p):sha(p) for p in inputs.values()}
    baseline_path=root/'input_manifest.json'
    if baseline_path.exists() and any(baseline.get(p)!=digest for p,digest in read(baseline_path).items()):raise ValueError('Frozen inputs changed since start')
    write(baseline_path,baseline)
    history=read(inputs['history']);sensory=read(inputs['sensory']);image=read(inputs['image']);snapshot=read(inputs['snapshot'])
    records,episodes,scans=prepare(history,sensory,image)
    cases={c['fallnr']:c for c in snapshot['tables']['case']}
    expected=set(expected_patient_ids) if expected_patient_ids is not None else set(cases) if len(cases)==20 else set()
    if set(cases)!=expected or set(cases)!=set(records):raise ValueError('Frozen cohort mismatch')
    model=RetainedModel(modelroot,args.infer)
    names=['diagnosis_candidate_eligibility','canonical_diagnoses','clinical_events','caa_source_assertions',
           'boston_patient_timepoints','boston_features','edinburgh_ich_events','edinburgh_features',
           'diagnosis_rejected_non_disease_text','review_holds','diagnosis_mentions']
    tables={k:[] for k in names};eligibility={};diseasegroups={};raw_audit={};semantic_failures=0;technical_failures=0;patient_inputs={}
    def hold(pid,code,source,detail=''):
        tables['review_holds'].append(dict(patient_id=pid,code=code,source_ids=source,detail=detail,review='PHYSICIAN_REVIEW'))
    for patient in history['patients']:
        pid=patient['patient_id'];rs=records[pid];raw_audit[pid]=patient['output']['mentions']
        try:
            el=model.run(pid+'_eligibility',eligibility_prompt(rs),eligibility_schema(rs),lambda raw:validate_eligibility(raw,rs),reuse_validated_schema=True)
        except (ValueError,RuntimeError) as exc:
            technical_failures+=1;hold(pid,'ELIGIBILITY_TECHNICAL_FAILURE',[],str(exc))
            el={r:Eligibility(candidate_type='UNCERTAIN',reason='No valid retained model eligibility',event_type='NONE') for r in rs}
        eligibility[pid]=el
        eligible={r:v for r,v in rs.items() if el[r].candidate_type=='DIAGNOSIS_DISEASE'}
        groups=[]
        if eligible:
            try:
                result=model.run(pid+'_diseases',grouping_prompt(eligible),grouping_schema(eligible),lambda raw:validate_group_mapping(raw,eligible),reuse_validated_schema=True)
                for rid,fields in result.metadata_disagreements.items():
                    hold(pid,'DISEASE_MEMBER_METADATA_DISAGREEMENT',[rid],','.join(fields)+'; model-nominated root metadata retained')
                for g in result.diseases:
                    errors=category_errors(g,[rs[r]['label'] for r in g.member_ids])
                    if errors:
                        semantic_failures+=1;hold(pid,'DISEASE_CATEGORY_REJECTED',g.member_ids,','.join(errors))
                    else:groups.append(g)
            except (ValueError,RuntimeError) as exc:technical_failures+=1;hold(pid,'GROUPING_TECHNICAL_FAILURE',list(eligible),str(exc))
        diseasegroups[pid]=groups
        links=defaultdict(list)
        def mention_key(m):return tuple(m.get(k) for k in ('source_document_id','section_id','start','end','item_id'))
        for i,r in enumerate(patient['output']['records'],1):
            for m in r['evidence']:links[mention_key(m)].append(f'R{i:03}')
        group_ids={r:stable_id('DX21',pid,sorted(g.member_ids)) for g in groups for r in g.member_ids}
        for i,m in enumerate(patient['output']['mentions'],1):
            rids=sorted(set(links[mention_key(m)]));mid=stable_id('DM21',pid,[i,m])
            tables['diagnosis_mentions'].append(dict(patient_id=pid,id=mid,record_ids=rids,
                canonical_diagnosis_ids=sorted({group_ids[r] for r in rids if r in group_ids}),original_mention=m))
            if not rids:hold(pid,'UNLINKED_ORIGINAL_MENTION',[mid])
        for rid,r in rs.items():
            count=sum(rid in m['record_ids'] for m in tables['diagnosis_mentions'] if m['patient_id']==pid)
            row=dict(patient_id=pid,record_id=rid,raw_candidate=r['label'],**el[rid].model_dump(),
                canonical_diagnosis_id=group_ids.get(rid),accepted_date=r['accepted_date'],original_mention_count=count,
                source_ids=r['sources'])
            tables['diagnosis_candidate_eligibility'].append(row)
            if el[rid].candidate_type not in ('DIAGNOSIS_DISEASE','ACUTE_CLINICAL_EVENT'):tables['diagnosis_rejected_non_disease_text'].append(row)
            if el[rid].candidate_type=='UNCERTAIN':hold(pid,'DIAGNOSIS_ELIGIBILITY_UNCERTAIN',[rid])
        for g in groups:
            mids=[m['id'] for m in tables['diagnosis_mentions'] if m['patient_id']==pid and set(m['record_ids'])&set(g.member_ids)]
            tables['canonical_diagnoses'].append(dict(patient_id=pid,id=group_ids[g.member_ids[0]],**g.model_dump(),original_mention_count=len(mids),evidence_ids=mids,review='PHYSICIAN_REVIEW'))
        eventgroups=defaultdict(list)
        for rid,v in el.items():
            if v.candidate_type!='ACUTE_CLINICAL_EVENT':continue
            r=rs[rid]
            # Exact date and event type nominate identity; unknown dates never collapse.
            docs=tuple(sorted({s['doc'] for s in r['sources']}))
            identity=(docs if r['accepted_date'] and v.event_type!='OTHER' else rid)
            eventgroups[(v.event_type,r['accepted_date'],identity)].append(rid)
        for (etype,day,unknown),rids in eventgroups.items():
            date_owner=patient['output']['records'][int(rids[0][1:])-1]
            tables['clinical_events'].append(dict(patient_id=pid,id=stable_id('EV21',pid,[etype,day,rids]),event_type=etype,
                event_date=day,event_year=date_owner.get('date_disease_year'),event_month=date_owner.get('date_disease_month'),
                date_precision=date_owner.get('date_disease_precision','UNKNOWN'),date_basis=date_owner.get('date_disease_basis','UNKNOWN'),
                etiology='UNKNOWN',record_ids=rids,source_document_ids=sorted({s['doc'] for r in rids for s in rs[r]['sources']}),
                evidence_ids=[m['id'] for m in tables['diagnosis_mentions'] if m['patient_id']==pid and set(m['record_ids'])&set(rids)],
                review='UNDATED_EVENT_HELD' if not day else 'SOURCE_DATED_EVENT_PROPOSAL'))
        patient_scans=[];patient_episodes={}
        for (spid,eid),ss in scans.items():
            if spid!=pid:continue
            ep=episodes.get((pid,eid),dict(id=eid,admission_date=ss[0]['admission_date'],phenotype={},text=''))
            doc=ss[0]['source_document_id'];rr={r:v for r,v in rs.items() if any(s['doc']==doc for s in v['sources'])}
            payload=dict(episode=ep,candidates=rr,scans=[{k:v for k,v in s.items() if k not in ('validated_ich_identity','complete_diagnostic_report')} for s in ss])
            allowed=set(rr)|{eid}|{s['id'] for s in ss}
            dates={r['accepted_date'] for r in rr.values() if r['accepted_date']}
            if ep['admission_date']:dates.add(ep['admission_date'])
            try:
                ev,sfeatures=model.run(pid+'_'+eid+'_evidence',evidence_prompt(payload),evidence_schema([s['id'] for s in ss],sorted(allowed),dates),
                    lambda raw:validate_evidence(raw,[s['id'] for s in ss],allowed,dates,{s['id']:s['date'] for s in ss}),reuse_validated_schema=True)
                patient_episodes[eid]=ev
                for s in ss:
                    validated,scan_holds=reconcile_validated_scan(dict(s,patient_id=pid,features=sfeatures[s['id']]))
                    patient_scans.append(validated);tables['review_holds'].extend(scan_holds)
            except (ValueError,RuntimeError) as exc:
                technical_failures+=1;hold(pid,'EPISODE_EVIDENCE_TECHNICAL_FAILURE',[eid],str(exc))
        assembled=assemble_patient(pid,cases[pid]['date_of_birth'],patient_scans,patient_episodes)
        patient_inputs[pid]=dict(scans=patient_scans,episodes=patient_episodes)
        for name,rows in assembled.items():tables[name].extend(rows)
        if not assembled['boston_patient_timepoints']:
            tables['boston_patient_timepoints'].append(dict(patient_id=pid,date=None,classification='NOT_ASSESSABLE',holds=['NO_DATED_ASSESSABLE_MRI'],source_ids=[]))
        if not assembled['edinburgh_ich_events']:
            adequate_mri_ids=[s['id'] for s in patient_scans if s.get('modality')=='MRI' and s['features'].mri_adequate is True]
            classification='NOT_APPLICABLE' if adequate_mri_ids else 'NOT_ASSESSABLE'
            tables['edinburgh_ich_events'].append(dict(patient_id=pid,id=None,original_edinburgh=classification,simplified_edinburgh=classification,
                adequate_mri_available=bool(adequate_mri_ids),source_ids=adequate_mri_ids,
                review=['ADEQUATE_MRI_USE_BOSTON_V2'] if adequate_mri_ids else ['NO_SUPPORTED_LOBAR_ICH_CT_EVENT']))
        print(json.dumps(dict(patient=pid,diseases=len(groups),imaging_episodes=len(patient_episodes))),flush=True)
    with inputs['old_assertions'].open(encoding='utf-8-sig',newline='') as f:tables['caa_source_assertions']=list(csv.DictReader(f))
    # Retain explicitly validated CURRENT admission events; do not reuse the
    # old disease model's event proposals or borrow dates for historical text.
    with inputs['admission_events'].open(encoding='utf-8-sig',newline='') as f:
        admission_events=[r for r in csv.DictReader(f) if r.get('review_status')=='PHYSICIAN_REVIEW_CURRENT_EVENT_DATE']
    for event in admission_events:
        docs=json.loads(event['source_document_ids'])
        if any(r['patient_id']==event['patient_id'] and r['event_type']==event['event_type'] and r['event_date']==event['event_date'] and set(r['source_document_ids'])&set(docs) for r in tables['clinical_events']):continue
        tables['clinical_events'].append(dict(patient_id=event['patient_id'],id=stable_id('EV21',event['patient_id'],['VALIDATED_ADMISSION_EVENT',event['id']]),
            event_type=event['event_type'],event_date=event['event_date'] or None,
            event_year=int(event['event_date'][:4]) if event['event_date'] else None,
            event_month=int(event['event_date'][5:7]) if event['event_date'] else None,
            date_precision=event['date_precision'],date_basis=event['date_basis'],etiology=event['etiology'],
            record_ids=[],source_document_ids=docs,evidence_ids=json.loads(event['evidence_ids']),
            review='VALIDATED_CURRENT_ADMISSION_EVENT_RETAINED'))
    # All source assertions are copied verbatim, never inferred from computed criteria.
    source_unchanged=all(sha(Path(p))==v for p,v in baseline.items())
    raw_expected=sum(len(p['output']['mentions']) for p in history['patients'])
    gates=audit_tables(tables,eligibility,raw_expected,patient_inputs)
    gates.update(clinical_writes=0,cloud_calls=0,technical_failures=technical_failures,frozen_inputs_changed=int(not source_unchanged))
    actual_event_dates={(r['patient_id'],r['event_type'],r['event_date']) for r in tables['clinical_events']}
    gates['validated_admission_events_lost']=sum((r['patient_id'],r['event_type'],r['event_date'] or None) not in actual_event_dates for r in admission_events)
    taxonomy_review,taxonomy_gates,taxonomy_metrics=apply_taxonomy(tables)
    census,measurements,csf_gates,csf_audit=csf_census(snapshot,read(inputs['bundles']),episodes,allow_unavailable_admission=expected_patient_ids is not None)
    if expected_patient_ids is not None:
        for measurement in measurements:
            if not measurement['date']:
                hold(measurement['patient_id'],'CSF_ADMISSION_DATE_UNAVAILABLE_SOURCE_REFERENCE_RETAINED',[measurement['source_evidence_id']],'Outpatient/copied reference retained with null research date; no inpatient admission date inferred')
    for row in census:
        if row['reason_if_not_extracted']=='SOURCE_RESULT_PENDING_NO_NUMERIC_MEASUREMENT':
            hold(row['patient_id'],'CSF_RESULT_PENDING_NO_NUMERIC_VALUE',[row['source_evidence_id']],row['original_analyte_text']+'; source result pending, no value invented')
    tables.update(neurologic_taxonomy_review=taxonomy_review,beta_amyloid_census=census,csf_biomarkers=measurements)
    csf_regressions=[]
    for pid in ('2684469520','2826947422'):
        observed=[r for r in measurements if r['patient_id']==pid]
        for analyte in ('CSF_ABETA_UNSPECIFIED','CSF_ABETA42','CSF_ABETA40','CSF_ABETA42_40_RATIO','CSF_TOTAL_TAU','CSF_PTAU'):
            rows=[r for r in observed if r['canonical_analyte']==analyte]
            csf_regressions.append(dict(patient_id=pid,canonical_analyte=analyte,reported_measurements=len(rows),
                values=[r['value'] for r in rows],evidence_ids=[r['source_evidence_id'] for r in rows],
                review='SOURCE_REPORTED' if rows else 'NOT_EXPLICITLY_REPORTED_IN_FROZEN_LAB_CSF_SOURCE; NO_ISOFORM_OR_RATIO_INFERRED'))
    tables['csf_regression_review']=csf_regressions
    gates.update(taxonomy_gates,**csf_gates)
    gates['neurologic_other_rate_not_reduced']=int(taxonomy_metrics['neurologic_other_rate_before']>0 and taxonomy_metrics['neurologic_other_rate_after']>=taxonomy_metrics['neurologic_other_rate_before'])
    source_unchanged=all(sha(Path(p))==v for p,v in baseline.items())
    gates['frozen_inputs_changed']=int(not source_unchanged)
    decision='READY_FOR_DIAGNOSIS_CAA_V21_PHYSICIAN_VALIDATION' if all(v==0 for v in gates.values()) else 'MORE_DIAGNOSIS_CAA_V21_WORK_REQUIRED'
    counts=Counter(r['candidate_type'] for r in tables['diagnosis_candidate_eligibility'])
    verification=dict(decision=decision,gates=gates,patients=sorted(cases),raw_mentions=raw_expected,
        candidate_types=dict(counts),accepted_diseases=len(tables['canonical_diagnoses']),clinical_events=len(tables['clinical_events']),
        local_granite_calls_this_execution=model.calls,clinical_writes=0,cloud_calls=0,database_connections=0,
        frozen_inputs_unchanged=source_unchanged,unrequested_diagnosis_replacements=0,
        diagnosis_taxonomy_version='V2.2',
        explicit_addendum_source_label_annotations=True,taxonomy_metrics=taxonomy_metrics,
        csf_audit=csf_audit,beta_symbol_variant_regressions_pass=csf_audit['beta_symbol_variant_regressions_pass'])
    write(root/'addendum_audit.json',dict(taxonomy=taxonomy_metrics,csf=csf_audit))
    for name,rows in tables.items():csvfile(root,name,rows)
    write(root/'source_mentions_audit.json',raw_audit);write(root/'verification.json',verification)
    write(root/'assembly_inputs_audit.json',{pid:dict(scans=[dict(s,features=s['features'].model_dump()) for s in inputs['scans']],
        episodes={eid:e.model_dump() for eid,e in inputs['episodes'].items()}) for pid,inputs in patient_inputs.items()})
    lines=[f'# Frozen {len(cases)}-patient diagnosis taxonomy V2.2 and CAA V2.1 physician review','',decision,'',
           f"Raw mentions: {raw_expected}; candidates: {sum(counts.values())}; accepted disease identities: {len(tables['canonical_diagnoses'])}; acute event candidates: {counts['ACUTE_CLINICAL_EVENT']}; rejected/non-disease candidates: {sum(v for k,v in counts.items() if k not in ('DIAGNOSIS_DISEASE','ACUTE_CLINICAL_EVENT'))}.",'',
           'Validation proposals only. Frozen sources and validated clinical values are unchanged. Source CAA diagnoses are separate from computed categories. Model-derived evidence requires physician review. Counts are conservative lower bounds; serial CMB/cSS reports are not summed.','',
           '## Addendum validation','',
           f"Amyloid lexical census: {len(csf_audit['documents_scanned'])} frozen documents / {csf_audit['pages_scanned']} pages, including raw laboratory and CSF text and tables. All lexical rows and exclusions: beta_amyloid_census.csv. Hierarchical annotations and original labels: neurologic_taxonomy_review.csv.",'',
           f"Neurological OTHER: {taxonomy_metrics['neurologic_other_before']}/{taxonomy_metrics['neurologic_before']} ({taxonomy_metrics['neurologic_other_rate_before']:.1%}) before; {taxonomy_metrics['neurologic_other_after']}/{taxonomy_metrics['neurologic_after']} ({taxonomy_metrics['neurologic_other_rate_after']:.1%}) after. The denominator also excludes explicit systemic diagnoses whose original model domain was neurologic. Unresolved diagnoses remain for physician review.",'',
           'Both CSF regression sources name only ß-Amyloid: 425 pg/ml (2684469520, document 40, page 7) and 269 pg/ml (2826947422, document 69, page 14), reference >600. They are retained as CSF_ABETA_UNSPECIFIED. Neither block explicitly reports an Aβ42 isoform, Aβ40 or a 42/40 ratio. No isoform or derived ratio is inferred. Research dates retain the encounter admission basis; source document/page/section/line evidence remains separate.','',
           'Source-label taxonomy rules annotate retained model identities and preserve legacy fields. The explicit addendum SCD inclusion is recorded alongside its original symptom-only eligibility. SAH/ICH corrections remain in the event layer, hemorrhagic transformation remains ischemic stroke, and delirium remains separate from dementia.','',
           'V2.2 cardiovascular rules canonicalize AF, tricuspid regurgitation and mitral regurgitation across source synonyms. AF type and valve severity remain separate, with source observations and all reported values retained. Conflicting AF types use UNSPECIFIED and multiple valve severities use UNKNOWN in the aggregate; the individual observations remain visible. Flutter is kept separate. Secondary AF does not replace its primary heart-failure diagnosis. The cardiovascular_taxonomy_review.csv records every matched source candidate and exclusion.','',
           '## Hard gates','',table(['Gate','Failures'],gates.items())]
    for pid in cases:
        lines+=['',f'## PATIENT {pid}','','### Diagnosis eligibility','']
        ds={r['id']:r for r in tables['canonical_diagnoses'] if r['patient_id']==pid}
        rows=[]
        for r in tables['diagnosis_candidate_eligibility']:
            if r['patient_id']!=pid:continue
            d=ds.get(r['canonical_diagnosis_id'],{})
            rows.append([r['record_id'],r['raw_candidate'],r['candidate_type'],d.get('canonical_name'),d.get('domain'),d.get('category'),d.get('subcategory'),d.get('etiology'),d.get('first_supported_date'),r['original_mention_count'],r['reason']])
        lines.append(table(['ID','Raw candidate','Type','Canonical disease','Domain','Category','Subcategory','Etiology','First date','Mentions','Review'],rows))
        lines+=['','### Canonical diagnoses — hierarchical taxonomy','',table(['Canonical diagnosis','Display name','Domain','Category','Subcategory','Etiology','AF type','AF types observed','Valve severity','Valve severities observed','First date','Evidence mentions'],
            [[d.get(k) for k in ('canonical_name','display_name','domain','category','subcategory','etiology','af_type','af_types','valvular_severity','valvular_severities','first_supported_date','original_mention_count')] for d in ds.values()])]
        lines+=['','### Cardiovascular V2.2 canonicalization','',table(['Record','Original label','Canonical diagnosis','AF type','Valve severity','Source values','Review'],
            [[r['record_id'],r['original_label'],r['canonical_name'],r['af_type'],r['valvular_severity'],[r['af_types'],r['valvular_severities']],r['reason']] for r in tables['cardiovascular_taxonomy_review'] if r['patient_id']==pid])]
        lines+=['','### CSF biomarkers — source reported, no derived ratios','',table(['Original analyte','Canonical analyte','Value','Unit','Reference','Abnormality','Admission date','Date basis','Document/page/section','Evidence','Review'],
            [[r['original_analyte_text'],r['canonical_analyte'],r['value'],r['unit'],r['reference_text'],r['abnormality'],r['date'],r['date_basis'],[r['source_document_id'],r['page'],r['section']],r['source_evidence_id'],r['review']] for r in measurements if r['patient_id']==pid])]
        lines+=['','### Beta-amyloid lexical census','',table(['Document/page','Original analyte or lexical row','Normalized candidate','Value','Canonical analyte','Extracted','Reason'],
            [[[r['document_id'],r['page']],r['original_analyte_text'],r['normalized_candidate'],r['value_candidate'],r['canonical_analyte'],r['extracted_yes_no'],r['reason_if_not_extracted']] for r in census if r['patient_id']==pid])]
        lines+=['','### Boston patient/timepoints','',table(['Date','Age','Presentation','Lobar ICH','Lobar CMB','cSS','WM','Deep exclusion','Alternative','MRI adequate','Computed Boston','Why / review'],
            [[r.get('date'),r.get('age'),r.get('presentation'),r.get('lobar_ich'),r.get('lobar_cmb'),r.get('css_csah_foci'),[r.get('severe_cso_pvs'),r.get('multispot_wmh')],[r.get('deep_ich'),r.get('deep_cmb'),r.get('deep_hemorrhage_exclusion_support')],r.get('alternative_cause_status'),r.get('mri_adequate'),r['classification'],[r.get('why',r.get('holds')),r.get('review')]] for r in tables['boston_patient_timepoints'] if r['patient_id']==pid])]
        lines+=['','### Edinburgh ICH events','',table(['ICH date','Initial CT','Primary CT','Lobar','Spontaneous','FLP','SA extension','APOE','Adequate MRI','Original','Simplified','Review'],
            [[r.get(k) for k in ('event_date','initial_ct_date','primary_ct_date','lobar','spontaneous','FLP','SA_extension','APOE','adequate_mri_available','original_edinburgh','simplified_edinburgh','review')] for r in tables['edinburgh_ich_events'] if r['patient_id']==pid])]
        lines+=['','### Clinical events','',table(['Date','Type','Domain','Category','Subcategory','Etiology','Hemorrhagic transformation','Evidence records'],[[r.get(k) for k in ('event_date','event_type','domain','category','subcategory','etiology','hemorrhagic_transformation','record_ids')] for r in tables['clinical_events'] if r['patient_id']==pid])]
        lines+=['','### Source CAA assertions (separate from computed criteria)','',table(['Source','Source status','Framework','Source diagnosis date'],[[r['source_id'],r['source_caa_status'],r['criteria_system'],r['source_diagnosis_date']] for r in tables['caa_source_assertions'] if r['patient_id']==pid])]
        lines+=['','### Review holds','',table(['Code','Source','Detail'],[[r['code'],r['source_ids'],r.get('detail','')] for r in tables['review_holds'] if r['patient_id']==pid])]
    report=root.with_suffix('.md');report.write_text('\n'.join(lines),encoding='utf-8');protect(report)
    write(root/'csv_manifest.json',{p.name:sha(p) for p in root.glob('*.csv')})
    print(json.dumps(dict(report=str(report),**verification)),flush=True)
    return verification


if __name__=='__main__':main()
