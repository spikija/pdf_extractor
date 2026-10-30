"""Explicit CERAD observations supplement, never replace, canonical MMSE."""
import re
from .clinical_scales_v23 import dates

def extract_cerad(text,*,patient_id,document_id,section_id,page=None):
    observations=[];census=[]
    patterns=[('CERAD_TOTAL',r'CERAD[ -]*(?:Total[ -]*Score|Gesamtscore)(?:[ \t]*[:=][ \t]*|[ \t]+bei\s+|[ \t]+)(\d+(?:[.,]\d+)?)(?![\d./])'),
        ('CERAD_SEMANTIC_FLUENCY',r'CERAD\s*:\s*Sem\.?\s*Fl[üu]ss\.?\s*:\s*(\d+(?:[.,]\d+)?)')]
    for name,rx in patterns:
        for m in re.finditer(rx,text,re.I):
            context=text[max(0,m.start()-100):m.end()+20]
            # Source reports may restate prior tests. Such observations are not
            # counted as an additional currently performed examination.
            historical=bool(re.search(r'Vorbefund|fr[üu]her|damals',context,re.I))
            prefix=text[max(0,m.start()-65):m.start()]
            month=re.findall(r'(?<!\d)(\d{1,2})/(\d{4})(?!\d)',prefix)
            ds=dates(prefix)
            eid=f'{document_id}:{section_id}:CERAD:{m.start()}'
            observations.append(dict(id=eid,patient_id=str(patient_id),case_id=None,event_id=None,encounter_id=None,
                scale_type='OTHER',test_name=name,assessment_timepoint='OTHER',assessment_date=ds[-1] if ds else None,assessment_datetime=None,
                assessment_year=int(month[-1][1]) if month else None,assessment_month=int(month[-1][0]) if month else None,
                date_precision='DAY' if ds else 'MONTH' if month else 'UNKNOWN',value_numeric=float(m[1].replace(',','.')),raw_score=float(m[1].replace(',','.')),maximum_score=None,value_text=None,
                source_type='EXPLICIT',source_document_id=str(document_id),source_page=page,source_section=section_id,evidence_id=eid,evidence_quote=context[:160],
                assessment_role='HISTORICAL_REFERENCE' if historical or month else 'EXPLICIT_TEST_OBSERVATION',assessability='ASSESSABLE',review_required=True,rule_version='COGNITIVE_TESTS_V2.3'))
    for n,line in enumerate(text.splitlines()):
        if re.search(r'CERAD|\bMoCA\b',line,re.I):census.append(dict(patient_id=str(patient_id),source_document_id=str(document_id),page=page,section_id=section_id,line=n,evidence_quote=line[:160],
            disposition='SOURCE_TEST_REFERENCE; SEE_EXPLICIT_OBSERVATIONS; NO_CONVERSION_OR_NEW_PERFORMED_TEST_INFERRED',review_required=True))
    return observations,census
