"""Lossless diagnosis-list inventory, independent of clinical ontology coverage."""
import re

VERSION = 'STRUCTURED_DIAGNOSIS_1.0'
HEADING = re.compile(r'^(?:(?:weitere|andere)\s+)?(?:diagnosen?(?:\s+bei\s+entlassung)?|hauptdiagnosen|nebendiagnosen|vorerkrankungen|vordiagnosen)\s*:?', re.I)
STOP = re.compile(r'^(?:\w*medikation|(?:empfohlene|weitere\s+empfohlene|aktuelle)\s+medikation|medikamente|therapieempfehl|therapie\s*:|allerg|noxen|anamnese|epikrise|krankheitsverlauf|familienanamnese|sozialanamnese|aufnahmestatus|neurologischer\s*befund|befunde|zusammenfassung|procedere|empfehlungen)', re.I)
NOISE = re.compile(r'Universitätsklinikum|Gemeinnützige|Paracelsus|Privatuniversität|Landesgericht|UID-Nr|FN\s*\d|Telefon|Telefax|www\.|geboren am|geb\.:', re.I)
MARKER = re.compile(r'^\s*(?:(?P<number>\d{1,2})[.)]\s+|(?P<bullet>[-–•]+)\s+)')


def inventory(bundle):
    items=[]
    for doc in bundle['documents']:
        active=False;list_id=None;position=0
        for section in doc['sections']:
            heading=section.get('heading') or ''
            if heading:
                active=bool(HEADING.match(heading))
                if active:list_id=section['section_id'];position=0
            current=None
            def flush():
                nonlocal current,position
                if current:
                    position+=1;current['diagnosis_list_position']=position
                    current['raw_text']='\n'.join(current.pop('lines')).strip()
                    items.append(current);current=None
            for line in re.finditer(r'(?m)^.*$',section.get('source_text',section['text'])):
                value=line[0].strip()
                if not value:
                    flush();continue
                if NOISE.search(value):flush();continue
                if HEADING.match(value):
                    flush();active=True;list_id=section['section_id'];position=0
                    tail=HEADING.sub('',value,count=1).strip()
                    if not tail:continue
                    value=tail
                if STOP.match(value):flush();active=False;continue
                if not active:continue
                marker=MARKER.match(line[0])
                # An unnumbered diagnostic line is retained independently too;
                # lower-case wrapped clauses remain with their source item.
                new_unmarked=bool(current and not marker and re.match(r'[A-ZÄÖÜ]',value)
                    and not re.match(r'(?:HER2|NST\b|Ki-?67|ED\b)',value))
                if marker or current is None or new_unmarked:
                    flush()
                    current=dict(item_id=f"{section['section_id']}:structured:{line.start()}",
                        document_id=doc['source_document_id'],page=section.get('page_start',1),section_id=section['section_id'],
                        diagnosis_list_id=list_id,diagnosis_item_number=int(marker['number']) if marker and marker['number'] else None,
                        is_bullet=bool(marker and marker['bullet']),start=line.start(),lines=[])
                # Preserve words and source line breaks; strip only the list marker.
                current['lines'].append(line[0][marker.end():].strip() if marker else value)
            flush()
    return items


def attach(units, items, sections):
    """Bind inventory to existing spans where possible; add unmapped source units otherwise."""
    def normalized(s):return ' '.join(re.sub(r'^\s*(?:\d+[.)]|[-–•]+)\s*','',s).split()).casefold()
    for item in items:
        matches=[u for u in units if u['section_id']==item['section_id'] and
            (normalized(u['raw_text']) in normalized(item['raw_text']) or normalized(item['raw_text']) in normalized(u['raw_text']))]
        if not matches:
            doc,section=sections[item['section_id']]
            from .diagnosis_events_v4 import header_date
            unit=dict(span_id=item['item_id'],raw_text=item['raw_text'],document_id=item['document_id'],
                section_id=item['section_id'],heading=section.get('heading'),page=item['page'],start=item['start'],
                end=item['start']+len(item['raw_text']),document_date=header_date(doc),origin='STRUCTURED_LIST_ITEM')
            units.append(unit);matches=[unit]
        for unit in matches:unit['structured_item_ids']=list(set(unit.get('structured_item_ids',[])+[item['item_id']]))
    return units


def reconcile(patient, items, units, mentions, assertions, classify):
    """One explicit disposition per inventoried item; unknowns are reviewable entities."""
    reconciled=[];unmapped=[]
    for item in items:
        bound=[u for u in units if item['item_id'] in u.get('structured_item_ids',[])]
        ids={u['span_id'] for u in bound}
        from .diagnosis_events_v4 import concepts
        names={h[2] for h in concepts(item['raw_text'])}
        typed=[a for a in assertions if a['evidence_id'] in ids and a['canonical_name'] in names]
        classification,_=classify(item['raw_text'],authority=True)
        if typed:disposition='MAPPED_DISEASE_OR_EVENT'
        elif classification in ('HEADING','THERAPY','PROCEDURE','SYMPTOM','INVESTIGATION_FINDING'):
            disposition='NON_DISEASE_EVENT'
        else:disposition='UNMAPPED_DIAGNOSIS_CANDIDATE'
        record=dict(item,patient_id=patient,disposition=disposition,
            canonical_candidates=sorted({a['canonical_name'] for a in typed}),
            source_evidence_ids=sorted(ids),review_required=True,inventory_version=VERSION)
        if disposition=='UNMAPPED_DIAGNOSIS_CANDIDATE':
            from .diagnosis_events_v4 import explicit_period
            value,precision=explicit_period(item['raw_text'])
            record.update(candidate_id=f"{patient}:UNMAPPED:{item['item_id']}",candidate_type=disposition,
                assertion='NEGATED' if classification=='NEGATED' else 'UNCERTAIN' if classification=='DIFFERENTIAL_DIAGNOSIS' or re.search(r'verdacht|suspekt|suspiz|\bDD\b',item['raw_text'],re.I) else 'SOURCE_ONLY',
                date=value,date_precision=precision,document_id=item['document_id'],section=item['section_id'])
            unmapped.append(record)
        reconciled.append(record)
    return reconciled,unmapped
