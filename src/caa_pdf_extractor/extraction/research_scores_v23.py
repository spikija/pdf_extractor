"""Pure, dated evidence-driven research scores. Missing never becomes zero."""
from datetime import date,timedelta
import math
from .score_bounds import minimum_score

VERSION='RESEARCH_SCORES_V2.4_BOUNDS'


def as_date(v):
    try:return date.fromisoformat(str(v)[:10])
    except (ValueError,TypeError):return None


def age_at(dob,index):
    b,d=as_date(dob),as_date(index)
    return d.year-b.year-((d.month,d.day)<(b.month,b.day)) if b and d and d>=b else None


def component(value,day,ids,**extra):return dict(value=value,evidence_date=day,source_evidence_ids=list(ids),**extra)


def at_index(evidence,index):
    """Reject undated, future, unsourced and unresolved facts as unknown."""
    d=as_date(index);e=evidence or {};day=as_date(e.get('evidence_date'))
    if not d or not day or day>d or not e.get('source_evidence_ids') or not isinstance(e.get('value'),bool):return None
    return e['value']


def finish(parts,index,refs):
    missing=[k for k,v in parts.items() if v is None]
    return dict(score=sum(parts.values()) if not missing else None,score_complete=not missing,missing_components=missing,
        score_index_date=index,components=parts,source_evidence_ids=sorted(set(refs)),rule_version=VERSION)


def cha2ds2_va(*,confirmed_af,index_date,dob,facts,af_state=None):
    if confirmed_af is not True or af_state != 'CONFIRMED_AF':
        return dict(score=None,score_complete=False,missing_components=['CONFIRMED_AF'],score_index_date=index_date,components={},source_evidence_ids=[],esc_oac_recommendation='NOT_ASSESSABLE',applicable=False,AF_state=af_state,rule_version=VERSION)
    age=age_at(dob,index_date);parts={};refs=[]
    for key,weight in [('C',1),('H',1),('D',1),('S2',2),('V',1)]:
        val=at_index(facts.get(key),index_date);parts[key]=weight if val is True else 0 if val is False else None
        if val is not None:refs+=facts[key]['source_evidence_ids']
    parts['A2']=2 if age is not None and age>=75 else 0 if age is not None else None
    parts['A']=1 if age is not None and 65<=age<75 else 0 if age is not None else None
    if age is not None:refs+=['DOB','INDEX_DATE']
    result=finish(parts,index_date,refs)
    result.update(applicable=True,AF_state=af_state,esc_oac_recommendation='NOT_ASSESSABLE' if result['score'] is None else 'LOW_RISK' if result['score']==0 else 'CONSIDER_OAC' if result['score']==1 else 'OAC_RECOMMENDED')
    result.update(minimum_score(parts))
    result['component_states']={k:dict(state='UNKNOWN' if v is None else 'PRESENT' if v else 'ABSENT',points=v,
        ascertainment_basis='INSUFFICIENT_DATA' if v is None else 'DERIVED_FROM_STRUCTURED_DATA' if k in ('A2','A') else 'EXPLICIT_POSITIVE' if v else 'ABSENT_AFTER_ADEQUATE_REVIEW',
        source_evidence_ids=facts.get(k,{}).get('source_evidence_ids',[]) if k not in ('A2','A') else ['DOB','INDEX_DATE']) for k,v in parts.items()}
    return result


def get_lab_value_before_index(patient_id,analyte,index_date,lookback_window,*,labs,admission_date=None):
    """Nearest prior valid measurement; dated samples only, never future.

    With multiple different same-day values and no sample times, return missing.
    A same-admission prior result outranks other prior results; the window still
    applies to all candidates. Units must be normalized by the caller.
    """
    index=as_date(index_date)
    if not index:return None
    eligible=[l for l in labs if str(l.get('patient_id'))==str(patient_id) and l.get('analyte')==analyte and l.get('validation_status','VALID')=='VALID' and isinstance(l.get('value'),(int,float)) and not isinstance(l.get('value'),bool) and math.isfinite(l['value']) and l['value']>=0 and as_date(l.get('sample_date')) and 0<=(index-as_date(l['sample_date'])).days<=lookback_window and l.get('source_evidence_ids')]
    if not eligible:return None
    same_day=[l for l in eligible if as_date(l['sample_date'])==index]
    same_admission=[l for l in eligible if admission_date and l.get('admission_date')==admission_date]
    pool=same_day or same_admission or eligible
    latest=max(as_date(l['sample_date']) for l in pool);pool=[l for l in pool if as_date(l['sample_date'])==latest]
    if len({(l['value'],l.get('unit')) for l in pool})!=1:return None
    chosen=dict(pool[0]);chosen['selection_basis']='INDEX_DATE' if same_day else 'SAME_ADMISSION_PRIOR' if same_admission else 'PRIOR_LOOKBACK'
    chosen['source_evidence_ids']=sorted({r for l in pool for r in l['source_evidence_ids']})
    return chosen


def orbit(*,patient_id,index_date,dob,sex,facts,labs,lookback_window=90,admission_date=None):
    age=age_at(dob,index_date);refs=[];parts=dict(age=1 if age is not None and age>=75 else 0 if age is not None else None)
    selected={k:get_lab_value_before_index(patient_id,k,index_date,lookback_window,labs=labs,admission_date=admission_date) for k in ('HEMOGLOBIN','HEMATOCRIT','EGFR')}
    checks=[]
    if sex in ('male','man','female','woman'):
        hbcut=13 if sex in ('male','man') else 12;hctcut=40 if sex in ('male','man') else 36
        for key,unit,cutoff in [('HEMOGLOBIN','g/dL',hbcut),('HEMATOCRIT','%',hctcut)]:
            l=selected[key]
            if l and l['unit']==unit:checks.append(l['value']<cutoff);refs+=l['source_evidence_ids']
    has_laboratory_check=bool(checks)
    history=at_index(facts.get('anemia_history'),index_date)
    if history is not None:checks.append(history);refs+=facts['anemia_history']['source_evidence_ids']
    parts['anemia']=2 if True in checks else 0 if history is False and has_laboratory_check and all(v is False for v in checks) else None
    # A normal Hb alone cannot exclude a documented but unassessed history of anemia.
    for name,weight in [('bleeding_history',2),('antiplatelet',1)]:
        val=at_index(facts.get(name),index_date);parts[name]=weight if val is True else 0 if val is False else None
        if val is not None:refs+=facts[name]['source_evidence_ids']
    egfr=selected['EGFR'];parts['renal']=1 if egfr and egfr.get('unit')=='mL/min/1.73m2' and egfr['value']<60 else 0 if egfr and egfr.get('unit')=='mL/min/1.73m2' else None
    if egfr:refs+=egfr['source_evidence_ids']
    if age is not None:refs+=['DOB','INDEX_DATE']
    result=finish(parts,index_date,refs);score=result['score']
    result.update(orbit_score=score,orbit_complete=result['score_complete'],orbit_category='NOT_ASSESSABLE' if score is None else 'LOW' if score<=2 else 'INTERMEDIATE' if score==3 else 'HIGH',labs_used=selected,lookback_days=lookback_window)
    return result


def stess(*,true_status,index_date,dob,previous_seizures,seizure_type,consciousness):
    if true_status is not True:return dict(score=None,score_complete=False,applicable=False,missing_components=['TRUE_STATUS_EPILEPTICUS'],components={},score_index_date=index_date,source_evidence_ids=[],interpretation='NOT_APPLICABLE')
    age=age_at(dob,index_date);history=at_index(previous_seizures,index_date)
    def owned_value(e):
        day=as_date((e or {}).get('evidence_date'));index=as_date(index_date)
        return e.get('value') if day and index and day<=index and e.get('source_evidence_ids') else None
    typ=owned_value(seizure_type);con=owned_value(consciousness)
    parts=dict(age=2 if age is not None and age>=65 else 0 if age is not None else None,
        previous_seizures=0 if history is True else 1 if history is False else None,
        seizure_type={'FOCAL':0,'FOCAL_IMPAIRED_AWARENESS':0,'GENERALIZED_CONVULSIVE':1,'NCSE_IN_COMA':2}.get(typ),
        consciousness={'AWAKE':0,'SOMNOLENT':0,'CONFUSED':0,'STUPOROUS':1,'COMATOSE':1}.get(con))
    refs=[r for e in (previous_seizures,seizure_type,consciousness) if e for r in e.get('source_evidence_ids',[])]
    if age is not None:refs+=['DOB','INDEX_DATE']
    result=finish(parts,index_date,refs)
    result.update(applicable=True,interpretation='NOT_ASSESSABLE' if result['score'] is None else 'FAVORABLE_STESS' if result['score']<=2 else 'HIGHER_RISK_STESS')
    return result
