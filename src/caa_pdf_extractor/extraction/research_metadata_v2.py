"""Literal source normalization for review proposals; never clinical persistence.

Omission is UNKNOWN. Matches include offsets in the exact supplied source string.
Imaging dimensions require an ICH-owned passage, not a whole document.
"""
import re
from decimal import Decimal

def matches(pattern,text):
    return [dict(start=m.start(),end=m.end(),text=m.group()) for m in re.finditer(pattern,text,re.I)]

def explicit_flag(text,positive,negative=None):
    if negative and re.search(negative,text,re.I): return False
    if re.search(positive,text,re.I): return True
    return None

SEQUENCES={
 'SWI':r'\bSWI\b|suszeptibilit[äa]tsgewicht',
 'T2STAR_GRE':r'T2\s*\*|T2[- ]?(?:star|stern)|\bGRE\b|T2[^.;\n]{0,30}Gradientenecho',
 'DWI':r'\bDWI\b|diffusionsgewicht', 'ADC':r'\bADC\b','FLAIR':r'\bFLAIR\b',
 'T1':r'\bT1(?:w|[- ]gewichtet)?\b','T2':r'\bT2(?:w|[- ]gewichtet)?\b',
 'TOF_MRA':r'\bTOF\b|time.of.flight',
 'POSTCONTRAST_T1':r'T1[^.;\n]{0,65}(?:nach\s+(?:intraven[öo]ser\s+)?(?:KM|Kontrast)|post.?kontrast|post.?contrast)',
}

def mri_technique(text):
    refs={key:matches(pattern,text) for key,pattern in SEQUENCES.items()}
    flags={key:(True if v else None) for key,v in refs.items()}
    for key,word in [('SWI',r'SWI'),('T2STAR_GRE',r'T2\s*\*|GRE')]:
        if re.search(r'(?:keine?|ohne|nicht\s+durchgef[üu]hrte?)\s+(?:'+word+r')\b',text,re.I): flags[key]=False
    blood=True if flags['SWI'] is True or flags['T2STAR_GRE'] is True else False if flags['SWI'] is False and flags['T2STAR_GRE'] is False else None
    # MRI-owned "mit KM" / administered IV contrast denotes the MRI contrast
    # class; this does not claim a named drug or infer contrast from enhancement.
    gad=matches(r'Gadolinium|Gadovist|Dotarem|Gadoter|Gadobutrol|Gadoters[äa]ure|mit\s+KM\b|nach\s+(?:intraven[öo]ser\s+)?Kontrastmittel(?:applikation|gabe)',text)
    none=matches(r'ohne\s+(?:i\.?v\.?\s*)?(?:KM|Kontrastmittel|Gadolinium)|keine\s+Kontrastmittelgabe|\b(?:craniell|Schädel)\s+nativ\b',text)
    contrast='GADOLINIUM' if gad else 'NONE' if none else 'UNKNOWN'
    return dict(sequences=[k for k,v in flags.items() if v is True],sequence_flags=flags,
        swi_performed=flags['SWI'],t2star_gre_performed=flags['T2STAR_GRE'],
        blood_sensitive_sequence_present=blood,contrast_status=contrast,
        postcontrast_t1=flags['POSTCONTRAST_T1'],contrast_agent_explicitly_named=bool(re.search(r'Gadolinium|Gadovist|Dotarem|Gadoter|Gadobutrol',text,re.I)),evidence={**refs,'contrast':gad or none})

def macro_location(location):
    t=location.casefold()
    lobes=[name for name,rx in [('FRONTAL',r'frontal'),('PARIETAL',r'pariet'),('TEMPORAL',r'temporal'),('OCCIPITAL',r'(?:occipital|okzipital)'),('INSULAR',r'(?<!sub)insul')]
           if re.search(rx,t)]
    deep=bool(re.search(r'thalam|basalgang|putamen|pallid|caudat|capsula\s+interna',t))
    infra=bool(re.search(r'cerebell|kleinhirn|hirnstamm|pons|pontin|mesenceph|medulla',t))
    groups=sum([bool(lobes),deep,infra])
    macro='MIXED' if groups>1 else 'LOBAR' if lobes else 'DEEP' if deep else 'INFRATENTORIAL' if infra else 'UNKNOWN'
    return dict(macro_location=macro,lobe='MULTILOBAR' if len(lobes)>1 else lobes[0] if lobes else 'UNKNOWN')

def cmb_location(location):
    # "Marklager" alone is not a strict lobar distribution.
    return macro_location(location)['macro_location']

def ich_dimensions(passage):
    result=dict(diameter_1_mm=None,diameter_2_mm=None,diameter_3_mm=None,max_diameter_mm=None,
        volume_ml_explicit=None,volume_ml_derived=None,volume_method=None,evidence=[])
    number=r'(\d+(?:[.,]\d+)?)'
    m=re.search(number+r'\s*[x×]\s*'+number+r'(?:\s*[x×]\s*'+number+r')?\s*(mm|cm)\b',passage,re.I)
    if m:
        scale=10 if m[4].lower()=='cm' else 1
        values=[float(Decimal(s.replace(',','.')))*scale for s in m.groups()[:3] if s]
        for i,value in enumerate(values,1): result[f'diameter_{i}_mm']=value
        result['max_diameter_mm']=max(values);result['evidence'].append(dict(start=m.start(),end=m.end(),text=m.group()))
    else:
        axis=re.search(number+r'\s*(mm|cm)\s*[x×]\s*(?:koronar\s*)?'+number+r'\s*(mm|cm)(?:\s*[x×]\s*(?:kraniokaudal\s*)?'+number+r'\s*(mm|cm))?',passage,re.I)
        if axis:
            values=[float(axis[i].replace(',','.'))*(10 if axis[i+1].lower()=='cm' else 1) for i in (1,3,5) if axis[i]]
            for i,value in enumerate(values,1):result[f'diameter_{i}_mm']=value
            result['max_diameter_mm']=max(values);result['evidence'].append(dict(start=axis.start(),end=axis.end(),text=axis.group()))
    volume=re.search(r'(?:Volumen[^0-9.;\n]{0,25})'+number+r'\s*(?:ml|cm3|cm³)\b',passage,re.I)
    if volume: result['volume_ml_explicit']=float(volume[1].replace(',','.'))
    return result

def edinburgh_morphology(text):
    return dict(
        finger_like_projections=explicit_flag(text,r'finger[- ]like\s+projections|fingerf[öo]rmige[nr]?\s+(?:Ausl[äa]ufer|Projektionen|Forts[äa]tze)',r'(?:keine|ohne)\s+(?:finger[- ]like\s+projections|fingerf[öo]rmige[nr]?\s+(?:Ausl[äa]ufer|Projektionen|Forts[äa]tze))'),
        subarachnoid_extension=explicit_flag(text,r'(?:subarachnoidale?\s+(?:Ausdehnung|Extension)|Einbruch\s+in\s+den\s+Subarachnoidalraum|subarachnoid\s+extension)',r'(?:keine|ohne)\s+(?:subarachnoidale?\s+(?:Ausdehnung|Extension)|subarachnoid\s+extension)'))

def source_caa_assertion(text):
    """A source assertion is never a computed Boston category or pathology proof."""
    if not re.search(r'amyloidangiopath|\bCAA\b',text,re.I): return dict(source_caa_status='NO_CAA_ASSERTION',criteria_system='UNKNOWN')
    framework='BOSTON_V2' if re.search(r'Boston[^.;\n]{0,16}(?:2[.,]0|v\.?\s*2)',text,re.I) else 'BOSTON_UNSPECIFIED_VERSION' if re.search('Boston',text,re.I) else 'EDINBURGH' if re.search('Edinburgh',text,re.I) else 'UNKNOWN'
    if re.search(r'kein(?:e[nr]?)?\s+(?:morphologischer?\s+)?Hinweis[^.;\n]{0,90}(?:Amyloidangiopath|\bCAA\b)|(?:Amyloidangiopathie|CAA)\s+(?:ausgeschlossen|nicht\s+nachweisbar)',text,re.I): status='CAA_EXPLICITLY_NEGATED'
    elif re.search(r'\bdefinite\s+CAA\b',text,re.I) and re.search(r'autops|post.?mortem',text,re.I) and re.search(r'patholog|histolog',text,re.I): status='DEFINITE_PATHOLOGICAL_CAA'
    elif re.search(r'probable\s+CAA\s+with\s+supporting\s+pathology',text,re.I) and re.search(r'biops|h[äa]matom|hematoma',text,re.I): status='PROBABLE_WITH_SUPPORTING_PATHOLOGY'
    elif re.search(r'(?:probable|wahrscheinlich)[^.;\n]{0,35}(?:CAA|Amyloidangiopath)',text,re.I): status='FORMAL_PROBABLE_CAA'
    elif re.search(r'(?:possible|m[öo]glich)[^.;\n]{0,35}(?:CAA|Amyloidangiopath)',text,re.I): status='FORMAL_POSSIBLE_CAA'
    elif re.search(r'Verdacht|V\s*\.?\s*a\s*\.|suspiz|Differentialdiagnos|\bDD\b|fraglich|fragliche',text,re.I): status='SUSPECTED_CAA'
    elif re.search(r'bekannt|bei\s+(?:cerebraler?\s+|zerebraler?\s+)?Amyloidangiopath|Amyloidangiopathie\s+(?:best[äa]tigt|diagnostiziert)|best[äa]tigte[^.;\n]{0,30}Amyloidangiopath|Amyloidangiopathie\s+mit',text,re.I): status='CLINICIAN_DIAGNOSED_CAA'
    else: status='UNCERTAIN'
    return dict(source_caa_status=status,criteria_system=framework)

MEDICATIONS=[
 ('ANTITHROMBOTIC','ANTICOAGULANT','LMWH',r'\b(?:Fragmin|Enoxaparin|Clexane|Lovenox|Dalteparin|Certoparin|Mono.embolex|Nadroparin|Fraxiparin|Inhixa|Tinzaparin)\b'),
 ('ANTITHROMBOTIC','ANTICOAGULANT','UFH',r'\b(?:Heparin|Liquemin|Calciparin)\b'),
 ('ANTITHROMBOTIC','ANTICOAGULANT','VKA',r'\b(?:Marcoumar|Phenprocoumon|Warfarin|Acenocoumarol|Sintrom)\b'),
 ('ANTITHROMBOTIC','ANTICOAGULANT','DOAC',r'\b(?:Lixiana|Eliquis|Xarelto|Pradaxa|Edoxaban|Apixaban|Rivaroxaban|Dabigatran)\b'),
 ('ANTITHROMBOTIC','ANTIPLATELET','ASPIRIN',r'\b(?:ASS|Aspirin|Acetylsalicyls[äa]ure|Thrombo\s*ASS)\b'),
 ('ANTITHROMBOTIC','ANTIPLATELET','P2Y12_INHIBITOR',r'\b(?:Plavix|Clopidogrel|Ticagrelor|Brilique|Prasugrel|Efient)\b'),
 ('ANTIHYPERTENSIVE',None,None,r'\b(?:Ramipril|Lannapril|Bisoprolol|Concor|Amlodipin|Amlodipine|Lisinopril|Candesartan|Atacand|Valsartan|Enalapril|Metoprolol|Nebivolol|Carvedilol|Losartan|Indapamid|Moxonidin|Urapidil)\b'),
 ('LIPID_LOWERING',None,None,r'\b(?:Atorvastatin|Simvastatin|Rosuvastatin|Pravastatin|Ezetimib|Sortis)\b'),
 ('ANTISEIZURE',None,None,r'\b(?:Levetiracetam|Levetirazetam|Levebon|Keppra|Fycompa|Perampanel|Lacosamid|Vimpat|Valproat|Depakine|Lamotrigin|Carbamazepin|Tegretol|Phenytoin|Gabapentin|Neurontin|Pregabalin|Lyrica)\b'),
 ('ANTIDEMENTIA',None,None,r'\b(?:Donepezil|Memantin|Rivastigmin|Galantamin|Exelon|Ebixa|Aricept)\b'),
 ('ANTIDEPRESSANT',None,None,r'\b(?:Sertralin|Escitalopram|Citalopram|Trazodon|Trittico|Mirtazapin|Duloxetin|Venlafaxin|Aurorix)\b'),
 ('ANTIPSYCHOTIC',None,None,r'\b(?:Quetiapin|Quetialan|Seroquel|Risperdal|Risperidon|Haldol|Haloperidol|Olanzapin)\b'),
 ('IMMUNOSUPPRESSIVE',None,None,r'\b(?:Prednisolon|Prednison|Methotrexat|Azathioprin|Mycophenolat|Tacrolimus)\b'),
 ('ANALGESIC',None,None,r'\b(?:Paracetamol|Mexalen|Metamizol|Novalgin|Ibuprofen|Diclofenac|Tramadol|Oxycodon|Morphin|Fentanyl|Hydal)\b'),
]

def medication_proposal(name,source,discharge_date):
    category,subclass,agent='OTHER',None,None
    for cat,sub,kind,pattern in MEDICATIONS:
        if re.search(pattern,name,re.I): category,subclass,agent=cat,sub,kind;break
    role='ANTIPLATELET' if subclass=='ANTIPLATELET' else 'UNKNOWN'
    if subclass=='ANTICOAGULANT':
        if re.search(r'(?:Thrombose|VTE)[- ]?prophylax|prophylaktisch',source,re.I): role='VTE_PROPHYLAXIS'
        elif re.search(r'therapeutisch|Vollheparinisierung|gewichtsadaptierte?\s+Heparin',source,re.I): role='THERAPEUTIC_ANTICOAGULATION'
    return dict(medication=name,date=discharge_date,date_basis='DISCHARGE_DATE',medication_context='DISCHARGE_MEDICATION',
        first_prescription_date=None,category=category,antithrombotic_subclass=subclass,agent_class=agent,antithrombotic_role=role)

def first_lab(observation,admission_date,reference_low=None,reference_high=None):
    role=observation['column_role'].upper().replace(' ','_')
    if role not in ('FIRST','FIRST_VALUE','ERSTER_WERT'): return None
    value=Decimal(str(observation['decimal_value']))
    abnormal='UNKNOWN'
    if reference_low is not None and value<Decimal(str(reference_low)): abnormal='LOW'
    elif reference_high is not None and value>Decimal(str(reference_high)): abnormal='HIGH'
    elif reference_low is not None and reference_high is not None: abnormal='NORMAL'
    return dict(parameter=observation['biomarker'],value=str(value),unit=observation.get('unit_text'),
        reference_low=reference_low,reference_high=reference_high,reference_text=None,abnormality=abnormal,
        measurement_date=admission_date,date_basis='ENCOUNTER_ADMISSION_DATE',phlebotomy_date_known=False,
        source_column_role=role)

def echo_metadata(text):
    tee=bool(re.search(r'\bTEE\b|trans[öo]sophage',text,re.I))
    tte=bool(re.search(r'\bTTE\b|transthorakal',text,re.I))
    subtype='TEE' if tee and not tte else 'TTE' if tte and not tee else 'UNKNOWN_ECHOCARDIOGRAPHY'
    patterns={'LV_THROMBUS':r'(?:linksventrikul[äa]r[^.;\n]{0,20}Thromb|LV[- ]Thromb)',
     'LA_LAA_THROMBUS':r'(?:Vorhof|Vorhofohr|LAA)[^.;\n]{0,30}Thromb',
     'PFO':r'\bPFO\b|offenes\s+Foramen\s+ovale','ASD':r'\bASD\b|Vorhofseptumdefekt',
     'VALVULAR_VEGETATION':r'Vegetation','VALVULAR_DISEASE':r'(?:Aorten|Mitral|Trikuspidal|Pulmonal)(?:klappen)?(?:insuffizienz|stenose)',
     'AORTIC_ARCH_ATEROMA':r'Aortenbogen[^.;\n]{0,30}(?:Atherom|Plaque)',
     'CARDIAC_TUMOR':r'Myxom|kardial[^.;\n]{0,20}Tumor'}
    findings={}
    for key,pattern in patterns.items():
        spans=matches(pattern,text)
        if spans:
            # A finding can be explicitly absent. Scope the negation to its clause.
            vals=[]
            for span in spans:
                clause=text[max(text.rfind('.',0,span['start']),text.rfind('\n',0,span['start']))+1:span['end']]
                vals.append(False if re.search(r'kein|ohne|Ausschluss',clause,re.I) else True)
            findings[key]=dict(value=vals[0] if len(set(vals))==1 else None,evidence=spans)
    ef=re.search(r'(?:LVEF|LV[- ]EF|Ejektionsfraktion|\bEF)\s*[:=]?\s*(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*%',text,re.I)
    return dict(echo_subtype=subtype,lvef_percent=float(ef[1]) if ef and not ef[2] else None,
                lvef_min_percent=float(ef[1]) if ef and ef[2] else None,lvef_max_percent=float(ef[2]) if ef and ef[2] else None,findings=findings)

def gender_promotion(live_value,proposal,physician_accepted=False):
    return dict(existing_gender=live_value,proposed_gender=proposal,
        physician_accepted=physician_accepted,promotion_eligible=live_value is None and proposal in ('FEMALE','MALE') and physician_accepted,
        review_status='PROPOSED_PROMOTION' if live_value is None and proposal in ('FEMALE','MALE') and physician_accepted else 'PHYSICIAN_ACCEPTANCE_REQUIRED' if live_value is None else 'EXISTING_VALUE_RETAINED')
