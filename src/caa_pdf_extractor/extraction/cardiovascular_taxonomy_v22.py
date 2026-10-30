"""Hard, source-preserving cardiovascular canonicalization for review only."""
from collections import defaultdict
import re
from .diagnosis_canonical_v2 import stable_id

RULES = {
    'ATRIAL_FIBRILLATION': (r'(?<!\w)(?:Vorhofflimmern|VHF|atrial[ _-]+fibrillation|AF)(?!\w)',
                           'Atrial fibrillation','ARRHYTHMIA'),
    'TRICUSPID_REGURGITATION': (r'(?<!\w)(?:TRINS|Trikuspidal(?:klappen)?insuffizienz|tricuspid[ _-]+regurgitation)(?=\W|$|Grad\b|(?:III|II|IV|I|[0-4])(?:°|\b))|(?<=gradige)TRINS(?!\w)',
                              'Tricuspid regurgitation','STRUCTURAL_HEART_DISEASE'),
    'MITRAL_REGURGITATION': (r'(?<!\w)(?:MINS|Mitral(?:klappen)?insuffizienz|mitral[ _-]+regurgitation)(?=\W|$|Grad\b|(?:III|II|IV|I|[0-4])(?:°|\b))|(?<=gradige)MINS(?!\w)',
                           'Mitral regurgitation','STRUCTURAL_HEART_DISEASE'),
}
MATCHERS={name:re.compile(rule[0],re.I) for name,rule in RULES.items()}
FLUTTER=re.compile(r'Vorhofflattern|atrial[ _-]+flutter',re.I)
NEGATIVE=re.compile(r'\b(?:kein\w*|ohne|no|without|nicht|not)\b',re.I)
UNCERTAIN=re.compile(r'\b(?:V\s*\.?\s*a\.?|Verdacht|suspected|Ausschluss|Risiko|risk)\b',re.I)


def findings(label):
    """Return explicit positive disease mentions and separate source qualifiers.

    Matching is bounded to names/abbreviations. It never maps flutter to AF.
    Negation and uncertainty in the local clause prevent a positive identity.
    """
    result=[]
    for name,matcher in MATCHERS.items():
        for match in matcher.finditer(label):
            left=label[:match.start()]
            clause=re.split(r'[.;\n]|\b(?:aber|jedoch|but)\b',left,flags=re.I)[-1]
            if NEGATIVE.search(clause) or UNCERTAIN.search(clause) or re.search(r'\bV\s*\.\s*a\s*\.\s*$',left,re.I):continue
            # Qualifiers belong to this disease clause, not a neighboring valve.
            other=[m for other_name,rx in MATCHERS.items() if other_name!=name for m in rx.finditer(label)]
            start=max([0]+[m.end() for m in other if m.end()<=match.start()])
            end=min([len(label)]+[m.start() for m in other if m.start()>=match.end()])
            separators=list(re.finditer(r'[;,]|\b(?:und|and)\b',label,re.I))
            start=max([start]+[m.end() for m in separators if m.end()<=match.start()])
            end=min([end]+[m.start() for m in separators if m.start()>=match.end()])
            local=label[start:end]
            af_types=[];severities=[]
            if name=='ATRIAL_FIBRILLATION':
                if re.search(r'long[ -]*standing[ -]*persistent|lang(?:e|jährig|j.hrig|andauernd)\w*[ -]+persist|lang\w*\s+(?:anhaltend|andauernd)\w*\s+persist',local,re.I):
                    af_types=['LONG_STANDING_PERSISTENT']
                elif re.search(r'permanent',local,re.I):af_types=['PERMANENT']
                elif re.search(r'persist',local,re.I):af_types=['PERSISTENT']
                elif re.search(r'paroxys|paroysm',local,re.I):af_types=['PAROXYSMAL']
            else:
                for pattern,value in [(r'\btrace\b|minimal|trivial|spur', 'TRACE'),
                        (r'leicht|geringgrad|\bmild\b', 'MILD'),
                        (r'mittelgrad|m.ßig|moderate', 'MODERATE'),
                        (r'hochgrad|schwer|severe', 'SEVERE')]:
                    if re.search(pattern,local,re.I):severities.append(value)
                # Numeric grades are recognized only with an explicit grade
                # label or immediately after the valve disease abbreviation.
                grade=re.search(r'(?:Grad(?:e)?\s*[:=]?\s*|'+re.escape(match[0])+r'\s*)(III|II|IV|I|[1-4])(?:\s*[-–]\s*(III|II|IV|I|[1-4]))?(?!\w)',local,re.I)
                if grade:
                    mapping={'I':'MILD','1':'MILD','II':'MODERATE','2':'MODERATE','III':'SEVERE','3':'SEVERE','IV':'SEVERE','4':'SEVERE'}
                    severities=list(dict.fromkeys(severities+[mapping[v.upper()] for v in grade.groups() if v]))
            primary=not re.search(r'\b(?:bei|due to|secondary to)\b',left,re.I)
            result.append(dict(canonical_name=name,display_name=RULES[name][1],domain='CARDIOVASCULAR',
                category=RULES[name][2],subcategory=name,matched_source_text=match[0],
                qualifier_source_text=local,primary=primary,
                af_types=af_types,valvular_severities=severities))
    return result


def standalone_diagnosis(label):
    """Permit a named diagnosis with optional qualifiers/date annotation.

    This corrects the frozen 'Vorhofflimmern (ED: Juni 2019)' heading rejection
    without promoting investigation prose, recommendations or negated findings.
    """
    if not findings(label) or NEGATIVE.search(label) or UNCERTAIN.search(label):return False
    return bool(re.fullmatch(r'\s*(?:(?:paroxysmal\w*|persistierend\w*|permanent\w*|long standing persistent|leicht\w*|mittelgradig\w*|hochgradig\w*)\s+)*'
        r'(?:Vorhofflimmern|VHF|AF|atrial fibrillation|TRINS|MINS|Trikuspidal(?:klappen)?insuffizienz|Mitral(?:klappen)?insuffizienz)'
        r'\s*(?:\([^\n]*\))?\s*',label,re.I))


def record_findings(record):
    result=findings(record['raw_candidate'])
    for f in result:
        sources=[]
        for source in record['source_ids']:
            for observed in findings(source.get('text','')):
                if observed['canonical_name']!=f['canonical_name']:continue
                sources.append(dict(source,af_types=observed['af_types'],valvular_severities=observed['valvular_severities']))
        f['source_qualifier_observations']=sources
        f['af_types']=sorted(set(f['af_types'])|{v for s in sources for v in s['af_types']})
        f['valvular_severities']=sorted(set(f['valvular_severities'])|{v for s in sources for v in s['valvular_severities']})
    return result


def canonicalize_cardiovascular(tables):
    candidates={(r['patient_id'],r['record_id']):r for r in tables['diagnosis_candidate_eligibility']}
    observed={key:record_findings(r) for key,r in candidates.items()}
    buckets=defaultdict(list);primary={};legacy_groups=defaultdict(list)
    for key,r in candidates.items():
        fs=observed[key]
        if not fs:continue
        if r['candidate_type']!='DIAGNOSIS_DISEASE':
            if not standalone_diagnosis(r['raw_candidate']) or r['candidate_type'] not in ('HEADING_OR_FRAGMENT','UNCERTAIN'):continue
            r.update(original_candidate_type=r.get('original_candidate_type',r['candidate_type']),
                candidate_type='DIAGNOSIS_DISEASE',addendum_reason='V22_EXPLICIT_CARDIOVASCULAR_DIAGNOSIS')
        for f in fs:buckets[(key[0],f['canonical_name'])].append((r,f))
        if len(fs)==1 and fs[0]['primary'] and not FLUTTER.search(r['raw_candidate']):primary[key]=fs[0]['canonical_name']
    retained=[]
    for d in tables['canonical_diagnoses']:
        removed=[rid for rid in d['member_ids'] if (d['patient_id'],rid) in primary]
        for rid in d['member_ids']:
            for f in observed.get((d['patient_id'],rid),[]):
                if (d['patient_id'],f['canonical_name']) in buckets:legacy_groups[(d['patient_id'],f['canonical_name'])].append(d)
        remaining=[rid for rid in d['member_ids'] if rid not in removed]
        if not remaining:continue
        if removed:
            d=dict(d,member_ids=remaining)
            d['id']=stable_id('DX22RES',d['patient_id'],[d['id'],remaining])
            # A removed AF root must not leave its name on a flutter residual.
            if findings(d['canonical_name']):
                d['legacy_canonical_name']=d['canonical_name']
                d['canonical_name']=candidates[(d['patient_id'],remaining[0])]['raw_candidate']
            dates=[candidates[(d['patient_id'],rid)]['accepted_date'] for rid in remaining if candidates[(d['patient_id'],rid)]['accepted_date']]
            d['first_supported_date']=min(dates) if dates else None
        retained.append(d)
    for (pid,name),observations in buckets.items():
        rids=sorted({r['record_id'] for r,_ in observations})
        types=sorted({v for _,f in observations for v in f['af_types']})
        severity=sorted({v for _,f in observations for v in f['valvular_severities']})
        dates=[r['accepted_date'] for r,_ in observations if r['accepted_date']]
        legacy=legacy_groups[(pid,name)];f=observations[0][1]
        statuses={d['status'] for d in legacy}
        retained.append(dict(patient_id=pid,id=stable_id('DX22',pid,[name]),canonical_name=name,display_name=f['display_name'],
            domain='CARDIOVASCULAR',category=f['category'],subcategory=name,etiology='UNKNOWN',status=next(iter(statuses)) if len(statuses)==1 else 'UNKNOWN',
            member_ids=rids,first_supported_date=min(dates) if dates else None,
            af_type=(types[0] if len(types)==1 else 'UNSPECIFIED') if name=='ATRIAL_FIBRILLATION' else None,
            af_types=types,af_type_conflict=len(types)>1,
            valvular_severity=(severity[0] if len(severity)==1 else 'UNKNOWN') if name!='ATRIAL_FIBRILLATION' else None,
            valvular_severities=severity,valvular_severity_multiple=len(severity)>1,
            qualifier_observations=[dict(record_id=r['record_id'],raw_label=r['raw_candidate'],accepted_date=r['accepted_date'],
                source_ids=r['source_ids'],**{k:f[k] for k in ('matched_source_text','qualifier_source_text','af_types','valvular_severities','source_qualifier_observations')}) for r,f in observations],
            legacy_canonical_names=sorted({d['canonical_name'] for d in legacy}),legacy_canonical_ids=sorted({d['id'] for d in legacy}),
            legacy_statuses=sorted(statuses),legacy_etiologies=sorted({d['etiology'] for d in legacy}),
            legacy_domains=sorted({d['domain'] for d in legacy}),legacy_subcategories=sorted({d['subcategory'] for d in legacy}),
            taxonomy_basis='V22_HARD_DETERMINISTIC_CARDIOVASCULAR_RULE',review='PHYSICIAN_REVIEW'))
    tables['canonical_diagnoses']=retained
    # Rebuild all joins from retained source membership; secondary AF can have
    # its own identity while keeping the primary heart-failure diagnosis.
    for key,r in candidates.items():
        groups=[d for d in retained if d['patient_id']==key[0] and key[1] in d['member_ids']]
        r['canonical_diagnosis_ids']=sorted(d['id'] for d in groups)
        nominated=next((d for d in groups if d['canonical_name']==primary.get(key)),None)
        original=next((d for d in groups if d['id']==r['canonical_diagnosis_id']),None)
        r['canonical_diagnosis_id']=(nominated or original or (groups[0] if groups else {})).get('id')
    for m in tables['diagnosis_mentions']:
        m['canonical_diagnosis_ids']=sorted({d['id'] for d in retained if d['patient_id']==m['patient_id'] and set(d['member_ids'])&set(m['record_ids'])})
    for d in retained:
        d['evidence_ids']=[m['id'] for m in tables['diagnosis_mentions'] if d['id'] in m['canonical_diagnosis_ids']]
        d['original_mention_count']=len(d['evidence_ids'])
    tables['diagnosis_rejected_non_disease_text']=[r for r in tables['diagnosis_rejected_non_disease_text'] if r['candidate_type']!='DIAGNOSIS_DISEASE']
    gates=audit_cardiovascular(tables)
    review=[]
    for key,r in candidates.items():
        if not observed[key] and not any(rx.search(r['raw_candidate']) for rx in MATCHERS.values()):continue
        for name,rx in MATCHERS.items():
            if not rx.search(r['raw_candidate']):continue
            d=next((d for d in retained if d['patient_id']==key[0] and d['canonical_name']==name and key[1] in d['member_ids']),None)
            review.append(dict(patient_id=key[0],record_id=key[1],original_label=r['raw_candidate'],
                candidate_type=r['candidate_type'],canonical_diagnosis_id=d['id'] if d else None,
                **{k:(d or {}).get(k) for k in ('canonical_name','display_name','domain','category','subcategory','af_type','af_types','valvular_severity','valvular_severities')},
                source_ids=r['source_ids'],reason='V22_CANONICALIZED' if d else 'NEGATED_UNCERTAIN_OR_NON_DIAGNOSIS_NOT_PROMOTED'))
    return review,gates


def audit_cardiovascular(tables):
    gates=dict(vorhofflimmern_not_canonicalized_to_AF=0,TRINS_as_neurologic=0,MINS_as_neurologic=0,
        TRINS_not_structural_heart_disease=0,MINS_not_structural_heart_disease=0,
        atrial_flutter_merged_into_AF=0,duplicate_v22_cardiovascular_identity=0,cardiovascular_qualifiers_not_preserved=0)
    diagnoses=tables['canonical_diagnoses']
    for r in tables['diagnosis_candidate_eligibility']:
        fs=record_findings(r)
        if r['candidate_type']!='DIAGNOSIS_DISEASE':continue
        # Independent spelling census catches OCR-glued names even if a name
        # matcher's word-boundary logic regresses.
        for prefix,expected,pattern in [('MINS','MITRAL_REGURGITATION',r'(?:\b|gradig\w{0,2})MINS\b'),('TRINS','TRICUSPID_REGURGITATION',r'(?:\b|gradig\w{0,2})TRINS\b')]:
            if re.search(pattern,r['raw_candidate'],re.I) and not any(f['canonical_name']==expected for f in fs) and not NEGATIVE.search(r['raw_candidate']):
                gates[prefix+'_not_structural_heart_disease']+=1
                gates[prefix+'_as_neurologic']+=any(d['domain']=='NEUROLOGIC' for d in diagnoses if d['patient_id']==r['patient_id'] and r['record_id'] in d['member_ids'])
        for f in fs:
            name=f['canonical_name']
            ds=[d for d in diagnoses if d['patient_id']==r['patient_id'] and r['record_id'] in d['member_ids'] and d['canonical_name']==name]
            valid=any(d['domain']=='CARDIOVASCULAR' and d['category']==RULES[name][2] and d['subcategory']==name for d in ds)
            if ds:
                gates['cardiovascular_qualifiers_not_preserved']+=not any(set(f['af_types'])<=set(d.get('af_types',[])) and set(f['valvular_severities'])<=set(d.get('valvular_severities',[])) for d in ds)
            if name=='ATRIAL_FIBRILLATION':
                gates['vorhofflimmern_not_canonicalized_to_AF']+=not (valid and any(d.get('display_name')=='Atrial fibrillation' for d in ds))
            else:
                prefix='TRINS' if name=='TRICUSPID_REGURGITATION' else 'MINS'
                gates[prefix+'_not_structural_heart_disease']+=not valid
                gates[prefix+'_as_neurologic']+=any(d['domain']=='NEUROLOGIC' for d in diagnoses if d['patient_id']==r['patient_id'] and r['record_id'] in d['member_ids'])
        if FLUTTER.search(r['raw_candidate']) and not any(f['canonical_name']=='ATRIAL_FIBRILLATION' for f in fs):
            gates['atrial_flutter_merged_into_AF']+=any(d['canonical_name']=='ATRIAL_FIBRILLATION' for d in diagnoses if d['patient_id']==r['patient_id'] and r['record_id'] in d['member_ids'])
    keys=[(d['patient_id'],d['canonical_name']) for d in diagnoses if d['canonical_name'] in RULES]
    gates['duplicate_v22_cardiovascular_identity']=len(keys)-len(set(keys))
    return gates
