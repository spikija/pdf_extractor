"""Source-preserving CSF analyte matching and numeric laboratory-row extraction.

No database, model or filesystem access. Normalization applies to analyte names
only; an unspecified amyloid assay never acquires an inferred isoform.
"""
import re
import unicodedata

AMYLOID = r'(?:A\s*[- ]?\s*(?:β|ß|beta)|(?:β|ß|beta|B)[-\s]*Amyloid|Amyloid(?:[-\s]*(?:β|ß|beta))?|Abeta)'
ISOFORM = r'(?:\s*[- ]?\s*(?:1\s*[-–]\s*)?(?:42|40))'
ANALYTE = re.compile(
    rf'(?<!\w)(?:{AMYLOID}{ISOFORM}?(?:\s*[/：:]\s*(?:{AMYLOID}\s*)?(?:1\s*[-–]\s*)?40)?(?:\s*(?:Quotient|ratio))?'
    r'|(?:Gesamt[-\s]*Tau|Tau\s*gesamt|total[-\s]*Tau|t[- ]?Tau)(?:[- ]*Protein)?'
    r'|(?:p[- ]?Tau|phospho[- ]*Tau|phosphoryliertes\s*Tau)(?:[- ]*Protein)?(?:\s*[-(]?\s*(?:181|217|231)\)?)?)(?!\w)', re.I)
LEXICAL = re.compile(r'β|ß[-\s]*Amyloid|Amyloid|\bAß|\bAbeta|\bA[- ]Beta', re.I)
UNIT = re.compile(r'(?<!\w)(?:pg\s*/\s*m[lL]|ng\s*/\s*[lL]|ng\s*/\s*m[lL]|ratio|dimensionless)(?!\w)', re.I)
NUMBER = r'[<>≤≥]?\s*[-+]?\d+(?:[.,]\d+)?'


def normalize_analyte(text):
    """Return a separate matching string; never normalize patient/source text."""
    value = unicodedata.normalize('NFKC', text).lower()
    value = re.sub(r'[βß](?=[-\s]*amyloid)', 'beta', value)
    value = re.sub(r'(amyloid[-\s]*)[βß]', r'\1beta', value)
    value = re.sub(r'\ba\s*[- ]?\s*[βß]', 'abeta', value)
    value = re.sub(r'\bb(?=[-\s]*amyloid)', 'beta', value)
    return re.sub(r'\s+', ' ', value).strip()


def canonical_analyte(text):
    norm = normalize_analyte(text)
    compact = re.sub(r'[\s\-–()]', '', norm)
    if 'amyloid' in compact or compact.startswith('abeta'):
        if re.search(r'42[/：:](?:abeta|betaamyloid|amyloidbeta)?(?:1)?40', compact):
            return 'CSF_ABETA42_40_RATIO'
        if re.search(r'(?:1)?42$', compact): return 'CSF_ABETA42'
        if re.search(r'(?:1)?40$', compact): return 'CSF_ABETA40'
        return 'CSF_ABETA_UNSPECIFIED'
    if re.match(r'(?:ptau|phosphotau|phosphoryliertestau)', compact):
        iso = re.search(r'(181|217|231)', compact)
        return 'CSF_PTAU' + (iso[0] if iso else '')
    if re.search(r'tau', compact): return 'CSF_TOTAL_TAU'
    return None


def decimal_text(text):
    return text.replace(' ', '').replace(',', '.')


def extract_row(line, *, csf_context=False):
    """Parse reported rows, retaining reference columns separately from results.

    A reference-before-unit table is identified explicitly. Ambiguous unlabelled
    multi-value rows are held rather than choosing a reference/serial result.
    """
    if not csf_context or re.search(r'\b(?:Serum|Plasma|blood)\b',line,re.I):
        return []
    output = []
    for match in ANALYTE.finditer(line):
        tail = line[match.end():]
        next_analyte = ANALYTE.search(tail)
        if next_analyte: tail = tail[:next_analyte.start()]
        unit = UNIT.search(tail)
        if not csf_context and not unit: continue
        before = tail[:unit.start()].strip(' :;=') if unit else ''
        after = tail[unit.end():] if unit else tail
        reference = None
        # Frozen tables: analyte | reference interval | unit | result.
        if before and re.fullmatch(rf'{NUMBER}(?:\s*[-–]\s*{NUMBER})?', before) and re.search(r'[-–<>≤≥]', before):
            reference = before
            candidates = list(re.finditer(NUMBER, after))
        else:
            # Narrative: analyte: value unit (reference ...).
            candidates = list(re.finditer(NUMBER, tail[:unit.start()] if unit else tail))
            ref = re.search(r'(?:Referenz\w*|Norm\w*|reference)\s*[:=]?\s*('+NUMBER+r'(?:\s*[-–]\s*'+NUMBER+r')?)', tail, re.I)
            if ref: reference = ref[1]
            # A single number before a unit is a result, never a reference.
            if unit and len(candidates)==1 and not re.search(r'[-–<>≤≥]', before):
                reference = ref[1] if ref else None
        if len(candidates)!=1:
            continue
        number = candidates[0][0].strip()
        # Require a numeric result cell, not a date or a prose reference.
        if not unit and (not csf_context or not re.match(r'^\s*[:=]?\s*'+NUMBER+r'\s*(?:$|[;,(!])', tail)):
            continue
        low = high = None
        if reference:
            nums = re.findall(r'\d+(?:[.,]\d+)?', reference)
            if len(nums)==2: low,high=map(decimal_text,nums)
            elif len(nums)==1:
                if re.match(r'\s*[>≥]',reference): low=decimal_text(nums[0])
                elif re.match(r'\s*[<≤]',reference): high=decimal_text(nums[0])
        output.append(dict(canonical_analyte=canonical_analyte(match[0]),original_analyte_text=match[0],
            normalized_matching_text=normalize_analyte(match[0]),value=decimal_text(number),
            unit=unit[0] if unit else None,reference_low=low,reference_high=high,
            reference_text=reference,abnormality='SOURCE_FLAGGED' if '!' in tail else 'NOT_REPORTED',
            source_start=match.start(),source_end=match.end(),original_source_row=line,
            derived=False,formula=None))
    return output
