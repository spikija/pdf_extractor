"""Literal reference-column provenance and reference-based research flags."""
from decimal import Decimal,InvalidOperation
import re


def parse_reference(text):
    text=text.strip();nums=re.findall(r'\d+(?:[.,]\d+)?',text)
    low=high=None
    if len(nums)==2 and re.search(r'[-–]',text):low,high=[n.replace(',','.') for n in nums]
    elif len(nums)==1:
        if re.match(r'\s*[>≥]',text):low=nums[0].replace(',','.')
        elif re.match(r'\s*[<≤]',text):high=nums[0].replace(',','.')
    return low,high


def reference_flag(value,low,high,text=None):
    try:
        number=Decimal(str(value));lo=Decimal(str(low)) if low is not None else None;hi=Decimal(str(high)) if high is not None else None
    except InvalidOperation:return 'UNKNOWN'
    if lo is not None and (number<lo or number==lo and re.match(r'^\s*>[^=]',text or '')):return 'LOW'
    if hi is not None and (number>hi or number==hi and re.match(r'^\s*<[^=]',text or '')):return 'HIGH'
    return 'NORMAL' if lo is not None or hi is not None else 'UNKNOWN'


def candidate_reference(candidate):
    label=candidate['biomarker_label_bbox'];unit=candidate.get('unit_bbox')
    if not unit:return dict(reference_low=None,reference_high=None,reference_text=None,reference_tokens=[])
    tokens=[t for t in candidate['source_tokens'] if t['x0']>=label[2] and t['x1']<=unit[0] and abs((t['y0']+t['y1'])/2-(label[1]+label[3])/2)<3]
    text=' '.join(t['text'] for t in sorted(tokens,key=lambda t:t['x0'])).strip()
    low,high=parse_reference(text)
    return dict(reference_low=low,reference_high=high,reference_text=text or None,reference_tokens=tokens)
