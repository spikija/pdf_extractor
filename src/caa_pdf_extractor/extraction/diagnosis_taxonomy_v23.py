"""Deterministic V2.3 source-label mappings, independent of persistence."""
import re
from .cardiovascular_taxonomy_v22 import findings as cardiovascular_findings

VERSION='DIAGNOSIS_TAXONOMY_V2.3'


def common_diagnoses(label):
    text=label.casefold();out=[]
    def positive(pattern):
        for match in re.finditer(pattern,text):
            before=re.split(r'[;\n]|\b(?:aber|jedoch|but)\b',text[:match.start()])[-1]
            before=before[-70:]
            if not re.search(r'\b(?:kein\w*|ohne|no|without|verdacht|suspected|ausschluss)\b|\bv\s*\.\s*a\s*\.',before):return True
        return False
    def add(name,display,domain,category,subcategory,etiology='UNKNOWN'):
        out.append(dict(canonical_name=name,display_name=display,domain=domain,category=category,subcategory=subcategory,etiology=etiology,rule_version=VERSION))
    # Pulmonary/portal hypertension is not arterial hypertension.
    if positive(r'\b(?:hypertoni\w*|hypertension|htn)\b') and not re.search(r'pulmonal|portal|intrakraniell',text):
        add('ARTERIAL_HYPERTENSION','Arterial hypertension','CARDIOVASCULAR','HYPERTENSION','OTHER')
    if positive(r'diabet|\bdm\s*(?:2|ii|1|i)\b'):
        t2=bool(re.search(r'(?:typ\s*(?:2|ii)\b|type\s*2\b|\bdm\s*(?:2|ii)\b)',text))
        t1=bool(re.search(r'(?:typ\s*(?:1|i)\b|type\s*1\b|\bdm\s*(?:1|i)\b)',text))
        if t2 and not t1:add('DIABETES_MELLITUS_TYPE_2','Diabetes mellitus type 2','ENDOCRINE_METABOLIC','DIABETES','TYPE_2','ENDOCRINE')
        elif t1 and not t2:add('DIABETES_MELLITUS_TYPE_1','Diabetes mellitus type 1','ENDOCRINE_METABOLIC','DIABETES','TYPE_1','ENDOCRINE')
    if positive(r'hypercholester|hyperlipid|dyslipid|hypertrigly'):
        sub='MIXED_HYPERLIPIDEMIA' if re.search(r'gemischt|mixed',text) else 'HYPERTRIGLYCERIDEMIA' if 'hypertrigly' in text else 'HYPERCHOLESTEROLEMIA' if 'hypercholester' in text else 'OTHER'
        name=sub if sub!='OTHER' else 'LIPID_DISORDER'
        add(name,'Lipid disorder' if sub=='OTHER' else sub.replace('_',' ').capitalize(),'ENDOCRINE_METABOLIC','LIPID_DISORDER',sub)
    if positive(r'an[äa]mie|anemia|anaemia'):
        sub='IRON_DEFICIENCY_ANEMIA' if re.search(r'eisenmangel|iron.deficien',text) else 'MACROCYTIC_ANEMIA' if re.search(r'makrozyt|macrocyt',text) else 'HEMOLYTIC_ANEMIA' if re.search(r'h[äa]molyt|hemolyt',text) else 'NORMOCYTIC_NORMOCHROMIC_ANEMIA' if re.search(r'normozyt|normocyt',text) and re.search(r'normochrom',text) else 'ANEMIA_UNSPECIFIED'
        add(sub,sub.replace('_',' ').capitalize(),'ONCOLOGIC_HEMATOLOGIC','ANEMIA',sub)
    return out


def normalize(label,original=None,eligible=True):
    if not eligible:return []
    mapped=common_diagnoses(label)
    cardiac=cardiovascular_findings(label)
    if cardiac:
        mapped += [dict(canonical_name=c['canonical_name'],display_name=c.get('display_name'),domain='CARDIOVASCULAR',
            category=c['category'],subcategory=c['canonical_name'],etiology='UNKNOWN',
            af_type=c['af_types'][0] if len(c.get('af_types',[]))==1 else 'UNSPECIFIED' if c['canonical_name']=='ATRIAL_FIBRILLATION' else None,
            af_types=c.get('af_types',[]),valvular_severity=c['valvular_severities'][0] if len(c.get('valvular_severities',[]))==1 else 'UNKNOWN' if c['canonical_name']!='ATRIAL_FIBRILLATION' else None,
            valvular_severities=c.get('valvular_severities',[]),rule_version=VERSION) for c in cardiac]
    if mapped:return mapped
    return [dict(original,rule_version=VERSION)] if original else []
