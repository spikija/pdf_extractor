"""Deterministic EEG study discovery, explicit tri-state findings and verified commits."""
from datetime import datetime,timezone,date
import json,re
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select,text
from ..database import SessionLocal
from ..database.models import EEGExamination,ExtractionRun
from ..preparation import build_patient_bundle
from .case_resolver import resolve_case_id
from .imaging_reports import document_text,parse_date,DATE
from .radiology_artifacts import artifact_directory,digest,VerifiedExtractionError

VERSION='eeg_source_tri_state_v1'
LABEL=r'(?:MOBILE\s+)?(?:(?:Routine|Langzeit|Video)[- ]EEG(?:[- ]Monitoring)?|vEEG|EEG|Elektroen[cz]ephalo(?:grafie|graphie))'
HEADER=re.compile(r'^[ \t]*(?:'+LABEL+r')\b[^\n]*',re.I|re.M)
PEER=re.compile(r'^[ \t]*(?:CT|CTA|MRT|MRI|MRA|Angiografie|DSA|EKG|EMG/NLG|Evozierte\s+Potenziale|FEES[- ]Befund|Kernspintomogra\w*|Computertomogra\w*|PET[- /]?CT|Röntgen|Neuroson\w*|Neuropsycholog\w*|Internistisch\w*\s+(?:Befund|Konsil)|ECHOKARDIO\w*|Ultraschall|Sonogra\w*|SPECT|Laborwerte|Entlassungsmedikation|Medikation\s*:|Mit freundlichen|Zusammenfassung\s+des\s+Aufenthalts)\b',re.I|re.M)
PATTERNS={
 'focal_slowing':r'fokal\w*\s+Verlangsamung|(?:lokal|regional|fokal)\w*\s+(?:Hirn)?funktionsstörung|verlangsam\w*\s+(?:Grundaktivität|Basisrhythmus)[^.!?;\n]{0,55}(?:links|rechts)|(?:Delta|Theta)[- ](?:(?:Theta|Delta)[- ])?(?:Aktivität|Gruppen|Wellen)[^.!?\n]{0,90}(?:links|rechts|bifrontal|bitemporal)',
 'epileptiform_discharges':r'epilepsietypisch\w*\s+(?:Potential\w*|Potenzial\w*)|epileptiform\w*\s+(?:Potential\w*|Potenzial\w*|Entladung\w*|discharges?)|spike[- ](?:wave|and[- ]wave)|(?<!broad-)\bsharp[- ]waves?\b',
 'rhythmic_periodic_pattern':r'rhythmisch[- ]periodisch\w*\s+Muster|periodisch\w*\s+Entladung\w*|pseudoperiodisch\w*\s+Ablauf|\b(?:LPD|GPD|LRDA|FIRDA|TIRDA)s?\b',
 'electrographic_seizure':r'elektrographisch\w*\s+(?:Anfall|Anfälle|Anfallsaktivität)|elektrografisch\w*\s+(?:Anfall|Anfälle)|electrographic\s+seizure\w*'}
BOOL_FIELDS={k:'eeg_'+k for k in PATTERNS}

def discover(bundle):
    result=[]
    for doc in bundle.documents:
        raw=document_text(doc);headers=[]
        for m in HEADER.finditer(raw):
            h=m.group().strip()
            if not (re.search(DATE,h) or re.search(r'Befund|Monitoring|angefordert|geplant|nicht durchgeführt',h,re.I) or re.fullmatch(LABEL+r'\s*:?\s*',h,re.I)):continue
            headers.append(m)
        for index,m in enumerate(headers):
            end=headers[index+1].start() if index+1<len(headers) else len(raw)
            peer=PEER.search(raw,m.end(),end)
            if peer:end=peer.start()
            left=m.end()
            while left<end and raw[left].isspace():left+=1
            while end>left and raw[end-1].isspace():end-=1
            report=raw[left:end];header=m.group().strip()
            dates={parse_date(x.group()) for x in re.finditer(DATE,re.split(r'Vergleich|Voruntersuch',header,flags=re.I)[0])}-{None}
            eeg_date=next(iter(dates)) if len(dates)==1 else None
            status='not_performed' if re.search(r'nicht\s+(?:durchgeführt|erfolgt)|abgesagt|not\s+performed',header+'\n'+report,re.I) else 'unknown' if re.search(r'geplant|angefordert|empfohlen',header,re.I) or not report.strip() else 'performed'
            if status=='performed' and re.search(r'geplant|angefordert|empfohlen|vorgesehen',report,re.I) and not any(re.search(p,report,re.I) for p in [*PATTERNS.values(),r'Normalbefund|Grenzen\s+der\s+Norm|unauffällig\w*\s+EEG|abnorm\w*\s+EEG|pathologisch\w*\s+EEG|Basisrhythmus|Grundaktivität']):status='unknown'
            proc=f'doc{doc.source_document_id}_eeg_'+digest({'document':doc.source_document_id,'start':m.start(),'header':header})[:16]
            result.append({'patient_id':bundle.patient_id,'source_document_id':doc.source_document_id,'procedure_id':proc,
                'eeg_date':eeg_date,'eeg_status':status,'eeg_report_text':report,'header':header,
                'source_offsets':{'coordinate_system':'document_text_v1','start':left,'end':end},
                'header_offsets':{'start':m.start(),'end':m.end()}})
    return result

def state(q,m):
    prefix=q[:m.start()];suffix=q[m.end():]
    # Stop at an independent neighboring finding; do not propagate its denial.
    prefix=re.split(r',\s*(?=keine?\b)|;|\.\s+',prefix,flags=re.I)[-1]
    if re.search(r'fraglich|Verdacht|möglich|kein\w*\s+sicher|nicht\s+auszuschließen',prefix+suffix,re.I) or '?' in q:return None
    if re.search(r'kein\w*|ohne|keinerlei|no\b',prefix,re.I) or re.search(r'nicht\s+(?:nachweisbar|zur\s+Darstellung)',suffix,re.I):return False
    return True

def analysis_clauses(text):
    """Analyze across page furniture, retaining contiguous original quotations."""
    furniture=re.compile(r'(?im)^\s*Universitätsklinikum[^\n]*\n(?:[^\n]*\n){0,7}?[^\n]*geb\.:\s*[^\n]*Seite\s+\d+[^\n]*')
    spans=[(m.start(),m.end()) for m in furniture.finditer(text)]
    view=[];offsets=[]
    for m in re.finditer(r'\S+',text):
        if any(a<=m.start()<b for a,b in spans):continue
        if view:view.append(' ');offsets.append(m.start())
        view.extend(m.group());offsets.extend(range(m.start(),m.end()))
    clean=''.join(view)
    for m in re.finditer(r'.+?(?:;|(?<=[.!])\s+(?=[A-Z])|,\s*(?=keine?\b)|$)',clean):
        if m.group().strip():yield m.group(),text[offsets[m.start()]:offsets[m.end()-1]+1]


def interpret(study):
    text=study['eeg_report_text'];evidence={};uncertain=[]
    values={v:None for v in BOOL_FIELDS.values()};present=[];sides=[]
    if study['eeg_status']=='performed':
        # Preserve original report while analyzing bounded sentences/clauses.
        pairs=list(analysis_clauses(text));clauses=[q for q,_ in pairs]
        original=dict(pairs)
        for name,pattern in PATTERNS.items():
            hits=[]
            for q in clauses:
                for m in re.finditer(pattern,q,re.I):
                    value=state(q,m)
                    if value is None:uncertain.append({'concept':name,'evidence_text':original[q]});continue
                    hits.append(value);evidence.setdefault(name,[]).append({'value':value,'evidence_text':original[q]})
                    if value:
                        if re.search(r'links',q,re.I):sides.append('left')
                        if re.search(r'rechts',q,re.I):sides.append('right')
                        if re.search(r'bifrontal|bitemporal|beidseits|bilateral',q,re.I):sides.extend(('left','right'))
            if len(set(hits))==1:values[BOOL_FIELDS[name]]=hits[0]
            elif hits:uncertain.append({'concept':name,'reason':'CONFLICTING_ASSERTIONS','evidence':evidence[name]})
            if values[BOOL_FIELDS[name]] is True:present.append(name)
        generalized=next((q for q in clauses if re.search(r'generalisierte\w*\s+Verlangsamung|Verlangsamung\s+des\s+Basisrhythmus|verlangsam\w*\s+(?:Grundaktivität|Basisrhythmus)|diffus[^.!]{0,35}(?:Delta[- ]Theta|Theta[- ]Delta|Theta|Delta)[- ]Aktivität',q,re.I) and not re.search(r'keine|ohne|fraglich',q,re.I)),None)
        if generalized:present.append('generalized_slowing');evidence['generalized_slowing']=[{'value':True,'evidence_text':original[generalized]}]
        normal=next((q for q in clauses if re.search(r'Normalbefund|unauffällig\w*\s+(?:interiktal\w*\s+)?EEG|interiktal\w*\s+(?:EEG|Befund)\s*(?::|war)\s*unauffällig|EEG\s+in\s+den\s+(?:weiten\s+)?Grenzen\s+der\s+Norm',q,re.I) and not re.search(r'kein\w*\s+(?:unauffällig|Normalbefund)|nicht\s+unauffällig|fraglich|möglich|\?',q,re.I)),None)
        if normal:present.append('normal');evidence['normal']=[{'value':True,'evidence_text':original[normal]}]
        if re.search(r'(?:EEG|Ableitung)\s+(?:technisch\s+)?(?:nicht\s+(?:beurteilbar|auswertbar)|uninterpretiert)|(?:technisch\s+)?unverwertbares\s+EEG',text,re.I):primary='uninterpretable'
        else:
            order=('electrographic_seizure','rhythmic_periodic_pattern','epileptiform_discharges','focal_slowing','generalized_slowing','other_abnormality','normal')
            if not present:
                for q in clauses:
                    m=re.search(r'\babnorm\w*|pathologisch\w*',q,re.I)
                    if m and state(q,m) is True:
                        present.append('other_abnormality');evidence['other_abnormality']=[{'value':True,'evidence_text':original[q]}];break
            primary=next((k for k in order if k in present),None)
    else:primary=None
    generalized_side=any(re.search(r'generalisiert',e['evidence_text'],re.I) for entries in evidence.values() for e in entries if e['value'] is True)
    laterality='bilateral' if set(sides)=={'left','right'} else sides[0] if sides else 'generalized' if primary=='generalized_slowing' or generalized_side else 'unknown'
    timing='during_episode' if re.search(r'EEG[^.!\n]{0,50}während\s+(?:der|einer)\s+(?:Episode|Attacke)|während\s+(?:der|einer)\s+(?:Episode|Attacke)[^.!\n]{0,50}EEG',text,re.I) else 'post_episode' if re.search(r'EEG[^.!\n]{0,50}nach\s+(?:der|einer)\s+(?:Episode|Attacke)',text,re.I) else 'interictal' if re.search(r'interiktal|interictal',text,re.I) else 'unknown'
    if study['eeg_status']!='performed':timing='unknown'
    event=None
    event_text=' '.join(text.split())
    captured=re.search(r'(?:klinisch\w*\s+|habituell\w*\s+)?(?:Ereignis\w*|Episode|Attacke|spell)[^.!]{0,65}(?:aufgezeichnet|erfasst|abgeleitet|captured)',event_text,re.I)
    if captured and study['eeg_status']=='performed' and not re.search(r'fraglich|möglich|nicht sicher',event_text[max(0,captured.start()-30):captured.end()],re.I):event=not bool(re.search(r'kein\w*|ohne',event_text[max(0,captured.start()-25):captured.end()],re.I))
    return {**{k:study[k] for k in ('source_document_id','procedure_id','eeg_date','eeg_status','eeg_report_text')},**values,
        'eeg_primary_finding':primary,'eeg_laterality':laterality,'eeg_tfne_timing':timing,
        'eeg_clinical_event_captured':event,'evidence':evidence,'uncertain_assertions':uncertain,
        'header':study['header'],'source_offsets':study['source_offsets'],'header_offsets':study['header_offsets']}

def normalize(bundle,case_id):return [{'case_id':case_id,**interpret(s)} for s in discover(bundle)]
def fingerprint(bundle):return digest({'bundle':bundle.to_dict(),'version':VERSION,'implementation':Path(__file__).read_text(encoding='utf-8')})
def values(r):return {k:(date.fromisoformat(r[k]) if k=='eeg_date' and r[k] else r[k]) for k in EEGExamination.__table__.columns.keys() if k not in ('id','deleted')}|{'deleted':False}
def load_verified(pid,bundle,cid,session,root):
    files=sorted(artifact_directory(pid,root).glob('eeg_*.json'))
    if not files:raise VerifiedExtractionError('NO_VERIFIED_EXTRACTION')
    a=json.loads(files[-1].read_text(encoding='utf-8'));sha=a.pop('artifact_sha256')
    if digest(a)!=sha:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    a['artifact_sha256']=sha
    if a.get('artifact_version')!=1 or a.get('version')!=VERSION or a['patient_id']!=pid or a['case_id']!=cid or a['source_fingerprint']!=fingerprint(bundle):raise VerifiedExtractionError('VERIFIED_EXTRACTION_STALE')
    expected=normalize(bundle,cid)
    if a['exams']!=expected:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    run=session.get(ExtractionRun,a['source_run_id'])
    if run is None or run.status!='SUCCESS' or run.source_document_id!=bundle.documents[0].source_document_id or run.prompt_version!=VERSION or run.model_name!='deterministic-eeg' or run.model_version!=VERSION or run.raw_output!={'patient_id':pid,'case_id':cid,'dry_run':True,'inference_performed':False,'source_fingerprint':a['source_fingerprint'],'exams':expected}:raise VerifiedExtractionError('VERIFIED_EXTRACTION_INVALID')
    return a,files[-1]
def extract_patient(pid,*,dry_run=True,session_factory=None,debug_root=None):
    factory=session_factory or SessionLocal;bundle=build_patient_bundle(pid,factory)
    if not bundle.documents:raise VerifiedExtractionError('NO_SOURCE_DOCUMENTS')
    with factory.begin() as s:
        cid=resolve_case_id(pid,session=s)
        if not dry_run:
            s.execute(text('SELECT pg_advisory_xact_lock(:id)'),{'id':cid});a,path=load_verified(pid,bundle,cid,s,debug_root);plan=[]
            for r in a['exams']:
                prior=s.scalar(select(EEGExamination).where(EEGExamination.case_id==cid,EEGExamination.source_document_id==r['source_document_id'],EEGExamination.procedure_id==r['procedure_id']).with_for_update())
                v=values(r)
                if prior is not None and any(getattr(prior,k)!=value for k,value in v.items()):raise VerifiedExtractionError('CONFLICT_EXISTING_EEG')
                plan.append((r,prior))
            inserted=[]
            for r,prior in plan:
                if prior is None:
                    row=EEGExamination(**values(r));s.add(row);s.flush();inserted.append(row.id)
            s.add(ExtractionRun(source_document_id=bundle.documents[0].source_document_id,model_name='deterministic-eeg',model_version=VERSION,prompt_version=VERSION,status='SUCCESS',completed_at=datetime.now(timezone.utc),raw_output={'patient_id':pid,'case_id':cid,'dry_run':False,'operation':'COMMIT_VERIFIED_EXTRACTION','inference_performed':False,'artifact_sha256':a['artifact_sha256'],'source_run_id':a['source_run_id'],'inserted_ids':inserted}))
            return {'status':'SUCCESS','inserted':len(inserted),'existing':len(plan)-len(inserted),'granite_called':False}
        exams=normalize(bundle,cid);fp=fingerprint(bundle)
        run=ExtractionRun(source_document_id=bundle.documents[0].source_document_id,model_name='deterministic-eeg',model_version=VERSION,prompt_version=VERSION,status='SUCCESS',completed_at=datetime.now(timezone.utc),raw_output={'patient_id':pid,'case_id':cid,'dry_run':True,'inference_performed':False,'source_fingerprint':fp,'exams':exams})
        s.add(run);s.flush();rid=run.id
    created=datetime.now(timezone.utc);a={'artifact_version':1,'patient_id':pid,'case_id':cid,'created_at':created.isoformat(),'version':VERSION,'source_fingerprint':fp,'source_run_id':rid,'exams':exams};a['artifact_sha256']=digest(a)
    folder=artifact_directory(pid,debug_root);folder.mkdir(parents=True,exist_ok=True);path=folder/f'eeg_{created.strftime("%Y%m%dT%H%M%S%fZ")}_{uuid4().hex}.json'
    with path.open('x',encoding='utf-8') as f:f.write(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
    return {'status':'SUCCESS','projected':len(exams),'verified_artifact':str(path),'granite_called':False}
