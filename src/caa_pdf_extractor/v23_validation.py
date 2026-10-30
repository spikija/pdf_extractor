"""Independent release regressions and lexical score census expectations."""
import re
from .extraction.clinical_scales_v23 import extract_section
from .extraction.research_scores_v23 import orbit,component

def explicit_expectations(line):
    """Separate lexical implementation for explicitly qualified source totals.

    Does not share the extraction regex. Unqualified observations are separately
    retained and reviewed; this census checks the five mandatory source slots.
    """
    tokens=list(re.finditer(r'(?<!\w)(NIHSS|m\s*RS)(?![a-z])',line,re.I));out=[]
    for token in tokens:
        tail=line[token.end():]
        number=re.match(r'\s*(?:(?:bei\s+)?(?:Aufnahme|Entlassung)\s*)?[:=]?\s*(?:von\s*)?(\d{1,2})(?!\d)',tail,re.I)
        if not number:continue
        prefix=line[:token.start()];owned=line[token.start():token.end()+number.end()]
        scale='NIHSS' if token[1].upper()=='NIHSS' else 'mRS'
        markers=list(re.finditer(r'pr[äae]+[ -]*$|pre[ -]*$|premorbid|baseline|bei\s+Aufnahme|bei\s+Entlassung|Aufnahme\s*:|Entlassung\s*:|initial|admission|discharge|Entlassungs-',prefix,re.I))
        if re.search(r'(?:bei\s+)?Aufnahme',owned,re.I):tp='ADMISSION'
        elif re.search(r'(?:bei\s+)?Entlassung',owned,re.I):tp='DISCHARGE'
        elif markers:
            marker=markers[-1][0]
            tp='PRE_EVENT' if re.search(r'pr[äae]|pre|baseline',marker,re.I) else 'ADMISSION' if re.search(r'Aufnahme|initial|admission',marker,re.I) else 'DISCHARGE'
        else:continue
        if tp=='PRE_EVENT' and scale!='mRS':continue
        out.append(dict(scale_name=scale,timepoint=tp,value=int(number[1])))
    return out

def regression_gates():
    text='''Ischämischer Infarkt im Mediastromgebiet links am 28.03.2022
- Ätiologie: offen
- Akuttherapie: mechanische Thrombektomie TICI 3 am 28.03.2022
- neurologisches Outcome: prä-mRS 1
- bei Aufnahme: NIHSS 10, mRS 4
- bei Entlassung: NIHSS 1, mRS 1'''
    rows,eps=extract_section(text,patient_id='SYNTHETIC_REGRESSION',document_id='SYNTHETIC',section_id='SYNTHETIC',admission_date='2022-03-28',discharge_date='2022-04-01')
    expected={('mRS','PRE_EVENT'):1,('NIHSS','ADMISSION'):10,('mRS','ADMISSION'):4,('NIHSS','DISCHARGE'):1,('mRS','DISCHARGE'):1}
    score_failure=int({(r['scale_name'],r['timepoint']):r['value'] for r in rows}!=expected or any(r['source']!='EXPLICIT' for r in rows))
    treatment_failure=int(not eps or (eps[0]['stroke_date'],eps[0]['side'],eps[0]['vascular_territory'],eps[0]['etiology'],eps[0]['tici'],eps[0]['treatment_date'])!=('2022-03-28','LEFT','MCA','UNKNOWN','3','2022-03-28'))
    labs=[dict(patient_id='SYNTHETIC',analyte=a,value=v,unit=u,sample_date='2022-03-28',source_evidence_ids=['SYNTHETIC']) for a,v,u in [('HEMOGLOBIN',11.4,'g/dL'),('EGFR',48,'mL/min/1.73m2')]]
    kw=dict(patient_id='SYNTHETIC',index_date='2022-03-28',dob='1944-01-01',sex='female',facts={k:component(True,'2022-03-28',['SYNTHETIC']) for k in ('antiplatelet','bleeding_history')})
    first=orbit(**kw,labs=labs);later=orbit(**kw,labs=labs+[dict(labs[0],value=15,sample_date='2022-03-29'),dict(labs[1],value=90,sample_date='2022-03-29')])
    return dict(required_stroke_five_explicit_values_failed=score_failure,required_stroke_treatment_regression_failed=treatment_failure,
        required_ORBIT_7_HIGH_regression_failed=int(first['score']!=7 or first['orbit_category']!='HIGH'),required_ORBIT_future_lab_invariance_failed=int(first!=later))
