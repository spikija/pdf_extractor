"""Conservative, evidence-first admission phenotypes and retrospective mRS.

Independent of history/imaging/EEG extraction. No model or clinical writer.
"""
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

VERSION = 'encounter_mrs_v1'
REASONS = ('FOCAL_NEUROLOGICAL_DEFICIT', 'EPILEPTIC_EVENT', 'COGNITIVE_DECLINE', 'OTHER', 'UNKNOWN')
FACTS = ('walking_independent', 'walking_aid', 'walking_human_assistance', 'bedbound',
         'independent_ADL', 'needs_ADL_help', 'toileting_independent', 'incontinence',
         'constant_nursing', 'usual_activities_possible', 'own_affairs_independent',
         'baseline_function', 'unspecified_help', 'no_symptoms', 'symptoms_present')
FACTS += ('unable_independent_walking',)
TEMPORAL = ('BASELINE', 'CURRENT_ADMISSION', 'DISCHARGE', 'UNKNOWN')
DATE = r'\d{1,2}\.\d{1,2}\.\d{4}'
REASON_SECTION = re.compile(r'^(?:Zusammenfassung des Aufenthalts|Anamnese|Aufnahmeanamnese|Vorstellungsgrund|Vorgeschichte / aktueller Beobachtungsgrund|aktueller Beobachtungsgrund)\s*$', re.I)
EXAM_SECTION = re.compile(r'^(?:Neurologischer Befund|Neurologischer Status|Neurologischer Aufnahmestatus|Status(?: bei Aufnahme)?|Aufnahmestatus|Status bei Aufnahme)\s*$', re.I)
EXCLUDE = re.compile(r'^(?:Vorerkrankungen|Vorbekannte Erkrankungen|Frühere Erkrankungen|Familienanamnese|Prämed|Dauermedikation|Aktuelle Medikation|Medikation|Allergien|SA:|VE:|Noxen)', re.I)
BASELINE = re.compile(r'\b(?:prämorbid|prästationär|prä|vor (?:dem Ereignis|der Aufnahme|diesem Aufenthalt)|zuvor|bisher|früher|vorbestehend|baseline)\b', re.I)
DISCHARGE = re.compile(r'\bEntlassung\b|\bentlassen\b|zum Entlassungszeitpunkt|Entlassungsstatus|am Entlassungstag', re.I)
CURRENT = re.compile(r'bei (?:der )?(?:Aufnahme|Übernahme|Vorstellung)|zum (?:Zeitpunkt der )?(?:Aufnahme|Vorstellung)|aktuell|derzeit|Aufnahmestatus|Aufnahmetag', re.I)
HISTORY = re.compile(r'vorbekannt|Vorerkrank|anamnestisch bekannt|Zustand nach|Z\.\s*n\.|bereits\s+(?:19|20)\d{2}|im Jahr|damals|Rezent war|letzten Aufenthalt|Voraufenthalt', re.I)
REASON_LINK = re.compile(r'aufgrund|auf Grund|wegen|zur (?:weiteren )?(?:Abklärung|Therapie|Kontrolle|Durchführung|Fortführung|Roborierung|Mobilisierung)|zur\s+klinischen|nach.{0,70}(?:Anfall|Sprachstörung)|mit.{0,70}(?:Anfall|Status epilepticus)|vorstellig|Vorstellung|Zutransfer|aufgenommen|Aufnahme|Übernahme|stellte? sich|kommt.{0,50}(?:zur|geplant)', re.I)
NEG = re.compile(r'\b(?:kein\w*|ohne|nicht|verneint)\b', re.I)
UNCERTAIN = re.compile(r'\b(?:Verdacht|fraglich|möglich\w*|DD|auszuschließen|unklar)\b', re.I)
CATEGORY = {
    'FOCAL_NEUROLOGICAL_DEFICIT': re.compile(r'Halbseitenlähmung|Halbseitenschwäche|Hemiparese|Aphasie|Dysphasie|Dysarthrie|Sprachstörung|Sprechstörung|fehlende\w*\s+Sprachproduktion|Hemianop\w*|Fazialis(?:parese|defizit)|Mundastdefizit|(?:links|rechts)seitig\w*\s+(?:Sensibilitäts|Gefühls)störung|(?:Taubheitsgefühl|Schwäche|reduziert\w*\s+Sensibilität).{0,35}(?:link|recht)\w*\s+(?:Hand|Arm|Bein)', re.I),
    'EPILEPTIC_EVENT': re.compile(r'(?:(?:epileptisch\w*|tonisch[- ]klonisch\w*|konvulsiv\w*|generalisiert\w*)\s+){1,3}Anfall\w*|Status epilepticus|fokaler?\s+(?:epileptischer?\s+)?Anfall', re.I),
    'COGNITIVE_DECLINE': re.compile(r'psychomotorisch\w*\s+Verlangsamung|Desorientiert\w*|Desoientierung\w*|Amnesie|Apraxie|Gedächtnis\w*|Kurzzeitgedächtnis\w*|kognitiv\w*\s+Verschlechterung|Verwirr\w*|Wesensveränderung|Verhalten\w*|Orientierungs\w*|Frontalhirnsyndrom', re.I),
    'OTHER': re.compile(r'geplant|elektiv|Kontrolle|Kontrollaufnahme|Fortführung|weiteren\s+Therapie|Cyclophosphamid-Gabe|Infliximab|Polysomnographie|Rehabilitation|Roborierung|Mobilisierung|sozial\w*\s+Versorgung|Zusammenbruch der häuslichen|Harnwegsinfekt|verschlechtertem Allgemeinzustand|Schwindel|Kopfschmerz\w*|Lumbalgie|Schmerz\w*', re.I),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fingerprint(bundle):
    return digest({'bundle': bundle.to_dict(), 'policy': Path(__file__).read_text(encoding='utf-8'),
                   'schema': Path(__file__).with_name('encounter_schema.py').read_text(encoding='utf-8')})


def proof(doc, sec, start, end):
    return {'source_document_id': doc.source_document_id, 'page': sec.page_start,
            'section_id': sec.section_id, 'source_start': start + getattr(sec, 'source_base', 0), 'source_end': end + getattr(sec, 'source_base', 0),
            'exact_source_text': sec.source_text[start:end]}


def source_views(doc):
    """Split embedded dated admission/exam headers without altering preparation.

    A page continuation inherits an explicit clinical subreport boundary, never
    a peer imaging, laboratory, diagnosis-list or medication boundary.
    """
    active = None
    header = re.compile(r'^\s*(?P<h>Anamnese|Aufnahmeanamnese|Neurologischer\s*Befund|Neurologischer\s*(?:Aufnahme)?status|Status bei Aufnahme|Status bei Entlassung)\s*(?:\((?P<date>' + DATE + r')\))?\s*:?\s*$', re.I)
    stop = re.compile(r'^\s*(?:Vorerkrankungen|Prämed|Medikation|Dauermedikation|SA:|VE:|Allergien|Familienanamnese|EEG|ELEKTROENZEPHAL|c?MRT|MRI|CT\b|Labor|Neuroson|Doppler|Duplex|EKG|Echokardio)', re.I)
    for sec in doc.sections:
        if sec.page_start != sec.page_end:
            active = None
            continue
        direct = REASON_SECTION.fullmatch(sec.heading or '') or EXAM_SECTION.fullmatch(sec.heading or '')
        if direct:
            active = (sec.heading, None)
        elif sec.heading is not None:
            active = None
        start = 0
        inherited = active
        for m in re.finditer(r'(?m)^.*$', sec.source_text):
            h = header.fullmatch(m[0])
            is_stop = bool(stop.match(m[0])) and not h
            if not h and not is_stop:
                continue
            if inherited and m.start() > start:
                yield SimpleNamespace(section_id=sec.section_id, source_text=sec.source_text[start:m.start()],
                                      source_base=start, heading=inherited[0], section_date=inherited[1],
                                      page_start=sec.page_start, page_end=sec.page_end,
                                      reason_continuation=not direct and start == 0)
            inherited = ('Neurologischer Befund' if h and re.match('Neurologischer', h['h'], re.I) else h['h'], iso(h['date']) if h and h['date'] else None) if h else None
            start = m.end()
        if inherited and start < len(sec.source_text):
            yield SimpleNamespace(section_id=sec.section_id, source_text=sec.source_text[start:], source_base=start,
                                  heading=inherited[0], section_date=inherited[1], page_start=sec.page_start,
                                  page_end=sec.page_end, reason_continuation=not direct and start == 0)
        active = inherited


def iso(raw):
    try:
        return date(*map(int, raw.split('.')[::-1])).isoformat()
    except ValueError:
        return None


def encounter_identity(doc):
    candidates, outpatient = [], False
    pattern = re.compile(r'wir berichten.{0,150}?sich\s+vo[nm]\s+(?P<a>' + DATE + r')\s+bis\s+(?:zum\s+)?(?P<d>' + DATE + r').{0,80}?stationären?\s+Behandlung', re.I | re.S)
    partial = re.compile(r'wir berichten.{0,150}?sich\s+vo[nm]\s+(?P<a>' + DATE + r')\s+bis\s+zur\s+stationären?\s+Behandlung', re.I | re.S)
    for sec in doc.sections:
        if sec.page_start != sec.page_end:
            continue
        outpatient |= bool(re.search(r'wir berichten.{0,180}?sich\s+am\s+' + DATE + r'.{0,60}?ambulanten', sec.source_text, re.I | re.S))
        for pattern_ in (pattern, partial):
            for m in pattern_.finditer(sec.source_text):
                candidates.append({'admission_date': iso(m['a']), 'discharge_date': iso(m.groupdict().get('d') or '') if m.groupdict().get('d') else None,
                                   'evidence': proof(doc, sec, m.start(), m.end())})
    unique = {(p['admission_date'], p['discharge_date']) for p in candidates}
    if len(unique) == 1 and all(a and (not d or d >= a) for a, d in unique):
        return {**candidates[0], 'status': 'VERIFIED_SOURCE_ENCOUNTER'}
    if candidates:
        return {'status': 'AMBIGUOUS_ENCOUNTER_IDENTITY', 'candidates': candidates}
    return {'status': 'EXCLUDED_OUTPATIENT' if outpatient else 'NO_EXPLICIT_INPATIENT_ENCOUNTER'}


def sentences(sec):
    """Keep exact offsets while avoiding date/academic abbreviation splits."""
    text = sec.source_text
    end_limit = len(text)
    for m in re.finditer(r'(?m)^\s*[^\n]+', text):
        if EXCLUDE.match(m[0].strip()):
            end_limit = m.start()
            break
    text = text[:end_limit]
    cuts = [0]
    for m in re.finditer(r'[.!?](?:\s+|$)|\n\s*\n', text):
        before = text[:m.start()]
        if m[0][0] == '.' and re.search(r'\b(?:Dr|Prof|ca|bzw|z|n|a|e|f|Pat|geb)$', before, re.I):
            continue
        cuts.append(m.end())
    cuts.append(len(text))
    for a, b in zip(cuts, cuts[1:]):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        raw = text[a:b]
        if raw and not (sec.heading and raw.strip() == sec.heading.strip()):
            yield a, b, raw


def temporal(raw, heading, admission_date, *, reason=False, context_date=None):
    text = ' '.join(raw.split())
    if DISCHARGE.search(text):
        return 'DISCHARGE'
    if context_date and admission_date and context_date != admission_date:
        return 'UNKNOWN'
    if re.search(r'Entlassung', heading or '', re.I):
        return 'DISCHARGE'
    baseline, current = bool(BASELINE.search(text)), bool(CURRENT.search(text))
    if baseline and current:
        return 'UNKNOWN'
    if baseline:
        return 'BASELINE'
    # A copied admission narrative from a different episode cannot silently
    # become the current encounter's reason or functional state.
    dates = [iso(m[0]) for m in re.finditer(DATE, text)]
    if dates and admission_date and not current and all(d and d < admission_date for d in dates):
        return 'UNKNOWN'
    if current:
        return 'CURRENT_ADMISSION'
    if HISTORY.search(text):
        return 'UNKNOWN'
    if EXAM_SECTION.fullmatch(heading or ''):
        return 'CURRENT_ADMISSION'
    if re.fullmatch(r'Anamnese|Aufnahmeanamnese', heading or '', re.I) and re.search(r'\b(?:ist|kann|geht|benötigt|braucht|kommt|wird)\b', text):
        return 'CURRENT_ADMISSION'
    if reason and REASON_LINK.search(text):
        return 'CURRENT_ADMISSION'
    return 'UNKNOWN'


def assertion(text, match, *, shared_list=False):
    prefix = re.sub(r'\(nicht\s+näher\s+bezeichnet\)', '', text[:match.start()], flags=re.I)
    before = re.split(r'[;:]|\baber\b|\bjedoch\b' if shared_list else r'[,;:]|\baber\b|\bjedoch\b', prefix, flags=re.I)[-1][-85:]
    after = text[match.end():match.end() + 30]
    if UNCERTAIN.search(before) or re.search(r'nicht auszuschließen|fraglich', after, re.I):
        return 'UNKNOWN'
    if NEG.search(before) or re.match(r'\s+(?:(?:werden|wird)\s+)?(?:verneint|nicht möglich)', after, re.I) or re.search(r'\bverneint\s*[.!]?$', text[match.end():], re.I):
        return 'ABSENT'
    return 'PRESENT'


def classify_reason(doc, identity):
    evidence, audit, selected = [], [], []
    for sec in source_views(doc):
        if not REASON_SECTION.fullmatch(sec.heading or '') or sec.reason_continuation:
            continue
        summary = bool(re.match(r'Zusammenfassung', sec.heading or '', re.I))
        units = list(sentences(sec))
        # Only bounded presentation sentences, not the later hospital course.
        episode_anchor = None
        for index, (a, b, raw) in enumerate(units[:10]):
            if a > 1800:
                break
            flat = ' '.join(raw.split())
            p = proof(doc, sec, a, b)
            time = temporal(raw, sec.heading, identity['admission_date'], reason=True, context_date=sec.section_date)
            frame = bool(re.search(r'\b(?:Aufnahme|Übernahme|übernommen|Zutransfer|zutransferiert|aufgenommen|vorstellig|Vorstellung\w*|kommt|stellte? sich)\b', flat, re.I))
            linked = bool((frame and REASON_LINK.search(flat)) or (CURRENT.search(flat) and re.search(r'erstmals|Aufnahmetag|plötzlich|aufgetreten', flat, re.I)))
            dates_here = [iso(x[0]) for x in re.finditer(DATE, flat)]
            old_date = bool(dates_here and all(d and d < identity['admission_date'] for d in dates_here))
            if linked and frame and not old_date and not DISCHARGE.search(flat) and not BASELINE.search(flat) and not (sec.section_date and sec.section_date != identity['admission_date']):
                time = 'CURRENT_ADMISSION'
            # One immediately following symptom sentence may complete an explicit
            # current presentation frame; retain that frame as separate evidence.
            if episode_anchor and index - episode_anchor[0] <= 2 and not HISTORY.search(flat) and not DISCHARGE.search(flat):
                if re.search(r'Anfall.{0,15}erlitten|im Notarztwagen|Danach kam es|Hierbei|plötzlich|gestern Nacht|gestern in der Nacht|Zusätzlich war', flat, re.I):
                    time, linked = 'CURRENT_ADMISSION', True
            if re.search(r'Zutransfer|zutransferiert|Vorstellung|Aufnahme|aufgenommen|übernommen|vorstellig|stellte? sich|stürzte', flat, re.I) and not HISTORY.search(flat):
                dates = [iso(x[0]) for x in re.finditer(DATE, flat)]
                if not dates or identity['admission_date'] in dates or re.search(r'\bam\s+' + str(date.fromisoformat(identity['admission_date']).day) + r'\.', flat):
                    episode_anchor = (index, p)
            if re.search(r'\b(?:Ehemann|Ehefrau|Sohn|Tochter|Mutter|Vater)\b.{0,25}(?:wurde|wird|erlitt|hatte)', flat, re.I) and not re.search(r'\bPatient(?:in)?\b', flat, re.I):
                linked = False
            # Assessment findings alone are not an admission indication.
            if re.match(r'Klinisch|Bei der Aufnahme zeigte|Bei Aufnahme zeigte', flat, re.I) and not re.search(r'erstmals|Aufnahmetag|plötzlich|aufgetreten', flat, re.I):
                linked = False
            causal_admission = bool(re.search(r'(?:Aufnahme|aufgenommen|vorstellig).{0,100}(?:aufgrund|wegen)|(?:aufgrund|wegen).{0,160}(?:aufgenommen|vorstellig)', flat, re.I))
            if old_date and not causal_admission:
                time, linked = 'UNKNOWN', False
            if re.search(r'konsiliar|nach initialer Aufnahme|Normalstation.{0,35}verlegt|bei Rückläufigkeit|im weiteren Verlauf', flat, re.I):
                linked = False
            selected.append(p)
            for category, pattern in CATEGORY.items():
                matches = list(pattern.finditer(flat))
                if not matches:
                    continue
                states = {('UNKNOWN' if HISTORY.search(re.split(r'[,;]', flat[:m.start()])[-1][-85:]) or
                           (category == 'OTHER' and re.search(r'nach\s*$', flat[:m.start()], re.I) and re.match('Kontrolle', m[0], re.I))
                           else assertion(flat, m, shared_list=True)) for m in matches}
                state = 'PRESENT' if 'PRESENT' in states else 'ABSENT' if states == {'ABSENT'} else 'UNKNOWN'
                row = {**p, 'category': category, 'assertion': state, 'temporal_ownership': time,
                       'reason_linked': linked, 'source_priority': 0 if summary else 1,
                       'unit_index': index,
                       'episode_anchor': episode_anchor[1] if episode_anchor and episode_anchor[0] != index and linked else None}
                if time == 'CURRENT_ADMISSION' and linked and state == 'PRESENT':
                    evidence.append(row)
                else:
                    audit.append({**row, 'rejection_reason': 'NOT_A_DEFINITE_CURRENT_ADMISSION_REASON'})
    flags = {category: True if any(e['category'] == category for e in evidence) else None for category in REASONS[:-1]}
    primary = 'UNKNOWN'
    if evidence:
        ordered = sorted(evidence, key=lambda e: (e['source_priority'], e['unit_index'], e['source_start']))
        first = ordered[0]
        tied = {e['category'] for e in ordered if (e['source_priority'], e['unit_index'], e['section_id']) == (first['source_priority'], first['unit_index'], first['section_id'])}
        clinical = tied - {'OTHER'}
        if len(clinical) == 1:
            primary = next(iter(clinical))
        elif clinical == {'FOCAL_NEUROLOGICAL_DEFICIT', 'EPILEPTIC_EVENT'} and re.search(r'postiktal', first['exact_source_text'], re.I):
            primary = 'EPILEPTIC_EVENT'
        elif not clinical:
            primary = 'OTHER'
    return {'primary_reason': primary, 'flags': flags,
            **{k.lower(): v for k, v in flags.items()}, 'evidence': evidence,
            'audit_only': audit, 'selected_source': selected,
            'primary_rule': 'EARLIEST_EXPLICIT_PRINCIPAL_PRESENTATION; ties remain UNKNOWN'}


FUNCTION_PATTERNS = {
    'walking_independent': r'(?:selbständig\w*|selbstständig\w*)\s+(?:gehen|Gehen|mobil)|mobil\s+ohne\s+Hilfsmittel|(?:gehen|Gehen)\s+ohne\s+(?:fremde\s+|personelle\s+)?Hilfe',
    'walking_aid': r'Rollator|Gehhilfe|Gehstock',
    'walking_human_assistance': r'Gehen\s+(?:nur\s+)?mit\s+(?:einer?\s+|1\s+)?(?:Hilfsperson|personeller\s+Hilfe)|(?:gehen|Gehen)\s+nur\s+mit\s+fremder\s+Hilfe|(?:gehen|Gehen)\s+ohne\s+(?:fremde\s+)?Hilfe\s+nicht\s+möglich',
    'bedbound': r'bettlägerig|bettgebunden',
    'independent_ADL': r'(?:in\s+(?:allen\s+)?(?:ADL|Aktivitäten\s+des\s+täglichen\s+Lebens)|bei\s+der\s+Körperpflege)\s+(?:vollständig\s+)?(?:selbständig|selbstständig)|versorgt\s+sich\s+(?:vollständig\s+)?selbst',
    'needs_ADL_help': r'(?:benötigt|braucht|bedarf)(?:\s+(?:(?:der|die)\s+Patient(?:in)?|er|sie))?\s+(?:fremde[rn]?\s+|personelle[rn]?\s+)?Hilfe\s+(?:vor\s+allem\s+)?bei[mr]?\s+(?:(?:der|dem)\s+)?(?:Waschen|Ankleiden|Körperpflege|Toilett\w*|Körperhygiene)|(?:kann|konnte)\s+(?:die\s+)?(?:körperlichen\s+Bedürfnisse|Körperpflege)\s+nicht\s+ohne\s+Hilfe',
    'toileting_independent': r'(?:Toilettengang|Toilettenbenutzung)\s+(?:selbständig|selbstständig)|(?:selbständig|selbstständig)\s+(?:zur|auf\s+die)\s+Toilette',
    'incontinence': r'inkontinent|Inkontinenz',
    'constant_nursing': r'(?:benötigt|braucht|bedarf|angewiesen\s+auf)(?:\s+(?:(?:der|die)\s+Patient(?:in)?|er|sie))?\s+(?:eine[rn]?\s+)?(?:ständige[rn]?|kontinuierliche[rn]?|24[- ]Stunden[- ]?)\s*(?:Pflege|Betreuung)|(?:ständige\s+Pflege|24[- ]Stunden[- ]Pflege)\s+(?:notwendig|erforderlich)',
    'usual_activities_possible': r'(?:alle|sämtliche)\s+(?:bisherigen|gewohnten|üblichen)\s+(?:Tätigkeiten|Aktivitäten)\s+(?:weiterhin\s+)?(?:möglich|durchführbar)|(?:kann|konnte)\s+(?:nicht(?:\s+mehr)?\s+)?alle\s+(?:bisherigen|gewohnten|üblichen)\s+(?:Tätigkeiten|Aktivitäten)\s+(?:ausüben|durchführen)',
    'own_affairs_independent': r'(?:eigene[n]?\s+Angelegenheiten)\s+(?:selbständig|selbstständig)|(?:regelt|erledigt)\s+(?:seine|ihre)\s+Angelegenheiten\s+(?:selbständig|selbstständig)',
    'baseline_function': r'(?:lebt|lebte)\s+(?:voll\s+)?(?:selbständig|selbstständig)|24[- ]Stunden[- ]Pflege',
    'unspecified_help': r'(?:benötigt|braucht|bedarf)\s+(?:etwas\s+|gelegentlich\s+)?Hilfe',
    'no_symptoms': r'(?:vollständig\s+|gänzlich\s+)beschwerdefrei|asymptomatisch|keinerlei\s+(?:(?:neurologische[n]?|residuelle[n]?)\s+)?Symptome',
    'symptoms_present': r'\bsymptomatisch\b|weiterhin\s+leichte\s+Beschwerden',
    'unable_independent_walking': r'(?:selbständig\w*|selbstständig\w*)\s+Gehen.{0,85}nicht(?: mehr)? möglich|Gehen\s+(?:selbständig|selbstständig)\s+nicht(?: mehr)? möglich',
}


def functional_facts(doc, identity):
    facts, explicit, selected = [], [], []
    views = list(source_views(doc))
    for sec in views:
        for a, b, raw in sentences(sec):
            # Preserve bounded evidence; no report-wide functional inference.
            if b - a > 1800:
                continue
            for part in re.finditer(r'[^;]+', raw):
                local = part[0]
                flat = ' '.join(local.split())
                p = proof(doc, sec, a + part.start(), a + part.end())
                time = temporal(local, sec.heading, identity['admission_date'], context_date=sec.section_date)
                habitual = bool(re.search(r'zu\s*Hause|zuhause|\bwohnt\b|\bwohnte\b|\bsonst\s+ist|\bansonsten\s+wohnt|mobil\s+gewesen|Fremdanamnestisch.{0,35}(?:Sohn|Tochter).{0,25}(?:braucht|benötigt)', flat, re.I))
                explicitly_current = bool(CURRENT.search(flat))
                if habitual and not explicitly_current and time != 'DISCHARGE':
                    time = 'BASELINE'
                if re.search(r'\b(?:soll|Ziel|angestrebt|geplant)\b', flat, re.I):
                    time = 'UNKNOWN'
                other_person = bool(re.search(r'(?:Gattin|Gatte|Ehefrau|Ehemann|Mutter|Vater|Sohn|Tochter).{0,80}(?:Pflege|Betreuung)|(?:Pflege|Betreuung).{0,30}für\s+die\s+Gattin', flat, re.I))
                if other_person:
                    time = 'UNKNOWN'
                if any(re.search(pattern, flat, re.I) for pattern in FUNCTION_PATTERNS.values()) or re.search(r'mRS|Rankin', flat, re.I):
                    selected.append(p)
                for fact, pattern in FUNCTION_PATTERNS.items():
                    for m in re.finditer(pattern, flat, re.I):
                        state = assertion(flat, m)
                        if fact == 'walking_independent' and re.search(r'(?:selbständig\w*|selbstständig\w*)\s+Gehen.{0,100}nicht(?: mehr)? möglich', flat, re.I):
                            state = 'ABSENT'
                        if fact in ('walking_human_assistance', 'needs_ADL_help', 'unable_independent_walking'):
                            state = 'ABSENT' if re.search(r'\b(?:kein\w*|nicht)\s+(?:benötigt|braucht|bedarf)', flat[:m.start() + 10], re.I) else 'PRESENT'
                        if fact == 'usual_activities_possible' and re.search(r'nicht(?: mehr)? alle', m[0], re.I):
                            state = 'ABSENT'
                        fact_time = time
                        if fact == 'baseline_function' and not habitual and not BASELINE.search(flat):
                            fact_time = 'UNKNOWN'
                        facts.append({**p, 'fact': fact, 'state': 'UNKNOWN' if other_person else state, 'temporal_ownership': fact_time,
                                      'rejection_reason': 'OTHER_PERSON_FUNCTION' if other_person else None})
    # Literal outcome fields in diagnosis lists are allowed only with their own
    # explicit temporal label. They never supply admission-reason categories.
    for sec in doc.sections:
        if sec.page_start != sec.page_end:
            continue
        for line in re.finditer(r'(?m)^.*$', sec.source_text):
            if not re.search(r'\bmRS\b|Rankin', line[0], re.I):
                continue
            flat = line[0]
            pattern = r'\b(?:mRS|Rankin(?:-Skala|-Scale)?)\s*(?:bei Aufnahme\s*)?[:=]?\s*([0-6])(?!\d|[.,]\d)(?:\s*[-–]\s*([0-6])(?!\d|[.,]\d))?'
            for m in re.finditer(pattern, flat, re.I):
                labels = [(x.start(), kind) for regex, kind in [(BASELINE, 'BASELINE'), (CURRENT, 'CURRENT_ADMISSION'), (DISCHARGE, 'DISCHARGE')]
                          for x in regex.finditer(flat[:m.end()])]
                start, time = max(labels) if labels else (m.start(), temporal(flat, sec.heading, identity['admission_date']))
                p = proof(doc, sec, line.start() + start, line.start() + m.end())
                if re.search(r'Diagnos', sec.heading or '', re.I) and not labels:
                    time = 'UNKNOWN'
                prior_dates = [iso(x[0]) for x in re.finditer(DATE, flat)]
                containing = next((v for v in views if v.section_id == sec.section_id and v.source_base <= line.start() + m.start() < v.source_base + len(v.source_text)), None)
                if (prior_dates and all(d and d < identity['admission_date'] for d in prior_dates)) or (containing and containing.section_date and containing.section_date != identity['admission_date']):
                    time = 'UNKNOWN'
                lo, hi = int(m[1]), int(m[2] or m[1])
                explicit.append({**p, 'min': lo, 'max': hi, 'temporal_ownership': time,
                                 'eligible': time in ('BASELINE', 'CURRENT_ADMISSION') and lo <= hi < 6})
    return {'facts': facts, 'explicit_scores': explicit, 'selected_source': selected}


def estimate_mrs(function, period):
    rows = [f for f in function['facts'] if f['temporal_ownership'] == period]
    states = {fact: {f['state'] for f in rows if f['fact'] == fact} - {'UNKNOWN'} for fact in FACTS}
    resolved = {fact: next(iter(values)) if len(values) == 1 else 'UNKNOWN' for fact, values in states.items()}
    conflicts = [f for f, values in states.items() if len(values) > 1]
    if resolved['walking_independent'] == 'PRESENT' and (resolved['walking_human_assistance'] == 'PRESENT' or resolved['bedbound'] == 'PRESENT'):
        conflicts.append('CONTRADICTORY_MOBILITY')
    if resolved['independent_ADL'] == resolved['needs_ADL_help'] == 'PRESENT':
        conflicts.append('CONTRADICTORY_ADL')
    explicit = [s for s in function['explicit_scores'] if s['temporal_ownership'] == period]
    out = {'score': None, 'min': None, 'max': None, 'source_type': 'ESTIMATED', 'confidence': 'LOW',
           'evidence': [], 'rule': 'INSUFFICIENT_FUNCTIONAL_INFORMATION', 'resolved_facts': resolved, 'conflicts': conflicts}
    if explicit:
        eligible = [s for s in explicit if s['eligible']]
        values = {(s['min'], s['max']) for s in eligible}
        if len(values) == 1 and len(eligible) == len(explicit):
            lo, hi = next(iter(values))
            discordance = (hi < 4 and (resolved['needs_ADL_help'] == 'PRESENT' or resolved['walking_human_assistance'] == 'PRESENT' or resolved['unable_independent_walking'] == 'PRESENT')) or (hi < 5 and resolved['bedbound'] == 'PRESENT')
            if discordance:
                return {**out, 'source_type': 'EXPLICIT', 'rule': 'LITERAL_MRS_FUNCTION_DISCORDANCE_REVIEW', 'evidence': eligible + rows}
            return {**out, 'score': lo if lo == hi else None, 'min': lo if lo != hi else None,
                    'max': hi if lo != hi else None, 'source_type': 'EXPLICIT',
                    'confidence': 'HIGH' if lo == hi else 'MODERATE', 'evidence': eligible, 'rule': 'LITERAL_OWNED_MRS'}
        return {**out, 'rule': 'EXPLICIT_SCORE_CONFLICT_OR_LIVING_ADMISSION_SCORE_6', 'evidence': explicit}
    if conflicts:
        return {**out, 'rule': 'CONFLICTING_FUNCTIONAL_FACTS', 'evidence': rows}
    def present(f): return resolved[f] == 'PRESENT'
    def absent(f): return resolved[f] == 'ABSENT'
    lo = hi = None
    used = []
    if present('bedbound') or present('constant_nursing'):
        lo = hi = 5
        used = [f for f in ('bedbound', 'constant_nursing') if present(f)]
    elif present('walking_human_assistance') or present('needs_ADL_help') or present('unable_independent_walking'):
        lo, hi = 4, 5
        used = [f for f in ('walking_human_assistance', 'needs_ADL_help', 'unable_independent_walking') if present(f)]
        if absent('bedbound') and absent('constant_nursing'):
            hi = lo
            used += ['bedbound', 'constant_nursing']
    elif absent('usual_activities_possible') and present('own_affairs_independent'):
        lo = hi = 2
        used = ['usual_activities_possible', 'own_affairs_independent']
    elif present('walking_independent') and present('unspecified_help'):
        lo, hi = 2, 3
        used = ['walking_independent', 'unspecified_help']
    elif present('symptoms_present') and present('usual_activities_possible'):
        lo = hi = 1
        used = ['symptoms_present', 'usual_activities_possible']
    elif present('no_symptoms'):
        lo = hi = 0
        used = ['no_symptoms']
    if lo is not None:
        out.update(score=lo if lo == hi else None, min=lo if lo != hi else None, max=hi if lo != hi else None,
                   confidence='HIGH' if lo == hi else 'MODERATE', evidence=[f for f in rows if f['fact'] in used],
                   rule='FUNCTIONAL_FACTS_' + '_AND_'.join(used))
    return out


def extract_document(doc):
    identity = encounter_identity(doc)
    if identity['status'] != 'VERIFIED_SOURCE_ENCOUNTER':
        return {'source_document_id': doc.source_document_id, 'status': identity['status'], 'identity_audit': identity}
    reason = classify_reason(doc, identity)
    function = functional_facts(doc, identity)
    return {'source_document_id': doc.source_document_id, 'filename': doc.filename,
            'status': 'VERIFIED_SOURCE', 'admission_date': identity['admission_date'],
            'discharge_date': identity['discharge_date'], 'encounter_evidence': identity['evidence'],
            'admission_reason': reason, 'functional_assessment': function,
            'pre_event_mrs': estimate_mrs(function, 'BASELINE'),
            'admission_mrs': estimate_mrs(function, 'CURRENT_ADMISSION')}


def extract_bundle(bundle):
    results = [extract_document(d) for d in bundle.documents]
    return {'encounters': [r for r in results if r['status'] == 'VERIFIED_SOURCE'],
            'excluded_or_held_documents': [r for r in results if r['status'] != 'VERIFIED_SOURCE']}


def create_artifact(bundle, case_id):
    value = {'artifact_version': 1, 'extraction_type': VERSION, 'patient_id': bundle.patient_id,
             'case_id': case_id, 'source_fingerprint': fingerprint(bundle), 'output': extract_bundle(bundle),
             'clinical_commit_authorized': False}
    value['artifact_sha256'] = digest(value)
    return value


def verify_artifact(value, bundle, case_id):
    checked = dict(value)
    checksum = checked.pop('artifact_sha256', None)
    if digest(checked) != checksum:
        raise ValueError('ENCOUNTER_ARTIFACT_CHECKSUM_INVALID')
    if value.get('artifact_version') != 1 or value.get('extraction_type') != VERSION or value.get('patient_id') != bundle.patient_id or value.get('case_id') != case_id or value.get('source_fingerprint') != fingerprint(bundle):
        raise ValueError('ENCOUNTER_ARTIFACT_STALE_OR_IDENTITY_MISMATCH')
    # Source-only revalidation has no dependency on a model or prompt backend.
    if value.get('output') != extract_bundle(bundle):
        raise ValueError('ENCOUNTER_ARTIFACT_UNSUPPORTED_ASSERTIONS')
    return True


def persistence_row(encounter, case_id):
    row = {'case_id': case_id, 'source_document_id': encounter['source_document_id'],
           'admission_date': encounter['admission_date'], 'discharge_date': encounter['discharge_date'],
           'admission_reason_primary': encounter['admission_reason']['primary_reason'],
           'admission_reason_text': '\n'.join(dict.fromkeys(e['exact_source_text'] for e in encounter['admission_reason']['evidence'])),
           'assessment_json': encounter, 'deleted': False}
    for category in REASONS[:-1]:
        row['admission_' + category.lower()] = encounter['admission_reason']['flags'][category]
    for prefix, key in [('pre_event_mrs', 'pre_event_mrs'), ('admission_mrs', 'admission_mrs')]:
        assessment = encounter[key]
        row[prefix] = assessment['score']
        row[prefix + '_min'], row[prefix + '_max'] = assessment['min'], assessment['max']
    for prefix, key in [('', 'admission_mrs'), ('pre_event_', 'pre_event_mrs')]:
        assessment = encounter[key]
        for name in ('source_type', 'confidence'):
            row[prefix + 'mrs_' + name] = assessment[name]
        row[prefix + 'mrs_evidence_text'] = '\n'.join(dict.fromkeys(p['exact_source_text'] for p in assessment['evidence']))
    return row


def plan_commit(artifact, bundle, case_id, existing):
    """No writes: immutable artifact-only persistence plan and conflict guard."""
    verify_artifact(artifact, bundle, case_id)
    plans = []
    for encounter in artifact['output']['encounters']:
        row = persistence_row(encounter, case_id)
        matches = [old for old in existing if old['case_id'] == case_id and old['source_document_id'] == row['source_document_id']]
        if not matches:
            status = 'INSERT'
        elif len(matches) == 1 and all(matches[0].get(k) == v for k, v in row.items()):
            status = 'UNCHANGED_IDEMPOTENT'
        else:
            status = 'CONFLICT_EXISTING'
        plans.append({'status': status, 'row': row})
    return plans
