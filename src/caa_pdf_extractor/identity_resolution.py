"""Multi-source local identity resolution; direct identifiers stay private.

Official IDs take priority. Demographic matches require full normalized names,
an exact DOB, a unique compatible identity, and no contradictory information.
No source PDFs, parser artifacts, clinical tables, or existing research exports
are rewritten. Austrian HMAC person IDs retain the original v1 namespace.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
from openpyxl import load_workbook
from . import person_identity as base

ROOT = base.ROOT
AT = 'AT_SVNR'
FOREIGN = 'FOREIGN_ID'
DEMO = 'DEMOGRAPHIC_IDENTITY'
UNRESOLVED = 'UNRESOLVED'
DEMO_HIGH = 'DEMOGRAPHIC_UNIQUE_HIGH_CONFIDENCE'
DEMO_AMBIGUOUS = 'DEMOGRAPHIC_AMBIGUOUS'
DEMO_CONFLICT = 'DEMOGRAPHIC_CONFLICT'
NOT_FOUND = 'IDENTITY_NOT_FOUND'
ID_LABEL = re.compile(
    r'\b(?P<label>SVNR|VSNR|VNR|SV[\s.-]*(?:NR|NUMMER|ZAHL)|'
    r'Sozialversicherungs[\s-]*(?:nummer|nr)|Versicherungs[\s-]*(?:nummer|nr)|'
    r'Vers\.?[\s-]*Nr|Versichertennummer|Krankenversichertennummer|KVNR|'
    r'Burgerservicenummer|Bürgerservicenummer|BSN|National\s+Insurance\s+Number)\b', re.I)
PARTICLES = {'VON','VAN','DE','DEL','DER','DEN','DA','DI','DOS','DU','LE','LA','EL','AL','ZU','ZUR','ZUM','TEN','TER'}
COUNTRY_ALIASES = {
    'AT': ('OSTERREICH', 'OESTERREICH', 'OSTERREICHISCH', 'OESTERREICHISCH', 'AUSTRIA', 'AUSTRIAN', 'AUT'),
    'DE': ('DEUTSCHLAND', 'DEUTSCH', 'GERMANY', 'GERMAN', 'DEU'),
    'NL': ('NIEDERLANDE', 'NIEDERLANDISCH', 'NIEDERLAENDISCH', 'NIEDERLANDER', 'NIEDERLAENDER', 'NEDERLAND', 'NEDERLANDS', 'DUTCH', 'HOLLAND', 'NLD'),
    'CH': ('SCHWEIZ', 'SCHWEIZERISCH', 'SWITZERLAND', 'CHE'),
    'IT': ('ITALIEN', 'ITALIENISCH', 'ITALY', 'ITA'),
    'CZ': ('TSCHECHIEN', 'TSCHECHISCH', 'CZECH', 'CZE'),
    'SK': ('SLOWAKEI', 'SLOWAKISCH', 'SLOVAK', 'SVK'),
    'SI': ('SLOWENIEN', 'SLOWENISCH', 'SLOVENIA', 'SVN'),
    'HR': ('KROATIEN', 'KROATISCH', 'CROATIA', 'HRV'),
    'PL': ('POLEN', 'POLNISCH', 'POLAND', 'POL'),
    'HU': ('UNGARN', 'UNGARISCH', 'HUNGARY', 'HUN'),
    'RO': ('RUMANIEN', 'RUMANISCH', 'ROMANIA', 'ROU'),
    'BA': ('BOSNIEN', 'BOSNISCH', 'BOSNIA', 'BIH'),
    'RS': ('SERBIEN', 'SERBISCH', 'SERBIA', 'SRB'),
    'TR': ('TURKEI', 'TURKISCH', 'TURKEY', 'TUR'),
    'GB': ('GROSSBRITANNIEN', 'BRITISCH', 'BRITISH', 'GBR'),
    'US': ('USA', 'UNITED STATES', 'AMERICAN'),
}


def ascii_text(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').upper()) if not unicodedata.combining(c))


def normalized_name(value):
    value = unicodedata.normalize('NFKC', str(value or '')).upper()
    value = value.replace('Ä','AE').replace('Ö','OE').replace('Ü','UE').replace('ß','SS')
    value = ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c))
    return ''.join(c for c in value if c.isalpha())


def country(value):
    value = ascii_text(value).strip()
    for code, aliases in COUNTRY_ALIASES.items():
        if value == code or any(re.search(r'\b'+re.escape(alias)+r'\w*\b',value) for alias in aliases):
            return code
    return None


def full_name(value,*,given=False):
    tokens = re.findall(r'[^\W\d_]+',str(value),re.UNICODE)
    return (len(normalized_name(value)) >= 2 and bool(tokens) and not all(len(t)==1 for t in tokens)
            and (not given or all(len(t)>1 for t in tokens)))


def demographics(text):
    """One explicitly birth-labelled patient header, retaining original fields."""
    header='\n'.join(text.splitlines()[:45])
    for match in base.BIRTH.finditer(header):
        birth=base.canonical_dob(match['date'])
        if not birth:
            continue
        start=header.rfind('\n',0,match.start())+1
        before=header[start:match.start()].strip(' ,;')
        candidates=[before]+list(reversed(header[:start].splitlines()[-2:]))
        for candidate in candidates:
            candidate=re.sub(r'^.*?\b(?:Patient(?:in|/in)?|Patientenname|Name)\s*:\s*','',candidate,flags=re.I)
            candidate=re.sub(r'^\s*(?:Herrn?|Frau)\s+','',candidate,flags=re.I).strip(' ,;')
            parts=[p.strip() for p in candidate.split(',') if p.strip()]
            if '^' in candidate:
                pn=candidate.split('=')[0].split('^')
                if len(pn)<2:continue
                surname,given=pn[0],' '.join(part for part in pn[1:3] if part)
            elif len(parts)>=2:
                surname,given=parts[-2:]
            elif len(parts)==1:
                words=parts[0].split()
                if len(words)<2:
                    continue
                split=len(words)-1
                while split>1 and ascii_text(words[split-1]) in PARTICLES:
                    split-=1
                surname,given=' '.join(words[split:]),' '.join(words[:split])
            else:
                continue
            if any(c.isdigit() for c in surname+given) or ':' in surname+given:
                continue
            if normalized_name(surname) and normalized_name(given):
                return dict(surname=surname,given=given,dob=birth,dob_raw=match['date'])
    return None


def context(text):
    header='\n'.join(text.splitlines()[:45])
    normalized_header=ascii_text(header)
    explicit=set();raw_country=set();sex=set();raw_sex=set()
    for match in re.finditer(r'(?:Staatsb(?:ü|ue|u)rgerschaft|Staatsangeh(?:ö|oe|o)rigkeit|Nationalit(?:ä|ae|a)t|Citizenship|Nationality)\s*[:=]?\s*([^\n]{1,70})',text,re.I):
        value=match[1].strip();code=country(value)
        if code:
            explicit.add(code);raw_country.add(value)
    for match in re.finditer(r'\b(deutsch\w*|niederl(?:ä|ae|a)ndisch\w*)\s+Staatsb(?:ü|ue|u)rger\w*\b',text,re.I):
        code=country(match[1])
        if code:explicit.add(code);raw_country.add(match[0])
    for match in re.finditer(r'(?:Geschlecht|Gender|Sex)\s*[:=]?\s*(männlich|maennlich|mannlich|weiblich|divers|male|female|[MWF])\b',header,re.I):
        raw=match[1];token=ascii_text(raw);sex.add('M' if token in {'MANNLICH','MAENNLICH','MALE','M'} else 'F' if token in {'WEIBLICH','FEMALE','W','F'} else 'X');raw_sex.add(raw)
    # Patient-address country is context for a foreign insurance identifier;
    # it is deliberately not treated as proof of citizenship.
    address=set()
    for code,aliases in COUNTRY_ALIASES.items():
        if any(re.search(r'\b'+re.escape(alias)+r'\b',normalized_header) for alias in aliases if len(alias)>3):
            address.add(code)
    return dict(countries=explicit, country_raw=raw_country, sex=sex, sex_raw=raw_sex, address_countries=address)


def identifier_evidence(text,source):
    """Explicit labels only; preserve alphanumeric foreign values without coercion."""
    text=unicodedata.normalize('NFKC',text).replace('\u00ad','')
    for found in ID_LABEL.finditer(text):
        tail=text[found.end():found.end()+100]
        tail=re.sub(r'^[\s.:#=()\-–—]*','',tail)
        first=re.match(r'[A-Za-z]{0,4}[0-9][A-Za-z0-9]*',tail)
        if not first:
            continue
        end=first.end();compact=re.sub(r'[^A-Z0-9]','',first[0].upper())
        if compact.isdigit() and len(compact)<10:
            while len(compact)<10:
                following=re.match(r'[\s./\-–—]+([0-9]+)',tail[end:])
                if not following:break
                end+=following.end();compact+=following[1]
        raw=tail[:end]
        value=''.join(c for c in unicodedata.normalize('NFKC',raw).upper() if c.isalnum())
        label=found['label']
        specific='NL' if re.fullmatch(r'BSN|B[uü]rgerservicenummer',label,re.I) else 'DE' if re.fullmatch(r'KVNR|Krankenversichertennummer',label,re.I) else ''
        yield dict(raw=raw,value=value,label=label,country=specific,source=source)


def collect(payload,profile,source):
    profile['dobs'].update(base.document_dobs(payload))
    pages=payload.get('pages') or []
    if pages:
        person=demographics(pages[0].get('text',''))
        if person:profile['demographics'].add(tuple(person[k] for k in ('surname','given','dob','dob_raw')))
    for page in pages:
        text=page.get('text','')
        profile['identifiers'].extend(identifier_evidence(text,source))
        extra=context(text)
        for key,values in extra.items():profile[key].update(values)


def new_profile():
    return dict(dobs=set(),demographics=set(),identifiers=[],countries=set(),country_raw=set(),
                sex=set(),sex_raw=set(),address_countries=set())


def demographic_key(profile):
    keys={(normalized_name(s),normalized_name(g),birth) for s,g,birth,raw in profile['demographics']}
    if len(keys)>1 or len(profile['dobs'])>1 or len(profile['sex'])>1 or len(profile['countries'])>1:
        return None,DEMO_CONFLICT
    if not keys:
        return None,NOT_FOUND
    placeholders={'NN','UNKNOWN','UNBEKANNT','ANONYM','ANONYMOUS','UNSPECIFIED','NOTKNOWN'}
    if any(normalized_name(s) in placeholders or normalized_name(g) in placeholders
           for s,g,birth,raw in profile['demographics']):
        return None,NOT_FOUND
    if any(not full_name(s) or not full_name(g,given=True) for s,g,birth,raw in profile['demographics']):
        return None,DEMO_AMBIGUOUS
    key=next(iter(keys))
    if profile['dobs'] and profile['dobs']!={key[2]}:
        return None,DEMO_CONFLICT
    return key,None


def official(profile):
    at=set();foreign=defaultdict(set);specific_person_ids=set()
    sources=defaultdict(set)
    for item in profile['identifiers']:sources[(item['country'],item['value'])].add(item['source'])
    non_at=(profile['countries'] or profile['address_countries'])-{'AT'}
    known_country=next(iter(non_at)) if len(non_at)==1 else ''
    explicit_foreign_country=bool(profile['countries']-{'AT'})
    for item in profile['identifiers']:
        value=item['value'];specific=item['country']
        if not specific and base.valid_svnr(value):
            at.add(value);continue
        if not any(c.isdigit() for c in value) or len(value)<5 or not any(c!='0' for c in value):
            continue
        if specific:
            if (specific=='DE' and re.fullmatch(r'[A-Z][0-9]{9}',value)) or (specific=='NL' and re.fullmatch(r'[0-9]{9}',value)):
                foreign[(specific,value)].add(item['source'])
                specific_person_ids.add((specific,value))
            elif len(sources[(specific,value)])>=2:
                foreign[(specific,value)].add(item['source'])
        elif re.fullmatch(r'[A-Z][0-9]{9}',value):
            foreign[(known_country or 'DE',value)].add(item['source'])
            specific_person_ids.add((known_country or 'DE',value))
        elif known_country and known_country!='AT' and (explicit_foreign_country or len(sources[('',value)])>=2):
            foreign[(known_country,value)].add(item['source'])
        elif value.isdigit() and len(value)!=10 and len(sources[('',value)])>=2:
            foreign[('UNKNOWN',value)].add(item['source'])
    # A recognized full person-identifier format takes priority over an
    # unspecified insurance/policy number. All original values remain private;
    # lower-specificity values are never turned into aliases by partial matching.
    return at,specific_person_ids or set(foreign)


def initial(profile):
    at,foreign=official(profile)
    key,demographic_issue=demographic_key(profile)
    result=dict(identity_type=UNRESOLVED,status=NOT_FOUND,confidence='NONE',token=None,
                at=at,foreign=foreign,demo=key,demographic_issue=demographic_issue)
    if len(at)>1:
        result['status']=base.MULTIPLE
    elif len(at)==1:
        value=next(iter(at))
        if len(profile['dobs'])>1 or any(base.dob_suffix(birth)!=value[-6:] for birth in profile['dobs']):
            result['status']=base.DOB_CONFLICT
        else:
            result.update(identity_type=AT,status=base.MATCH if profile['dobs'] else base.UNAVAILABLE,
                          confidence='HIGH' if profile['dobs'] else 'MEDIUM',token=(AT,value))
    elif len(foreign)>1:
        result['status']='FOREIGN_ID_CONFLICT'
    elif len(foreign)==1:
        if demographic_issue==DEMO_CONFLICT:
            result['status']=DEMO_CONFLICT
        else:
            code,value=next(iter(foreign))
            result.update(identity_type=FOREIGN,status='FOREIGN_ID_LABELLED_STABLE',confidence='HIGH',token=(FOREIGN,code,value))
    elif demographic_issue:
        result['status']=demographic_issue
    else:
        result.update(identity_type=DEMO,status=DEMO_HIGH,confidence='HIGH',token=(DEMO,*key))
    return result


def compatible(profiles,pids):
    return all(len(set().union(*(profiles[pid][field] for pid in pids)))<=1 for field in ('dobs','sex','countries'))


def assign(profiles):
    results={pid:initial(profile) for pid,profile in profiles.items()}
    anchors=defaultdict(list)
    for pid,result in results.items():
        if result['token'] and result['identity_type']!=DEMO:anchors[result['token']].append(pid)
    for token,pids in anchors.items():
        if not compatible(profiles,pids):
            for pid in pids:results[pid].update(identity_type=UNRESOLVED,status=DEMO_CONFLICT,confidence='NONE',token=None)
    by_demo=defaultdict(list)
    for pid,result in results.items():
        if result['demo']:by_demo[result['demo']].append(pid)
    aliases={result['token']:result['token'] for result in results.values() if result['token']}
    def root(token):
        while aliases.get(token,token)!=token:token=aliases[token]
        return token
    for key,pids in by_demo.items():
        official_tokens={root(results[pid]['token']) for pid in pids if results[pid]['token'] and results[pid]['identity_type']!=DEMO}
        at_tokens={token for token in official_tokens if token[0]==AT}
        foreign_tokens={token for token in official_tokens if token[0]==FOREIGN}
        safe=compatible(profiles,pids) and len(at_tokens)<=1 and len(foreign_tokens)<=1
        if any(results[pid]['identity_type']==UNRESOLVED and results[pid]['status']!=NOT_FOUND for pid in pids):
            safe=False
        if safe and len(official_tokens)==2:
            preferred=next(iter(at_tokens));other=next(iter(foreign_tokens))
            # Never form a component containing two different Austrian numbers.
            component={token for token in aliases if root(token) in {preferred,other} and token[0]==AT}
            if len(component)>1:safe=False
            else:aliases[other]=preferred;official_tokens={preferred}
        for pid in pids:
            result=results[pid]
            if result['identity_type']!=DEMO:continue
            if not safe or len(official_tokens)>1:
                result.update(identity_type=UNRESOLVED,status=DEMO_CONFLICT if not compatible(profiles,pids) else DEMO_AMBIGUOUS,
                              confidence='NONE',token=None)
            elif official_tokens:
                result['token']=next(iter(official_tokens))
    for result in results.values():
        if result['token']:result['token']=root(result['token'])
    return results


def pseudonym(secret,token):
    if token[0]==AT:return base.person_id(secret,token[1])
    domain=b'pdf-extractor.person-identity.foreign.v1\x00' if token[0]==FOREIGN else b'pdf-extractor.person-identity.demographic.v1\x00'
    value=json.dumps(token[1:],ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return 'P_'+hmac.new(secret,domain+value,hashlib.sha256).hexdigest()[:16]


def fallback_demographics(profiles):
    path=ROOT/'data/old_retool_db/orbis_01012015_01062023_amyloidangiopat.xlsx'
    if not path.exists():return
    workbook=load_workbook(path,read_only=True,data_only=True)
    try:
        for sheet in ('CAA','orbis_amyloidangiopat'):
            iterator=workbook[sheet].iter_rows(values_only=True);headers=next(iterator)
            columns={key:headers.index(key) for key in ('Fallnummer','Nachname','Vorname','Geburtsdatum')}
            found=defaultdict(set)
            for row in iterator:
                pid=str(row[columns['Fallnummer']] or '').strip()
                if pid not in profiles or profiles[pid]['demographics']:continue
                birth=row[columns['Geburtsdatum']]
                if hasattr(birth,'date'):birth=birth.date()
                parsed=base.canonical_dob(birth)
                if parsed and row[columns['Nachname']] and row[columns['Vorname']]:
                    found[pid].add((str(row[columns['Nachname']]),str(row[columns['Vorname']]),parsed,str(birth)))
            for pid,values in found.items():profiles[pid]['demographics'].update(values)
    finally:workbook.close()


def ocr_header(pdf, page_number=0, rotation=0):
    """Installed Windows OCR, private scratch image, no cloud/OCR text logging."""
    if os.name!='nt':return ''
    directory=ROOT/'data/private_identity';directory.mkdir(parents=True,exist_ok=True);base.protect(directory)
    with tempfile.TemporaryDirectory(prefix='identity_header_',dir=directory) as scratch:
        path=Path(scratch)/'header.png'
        with pymupdf.open(pdf) as document:
            if page_number >= len(document):return ''
            page=document[page_number];scale=min(3,2550/max(page.rect.width,page.rect.height))
            page.get_pixmap(matrix=pymupdf.Matrix(scale,scale).prerotate(rotation),alpha=False).save(path)
        helper=ROOT/'scripts/identity_header_ocr.ps1'
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(helper),'-ImagePath',str(path)],
                              capture_output=True,encoding='utf-8',errors='replace',timeout=60)
        if result.returncode:return ''
        try:return '\n'.join(json.loads(result.stdout))
        except (ValueError,TypeError):return ''


def regression_failures(profiles,results):
    path=ROOT/'data/private_identity/identity_regression_cases.csv'
    if not path.exists():return 1
    with path.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    failures=0
    for row in rows:
        pid=row['fallnr'];result=results.get(pid);profile=profiles.get(pid)
        if result is None or profile is None:failures+=1;continue
        expected=row['expected_identifier'];found={item['value'] for item in profile['identifiers']}
        if row['check']=='ABSENT_AT':
            failures+=int(result['identity_type']==AT or bool(expected and expected in found))
        elif row['check']=='AT_SOURCE':
            failures+=int(expected not in found or not base.valid_svnr(expected) or result['identity_type']!=AT)
        elif row['check']=='FOREIGN_SOURCE':
            failures+=int(expected not in found or result['identity_type']!=FOREIGN or expected not in {value for code,value in result['foreign']})
        elif row['check']=='ACCEPTED_ANY_TYPE':failures+=int(result['identity_type']==UNRESOLVED)
    return failures


def run(key_file=None):
    # A folder can contain multiple physician-adjudicated people. Never restore
    # the old folder-only conflict rows on a subsequent automatic rebuild.
    if (ROOT/'data/private_identity/pdf_identity_adjudications.csv').exists():
        from .identity_adjudication import run as rebuild_adjudicated_identity
        return rebuild_adjudicated_identity(key_file)
    private_path=ROOT/'data/private_identity/person_identity_crosswalk.csv'
    safe_path=ROOT/'data/patient_identity_crosswalk.csv'
    review_path=ROOT/'output/person_identity_review.csv'
    protected={path:base.utils.get_sha256(path) for path in (ROOT/'output').glob('*.csv') if path!=review_path}
    secret_path=Path(key_file or os.getenv('PERSON_ID_HMAC_KEY_FILE') or ROOT/'caa_private_linkage/mri_exam_key_PRIVATE.key')
    secret=base.load_secret(secret_path)
    if secret is None:raise RuntimeError('LOCAL_HMAC_KEY_REQUIRED')
    protected[secret_path]=base.utils.get_sha256(secret_path)
    old_ids={}
    if private_path.exists():
        with private_path.open(encoding='utf-8-sig',newline='') as stream:
            for row in csv.DictReader(stream):
                if row.get('person_id') and base.valid_svnr(row.get('svnr_normalized','')):
                    old_ids[row['svnr_normalized']]=row['person_id']
    patients,rows=base.inventory();parsed,reparsed,unavailable=base.payloads(rows)
    profiles={pid:new_profile() for pid in patients}
    considered=Counter()
    for number,row in enumerate(rows,1):
        payload=parsed.get(row['digest'])
        if payload:collect(payload,profiles[row['fallnr']],row['digest'])
        considered[row['fallnr']]+=1
        if number%500==0:print(f'IDENTITY_SCAN {number}/{len(rows)}',flush=True)
    fallback_demographics(profiles)
    # OCR only when neither a usable official identifier nor complete parsed/
    # structured demographics exists; valid IDs never require readable PDFs elsewhere.
    ocr_reads=0
    for pid,profile in profiles.items():
        at,foreign=official(profile);key,issue=demographic_key(profile)
        if at or foreign or key or issue==DEMO_CONFLICT:continue
        for row in rows:
            if row['fallnr']!=pid:continue
            try:
                value=ocr_header(row['path']);ocr_reads+=1
            except (OSError,RuntimeError,subprocess.TimeoutExpired):continue
            if value:collect(dict(document={},pages=[dict(text=value)]),profile,'ocr:'+row['digest'])
    results=assign(profiles)
    failures=regression_failures(profiles,results)
    if failures:raise RuntimeError('PRIVATE_IDENTITY_REGRESSION_FAILED')
    private=[];safe=[];review=[];groups=defaultdict(set);collision={}
    for pid in patients:
        result=results[pid];profile=profiles[pid];token=result['token']
        person=pseudonym(secret,token) if token else ''
        if token:
            if person in collision and collision[person]!=token:raise ValueError('PERSON_ID_COLLISION')
            collision[person]=token;groups[person].add(pid)
            if token[0]==AT and token[1] in old_ids and old_ids[token[1]]!=person:raise ValueError('EXISTING_PERSON_ID_CHANGED')
        common=dict(person_id=person,fallnr=pid,identity_type=result['identity_type'],
                    identity_status=result['status'],identity_confidence=result['confidence'])
        safe.append(common)
        identity_value='';identity_country=''
        if result['identity_type']==AT:identity_value=next(iter(result['at']));identity_country='AT'
        elif result['identity_type']==FOREIGN:identity_country,identity_value=next(iter(result['foreign']))
        elif result['identity_type']==DEMO:identity_value=json.dumps(result['demo'],ensure_ascii=False,separators=(',',':'))
        private.append(dict(common,identity_value_private=identity_value,identity_country=identity_country,
            svnr_normalized=';'.join(sorted(result['at'])),
            surname_raw=';'.join(sorted({item[0] for item in profile['demographics']})),
            given_name_raw=';'.join(sorted({item[1] for item in profile['demographics']})),
            dob=';'.join(sorted(profile['dobs'] or {item[2] for item in profile['demographics']})),
            dob_raw=';'.join(sorted({item[3] for item in profile['demographics']})),
            sex_raw=';'.join(sorted(profile['sex_raw'])),nationality_raw=';'.join(sorted(profile['country_raw'])),
            identifiers_raw=';'.join(sorted({item['label']+'='+item['raw'] for item in profile['identifiers']}))))
        if result['identity_type']==UNRESOLVED:
            review.append(dict(fallnr=pid,identity_status=result['status'],short_reason={
                base.MULTIPLE:'Multiple different valid Austrian identifiers',base.DOB_CONFLICT:'DOB conflicts with Austrian identifier',
                'FOREIGN_ID_CONFLICT':'Multiple different stable foreign identifiers',DEMO_CONFLICT:'Contradictory demographic evidence',
                DEMO_AMBIGUOUS:'Full identity is incomplete or not unique',NOT_FOUND:'No usable identifier or complete full-name/DOB identity'
                }.get(result['status'],'Manual identity review required')))
    counts=Counter(result['identity_type'] for result in results.values())
    assert sum(counts.values())==len(patients)==len({row['fallnr'] for row in safe})
    assert considered==Counter(row['fallnr'] for row in rows)
    assert all(bool(row['person_id'])==(row['identity_type']!=UNRESOLVED) for row in safe)
    assert all(len(person)==18 and person.startswith('P_') for person in groups)
    assert set(profiles)=={path.name for path in (ROOT/'pdf_db').iterdir() if path.is_dir()}
    base.write_csv(private_path,list(private[0]),private,private=True)
    base.write_csv(safe_path,['person_id','fallnr','identity_type','identity_status','identity_confidence'],safe)
    base.write_csv(review_path,['fallnr','identity_status','short_reason'],review)
    assert all(base.utils.get_sha256(path)==digest for path,digest in protected.items())
    print(f'EXISTING_PARSES_REUSED={len(rows)-reparsed} LOCAL_OCR_FIRST_PAGES={ocr_reads}',flush=True)
    for label,value in [('TOTAL_FALLNR',len(patients)),('IDENTITY_AT_SVNR',counts[AT]),('IDENTITY_FOREIGN_ID',counts[FOREIGN]),
        ('IDENTITY_DEMOGRAPHIC_HIGH_CONFIDENCE',counts[DEMO]),('IDENTITY_UNRESOLVED',counts[UNRESOLVED]),
        ('UNIQUE_PERSONS',len(groups)),('PERSONS_WITH_MULTIPLE_FALLNR',sum(len(group)>1 for group in groups.values())),
        ('SVNR_EXTRACTION_REGRESSION_FAILURES',failures),
        ('IDENTITY_CONFLICTS',sum(row['status']!=NOT_FOUND and row['identity_type']==UNRESOLVED for row in results.values())),
        ('PDF_FILES_MOVED',0),('LLM_CALLS',0),('DATABASE_WRITES','NONE')]:print(f'{label} = {value}',flush=True)
