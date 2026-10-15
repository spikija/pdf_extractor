"""Fail-closed disease-date ownership, with exact selected-source offsets.

The model supplies semantics only. No date is authorized by item co-occurrence.
"""
from datetime import date
import re

VERSION = 'disease_date_ownership_v1'
FULL = re.compile(r'(?<!\d)(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\d)')
PARTIAL = re.compile(r'(?<![\d./-])(?:[01]?\d/\d{2}(?:\d{2})?|(?:19|20)\d{2})(?![\d./-])')
PROCEDURE = re.compile(r'\b(?:Gastroskopie|Endoskopie|Biopsie|Operation|operat\w*|OP|AK-Ersatz|Kraniotomie|Craniotomie|Entleerung|Hämatomentleerung|Subtraktionsangiographie|Eradikation|Resektion|Segmentresektion|TUR-B|MRT|MRI|CT|Untersuchung|Kontrolle|MMSE|MoCA|Aufnahme|Entlassung|Behandlung|Therapie|Reposition|Verplattung)\b', re.I)
ASSOCIATED = re.compile(r'\b(?:bei|im\s+Rahmen\s+(?:einer|eines|von))\s+', re.I)


def structure(text, offset=0):
    # Wrapped lines remain together; explicit bullets define independent children.
    boundaries = [0] + [m.start() for m in re.finditer(r'(?m)^[ \t]*[-•][ \t]+', text)] + [len(text)]
    # Sentences and semicolon-separated events cannot borrow each other's dates.
    boundaries += [m.end() for m in re.finditer(r';[ \t]*|(?<=[a-zäöü])\.[ \t]+(?=[A-ZÄÖÜ])',text)]
    boundaries = sorted(set(boundaries))
    clauses = []
    parents = []
    for n, (a,b) in enumerate(zip(boundaries, boundaries[1:])):
        if not text[a:b].strip(): continue
        indent = -1 if a==0 else len(re.match(r'[ \t]*',text[a:b]).group().expandtabs(4))
        while parents and parents[-1][0]>=indent: parents.pop()
        clause_id=f'clause{n+1:02d}'
        clauses.append({'clause_id':clause_id,'start':offset+a,'end':offset+b,
                        'source_text':text[a:b],'indent':indent,
                        'parent_clause_id':parents[-1][1] if parents else None})
        parents.append((indent,clause_id))
    tokens = []
    for pattern, kind in ((FULL,'FULL'),(PARTIAL,'PARTIAL')):
        for m in pattern.finditer(text):
            if kind=='PARTIAL' and any(a.start()<=m.start()<a.end() for a in FULL.finditer(text)): continue
            tokens.append({'text':m.group(),'start':offset+m.start(),'end':offset+m.end(),'kind':kind})
    return clauses, sorted(tokens,key=lambda t:t['start'])


def attribute(text, disease, *, offset=0, item_id=None):
    from .diagnosis_items import normalized_label
    clauses,tokens = structure(text,offset)
    label = normalized_label(disease)
    matches = [c for c in clauses if label and label in normalized_label(c['source_text'])]
    out = {'rule_version':VERSION,'item_id':item_id,'clauses':clauses,'date_tokens':tokens,
           'clause_id':None,'disease_span':None,'date_span':None,'relation':'NONE',
           'status':'NULL_NO_DIRECT_DATE','reason':'DATE_NOT_OWNED_BY_DISEASE',
           'partial_date_text':[]}
    # Multiple source occurrences cannot establish which event the model meant.
    if len(matches)!=1: return None,out
    c=matches[0]; body=c['source_text']; base=c['start']
    out['clause_id']=c['clause_id']
    # Token-level normalized matching preserves offsets despite supported expansions.
    words=list(re.finditer(r'\S+',body)); spans=[]
    for n,w in enumerate(words):
        for end in words[n:]:
            fragment=body[w.start():end.end()]
            norm=normalized_label(fragment)
            if norm==label:
                spans.append((w.start(),end.end()));break
            if len(norm)>len(label)+35: break
    if not spans: return None,out
    a,b=min(spans,key=lambda p:p[1]-p[0])
    out['disease_span']={'start':base+a,'end':base+b,'text':body[a:b]}
    local=[t for t in tokens if base<=t['start']<c['end']]
    full=[t for t in local if t['kind']=='FULL']
    partial=[t for t in local if t['kind']=='PARTIAL']
    # A nested etiological continuation may contain the year of the preceding child.
    idx=clauses.index(c)
    if idx+1<len(clauses):
        child=clauses[idx+1]
        if re.search(r'ätiologisch\s+(?:nicht\s+klassifiziert|unklar)',child['source_text'],re.I):
            partial += [t for t in tokens if child['start']<=t['start']<child['end'] and t['kind']=='PARTIAL']
    out['partial_date_text']=[t['text'] for t in partial]
    associated=[m for m in ASSOCIATED.finditer(body) if m.end()<=a]
    explicit=any(re.search(r'\b(?:diagnostiziert|Erstdiagnose|ED|Beginn|aufgetreten|seit)\s*(?:am|:)?\s*$',body[a:t['start']-base],re.I) for t in full)
    if associated and not explicit:
        out.update(relation='HISTORICAL_OTHER_EVENT',reason='ASSOCIATED_DISEASE_NO_DATE');return None,out
    # Procedure context may be a parent heading or part of the same event line.
    if (PROCEDURE.search(body) or (c['parent_clause_id'] and PROCEDURE.search(clauses[0]['source_text']))) and not explicit:
        out.update(relation='PROCEDURE_DATE',reason='PROCEDURE_DATE_NOT_DISEASE_DATE');return None,out
    if partial and not full:
        out.update(relation='PARTIAL_DATE',reason='PARTIAL_DATE_NOT_PERSISTABLE');return None,out
    if not full:
        if c['parent_clause_id'] and any(t['kind']=='FULL' and t['start']<base for t in tokens):
            out.update(relation='PARENT_EVENT',reason='PARENT_DATE_NOT_INHERITED')
        return None,out
    # The date must follow this disease within its own clause. A preceding event's
    # date, a separate comma/semicolon finding, or a second date is not sufficient.
    if len(full)!=1: return None,out
    t=full[0]; pos=t['start']-base
    before=body[a:pos]
    if ASSOCIATED.search(before) and not explicit:
        out.update(relation='HISTORICAL_OTHER_EVENT',reason='DATE_NOT_OWNED_BY_DISEASE');return None,out
    # A long model label can contain its own date; authorize only a single event.
    if pos<a or re.search(r';|\n[ \t]*[-•]|\b(?:sowie|danach|anschließend)\b',before,re.I): return None,out
    if ',' in body[b:pos] or ASSOCIATED.search(body[b:pos]): return None,out
    # Require adjacency through event modifiers/date semantics, not a distant clause.
    gap=body[b:pos] if b<=pos else ''
    if len(gap)>80 or re.search(r'\b(?:und|mit)\b',gap,re.I): return None,out
    # Unknown intervening disease/event words cannot establish direct ownership.
    # Anatomical modifiers and explicit onset labels are a narrow allowed bridge.
    bridge=re.sub(r'\b(?:am|seit|vom|ED|Erstdiagnose|diagnostiziert|Beginn|aufgetreten|anamnestisch|wohl|bereits|links|rechts|beidseits|bds|frontal\w*|temporal\w*|parietal\w*|okzipital\w*|suprainsulär\w*)\b','',gap,flags=re.I)
    if re.search(r'\w',bridge): return None,out
    try:
        day=date.fromisoformat(t['text']) if '-' in t['text'] else date(*map(int,reversed(t['text'].split('.'))))
    except ValueError: return None,out
    out.update(relation='DIRECT',status='VERIFIED_DIRECT',reason=None,date_span=t)
    return day.isoformat(),out


def require_date_ownership(records, metadata):
    """Explicit commit barrier in addition to checksum and original-audit replay."""
    from .radiology_artifacts import VerifiedExtractionError
    for record in records:
        matches=[m for m in metadata if m.get('record')==record]
        if not matches or any(m.get('date_validation',{}).get('rule_version')!=VERSION for m in matches):
            raise VerifiedExtractionError('DATE_ATTRIBUTION_REQUIRED')
        if record.get('date_disease') and any(m['date_validation'].get('status')!='VERIFIED_DIRECT'
                                            or m['date_validation'].get('relation')!='DIRECT' for m in matches):
            raise VerifiedExtractionError('DATE_NOT_OWNED_BY_DISEASE')
