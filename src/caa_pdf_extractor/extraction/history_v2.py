"""Source-bound hierarchical history, partial dates and conservative canonical evidence."""
import re,json,hashlib
from datetime import date
from pathlib import Path
from collections import defaultdict
from .history_target_policy import classify_target
from .history_target_policy import PROCEDURE as TARGET_PROCEDURE,SURGERY
from .date_attribution import PROCEDURE as LEGACY_PROCEDURE

VERSION='history_v2_1'
PRECISIONS=('DAY','MONTH','YEAR','ADMISSION_PROXY','UNKNOWN')
BASES=('EXPLICIT_DISEASE_DATE','EXPLICIT_MONTH_YEAR','EXPLICIT_YEAR','CURRENT_ADMISSION_PROXY','UNKNOWN')
ROLES=('PRIMARY_DIAGNOSIS','CHILD_DIAGNOSIS','PAST_HISTORY','CURRENT_NARRATIVE','CORROBORATING','COPIED_FORWARD')
FULL=re.compile(r'(?<!\d)(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\d)')
MONTHS={m:i+1 for i,m in enumerate('Januar Februar März April Mai Juni Juli August September Oktober November Dezember'.split())}
MONTH=re.compile(r'(?<![\d./])(?P<month>0?[1-9]|1[0-2])/(?P<year>(?:19|20)\d{2})(?!\d)|\b(?P<name>'+ '|'.join(MONTHS)+r')\s+(?P<namedyear>(?:19|20)\d{2})\b',re.I)
YEAR=re.compile(r'(?<![\d./])(?:19|20)\d{2}(?![\d./])')
CATEGORY=re.compile(r'^(?:(?:zerebrovaskuläre|kardiovaskuläre|vaskuläre)\s+)?Risikofaktoren\b|^(?:Kardiale Diagnosen|Weitere Diagnosen|Vorerkrankungen|Nebendiagnosen|Diagnosen)(?:\s+bei Entlassung)?\s*:?',re.I)
DETAIL=re.compile(r'^(?:a\.e\.|Ätiologie|Histologie|HbA1c|LDL|c?MRT|MRI|CT\b|SWI\b|FLAIR\b|T2\b|Fazekas|Mikroblutungen|Microbleeds|Hämosiderin|Marklager|Thrombusnachweis|Hirnvolumen|Ventrikelweite|Polygraphische Diagnose|Tumorstadium|Rezeptorstatus|Adjuvante|Segmentresektion|MMSE|MoCA|Aciclovir|Thioctacid|elektrische Kardioversion|Kardioversion|Therapie|Behandlung|Ausschluss|kein\w*\b|Zielwert|Referenz)',re.I)
DIAGNOSIS_START=re.compile(r'^(?:Herpes|Kardiale Dekompensation|Neuritis|(?:arterielle\s*)?Hypertonie|Diabetes|Hypercholesterinämie|Chronische\s+Niereninsuff|Polyneuropathie|HFpEF|Vorhofflimmern|Latent rhythmogene|Diarrhoen|Nierenzellkarzinom|Prostatahyperplasie|Gangunsicherheit|Enzephalopathie|Stauungsdermatitis|Druckulcus|Ulcus)\b',re.I)
PROCEDURE=re.compile(r'Gastroskopie|Endoskopie|Biopsie|Excision|Exzision|Operation|operat\w*|operiert|\bOP\b|\bCHE\b|Resektion|resektion|Nephrektomie|Kardioversion|Kraniotomie|Craniotomie|Entleerung|Reposition|Verplattung|Anlage|Katheterisierung|Thrombektomie|\bLyse\b|Thrombolyse|HbA1c|LDL(?:-C)?|MMSE|MoCA|Polygraph|\bc?MRT\b|\bMRI\b|\bCT\b|Infusion|Behandlung|Therapie|Kontrolle|Vergleich|Voruntersuchung|stabiler\s+Befund|Aufnahme|Entlassung',re.I)
PROCEDURE=re.compile('|'.join('(?:'+pattern.pattern+')' for pattern in (PROCEDURE,TARGET_PROCEDURE,SURGERY,LEGACY_PROCEDURE)),re.I)
FOOTER=re.compile(r'Universitätsklinikum|Gemeinnützige|Paracelsus|Privatuniversität|Landesgericht|UID-Nr|FN \d|Telefon|Telefax|www\.|geb\.:|geboren am',re.I)
STOP=re.compile(r'^(?:Dauermedikation|Prämedikation|Medikation|Medikamente|Empfohlene|Weitere empfohlene|Allergien|Noxen|Familienanamnese|FA:|SA:|Sozialanamnese|Aufnahmestatus|Status\b|Befunde\b|Kontrollen)',re.I)

def norm(s):return ' '.join(re.sub(r'[^\w]+',' ',s.casefold()).split())
def unknown():return dict(date_disease=None,date_disease_year=None,date_disease_month=None,date_disease_precision='UNKNOWN',date_disease_basis='UNKNOWN',date_disease_text=None,date_disease_proxy=None)

def label_from(text):
    label=' '.join(text.split())
    # Tests/procedures after a disease are attributes, not onset or disease names.
    label=re.split(r'\s*[-–]\s*(?=HbA1c|LDL|c?MRT|MRI|CT\b|Thioctacid|Aciclovir)',label,flags=re.I)[0]
    label=re.split(r'\s+DD\s+',label,maxsplit=1)[0]
    label=re.split(r'\s+(?:mit|nach)\s+(?=Nierenteilresektion|Nephrektomie|Resektion|Operation)',label,flags=re.I)[0]
    label=re.sub(r'\s*(?:am ehesten\s+)?(?:im|am|vom|ED|Erstdiagnose|seit)?\s*(?:'+FULL.pattern+'|'+MONTH.pattern+r')\s*(?:[-–].*)?$','',label,flags=re.I)
    label=re.sub(r'\s+(?:ED|Erstdiagnose)\s+(?:19|20)\d{2}\s*$','',label,flags=re.I)
    label=re.sub(r'\s+(?:19|20)\d{2}\s*$','',label)
    label=re.sub(r'\s+(?:mit|am|vom|im|seit)\s*$','',label,flags=re.I)
    return label.strip(' -–,;.')

def eligible(label):
    return bool(label and not CATEGORY.match(label) and not DETAIL.match(label) and not re.match(r'^(?:DD\b|V\.?\s*a\.|Verdacht auf|kein\w*\b|Die Patientin\b|Der Patient\b|Wir\b|Es\b|Bitte\b|Empfohlen\b)',label,re.I) and (classify_target(label)['persistence_eligible'] or re.match(r'^(?:Kardiale Dekompensation|HFpEF|Koronare Herzkrankheit|KHK|Druckulcus|Ulcus|(?:permanentes?\s+)?VHF)\b',label,re.I)))

def hierarchical_mentions(bundle):
    mentions=[];audit=[]
    for doc in bundle.documents:
        active=False;category=None;past=False;parents={}
        for section in doc.sections:
            if section.heading:
                active=bool(re.match(r'Diagnos',section.heading,re.I));past=False
                parents.clear()
                if not active:category=None
            current=None;blank=False
            def flush():
                nonlocal current
                if current:
                    current['evidence_text']=section.source_text[current['start']:current['end']].strip()
                    pieces=[current]
                    # Repeated explicit ICH concepts are separate events, including
                    # when one has an unknown age. Never share the left date.
                    from .diagnosis_items import CONCEPT
                    conjunction=re.search(r'\s+sowie\s+',current['evidence_text'],re.I)
                    if conjunction:
                        left=current['evidence_text'][:conjunction.start()];right=current['evidence_text'][conjunction.end():]
                        a,b=CONCEPT.search(left),CONCEPT.search(right)
                        if a and b and a[0].casefold()==b[0].casefold():
                            audit.append({**current,'disposition':'SPLIT_EVENT_CONTAINER'})
                            pieces=[{**current,'item_id':current['item_id']+':left','evidence_text':left,'end':current['start']+conjunction.start()},{**current,'item_id':current['item_id']+':right','evidence_text':right,'start':current['start']+conjunction.end()}]
                    for piece in pieces:
                        piece['disease']=label_from(piece['evidence_text'])
                        if eligible(piece['disease']):mentions.append(piece)
                        else:audit.append({**piece,'disposition':'CATEGORY_OR_DETAIL_OR_UNAPPROVED_TARGET'})
                current=None
            for line in re.finditer(r'(?m)^.*$',section.source_text):
                raw=line[0];value=raw.strip()
                if not value:blank=True;continue
                if FOOTER.search(value):flush();continue
                if re.match(r'Vorerkrankungen\s*:',value,re.I):
                    flush();past=True;category=value;parents={0:f'{section.section_id}:category:{line.start()}'}
                    audit.append({'source_document_id':doc.source_document_id,'section_id':section.section_id,'page':section.page_start,'item_id':parents[0],'evidence_text':value,'disposition':'CATEGORY_HEADING'});continue
                if STOP.match(value):flush();active=False;past=False;category=None;continue
                if not (active or past):continue
                marker=re.match(r'^\s*(?:(?P<number>\d{1,2})[.)]\s+|(?P<bullet>[-•]{1,2})\s*|[.]\s+)',raw)
                content=raw[marker.end():] if marker else raw.lstrip()
                start=line.start()+(marker.end() if marker else len(raw)-len(raw.lstrip()))
                if CATEGORY.match(content):
                    flush();category=content;parents={0:f'{section.section_id}:category:{line.start()}'}
                    audit.append({'source_document_id':doc.source_document_id,'section_id':section.section_id,'page':section.page_start,'item_id':parents[0],'evidence_text':content,'disposition':'CATEGORY_HEADING'});continue
                new_diagnosis=bool(DIAGNOSIS_START.match(content) or (re.match(r'[A-ZÄÖÜ]',content) and eligible(label_from(content))))
                if current and DETAIL.match(current['evidence_text']) and not marker and not blank and not DIAGNOSIS_START.match(content):
                    current['end']=line.end();continue
                if current and not marker and not blank and not new_diagnosis and not DETAIL.match(content) and not (past and re.match(r'[A-ZÄÖÜ]',content)):
                    current['end']=line.end();continue
                flush()
                level=len(marker['bullet']) if marker and marker['bullet'] else 0
                parent=parents.get(level-1,parents.get(0)) if level else None
                if not level and marker and marker['number']:category=None
                current={'source_document_id':doc.source_document_id,'section_id':section.section_id,'page':section.page_start,'start':start,'end':line.end(),'item_id':f'{section.section_id}:span:{start}','evidence_role':'PAST_HISTORY' if past else 'CHILD_DIAGNOSIS' if marker and marker['bullet'] else 'PRIMARY_DIAGNOSIS','parent_category':category,'parent_item_id':parent,'hierarchy_level':level,'evidence_text':content}
                parents={k:v for k,v in parents.items() if k<level};parents[level]=current['item_id']
                blank=False
            flush()
    return mentions,audit

def temporal(text,disease,*,admission=None,current_evidence=None):
    """Own clause only. Procedure/lab context blocks both full and partial dates."""
    out=unknown();tokens=[];blocked=[];qualifiers=[]
    if re.search(r'genauer\s+Onset\s+unklar',text,re.I):qualifiers.append('genauer Onset unklar')
    # The first source diagnosis clause cannot borrow any subsequent bullet date.
    head=re.split(r'\n\s*[-•]',text)[0]
    for pattern,precision in ((FULL,'DAY'),(MONTH,'MONTH'),(YEAR,'YEAR')):
        for match in pattern.finditer(head):
            if any(a<=match.start()<b for a,b,_,_ in tokens):continue
            tokens.append((match.start(),match.end(),precision,match))
    dates=[]
    ordered=sorted(tokens)
    for left,right in zip(ordered,ordered[1:]):
        if re.fullmatch(r'\s*[,–-]\s*',head[left[1]:right[0]]) and not PROCEDURE.search(head[:left[0]]):
            return out,{'date_tokens':[t[3][0] for t in ordered],'blocked_tokens':[{'reason':'PARALLEL_EVENT_DATES_REQUIRE_REVIEW'}],'temporal_qualifiers':qualifiers,'current_evidence':current_evidence,'admission_date':admission}
    for start,end,precision,m in sorted(tokens):
        prefix=head[:start]
        if re.search(r'\bzu\s*$',prefix,re.I):
            blocked.append({'text':m[0],'reason':'COMPARISON_DATE_NOT_ONSET'});continue
        if norm(disease) not in norm(prefix) and not (norm(head).startswith(norm(disease)) and norm(m[0]) in norm(disease)):
            blocked.append({'text':m[0],'reason':'DATE_PRECEDES_OR_IS_OUTSIDE_OWN_DISEASE_CLAUSE'});continue
        # An ED cue directly attached to this date can independently own it.
        direct=bool(re.search(r'\b(?:ED|Erstdiagnose|diagnostiziert|Beginn)\s*(?:am|:)?\s*$',prefix,re.I))
        if PROCEDURE.search(prefix) and not direct:
            blocked.append({'text':m[0],'reason':'PROCEDURE_OR_MEASUREMENT_DATE'});continue
        # A date for an associated condition cannot date the leading diagnosis.
        association_text=re.sub(r'mit\s+(?:punktförmiger\s+)?hämorrhagischer\s+Transformation','',prefix,flags=re.I)
        association_text=re.sub(r'mit\s+Ventrikeleinbruch(?:\s+und\s+Mittellinienshift)?','',association_text,flags=re.I)
        if re.search(r'\b(?:bei|mit)\s+',association_text,re.I) and not direct:
            # Explicit date of decompensation remains owned despite a chronic HF modifier.
            if not re.match(r'Kardiale Dekompensation bei vorbekannter Herzinsuffizienz',prefix,re.I):
                blocked.append({'text':m[0],'reason':'ASSOCIATED_CONDITION_OR_PROCEDURE'});continue
        try:
            value=unknown()
            if precision=='DAY':
                day=date.fromisoformat(m[0]) if '-' in m[0] else date(*map(int,m[0].split('.')[::-1]));year,month=day.year,day.month
                value.update(date_disease=day.isoformat(),date_disease_year=year,date_disease_month=month,date_disease_precision='DAY',date_disease_basis='EXPLICIT_DISEASE_DATE',date_disease_text=m[0])
            elif precision=='MONTH':
                year=int(m['year'] or m['namedyear']);month=int(m['month']) if m['month'] else next(i for name,i in MONTHS.items() if name.casefold()==m['name'].casefold())
                value.update(date_disease_year=year,date_disease_month=month,date_disease_precision='MONTH',date_disease_basis='EXPLICIT_MONTH_YEAR',date_disease_text=m[0])
            else:
                year=int(m[0])
                # Bare historical year is allowed only immediately after a diagnosis
                # label or an explicit ED cue, not after arbitrary narrative words.
                bridge=prefix[len(disease):] if prefix.casefold().startswith(disease.casefold()) else prefix
                if not direct and re.search(r'\w',bridge):blocked.append({'text':m[0],'reason':'YEAR_NOT_DIRECTLY_OWNED'});continue
                value.update(date_disease_year=year,date_disease_precision='YEAR',date_disease_basis='EXPLICIT_YEAR',date_disease_text=m[0])
            dates.append(value)
        except (ValueError,IndexError):blocked.append({'text':m[0],'reason':'INVALID_DATE'})
    if len(dates)==1:out=dates[0]
    elif len(dates)>1:blocked.append({'reason':'MULTIPLE_DIRECT_DATES_REQUIRE_EVENT_REVIEW'})
    if not dates and not tokens and not re.search(r'\d{1,2}/\d{4}|\b(?:19|20)\d{2}\b',text) and admission and current_evidence and not re.search(r'Vorerkrank|Z\.\s*n\.|vorbekannt|Risikofaktor|historisch|Zustand nach',text+' '+current_evidence,re.I):
        if re.search(r'neu diagnostiziert|hierorts\s+(?:wurde\s+)?(?:die\s+)?Diagnose|im Rahmen dieses Aufenthaltes diagnostiziert',current_evidence,re.I) and norm(disease) in norm(current_evidence):
            date.fromisoformat(admission)
            out.update(date_disease_proxy=admission,date_disease_precision='ADMISSION_PROXY',date_disease_basis='CURRENT_ADMISSION_PROXY')
    return out,{'date_tokens':[t[3][0] for t in tokens],'blocked_tokens':blocked,'temporal_qualifiers':qualifiers,'current_evidence':current_evidence,'admission_date':admission}

def validate_temporal(record):
    p=record['date_disease_precision'];assert p in PRECISIONS
    y,m,d,proxy=(record.get(k) for k in ('date_disease_year','date_disease_month','date_disease','date_disease_proxy'))
    assert y is None or type(y) is int and 1<=y<=9999
    assert m is None or type(m) is int and 1<=m<=12
    basis=record['date_disease_basis'];assert basis in BASES
    if p=='DAY':assert d and (date.fromisoformat(d).year,date.fromisoformat(d).month)==(y,m) and not proxy and basis=='EXPLICIT_DISEASE_DATE'
    elif p=='MONTH':assert not d and y and m and not proxy and basis=='EXPLICIT_MONTH_YEAR'
    elif p=='YEAR':assert not d and y and m is None and not proxy and basis=='EXPLICIT_YEAR'
    elif p=='ADMISSION_PROXY':assert not d and y is None and m is None and proxy and basis=='CURRENT_ADMISSION_PROXY'
    else:assert not d and y is None and m is None and not proxy and basis=='UNKNOWN'

def identity(disease):
    value=norm(disease)
    value=re.sub(r'arteriellehypertonie','arterielle hypertonie',value)
    value=re.sub(r'nierenzellkarzinom(links|rechts)\b',r'nierenzellkarzinom \1',value)
    if re.match(r'^(?:arterielle|arterieller) hypertonie\b',value):return 'CHRONIC:ARTERIAL_HYPERTENSION'
    if re.match(r'^(?:permanentes? )?(?:vorhofflimmern|vhf)\b',value):return 'CHRONIC:AF'
    if re.match(r'^diabetes mellitus (?:typ )?(?:2|ii)\b',value) and not re.search(r'induziert|sekundär',value):return 'CHRONIC:DM2'
    if re.match(r'^(?:chronische )?niereninsuffiz',value):
        grade=re.search(r'\bg\s*([1-5])\b|insuffiz\w*g([1-5])\b',value)
        return 'CHRONIC:CKD:'+(''.join(x for x in grade.groups() if x) if grade else 'UNSPECIFIED')
    if re.match(r'^hfpef\b',value):return 'CHRONIC:HFPEF'
    return 'EXACT:'+value

def compatible(a,b):
    if a['date_disease_precision'] in ('UNKNOWN','ADMISSION_PROXY') or b['date_disease_precision'] in ('UNKNOWN','ADMISSION_PROXY'):return True
    if a['date_disease_year']!=b['date_disease_year']:return False
    if a.get('date_disease_month') and b.get('date_disease_month') and a['date_disease_month']!=b['date_disease_month']:return False
    return not (a.get('date_disease') and b.get('date_disease') and a['date_disease']!=b['date_disease'])

def canonicalize(mentions):
    groups=defaultdict(list)
    for mention in mentions:groups[identity(mention['disease'])].append(mention)
    known_ckd=[key for key in groups if key.startswith('CHRONIC:CKD:') and key!='CHRONIC:CKD:UNSPECIFIED']
    if len(known_ckd)==1 and 'CHRONIC:CKD:UNSPECIFIED' in groups:
        groups[known_ckd[0]].extend(groups.pop('CHRONIC:CKD:UNSPECIFIED'))
    rows=[];holds=[]
    for key,items in groups.items():
        # Chronic aliases are a small allowlist. All other labels require exact
        # normalized identity; full/partial event dates must also agree.
        buckets=[]
        for item in sorted(items,key=lambda m:PRECISIONS.index(m['temporal']['date_disease_precision'])):
            matching=[b for b in buckets if all(compatible(item['temporal'],old['temporal']) for old in b)]
            if len(matching)==1:matching[0].append(item)
            elif not matching:buckets.append([item])
            else:holds.append({'mention':item,'reason':'UNDATED_MENTION_AMBIGUOUS_BETWEEN_DISTINCT_EVENTS'})
        for bucket in buckets:
            best=min(bucket,key=lambda m:PRECISIONS.index(m['temporal']['date_disease_precision']))
            evidence=[];seen=set()
            for item in bucket:
                unique=(item['source_document_id'],item['section_id'],item['item_id'],item['evidence_text'])
                if unique in seen:continue
                seen.add(unique)
                evidence.append({k:item.get(k) for k in ('source_document_id','section_id','item_id','evidence_text','evidence_role','page','start','end','parent_item_id','parent_category','hierarchy_level')}|{'date_text':item['temporal']['date_disease_text'],'date_assertion':item['temporal'],'date_audit':item['date_audit']})
            row={'canonical_key':key,'disease':best['disease'],**best['temporal'],'evidence':evidence,'supporting_source_mentions':len(evidence),'temporal_qualifiers':sorted({q for item in bucket for q in item['date_audit']['temporal_qualifiers']}),'commit_eligible':True}
            validate_temporal(row);rows.append(row)
        if key.startswith('CHRONIC:') and len(buckets)>1:
            for row in rows:
                if row['canonical_key']==key:row['commit_eligible']=False
            holds.append({'canonical_key':key,'reason':'CONFLICTING_CHRONIC_ONSET_DATES'})
    return rows,holds

def extract_bundle(bundle,legacy_records=()):
    candidates,audit=hierarchical_mentions(bundle)
    # Retain source-validated legacy recall, with independently reassessed dates.
    for index,r in enumerate(legacy_records):
        sec=next((s for d in bundle.documents for s in d.sections if s.section_id==r['section_id'] and d.source_document_id==r['source_document_id']),None)
        if not sec or ' '.join(r['source_text'].split()) not in ' '.join(sec.source_text.split()):
            audit.append({'record':r,'disposition':'LEGACY_SOURCE_BOUNDARY_REVIEW'});continue
        label=label_from(r['disease'])
        if not eligible(label):audit.append({'record':r,'disposition':'LEGACY_CATEGORY_OR_INELIGIBLE_TARGET'});continue
        from .diagnosis_items import normalized_label
        if normalized_label(label) not in normalized_label(r['source_text']):
            audit.append({'record':r,'disposition':'LEGACY_LABEL_REQUIRES_SOURCE_REVIEW'});continue
        represented={identity(c['disease']) for c in candidates if c['source_document_id']==r['source_document_id'] and c['section_id']==r['section_id'] and norm(c['disease']) in norm(label)}
        if len(represented)>1:
            audit.append({'record':r,'disposition':'MERGED_LEGACY_LABEL_REPLACED_BY_INDEPENDENT_SOURCE_CHILDREN','represented_children':sorted(represented)});continue
        # Select the legacy disease's own bullet, never the entire multi-item quote.
        quoted=r['source_text'];own=quoted
        if re.search(r'\n\s*[-•]',quoted):
            clauses=re.split(r'\n\s*[-•]\s*',quoted)
            matching=[x for x in clauses if norm(label) in norm(x)]
            if len(matching)==1:own=matching[0]
        if any(c['source_document_id']==r['source_document_id'] and c['section_id']==r['section_id'] and identity(c['disease'])==identity(label) for c in candidates):continue
        candidates.append({'disease':label,'evidence_text':quoted,'date_source_text':own,'source_document_id':r['source_document_id'],'section_id':r['section_id'],'page':r['page_number'],'start':None,'end':None,'item_id':f'{r["section_id"]}:legacy:{index}','evidence_role':'CORROBORATING'})
    admissions={}
    for document in bundle.documents:
        values=[]
        for section in document.sections:
            if section.page_start!=1:continue
            for m in re.finditer(r'von\s+('+FULL.pattern+r')\s+bis\s+'+FULL.pattern+r'\s+zur\s+stationären\s+Behandlung|Aufnahmedatum\s*:\s*('+FULL.pattern+r')',section.source_text,re.I):
                raw=m[1] or m[2]
                try:day=date.fromisoformat(raw) if '-' in raw else date(*map(int,raw.split('.')[::-1]))
                except ValueError:continue
                values.append({'date':day.isoformat(),'source_document_id':document.source_document_id,'section_id':section.section_id,'page':section.page_start,'evidence_text':m[0]})
        if len({v['date'] for v in values})==1:admissions[document.source_document_id]=values[0]
    mentions=[]
    for c in candidates:
        admission=admissions.get(c['source_document_id'])
        t,a=temporal(c.get('date_source_text',c['evidence_text']),c['disease'],admission=admission['date'] if admission else None,current_evidence=c['evidence_text'] if c['evidence_role']!='PAST_HISTORY' else None)
        if admission:a['admission_provenance']=admission
        mentions.append({**c,'temporal':t,'date_audit':a})
    rows,holds=canonicalize(mentions)
    return {'version':VERSION,'patient_id':bundle.patient_id,'records':rows,'mentions':mentions,'audit_only':audit,'identity_holds':holds,'counts':{'source_mentions':len(mentions),'canonical_records':len(rows),'merged_mentions':len(mentions)-sum(r['supporting_source_mentions'] for r in rows)+sum(r['supporting_source_mentions']-1 for r in rows),'date_precisions':{p:sum(r['date_disease_precision']==p for r in rows) for p in PRECISIONS}}}

def source_fingerprint(bundle):
    return hashlib.sha256(json.dumps({'bundle':bundle.to_dict(),'implementation':Path(__file__).read_text(encoding='utf8'),'workflow':Path(__file__).with_name('clinical_history_v2.py').read_text(encoding='utf8'),'target_policy':Path(__file__).with_name('history_target_policy.py').read_text(encoding='utf8'),'date_ownership_policy':Path(__file__).with_name('date_attribution.py').read_text(encoding='utf8'),'source_binding_policy':Path(__file__).with_name('diagnosis_items.py').read_text(encoding='utf8'),'quotation_policy':Path(__file__).with_name('validators.py').read_text(encoding='utf8')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def plan_commit(artifact,existing,evidence=()):
    """Pure accepted-only persistence plan; duplicate legacy rows require review."""
    plan=[]
    for row in artifact['records']:
        validate_temporal(row)
        same=[r for r in existing if identity(label_from(r['disease']))==row['canonical_key'] and not r.get('deleted')]
        def existing_date(r):
            value={**unknown(),**{k:v for k,v in r.items() if k in unknown() and v is not None}}
            if r.get('date_disease') and not r.get('date_disease_precision'):
                day=date.fromisoformat(str(r['date_disease']));value.update(date_disease_precision='DAY',date_disease_year=day.year,date_disease_month=day.month)
            return value
        matching=[r for r in same if compatible(existing_date(r),row)]
        if row['canonical_key'].startswith('CHRONIC:') and same and not matching:
            plan.append({'record':row,'status':'BLOCKED_EXISTING_DATE_CONFLICT'});continue
        if not row['commit_eligible'] or len(matching)>1:plan.append({'record':row,'status':'BLOCKED_IDENTITY_REVIEW'});continue
        target=matching[0] if matching else None
        if target and target.get('date_disease') and str(target['date_disease'])!=row['date_disease']:
            plan.append({'record':row,'status':'BLOCKED_EXISTING_DATE_CONFLICT'});continue
        values={k:row[k] for k in unknown()}
        status='INSERT' if target is None else 'UNCHANGED' if all(target.get(k)==v for k,v in values.items()) else 'REVIEW_DATE_UPGRADE'
        links=[]
        for e in row['evidence']:
            key=(target.get('id') if target else None,e['source_document_id'],e['section_id'],e['item_id'],e['evidence_text'])
            if not any((x['clinical_history_id'],x['source_document_id'],x['section_id'],x['item_id'],x['evidence_text'])==key for x in evidence):links.append(e)
        plan.append({'record':row,'existing_id':target.get('id') if target else None,'status':status,'new_evidence':links})
    return plan
