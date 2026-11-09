"""Read-only local EEG document screening. No clinical extraction or model/DB calls."""
import re
import csv
import json
from pathlib import Path
import pymupdf

LABEL=r'(?:(?:MOBILE|Routine|Video|Langzeit|Schlaf|Ambulant)\s*[- ]\s*)?EEG(?:[- ]Monitoring)?|Elektroen[cz]ephalo(?:gramm|gra(?:ph|f)ie)'
MENTION=re.compile(r'\b(?:'+LABEL+r')\b',re.I)
HEADER=re.compile(r'(?im)^[ \t]*(?:'+LABEL+r')\b[^\n]{0,140}')
DATE=r'(?:\d{1,2}\.\s*\d{1,2}\.\s*(?:19|20)\d{2}|\d{4}-\d{2}-\d{2})'
RESULT=re.compile(r'grundaktivit|grundrhythm|basisrhythm|hintergrundaktivit|(?:\d+[,.]?\d*\s*[-–]\s*)?\d+[,.]?\d*\s*Hz|epilepsietypisch\w*\s+(?:Potential|Potenzial)|herdbefund|herdstörung|abnorm\w*\s+EEG|Normalbefund|Grenzen\s+der\s+Norm|EEG\s+(?:zeigte|ergab|war)|(?:fokale|generalisierte)\s+Verlangsamung',re.I)
PLAN=re.compile(r'empfohlen|geplant|angefordert|vorgesehen|soll\s+.*?(?:erfolg|durchführ)|nicht\s+durchgeführt',re.I)
FEATURES={
    'normal':r'Normalbefund|normales?\s+EEG|Grenzen\s+der\s+Norm|weitgehend\s+normal',
    'focal_slowing':r'fokal\w*\s+Verlangsamung|Herdstörung|lokal\w*\s+Funktionsstörung|(?:links|rechts)[- ]temporal|fokal\w*\s+Delta',
    'generalized_slowing':r'generalisiert\w*\s+Verlangsamung|diffus\w*\s+Verlangsamung|Allgemeinveränderung|diffus\w*\s+Theta',
    'epileptiform':r'Spikes?|Sharp[- ]Waves?|epileptiform\w*\s+Entladung|epilepsietypisch\w*\s+Potential',
    'periodic_rhythmic':r'\bLPDs?\b|\bGPDs?\b|\bLRDA\b|\bGRDA\b|periodisch\w*\s+(?:Entladung|Muster)|rhythmisch\w*\s+Delta',
    'sleep':r'Schlafspindel|K[- ]Komplex|Vertex|Schlafstadium|Schlaf[- ]EEG|subvigil',
    'artifact_limited':r'Artefakt|nicht\s+beurteilbar|nicht\s+auswertbar',
    'seizure_status_context':r'Status\s+epilepticus|Anfall|Anfälle|Epilepsie',
    'TFNE_context':r'\bTFNE\b|transient\w*\s+neurolog|transitorisch\w*\s+Episode|Amyloid\s+spells',
    'CAA_ICH_context':r'Amyloidangiopathie|\bCAA\b|Hirnblutung|intracerebral\w*\s+Blutung',
    'stroke_context':r'Infarkt|Schlaganfall|ischämisch',
    'dementia_encephalopathy_context':r'Demenz|dementiell|Enzephalopathie',
    'ICU_context':r'Intensivstation|komatös|Bewusstseinsstörung|Somnolenz|Koma',
}


def classify(text):
    headers=list(HEADER.finditer(text));performed=[]
    for h in headers:
        block=text[h.start():min(len(text),h.end()+1000)]
        if RESULT.search(block) and (re.search(DATE,h[0]) or re.search(r'Befund|Grund|Basis|\bHz\b',block,re.I)) and not PLAN.search(h[0]):performed.append(h)
    performed_prose=bool(re.search(r'(?:EEG|Elektroen[cz]ephalo\w*)[^.!?\n]{0,60}(?:zeigte|ergab|durchgeführt|abgeleitet|unauffällig)',text,re.I))
    if performed or performed_prose:return 'EEG_PERFORMED',max(len(performed),1)
    if PLAN.search(text) and MENTION.search(text):return 'EEG_PLANNED',0
    if MENTION.search(text):return 'EEG_REFERENCED',0
    return 'EEG_UNKNOWN',0


def screen(pdf_root,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);rows=[];patients=[]
    for index,folder in enumerate(sorted(p for p in Path(pdf_root).iterdir() if p.is_dir()),1):
        count=0;performed=0;estimates=0;found=[];alltext=[];eegtext=[]
        for pdf_path in sorted(folder.glob('*.pdf')):
            try:
                with pymupdf.open(pdf_path) as pdf:
                    pages=[dict(page=n+1,text=p.get_text('text',sort=True)) for n,p in enumerate(pdf)]
                text='\n'.join(p['text'] for p in pages);alltext.append(text)
                if not MENTION.search(text):continue
                count+=1
                contexts=[]
                for p in pages:
                    for m in MENTION.finditer(p['text']):contexts.append(dict(page=p['page'],text=p['text'][max(0,m.start()-100):m.end()+1400]))
                evidence='\n'.join(c['text'] for c in contexts);status,n=classify(evidence)
                # Header tests run against complete original text too, without clipped prefixes.
                native_status,native_n=classify(text)
                if native_status=='EEG_PERFORMED':status,n=native_status,native_n
                if status=='EEG_PERFORMED':performed+=1;estimates+=n;eegtext.append(evidence)
                row=dict(patient_id=folder.name,source_document=pdf_path.name,pdf_path=str(pdf_path.resolve()),
                    screening_status=status,exam_count_estimate=n,screening_contexts=contexts)
                rows.append(row);found.append(pdf_path.name)
            except Exception as exc:
                rows.append(dict(patient_id=folder.name,source_document=pdf_path.name,pdf_path=str(pdf_path.resolve()),screening_status='SOURCE_ERROR',exam_count_estimate=0,error=type(exc).__name__,screening_contexts=[]))
        context='\n'.join(alltext);reports='\n'.join(eegtext)
        features={name:bool(re.search(pattern,context if name.endswith('_context') else reports,re.I)) for name,pattern in FEATURES.items()}
        patients.append(dict(patient_id=folder.name,EEG_document_count=count,performed_EEG_document_count=performed,
            EEG_exam_count_estimate=estimates,source_documents=found,screening_features=features))
        if index%25==0:print('SCREENED',index,flush=True)
    (output/'screening_documents.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    (output/'screening_patients.json').write_text(json.dumps(patients,ensure_ascii=False,indent=2),encoding='utf8')
    choose(patients,output)
    print('SCREENING_COMPLETE',len(patients),'patients;',sum(r['performed_EEG_document_count']>0 for r in patients),'with performed signals',flush=True)
    return patients,rows


def choose(patients,output,count=20):
    pool=[r for r in patients if r['performed_EEG_document_count']>0];selected=[];covered=set()
    frequency={f:sum(r['screening_features'][f] for r in pool) for f in FEATURES}
    while pool and len(selected)<count:
        def score(row):
            novelty=sum(1+3/max(frequency[f],1) for f,v in row['screening_features'].items() if v and f not in covered)
            # After coverage, retain a varied low-burden sample rather than only long epilepsy files.
            richness=sum(row['screening_features'].values())
            return (novelty, -abs(row['EEG_exam_count_estimate']-3),-richness,row['patient_id'])
        best=max(pool,key=score);pool.remove(best);selected.append(best);covered|={f for f,v in best['screening_features'].items() if v}
    with (Path(output)/'eeg_validation_cohort.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['patient_id','EEG_document_count','EEG_exam_count_estimate','selection_reason','source_document_ids']);w.writeheader()
        for r in selected:w.writerow(dict(patient_id=r['patient_id'],EEG_document_count=r['performed_EEG_document_count'],EEG_exam_count_estimate=r['EEG_exam_count_estimate'],
            selection_reason='; '.join(k for k,v in r['screening_features'].items() if v),source_document_ids='PENDING_SOURCE_SNAPSHOT:'+','.join(r['source_documents'])))
    (Path(output)/'selected_patients.json').write_text(json.dumps([r['patient_id'] for r in selected],indent=2),encoding='utf8')
    return selected


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--pdf-root',type=Path,default=Path('pdf_db'));p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();screen(a.pdf_root,a.output)
