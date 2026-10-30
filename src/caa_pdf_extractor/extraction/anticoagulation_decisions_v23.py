"""Separate semantic recognition layer; no score or clinical treatment advice."""
import re
from .clinical_scales_v23 import dates

OAC=re.compile(r'\bOAK\b|\bOAC\b|Antikoagul\w*|anticoagul\w*|Marcumar|Marcoumar|Eliquis|Xarelto|Pradaxa|Lixiana|Vorhofohr\w*|\bLAAO\b',re.I)
RULES=[('LAAO_CONSIDERED',r'Vorhofohr\w*|\bLAAO\b'),('ANTIPLATELET_INSTEAD',r'(?:ASS|Clopidogrel|Thrombo\s*ASS).{0,55}(?:statt|anstelle)|(?:statt|anstelle).{0,55}(?:ASS|Clopidogrel)'),
 ('WITHHOLD',r'kontraindiz|keine\s+(?:erneute\s+|orale\s+)?(?:Antikoag|OAK|OAC)|(?:OAK|OAC|Antikoag\w*).{0,40}(?:verzicht|nicht\s+(?:wieder|beginn|empfohl))|Verzicht\s+auf'),
 ('PAUSE',r'pausier|vor[üu]bergehend\s+(?:absetz|unterbr)|temporar\w*\s+stop'),('SWITCH',r'umstell|wechsel|switch'),
 ('STOP',r'absetz|beend|stop|abgesetzt'),('START',r'einleit|beginn|start|Neueinstellung'),('CONTINUE',r'fortf[üu]hr|weiterf[üu]hr|beibehalt|continu')]
REASONS={'CAA':r'Amyloidangiopath|\bCAA\b','PRIOR_ICH':r'intrazerebral|intracerebral|Hirnblutung|lob[äa]r\w*\s+Blutung',
 'CSS_CSAH':r'Siderose|Siderosis|\bcSS\b|Subarachnoidal','MICROBLEEDS':r'Mikroblut|Microbleed',
 'AF':r'Vorhofflimmern|\bVHF\b|atrial fibrillation','ISCHEMIC_STROKE':r'isch[äa]m\w*\s+(?:Infarkt|Insult)|\bTIA\b',
 'GI_BLEEDING':r'gastrointestinal\w*\s+Blutung|GI[- ]Blutung','RENAL_IMPAIRMENT':r'Niereninsuff|Nierenversagen',
 'PATIENT_PREFERENCE':r'Patient\w*.{0,25}(?:wunsch|lehn)|Wunsch\s+de[rs]\s+Patient',
 'FALL_RISK':r'Sturz','MECHANICAL_VALVE':r'mechanisch\w*.{0,20}(?:Klappe|AK-Ersatz)'}

def extract(text,*,patient_id,document_id,section_id,page,admission_date,discharge_date,heading=''):
    rows=[]
    # Paragraphs preserve reason/action relationships; quotes are bounded.
    offset=0
    for paragraph in re.split(r'\n\s*\n',text):
        if not OAC.search(paragraph):offset+=len(paragraph)+2;continue
        # Sentence splitting avoids an unrelated recommendation later in a paragraph.
        sentences=re.split(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ])|\n(?=\s*[-•])',paragraph)
        for sentence in sentences:
            if not OAC.search(sentence):continue
            matches=[(kind,re.search(pattern,sentence,re.I)) for kind,pattern in RULES]
            action=next((kind for kind,m in matches if m),'NO_DOCUMENTED_DECISION')
            # A negated action is not a positive start/continue decision.
            if action in ('START','CONTINUE') and re.search(r'nicht|kein\w*',sentence,re.I):action='WITHHOLD'
            ds=dates(sentence)
            historical=bool(re.search(r'\b(?:damals|seinerzeit|anamnestisch|fr[üu]her)\b',sentence,re.I))
            # A date in a reason (e.g. prior ICH) is not the action date.
            action_match=next((m for kind,m in matches if kind==action and m),None)
            owned_date=bool(action_match and re.search(r'(?:am|on)\s+\d{1,2}\.\d{1,2}\.\d{4}',sentence[max(0,action_match.start()-18):action_match.end()+24],re.I))
            day=ds[0] if len(set(ds))==1 and owned_date else discharge_date if not historical else None
            date_basis='EXPLICIT_IN_DECISION_SENTENCE' if len(set(ds))==1 and owned_date else 'DOCUMENT_DISCHARGE_AVAILABLE_BY' if day else 'UNKNOWN'
            reasons=[key for key,rx in REASONS.items() if re.search(rx,sentence,re.I)]
            # Nearby AF is only an indication if the sentence explicitly links it.
            indication='AF' if 'AF' in reasons and re.search(r'bei|wegen|aufgrund|auf\s+Grund|for|due to',sentence,re.I) else 'MECHANICAL_VALVE' if 'MECHANICAL_VALVE' in reasons and re.search(r'bei|wegen|aufgrund',sentence,re.I) else 'UNKNOWN'
            at=text.find(sentence,offset);eid=f'{document_id}:{section_id}:decision:{max(at,offset)}'
            reason_evidence=[]
            for reason in reasons:
                match=re.search(REASONS[reason],sentence,re.I)
                before=sentence[max(0,match.start()-35):match.start()]
                reason_evidence.append(dict(reason_code=reason,source_evidence_id=eid,
                    polarity='NEGATED' if re.search(r'\bkein\w*|\bohne\b|\bno\b',before,re.I) else 'ASSERTED_OR_CONTEXTUAL_REQUIRES_REVIEW',
                    evidence_quote=sentence[max(0,match.start()-40):match.end()+60][:160]))
            rows.append(dict(decision_id=eid,patient_id=str(patient_id),source_document_id=str(document_id),page=page,section_id=section_id,
                admission_date=admission_date,decision_date=day,decision_date_basis=date_basis,document_available_date=discharge_date,
                decision_type=action,decision_status='DOCUMENTED_PROPOSAL' if re.search(r'erw[äa]g|empfehl|sollte|eventuell|ggf',sentence,re.I) else 'DOCUMENTED_STATEMENT',
                indication=indication,indication_link_explicit=indication!='UNKNOWN',reason_codes=reasons,reason_evidence=reason_evidence,
                reason_direction='AGAINST_OAC' if action in ('WITHHOLD','STOP','PAUSE','ANTIPLATELET_INSTEAD') else 'SUPPORTS_OAC' if action in ('START','CONTINUE') else 'UNRESOLVED',
                evidence_id=eid,evidence_quote=sentence[:160],review_required=True,
                review_reasons=['LOCAL_SEMANTIC_RULE_REQUIRES_PHYSICIAN_REVIEW']+(['UNCERTAIN_DECISION_DATE'] if date_basis!='EXPLICIT_IN_DECISION_SENTENCE' else []),rule_version='OAC_DECISION_V2.3'))
        offset+=len(paragraph)+2
    return rows
