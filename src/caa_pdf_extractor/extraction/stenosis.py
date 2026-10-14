"""Conservative classification of explicit, current arterial stenosis statements."""
import re

VESSEL = re.compile(r'\b(?:ICA|ACI|ACM|MCA|A\.?\s*carotis\s+interna|arteria\s+carotis\s+interna|'
                    r'A\.?\s*cerebri\s+media|arteria\s+cerebri\s+media)\b',re.I)
SIDE = re.compile(r'\b(?:rechts|rechtsseitig\w*|rechte[nrms]?|right|links|linksseitig\w*|linke[nrms]?|left)\b',re.I)
PERCENT = re.compile(r'(?P<op>>=?|>=?|≤|≥|<)?\s*(?P<number>\d{1,3}(?:[.,]\d+)?)\s*(?:%|perc\b|Prozent\b)',re.I)
NEGATIVE = re.compile(r'\b(?:kein\w*|ohne|no|not|ausgeschlossen)\b|\bnicht\s+(?:nachweisbar|vorhanden)',re.I)
HISTORICAL = re.compile(r'\b(?:historisch\w*|fr[üu]her\w*|vormals|previous|historical)\b',re.I)
UNCERTAIN = re.compile(r'\b(?:Verdacht|fraglich|suspekt|suspected|possible)\b',re.I)


def degree_attached_to_stenosis(clause, percent):
    prefix, suffix=clause[:percent.start()],clause[percent.end():]
    # "60% Stenose" and the enum's "50perc right stenosis" are explicit.
    if re.match(r'\s*(?:ige\w*\s*)?stenos',SIDE.sub('',suffix),re.I): return True
    before=re.search(r'stenos\w*(.*)$',prefix,re.I)
    if not before: return False
    tail=SIDE.sub('',VESSEL.sub('',before.group(1)))
    tail=re.sub(r'\b(?:der|des|in|von|mit|grad|grade|degree|of)\b','',tail,flags=re.I)
    return not re.sub(r'[\s:=(\-]','',tail)


def above_fifty(match):
    n=float(match.group('number').replace(',','.')); op=match.group('op') or ''
    if n>100: return False
    if op in ('<','<=','≤'): return False
    # >=50 includes 50 and therefore does not prove >50.
    return n>50 or (n==50 and op=='>')


def supported_categories(text):
    categories=set()
    # Keep vessel, laterality and severity in one clause. Never borrow right/left
    # from a neighbouring artery or a percentage from another finding.
    clauses=re.split(r';|(?<=[.!?])\s+(?=[A-ZÄÖÜ])',' '.join(text.split()))
    for clause in clauses:
        if not re.search(r'stenos',clause,re.I) or NEGATIVE.search(clause) or HISTORICAL.search(clause) or UNCERTAIN.search(clause) or '?' in clause: continue
        vessels=list(VESSEL.finditer(clause)); sides=list(SIDE.finditer(clause)); percentages=list(PERCENT.finditer(clause))
        if vessels:
            if len(vessels)!=1 or len(sides)!=1 or len(percentages)!=1: continue
            if re.search(r'\d\s*[-–]\s*'+re.escape(percentages[0].group('number')),clause): continue
            if not above_fifty(percentages[0]): continue
            if not degree_attached_to_stenosis(clause,percentages[0]): continue
            vessel=vessels[0].group().casefold()
            family='ica' if re.search(r'ica|aci|carotis',vessel) else 'acm'
            side='right' if re.match(r'recht|right',sides[0].group(),re.I) else 'left'
            value={('ica','right'):'ica > 50perc right stenosis',('ica','left'):'ica > 50perc left stenosis',
                   ('acm','right'):'acm right > 50 perc stenosis',('acm','left'):'acm left >50perc stenosis'}[(family,side)]
            categories.add(value)
        elif re.search(r'A\.?\s*cerebri\s+(?:anterior|posterior)|ACA\b|ACP\b|PCA\b|cerebral|zerebral|intrakraniell',clause,re.I):
            categories.add('other cerebral vessel stenosis')
    return categories
