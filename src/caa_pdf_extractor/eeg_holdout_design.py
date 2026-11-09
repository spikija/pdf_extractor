"""Source-only design of a fresh EEG holdout. Never invokes EEG extraction."""
import ast
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from .workbench_store import ROOT,STABLE_ROOT,read,atomic,sha
from .module_artifacts import digest

LABEL=r'(?:(?:Routine|Video|Langzeit|Schlafentzugs|Schlaf|Ambulant|MOBILE)[- ]*)?EEG(?:[- ]Monitoring)?|Elektroen[cz]ephalo(?:gramm|gra(?:ph|f)ie)|(?:Intensives\s+)?Epilepsiemonitoring'
HEADER=re.compile(r'(?im)^[ \t]*(?:[-•]\s*)?(?:'+LABEL+r')\b[^\n]{0,180}')
MENTION=re.compile(r'\b(?:'+LABEL+r')\b',re.I)
PLAN=re.compile(r'empfohlen|geplant|angefordert|vorgesehen|soll(?:te)?\b|wäre\b|nicht\s+durchgeführt|Befund\s+(?:nicht\s+vorliegend|ausständig)',re.I)
RESULT=re.compile(r'(?:abnorm\w*|pathologisch\w*|normal\w*|unauffällig\w*)\s+EEG|EEG\s+(?:in\s+den\s+)?Grenzen\s+der\s+Norm|Normalbefund|Grundaktivität|Basisrhythmus|Hirnfunktionsstörung|epilep(?:tiform|sietypisch)\w*\s+Pot[ei]n[zt]ial|Theta[-/ ]|Delta[-/ ]',re.I)
DATE=re.compile(r'(?<!\d)\d{1,2}\.\s*\d{1,2}\.\s*(?:19|20)\d{2}(?!\d)')
PEER=re.compile(r'(?im)^\s*(?:24[- ]STUNDEN[- ]EKG|Holter[- ]EKG|(?:CT|MRT|MRI|Neurosonologie|Computertomograf\w*|Kernspintomograf\w*|Laborbefund|Internistisch\w*\s+Befund|Therapiebericht)\b)')
SIGNALS={
 'normal_signal':r'Normalbefund|normal\w*\s+EEG|Grenzen\s+der\s+Norm|weitgehend\s+normal',
 'diffuse_signal':r'diffus|generalisiert',
 'focal_signal':r'lokal\w*\s+(?:Hirn)?funktionsstörung|fokal|Herdbefund|Theta[^.;]{0,50}(?:links|rechts)',
 'bilateral_asymmetry_signal':r'links\s*>\s*rechts|rechts\s*>\s*links|mehr\s+als\s+(?:links|rechts)|[Ll]inksakzent|[Rr]echtsakzent',
 'epileptiform_terminology_signal':r'spikes?|sharp[- ]waves?|epileptiform|epilepsietypisch',
 'negative_epileptiform_signal':r'kein\w*\s+(?:eindeutig\w*\s+)?(?:epileptiform|epilepsietypisch)|keine\s+Zeichen\s+erhöhter',
 'rhythmic_signal':r'\bFIRDA\b|\bGRDA\b|\bLRDA\b|rhythmisch\w*[^.;]{0,30}Delta',
 'artifact_signal':r'Artefakt|soweit[^.;]{0,70}beurteilbar|nicht\s+beurteilbar',
 'epilepsy_signal':r'Epilepsie|Anfall|Anfälle|Status\s+epilepticus',
 'stroke_signal':r'Infarkt|Schlaganfall|ischämisch',
 'ICH_signal':r'intra[cz]erebral\w*\s+(?:Blutung|Hämorrhagie)|Hirnblutung|\bICH\b',
 'CAA_signal':r'Amyloidangiopathie|\bCAA\b|Amyloid\s+spells',
 'encephalopathy_signal':r'En[cz]ephalopath|Demenz|dementiell',
}


def csv_file(path,rows,fields):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def verify_freeze(directory):
    freeze=read(Path(directory)/'eeg_v1_1_freeze.json')
    if any(sha(path)!=expected for path,expected in freeze['files'].items()):raise ValueError('Frozen EEG v1.1 code changed; holdout must stop')
    if digest(freeze['files'])!=freeze['code_hash']:raise ValueError('Invalid EEG freeze fingerprint')
    return freeze


def exclusions(directory):
    directory=Path(directory).resolve();verify_freeze(directory);reasons=defaultdict(set)
    def add(patient,reason):
        if isinstance(patient,str) and re.fullmatch(r'[0-9]{1,32}',patient) and (ROOT/'pdf_db'/patient).is_dir():reasons[patient].add(reason)
    registry=STABLE_ROOT/'eeg_validation_cohort.json'
    for patient in read(registry)['patient_ids']:add(patient,'EEG_DEVELOPMENT_REGISTRY:'+str(registry.relative_to(ROOT)))
    fixture=ROOT/'src/caa_pdf_extractor/eeg_regression_v11.py'
    tree=ast.parse(fixture.read_text('utf-8'))
    for node in ast.walk(tree):
        if isinstance(node,ast.keyword) and node.arg=='patient_id' and isinstance(node.value,ast.Constant):add(node.value.value,'EEG_V1_1_PHYSICIAN_REGRESSION_FIXTURE')
    for path in STABLE_ROOT.glob('*/module_manifest.json'):
        manifest=read(path)
        for module,group in manifest.get('modules',{}).items():
            if module.lower()!='eeg':continue
            for entry in group.get('versions',{}).values():
                version=str(entry.get('module_version',entry.get('version','')))
                if version.startswith(('1.0','1.1')):add(manifest['patient_id'],'PREVIOUS_EEG_ARTIFACT:'+version)
    # Include materialized regression fixtures and execution evidence, even if a
    # patient was subsequently removed from the registry or has no stable pointer.
    for folder in (ROOT/'data/debug').glob('*eeg*'):
        if folder.resolve()==directory:continue
        for path in folder.rglob('regression_results.json'):
            for row in read(path).get('checks',[]):add(row.get('patient_id'),'MATERIALIZED_EEG_REGRESSION:'+str(path.relative_to(ROOT)))
        for path in folder.rglob('execution.json'):
            value=read(path)
            if (path.parent/'eeg_exams.json').exists():add(value.get('patient_id'),'PREVIOUS_EEG_WORKER_RUN:'+str(path.relative_to(ROOT)))
        for path in folder.rglob('module_metadata.json'):
            value=read(path)
            if value.get('module_name','').lower()=='eeg' and str(value.get('module_version','')).startswith(('1.0','1.1')):
                add(value.get('execution_evidence',{}).get('patient_id'),'PREVIOUS_EEG_RUN_METADATA:'+str(path.relative_to(ROOT)))
    for path in (ROOT/'data/review').rglob('eeg_review.json'):
        for record in read(path).values():add(record.get('patient_id'),'EXISTING_PHYSICIAN_EEG_REVIEW')
    rows=[dict(patient_id=p,exclusion_reason='; '.join(sorted(why))) for p,why in sorted(reasons.items())]
    csv_file(directory/'eeg_v1_1_holdout_exclusions.csv',rows,['patient_id','exclusion_reason'])
    atomic(directory/'exclusions.json',{p:sorted(why) for p,why in sorted(reasons.items())})
    verify_freeze(directory)
    return set(reasons)


def screen_source_text(text):
    """Performance evidence only. Does not extract or classify EEG findings."""
    windows=[];heads=list(HEADER.finditer(text));planned=False
    for i,head in enumerate(heads):
        if re.search(r'^EEG[- ](?:Ende|Beginn|Dauer|Muster)',head[0].strip(),re.I):continue
        end=heads[i+1].start() if i+1<len(heads) else len(text)
        end=min(end,head.end()+2200)
        boundary=PEER.search(text,head.end(),end)
        if boundary:end=boundary.start()
        header=head[0].strip();body=text[head.end():end].strip()
        if PLAN.search(header) or re.search(r'^(?:\s*\W)*(?:nicht\s+durchgeführt|Befund\s+(?:ausständig|nicht\s+vorliegend))',body,re.I):planned=True;continue
        date=DATE.search(header)
        if RESULT.search(header+'\n'+body) and (date or re.search(r'Befund|Monitoring|Epilepsiemonitoring',header,re.I)):
            windows.append(dict(start=head.start(),header=header,evidence=body[:1700],date_signal=date[0] if date else None,method='DATED_REPORT_OR_EXPLICIT_REPORT_HEADING'))
    # A completed narrative is eligible only with an actual past/result verb.
    prose=re.compile(r'\b(?:EEG|Elektroen[cz]ephalo\w*)\b[^\n.!?]{0,100}(?:zeigte|ergab|erfolgte|wurde[^\n.!?]{0,50}(?:durchgeführt|abgeleitet))[^\n.!?]{0,180}',re.I)
    if not windows:
        for match in prose.finditer(text):
            if PLAN.search(match[0]) or re.search(r'kein\s+EEG',match[0],re.I):planned=True;continue
            windows.append(dict(start=match.start(),header=match[0],evidence=text[match.start():match.end()+300],date_signal=None,method='COMPLETED_NARRATIVE'))
    status='EEG_PERFORMED' if windows else 'EEG_PLANNED' if planned or MENTION.search(text) and PLAN.search(text) else 'EEG_REFERENCED' if MENTION.search(text) else 'EEG_UNKNOWN'
    return status,windows


def screen(directory):
    import hashlib
    import pymupdf
    directory=Path(directory).resolve();excluded=set(read(directory/'exclusions.json'));verify_freeze(directory)
    protocol=dict(selection_inputs='Native PDF source only; never EEG v1.1 output',target_patients=12,target_exam_range=[20,40],
        eligibility='Actually performed EEG with explicit source evidence; planned/reference-only patients ineligible',
        selection_seed='EEG11_HOLDOUT_SOURCE_ONLY_20261029',strategy='Source-feature coverage with soft preference for 2-3 source-estimated EEG dates per patient; deterministic hash tie-break',
        frozen_before_screening=True,screening_signals_are_not_final_findings=True)
    if (directory/'screening_protocol.json').exists() and read(directory/'screening_protocol.json')!=protocol:raise ValueError('Screening protocol changed')
    atomic(directory/'screening_protocol.json',protocol)
    development_hashes=set()
    for patient in excluded:
        manifest=read(STABLE_ROOT/patient/'module_manifest.json') if (STABLE_ROOT/patient/'module_manifest.json').exists() else {}
        for entry in manifest.get('modules',{}).get('eeg',{}).get('versions',{}).values():
            if entry.get('metadata_path'):development_hashes.update(read(entry['metadata_path']).get('source_pdf_hashes',{}).values())
    documents=[];patients=[];duplicate_patients=set()
    folders=[p for p in (ROOT/'pdf_db').iterdir() if p.is_dir() and p.name not in excluded]
    for n,folder in enumerate(sorted(folders),1):
        alltext=[];eegtext=[];performed=[];pages_total=0;files=sorted(folder.glob('*.pdf'));pdf_hashes={}
        for path in files:
            try:
                raw=path.read_bytes();checksum=hashlib.sha256(raw).hexdigest();pdf_hashes[path.name]=checksum
                if checksum in development_hashes:duplicate_patients.add(folder.name)
                with pymupdf.open(stream=raw,filetype='pdf') as pdf:pages=[page.get_text('text',sort=True) for page in pdf]
                text='\n'.join(pages);pages_total+=len(pages);alltext.append(text)
                status,windows=screen_source_text(text)
                page_ends=[];page_offset=0
                for page in pages:page_offset+=len(page)+1;page_ends.append(page_offset)
                for window in windows:
                    window['page']=next((i+1 for i,end in enumerate(page_ends) if window['start']<end),len(pages))
                if status=='EEG_PERFORMED':performed.extend(windows);eegtext.extend(w['header']+'\n'+w['evidence'] for w in windows)
                if status!='EEG_UNKNOWN':documents.append(dict(patient_id=folder.name,filename=path.name,sha256=checksum,page_count=len(pages),screening_status=status,source_windows=windows))
            except Exception as exc:documents.append(dict(patient_id=folder.name,filename=path.name,screening_status='EEG_UNKNOWN',error=type(exc).__name__,source_windows=[]))
        context='\n'.join(alltext);reports='\n'.join(eegtext)
        features={name:bool(re.search(pattern,context if name in ('epilepsy_signal','stroke_signal','ICH_signal','CAA_signal','encephalopathy_signal') else reports,re.I)) for name,pattern in SIGNALS.items()}
        dates={w['date_signal'] for w in performed if w['date_signal']};estimate=len(dates)+sum(w['date_signal'] is None for w in performed)
        form=bool(re.search(r'\[[ xX]\]|☐|☒|☑',reports)) or sum(bool(re.search(label,reports,re.I)) for label in ('Frequenzstabilität','Modulation','Amplitude','Blockierung','Grundrhythmus'))>=3
        patients.append(dict(patient_id=folder.name,screening_status='EEG_PERFORMED' if performed else 'EEG_UNKNOWN',EEG_exam_estimate=estimate,document_count=len(files),page_count=pages_total,
            source_format_signal='STRUCTURED_FORM_SIGNAL' if form else 'FREE_TEXT_REPORT' if performed else 'UNKNOWN',multiple_EEG_signal=estimate>1,
            **features,duplicate_development_PDF=folder.name in duplicate_patients,pdf_hashes=pdf_hashes))
        if n%50==0:print('SOURCE_SCREENED',n,flush=True)
    atomic(directory/'holdout_screening_documents.json',documents);atomic(directory/'holdout_screening_patients.json',patients)
    verify_freeze(directory)
    print('SOURCE_SCREENING_COMPLETE',len(patients),'remaining patients;',sum(p['screening_status']=='EEG_PERFORMED' for p in patients),'with performed-source signals;',len(duplicate_patients),'possible source-PDF overlap exclusions',flush=True)
    return patients,documents


def select(directory):
    """Freeze a source-only greedy coverage selection, before any worker runs."""
    import hashlib
    from datetime import datetime,timezone
    directory=Path(directory).resolve();freeze=verify_freeze(directory)
    registry=STABLE_ROOT/'eeg_v1_1_holdout_cohort.json'
    if registry.exists() or (directory/'published_candidates.json').exists():raise ValueError('Holdout already frozen or run')
    protocol=read(directory/'screening_protocol.json');excluded=set(read(directory/'exclusions.json'))
    rows=read(directory/'holdout_screening_patients.json')
    features=[k for k in rows[0] if k.endswith('_signal') and k not in ('source_format_signal','multiple_EEG_signal')]
    pool=[r for r in rows if r['screening_status']=='EEG_PERFORMED' and r['patient_id'] not in excluded and not r['duplicate_development_PDF']]
    selected=[];covered=set()
    while len(selected)<protocol['target_patients']:
        def flags(r):return {k for k in features if r[k]}|{r['source_format_signal']}
        def score(r):return (len(flags(r)-covered),-abs(r['EEG_exam_estimate']-2.5),hashlib.sha256((protocol['selection_seed']+r['patient_id']).encode()).hexdigest())
        row=max(pool,key=score);pool.remove(row)
        selected.append({**row,'selection_reason':'SOURCE_ONLY_GREEDY_COVERAGE; new signals='+','.join(sorted(flags(row)-covered))+'; 2-3 exam soft preference; frozen hash tie-break'})
        covered|=flags(row)
    fields='patient_id EEG_exam_estimate document_count page_count selection_reason source_format_signal artifact_signal epilepsy_signal stroke_signal ICH_signal CAA_signal encephalopathy_signal multiple_EEG_signal'.split()
    csv_file(directory/'eeg_v1_1_holdout_selection.csv',[{k:r[k] for k in fields} for r in selected],fields)
    atomic(directory/'selected_source_indicators.json',selected)
    atomic(directory/'selected_patients.json',[r['patient_id'] for r in selected])
    value=dict(patient_ids=[r['patient_id'] for r in selected],module_version='1.1',phase='HOLDOUT',frozen_at=datetime.now(timezone.utc).isoformat(),
        code_hash=freeze['code_hash'],output_directory=str(directory),selection_sha256=sha(directory/'eeg_v1_1_holdout_selection.csv'),
        exclusions_sha256=sha(directory/'eeg_v1_1_holdout_exclusions.csv'),excluded_patient_ids=sorted(excluded),
        source_screening_sha256=sha(directory/'holdout_screening_patients.json'),selection_before_extraction=True,
        source_estimated_exams=sum(r['EEG_exam_estimate'] for r in selected),review_status='UNREVIEWED',auto_promotion=False)
    atomic(registry,value);atomic(directory/'frozen_cohort.json',value)
    return value


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--screen',action='store_true');parser.add_argument('--select',action='store_true')
    args=parser.parse_args()
    if args.select:print(json.dumps(select(args.directory),indent=2))
    elif args.screen:screen(args.directory)
    else:print('Recorded excluded development patients:',len(exclusions(args.directory)))
