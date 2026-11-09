"""Selective local interpretation of complex EEG clauses with mandatory source/polarity gates."""
import json
import re
from ..llm.granite import GraniteClient
from ..module_artifacts import digest
from .eeg_schema import validate_value,BOOL_FIELDS
from .eeg_extraction_v1 import clauses,polarity,EP_PATTERN,FOCAL,GENERAL,Extractor

FIELDS=('focal_slowing_present','generalized_slowing_present','epileptiform_discharge_present','periodic_discharge_present','rhythmic_delta_activity_present','seizure_recorded','electrographic_status_epilepticus','encephalopathy_pattern','eeg_clinical_event_captured','clinical_eeg_correlation')
PATTERNS={'focal_slowing_present':FOCAL,'generalized_slowing_present':GENERAL,'epileptiform_discharge_present':EP_PATTERN,
    'periodic_discharge_present':r'periodisch\w*\s+Entladung|\bLPD|\bGPD',
    'rhythmic_delta_activity_present':r'rhythmisch[^.;]*Delta|\bLRDA|\bGRDA|\bFIRDA',
    'seizure_recorded':r'elektro(?:gra[fp]hisch|klinisch)|Anfallsmuster|iktal\w*\s+EEG|\bAnfälle\b[^.;]{0,50}(?:aufgezeichnet|registriert|erfasst)|\bAnfall\b[^.;]{0,45}(?:aufgezeichnet|EEG[- ]Korrelat)',
    'electrographic_status_epilepticus':r'Status\s+epilepticus|Status[- ]epilepticus[- ]Muster',
    'encephalopathy_pattern':r'En[cz]ephalopathie|triphasisch|burst[- ]suppression',
    'eeg_clinical_event_captured':r'(?:Anfall|Ereignis|Episode|Attacke)[^.;]{0,65}(?:aufgezeichnet|erfasst|registriert)',
    'clinical_eeg_correlation':r'Korrelat|Korrelation|klinisch|iktal|Anfall'}
PROMPT='''Interpret only the supplied EEG finding clauses. Clinical indication is excluded.
Never infer epilepsy, a TFNE diagnosis, seizures or status from spikes alone. Never make missing data negative.
Propose only directly supported constrained fields. Preserve negative, uncertain and nondiagnostic scope.
Return {"findings":[{"field":one allowed field,"value":true/false/null or exact correlation source text,
"assertion":"ESTABLISHED"|"NEGATED"|"UNCERTAIN"|"NOT_ASSESSABLE","evidence_id":one supplied clause ID}]}.
Use an empty list when the clauses do not support a useful additional finding. No generated clinical prose.
Allowed fields: '''+', '.join(FIELDS)+'\nEEG clauses:\n'


def eligible(report):
    text=report['eeg_report_text']
    return bool(re.search(r'broad[- ]sharp|nicht\s+(?:sicher\s+)?beurteilbar|iktal|Korrelation|Phasenumkehr|Propogation|triphasisch|bifronto|fronto.zentro.temporal|soweit.*Artefakt',text,re.I))


def infer(report,client=None):
    client=client or GraniteClient()
    if client.model_version is None:client.check_model()
    source=[dict(id=f'C{i:04}',text=q,start=a) for i,(q,a,b,role) in enumerate(clauses(report),1) if role=='FINDING']
    prompt=PROMPT+json.dumps(source,ensure_ascii=False)
    raw=client.generate_json(prompt);parsed=json.loads(raw)
    if not isinstance(parsed,dict) or set(parsed)!={'findings'} or not isinstance(parsed['findings'],list):raise ValueError('Invalid local EEG semantic response')
    return dict(report_occurrence_id=report['report_occurrence_id'],model=client.model_name,model_digest=client.model_version,
        endpoint=client.base_url,prompt=prompt,prompt_sha256=digest(prompt),raw_output=raw,clauses=source,proposals=parsed['findings'])


def apply_proposals(report,values,response):
    ex=Extractor(report);lookup={r['id']:r for r in response['clauses']}
    current={(q,a) for q,a,b,role in clauses(report) if role=='FINDING'}
    for p in response['proposals']:
        try:
            if set(p)!={'field','value','assertion','evidence_id'} or p['field'] not in FIELDS:continue
            field=p['field'];value=p['value'];source=lookup[p['evidence_id']];q=source['text']
            if (q,source['start']) not in current:continue
            if p['assertion'] not in ('ESTABLISHED','NEGATED','UNCERTAIN','NOT_ASSESSABLE'):continue
            validate_value(field,value)
            m=re.search(PATTERNS[field],q,re.I)
            if not m:continue
            deterministic,state=polarity(q,m)
            if state=='UNCERTAIN':value=None
            elif deterministic is False:value=False if field in BOOL_FIELDS else None
            elif value is False:continue  # A model cannot manufacture explicit absence.
            if value is True and p['assertion'] in ('UNCERTAIN','NOT_ASSESSABLE'):value=None
            if value is True and p['assertion']!='ESTABLISHED':continue
            if field=='electrographic_status_epilepticus' and value is True and (not re.search(r'EEG|elektrogra[fp]h|nonkonvulsiv|Muster',q,re.I) or re.search(r'Zustand\s+nach|anamnestisch|\bZ\.?\s*n\.?',q[:m.start()],re.I)):continue
            if values.get(field) is False and value is not False:continue
            if field=='clinical_eeg_correlation':
                value=q  # Preserve the source, never generated prose.
            if value is None and field not in BOOL_FIELDS:continue
            ex.add(field,value,q,source['start'],assertion='NEGATED' if value is False else 'UNCERTAIN' if value is None else p['assertion'],method='LOCAL_GRANITE_GUARDED')
            if values.get(field) in (None,'UNKNOWN'):values[field]=value
        except (ValueError,TypeError,KeyError):continue
    return values,ex.findings
