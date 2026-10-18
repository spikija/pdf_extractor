"""Deterministic target eligibility after exact-source validation.

This policy never infers a disease from the name of a treatment. Source and model
records remain auditable even when they are outside the disease/event target.
"""
import re

VERSION = 'clinical_history_target_policy_v2'
CATEGORIES = ('DISEASE','NEUROLOGIC_EVENT','MAJOR_RELEVANT_MEDICAL_HISTORY',
              'SURGERY_ONLY','PROCEDURE_ONLY','OTHER')
ELIGIBLE = frozenset(CATEGORIES[:3])
SURGERY = re.compile(
    r'\b(?:Appendektomie|appendectomy|Tonsillektomie|tonsillectomy|Hernien[- ]?(?:operation|OP)|'
    r'Leistenhernienoperation|Chole[zc]ystektomie|cholecystectomy|Strumektomie|'
    r'Nephrektomie|Darmresektion|Dünndarmresektion|Resektion|Segmentresektion|'
    r'Excision|Exzision|Operation|OP|Kraniotomie|Craniotomie|AK-Ersatz|Conduit-OP|'
    r'Hüft-TEP|TAPP|Patchplastik|Reposition|Verplattung|Nierenteilresektion)\b',re.I)
PROCEDURE = re.compile(
    r'\b(?:Gastroskopie|gastroscopy|Koloskopie|colonoscopy|Endoskopie|Biopsie|'
    r'PEG[- ]Anlage|Portimplantation|Subtraktionsangiographie|Angiographie|'
    r'Eradikation|YAG[- ]Iridotomie|MRT|MRI|CT|MMSE|MoCA)\b',re.I)
NEURO = re.compile(r'(?:intracerebr|intrazerebr|intracran|intrakran|cerebral|zerebral).{0,80}(?:blutung|h[äa]matom|h[äa]morrhag|hemorrhag|infarkt)|(?:blutung|h[äa]morrhag|infarkt).{0,80}(?:zerebr|cerebr)|subarach\w*blutung|subarchonoidalblutung|subduralh[äa]matom|\b(?:ICB|ICH|stroke|Epilep\w*|Anf[äa]lle?|Anfall)\b|isch[äa]misch.{0,80}(?:Infarkt|Attacke)|atypisch.{0,30}Blutung',re.I)
MEDICAL = re.compile(
    r'appendizitis|Gastritis|Duodenitis|Hernia\b|Hernie\b|Karzinom|carcinom|carzinom|'
    r'Tumor|Basaliom|Nierenzyste|Stenose|Ektasie|Aneurysma|Hyperton|hypertension|'
    r'Fraktur|fracture|Fract\.|Demenz|dement|Epilep|Diabetes|Insuffiz|Amyloid|'
    r'Infekt|Pneumon|Sepsis|Thromb|Embol|Infarkt|H[äa]matom|Blutung|hemorrhag|'
    r'itis\b|ose\b|osie\b|ämie|aemie|lipid|thyre|Schwindel|Vertigo|Schmerz|'
    r'neuropath|Neuritis|Syndrom|Schlaf|Delir|Depress|Tremor|Zerviko|'
    r'Asthma|Glaukom|Herpes|Zoster|Erysipel|Hydrozele|Adipositas|'
    r'Insuff|Hyperplas|Hypakus|Hörminder|Gangunsicher|Lumbago|atopi|'
    r'Osteopen|lithiasis|Angiomyolipom|Migräne|Enzephalopath|Polymyalgia|Nierenversagen|Lymphom|'
    r'Schluckstör|Restless|Parkinson|Raumforderung|Lichen|Anämie|Agranulo|'
    r'Exanthem|allerg|Npl\.|NIT\b|TBC\b|PAVK|QT-Verlängerung|Schenkelblock|'
    r'Vorhoff|MINS\b|TRINS\b|OSAS\b|Zyste|Polyglob|Demez|kognitive|'
    r'Kolitis|Cholecystitis|Cholezystitis|Kolonadenom|Colon.*adenom|Rectumadenom|'
    r'Risikofaktoren|Sturz|Exitus|Tod|death',re.I)


def classify_target(disease):
    """Classify only the already source-supported label, not its whole document."""
    label=disease.strip() if isinstance(disease,str) else ''
    surgery=SURGERY.search(label);procedure=PROCEDURE.search(label)
    # An explicit diagnosis followed by a separated treatment retains its literal
    # diagnosis prefix. Dates are still derived from the original source item.
    for m in re.finditer(r'[,;]\s*',label):
        prefix,tail=label[:m.start()].strip(),label[m.end():]
        if MEDICAL.search(prefix) and not (SURGERY.search(prefix) or PROCEDURE.search(prefix)) and (SURGERY.match(tail) or PROCEDURE.match(tail)) and not re.match(r'MMSE|MoCA',tail,re.I):
            result=classify_target(prefix)
            return {**result,'model_disease':label,'procedure_text':tail,'normalization_method':'EXPLICIT_DISEASE_BEFORE_PROCEDURE'}
    if procedure and not surgery and re.search(r':\s*',label):
        prefix,tail=re.split(r':\s*',label,maxsplit=1)
        if PROCEDURE.search(prefix) and MEDICAL.search(tail) and not (SURGERY.search(tail) or PROCEDURE.search(tail)):
            result=classify_target(tail)
            return {**result,'model_disease':label,'procedure_text':prefix,'normalization_method':'EXPLICIT_DISEASE_AFTER_PROCEDURE'}
    if surgery or procedure:
        # Major events/conditions explicitly stated WITH treatment remain history;
        # the procedure name alone never supplies an underlying diagnosis.
        medical=MEDICAL.search(label)
        explicit_context=bool(NEURO.search(label) or (medical and (medical.start() < min(m.start() for m in (surgery,procedure) if m)
                              or re.search(r'\b(?:bei|wegen|einer|eines|mit)\s+.{0,100}',label,re.I))))
        if explicit_context and medical:
            category='MAJOR_RELEVANT_MEDICAL_HISTORY'
        else:
            category='SURGERY_ONLY' if surgery else 'PROCEDURE_ONLY'
    elif NEURO.search(label):category='NEUROLOGIC_EVENT'
    elif re.search(r'\b(?:Exitus|Tod|death|Sturz|Risikofaktoren)\b',label,re.I):category='MAJOR_RELEVANT_MEDICAL_HISTORY'
    elif MEDICAL.search(label):category='DISEASE'
    else:category='OTHER'
    return {'target_policy_version':VERSION,'target_classification':category,
            'persistence_eligible':category in ELIGIBLE,'disease':label,
            'normalization_method':'DETERMINISTIC_TARGET_POLICY'}


def require_target_eligibility(records,metadata):
    """Independent commit barrier; recompute rather than trust artifact labels."""
    from .radiology_artifacts import VerifiedExtractionError
    for record in records:
        decision=classify_target(record.get('disease'))
        matches=[m for m in metadata if m.get('record')==record]
        if (not decision['persistence_eligible'] or decision['disease']!=record.get('disease')
                or not matches or any(m.get('target_policy_version')!=VERSION
                    or m.get('target_classification') not in ELIGIBLE
                    or m.get('target_classification')!=decision['target_classification']
                    or m.get('persistence_eligible') is not True for m in matches)):
            raise VerifiedExtractionError('CLINICAL_HISTORY_TARGET_NOT_ELIGIBLE')
