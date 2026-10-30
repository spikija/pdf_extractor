"""Explicit source-label taxonomy for review proposals, independent of identity.

Rules annotate an existing diagnosis/event. They do not infer disease from lab
values, imaging findings, causal suffixes, or create dates or disease identities.
"""
import re

TAXONOMY = {
 'CEREBROVASCULAR': 'ISCHEMIC_STROKE TIA INTRACEREBRAL_HEMORRHAGE SUBARACHNOID_HEMORRHAGE CONVEXITY_SUBARACHNOID_HEMORRHAGE SUBDURAL_HEMATOMA CEREBRAL_VENOUS_THROMBOSIS CEREBRAL_AMYLOID_ANGIOPATHY CEREBRAL_VASCULITIS CNS_VASCULITIS_OTHER CEREBRAL_ANEURYSM AVM CAROTID_STENOSIS INTRACRANIAL_STENOSIS OTHER_CEREBROVASCULAR',
 'EPILEPSY_SEIZURE': 'EPILEPSY FOCAL_EPILEPSY GENERALIZED_EPILEPSY SYMPTOMATIC_EPILEPSY POST_STROKE_EPILEPSY OTHER_EPILEPSY EPILEPTIC_SEIZURE FOCAL_SEIZURE GENERALIZED_TONIC_CLONIC_SEIZURE STATUS_EPILEPTICUS CONVULSIVE_STATUS_EPILEPTICUS NONCONVULSIVE_STATUS_EPILEPTICUS',
 'COGNITIVE_DEMENTIA': 'SUBJECTIVE_COGNITIVE_DECLINE MILD_COGNITIVE_IMPAIRMENT DEMENTIA_UNSPECIFIED ALZHEIMER_DISEASE VASCULAR_DEMENTIA MIXED_DEMENTIA LEWY_BODY_DEMENTIA FRONTOTEMPORAL_DEMENTIA CAA_RELATED_COGNITIVE_IMPAIRMENT OTHER_COGNITIVE_DISORDER',
 'INFLAMMATORY_AUTOIMMUNE': 'CNS_VASCULITIS CAA_RELATED_INFLAMMATION ABETA_RELATED_ANGIITIS AUTOIMMUNE_ENCEPHALITIS OTHER_INFLAMMATORY_NEUROLOGIC',
 'PERIPHERAL_NERVE': 'POLYNEUROPATHY SENSORY_POLYNEUROPATHY AXONAL_POLYNEUROPATHY AXONAL_SENSORY_POLYNEUROPATHY DEMYELINATING_POLYNEUROPATHY CARPAL_TUNNEL_SYNDROME RADICULOPATHY OTHER_PERIPHERAL_NEUROPATHY',
 'MOVEMENT_DISORDER': 'PARKINSON_DISEASE PARKINSONISM RESTLESS_LEGS_SYNDROME TREMOR DYSTONIA OTHER_MOVEMENT_DISORDER',
 'HEADACHE': 'MIGRAINE_WITH_AURA MIGRAINE_WITHOUT_AURA TENSION_TYPE_HEADACHE OTHER_PRIMARY_HEADACHE SECONDARY_HEADACHE',
 'VESTIBULAR': 'BPPV PERIPHERAL_VESTIBULOPATHY CENTRAL_VERTIGO MULTIFACTORIAL_DIZZINESS OTHER_VESTIBULAR',
 'ENCEPHALOPATHY': 'METABOLIC_ENCEPHALOPATHY INFECTIOUS_ENCEPHALOPATHY TOXIC_ENCEPHALOPATHY HYPOXIC_ENCEPHALOPATHY OTHER_ENCEPHALOPATHY',
 'INFECTIOUS_NEUROLOGY': 'MENINGITIS ENCEPHALITIS OTHER_INFECTIOUS_NEUROLOGIC',
 'NEURO_ONCOLOGY': 'PRIMARY_CNS_TUMOR CNS_METASTASIS OTHER_NEURO_ONCOLOGY',
 'NEUROMUSCULAR': 'MYASTHENIA_GRAVIS MYOPATHY OTHER_NEUROMUSCULAR',
 'DEMYELINATING': 'MULTIPLE_SCLEROSIS OTHER_DEMYELINATING',
 'NEURODEGENERATIVE_OTHER': 'MOTOR_NEURON_DISEASE OTHER_NEURODEGENERATIVE',
 'SLEEP_NEUROLOGY': 'OBSTRUCTIVE_SLEEP_APNEA CENTRAL_SLEEP_APNEA MIXED_SLEEP_APNEA SLEEP_APNEA_UNSPECIFIED NARCOLEPSY OTHER_SLEEP_NEUROLOGY',
 'OTHER_NEUROLOGIC': 'OTHER_NEUROLOGIC',
}
PARENT = {s:c for c,values in TAXONOMY.items() for s in values.split()}
ACUTE = set('ISCHEMIC_STROKE TIA INTRACEREBRAL_HEMORRHAGE SUBARACHNOID_HEMORRHAGE CONVEXITY_SUBARACHNOID_HEMORRHAGE SUBDURAL_HEMATOMA EPILEPTIC_SEIZURE FOCAL_SEIZURE GENERALIZED_TONIC_CLONIC_SEIZURE STATUS_EPILEPTICUS CONVULSIVE_STATUS_EPILEPTICUS NONCONVULSIVE_STATUS_EPILEPTICUS'.split())


def classify_neurologic(label, *, domain='NEUROLOGIC', legacy_subcategory='OTHER', layer='DISEASE'):
    full = label.lower()
    # Identity belongs to the main clause; a cause does not change its category.
    main = re.split(r'\b(?:bei|auf dem boden|infolge|due to|secondary to)\b',full)[0]
    compact = re.sub(r'[-\s_]', '', main)
    def has(pattern): return bool(re.search(pattern, main, re.I))
    subtype = None
    # Previously retained model domains can be wrong even for explicit systemic
    # labels. Keep these out of the neurological OTHER denominator.
    systemic=[(r'diabetes|hyperurik|hypothyre|hyperthyre','ENDOCRINE_METABOLIC'),
        (r'pseudoaneurysma.*femoralis','CARDIOVASCULAR'),
        (r'harnwegsinfekt|harnwegsinfektion|akut\w*\s+nierenversagen','RENAL_UROLOGIC'),
        (r'pneumoni|pulmonal.*hyperton','PULMONARY'),
        (r'gastroenteritis|gastritis|kolitis|colitis','GASTROINTESTINAL_HEPATIC'),
        (r'glaukom|glaucoma','OPHTHALMOLOGIC'),
        (r'eisenmangelan.m|iron.deficiency.an','ONCOLOGIC_HEMATOLOGIC'),
        (r'coxarthros|cervical.spine.degeneration','MUSCULOSKELETAL_TRAUMA'),
        (r'altersatopie|atopische.dermatitis','DERMATOLOGIC'),
        (r'sinusitis|^infection$','INFECTIOUS')]
    for pattern,target in systemic:
        if has(pattern):return dict(domain=target,category=None,subcategory='OTHER',etiology='UNKNOWN',hemorrhagic_transformation=False,taxonomy_basis='EXPLICIT_NON_NEUROLOGIC_SOURCE_LABEL')
    if has(r'\bdelir'): return dict(domain='PSYCHIATRIC',category='DELIRIUM',subcategory='DELIRIUM',etiology='UNKNOWN',hemorrhagic_transformation=False,taxonomy_basis='EXPLICIT_SOURCE_LABEL')
    if has(r'depress'): return dict(domain='PSYCHIATRIC',category='MOOD_DISORDER',subcategory='DEPRESSION',etiology='UNKNOWN',hemorrhagic_transformation=False,taxonomy_basis='EXPLICIT_SOURCE_LABEL')
    if has(r'\bABRA\b|a[βß]eta.related.angiitis|amyloid.beta.related.angiitis|abeta.related.angiitis'): subtype='ABETA_RELATED_ANGIITIS'
    elif has(r'CAA[- ]?ri\b|CAA.related.inflamm|amyloidangiopathie.*inflamm'): subtype='CAA_RELATED_INFLAMMATION'
    elif has(r'(?:zerebral|cerebral|cns|zns).*?(?:vaskulitis|vasculitis|arteriitis)'): subtype='CNS_VASCULITIS'
    elif has(r'autoimmun.*enzephal|autoimmune.*encephal'): subtype='AUTOIMMUNE_ENCEPHALITIS'
    elif has(r'en[cz]ephalopath'):
        subtype=next((s for pattern,s in [(r'influenza|infekti|infect','INFECTIOUS_ENCEPHALOPATHY'),(r'metaboli','METABOLIC_ENCEPHALOPATHY'),(r'toxisch|toxic','TOXIC_ENCEPHALOPATHY'),(r'hypoxi','HYPOXIC_ENCEPHALOPATHY')] if re.search(pattern,full)), 'OTHER_ENCEPHALOPATHY')
    elif has(r'epilepsie|epilepsy'):
        subtype=next((s for p,s in [(r'post.?stroke|post.?isch.m|post.?schlaganfall','POST_STROKE_EPILEPSY'),(r'symptomatisch|symptomatic','SYMPTOMATIC_EPILEPSY'),(r'generalis','GENERALIZED_EPILEPSY'),(r'fokal|focal','FOCAL_EPILEPSY')] if has(p)), 'EPILEPSY')
    elif has(r'status epilepticus|status_epilepticus'):
        subtype='NONCONVULSIVE_STATUS_EPILEPTICUS' if has(r'non.?konvuls|non.?convuls|nicht.?konvuls') else 'CONVULSIVE_STATUS_EPILEPTICUS' if has(r'konvuls|convuls') else 'STATUS_EPILEPTICUS'
    elif has(r'anfall|anfälle|seizure|anf.ll'):
        subtype='GENERALIZED_TONIC_CLONIC_SEIZURE' if has(r'tonisch.klon|tonic.clonic') else 'FOCAL_SEIZURE' if has(r'fokal|focal') else 'EPILEPTIC_SEIZURE'
    elif has(r'subjektiv.*kognitiv|subjective.*cognitive'): subtype='SUBJECTIVE_COGNITIVE_DECLINE'
    elif has(r'\bMCI\b|mild cognitive|leichte.*kognitive'): subtype='MILD_COGNITIVE_IMPAIRMENT'
    elif has(r'demen|demez|alzheimer|lewy|frontotemporal'):
        subtype=next((s for p,s in [(r'mischtyp|mixed|gemischt','MIXED_DEMENTIA'),(r'lewy','LEWY_BODY_DEMENTIA'),(r'frontotemporal','FRONTOTEMPORAL_DEMENTIA'),(r'alzheimer','ALZHEIMER_DISEASE'),(r'vaskul.r|vascular','VASCULAR_DEMENTIA')] if has(p)), 'DEMENTIA_UNSPECIFIED')
    elif has(r'polyneuropath|\bPNP\b'):
        subtype='DEMYELINATING_POLYNEUROPATHY' if has(r'demyelin') else 'AXONAL_SENSORY_POLYNEUROPATHY' if has(r'axonal') and has(r'sensib|sensor') else 'AXONAL_POLYNEUROPATHY' if has(r'axonal') else 'SENSORY_POLYNEUROPATHY' if has(r'sensib|sensor') else 'POLYNEUROPATHY'
    elif has(r'neuropath'): subtype='OTHER_PERIPHERAL_NEUROPATHY'
    elif has(r'karpaltunnel|carpal tunnel'): subtype='CARPAL_TUNNEL_SYNDROME'
    elif has(r'radikulopath|radiculopath'): subtype='RADICULOPATHY'
    elif re.search(r'restlesslegs|\brls\b',compact): subtype='RESTLESS_LEGS_SYNDROME'
    elif has(r'parkinson'): subtype='PARKINSONISM' if has(r'syndrom|parkinsonism') else 'PARKINSON_DISEASE'
    elif has(r'tremor'): subtype='TREMOR'
    elif has(r'dystoni'): subtype='DYSTONIA'
    elif has(r'migr.ne|migraine'): subtype='MIGRAINE_WITHOUT_AURA' if has(r'ohne aura|without aura') else 'MIGRAINE_WITH_AURA' if has(r'mit aura|with aura') else 'OTHER_PRIMARY_HEADACHE'
    elif has(r'spannungskopfschmerz|tension.type.headache'): subtype='TENSION_TYPE_HEADACHE'
    elif has(r'kopfschmerz|headache'): subtype='SECONDARY_HEADACHE' if has(r'sekund.r|secondary') else 'OTHER_PRIMARY_HEADACHE'
    elif has(r'lagerungsschwindel|\bbppv\b'): subtype='BPPV'
    elif has(r'multifaktoriell.*schwindel|multifactorial.*dizz'): subtype='MULTIFACTORIAL_DIZZINESS'
    elif has(r'zentral.*schwindel|central.*vertigo'): subtype='CENTRAL_VERTIGO'
    elif has(r'peripher.*vestibul|vestibular.neuritis|neuritis.vestibul'): subtype='PERIPHERAL_VESTIBULOPATHY'
    elif has(r'schlafapnoe|sleep.apnea|\bOSAS\b'):
        subtype='MIXED_SLEEP_APNEA' if has(r'gemischt|mixed') else 'CENTRAL_SLEEP_APNEA' if has(r'zentral|central') else 'OBSTRUCTIVE_SLEEP_APNEA' if has(r'obstrukt|obstruct|\bOSAS\b') else 'SLEEP_APNEA_UNSPECIFIED'
    elif has(r'narkoleps|narcoleps'): subtype='NARCOLEPSY'
    elif layer=='EVENT' and re.search(r'h.mm?orrhagisch\w*\s+infarkt',full): subtype='ISCHEMIC_STROKE'
    elif has(r'multiinfarktgeschehen|basilaristhrombose'): subtype='OTHER_CEREBROVASCULAR'
    elif has(r'isch.m.*(?:infarkt|insult|schlaganfall)|ischemic.stroke|embolic stroke|ischämischer_infarkt'): subtype='ISCHEMIC_STROKE'
    elif has(r'\bTIA\b|transitor.*isch.m'): subtype='TIA'
    elif has(r'\bcSAH\b|konvexit.*subarach|convexity.*subarach'): subtype='CONVEXITY_SUBARACHNOID_HEMORRHAGE'
    elif has(r'subarach|subarchono|\bSAB\b|\bSAH\b'): subtype='SUBARACHNOID_HEMORRHAGE'
    elif has(r'subdural'): subtype='SUBDURAL_HEMATOMA'
    elif has(r'intra[cz]erebral.*(?:blutung|h.mat|hemorr|haemorr|h.morr)|\bICH\b|lob.r.*h.mat'): subtype='INTRACEREBRAL_HEMORRHAGE'
    elif has(r'amyloidangiopath|amyloid.angiopath|\bCAA\b'): subtype='CEREBRAL_AMYLOID_ANGIOPATHY'
    elif has(r'sinusvenenthromb|cerebral.venous.thromb'): subtype='CEREBRAL_VENOUS_THROMBOSIS'
    elif has(r'(?:zerebral|cerebral|hirn).*aneurysm'): subtype='CEREBRAL_ANEURYSM'
    elif has(r'\bAVM\b|arterioven.se.malformation'): subtype='AVM'
    elif has(r'karotisstenos|carotid.stenos'): subtype='CAROTID_STENOSIS'
    elif has(r'intrakraniell.*stenos|intracranial.stenos'): subtype='INTRACRANIAL_STENOSIS'
    elif has(r'multiple.sklerose|multiple.sclerosis'): subtype='MULTIPLE_SCLEROSIS'
    elif has(r'myasthenia'): subtype='MYASTHENIA_GRAVIS'
    elif has(r'myopath'): subtype='MYOPATHY'
    elif has(r'meningitis'): subtype='MENINGITIS'
    elif has(r'infekt.*(?:hirnhaut|gehirnh.ut)|infektion.*(?:hirnhaut|gehirnh.ut)|herpes.zoster'): subtype='OTHER_INFECTIOUS_NEUROLOGIC'
    elif has(r'en[cz]ephalitis'): subtype='ENCEPHALITIS'
    elif has(r'gliom|meningeom|hirntumor'): subtype='PRIMARY_CNS_TUMOR'
    elif has(r'hirnmetasta|cns.metasta'): subtype='CNS_METASTASIS'
    elif has(r'amyotroph.*lateralsklerose|motor.neuron.disease'): subtype='MOTOR_NEURON_DISEASE'
    if layer=='EVENT' and subtype in ('EPILEPSY','FOCAL_EPILEPSY','GENERALIZED_EPILEPSY','SYMPTOMATIC_EPILEPSY','POST_STROKE_EPILEPSY','OTHER_EPILEPSY'):
        if has(r'status epilepticus'):
            subtype='NONCONVULSIVE_STATUS_EPILEPTICUS' if has(r'non.?konvuls|non.?convuls') else 'CONVULSIVE_STATUS_EPILEPTICUS' if has(r'konvuls|convuls') else 'STATUS_EPILEPTICUS'
        else:
            subtype='FOCAL_SEIZURE' if has(r'fokal|focal') else 'EPILEPTIC_SEIZURE'
    if subtype is None and layer=='EVENT' and legacy_subcategory in ACUTE:
        subtype=legacy_subcategory
    if subtype is None and domain!='NEUROLOGIC':
        return dict(domain=domain,category=None,subcategory=legacy_subcategory,etiology='UNKNOWN',hemorrhagic_transformation=False,taxonomy_basis='NON_NEUROLOGIC_RETAINED')
    if subtype is None:
        subtype={'CAA':'CEREBRAL_AMYLOID_ANGIOPATHY','EPILEPSY':'EPILEPSY','ICH':'INTRACEREBRAL_HEMORRHAGE','INTRACEREBRAL_HEMORRHAGE':'INTRACEREBRAL_HEMORRHAGE','ISCHEMIC_STROKE':'ISCHEMIC_STROKE','TIA':'TIA','COGNITIVE_DISORDER_DEMENTIA':'OTHER_COGNITIVE_DISORDER','PERIPHERAL_NEUROPATHY':'OTHER_PERIPHERAL_NEUROPATHY'}.get(legacy_subcategory,'OTHER_NEUROLOGIC')
    etiology='UNKNOWN'
    if subtype in ACUTE or PARENT[subtype]=='EPILEPSY_SEIZURE':
        if re.search(r'(?<![a-z])(?:traumatisch|traumatic)',full) and not re.search(r'atraumatisch|nicht traumatisch|non.?traumatic',full): etiology='TRAUMATIC'
        elif re.search(r'amyloidangiopath|\bcaa\b', full) and subtype!='CEREBRAL_AMYLOID_ANGIOPATHY': etiology='CAA'
        elif re.search(r'vaskul.r.*metabol|vascular.*metabol',full): etiology='VASCULAR_METABOLIC'
        elif re.search(r'metabol',full): etiology='METABOLIC'
        elif re.search(r'hypertensiv',full): etiology='HYPERTENSIVE'
        elif re.search(r'antikoagulation|anticoagulation',full): etiology='ANTICOAGULATION_ASSOCIATED'
        elif re.search(r'post.?stroke|post.?schlaganfall',full): etiology='POST_STROKE'
        elif re.search(r'vaskul.r|vascular',full): etiology='VASCULAR'
    return dict(domain='NEUROLOGIC',category=PARENT[subtype],subcategory=subtype,etiology=etiology,
        hemorrhagic_transformation=subtype=='ISCHEMIC_STROKE' and bool(re.search(r'sekund.r.*(?:h.mm?orrhag|einblutung)|hemorrhagic transformation|h.morrhagisch.*transformation|h.mm?orrhagisch\w*\s+infarkt',full)),
        taxonomy_basis='EXPLICIT_SOURCE_LABEL' if subtype!='OTHER_NEUROLOGIC' else 'UNRESOLVED_NEUROLOGIC_REVIEW')


def validate_taxonomy(row):
    if row['domain']=='NEUROLOGIC' and PARENT.get(row['subcategory'])!=row['category']:
        raise ValueError('Invalid neurologic category/subcategory parent')
