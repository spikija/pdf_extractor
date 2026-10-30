"""General source-bound retrospective function schemas and validators.
No patient identifiers, file access, model transport or persistence.
"""
import re,math
from datetime import date
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .encounter_granite_v2 import MRSValue
VERSION='FUNCTIONAL_STATUS_V1'
CONSISTENCY_RETRY_INSTRUCTION='Reconsider the CFS anchor using your already extracted functional phenotype. Do not add facts. A CFS 5 phenotype is primarily dependent in higher-order activities; your extracted phenotype indicates likely personal-ADL dependence. Select the best-fitting anchor and uncertainty interval without assuming undocumented ADLs.'
def norm(s):return ' '.join(s.split()).casefold()

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)

class Evidence(Strict):
    text_concept:str
    exact_quote:str
    temporal_class:Literal['BASELINE','ACUTE','UNCERTAIN']
    functional_domain:Literal['MOBILITY','TRANSFER','PERSONAL_ADL','IADL','FEEDING','CONTINENCE','COMMUNICATION','COGNITION','CAREGIVER_DEPENDENCY','OTHER']
    evidence_id:str
    temporal_reason:str

class CFS(Strict):
    candidate_cfs:int|None=Field(ge=1,le=9)
    confidence:Literal['HIGH','MODERATE','LOW']
    review_required:bool
    baseline_dependency_summary:str
    supporting_evidence_ids:list[str]
    missing_information:list[str]
    acute_features_excluded:list[str]

class MRS(Strict):
    pre_event_mrs_candidate:int|None=Field(ge=0,le=5)
    candidate_range:list[int]|None
    confidence:Literal['HIGH','MODERATE','LOW']
    review_required:bool
    supporting_evidence_ids:list[str]
    missing_information:list[str]
    rationale:str

class Proposal(Strict):
    evidence:list[Evidence]
    cfs:CFS
    pre_event_mrs:MRS
    admission_mrs:int|None=Field(ge=0,le=5)

def validate_candidates(raw,units):
    """Existing numeric validator plus experiment-only temporal/provenance checks.

    This permits baseline inference, never infers admission function and never
    treats a catheter as proof of incontinence or PEG as proof of bedbound state.
    """
    p=Proposal.model_validate(raw);index={u['evidence_id']:u for u in units};errors=[];accepted=[]
    for claim in p.evidence:
        unit=index.get(claim.evidence_id)
        if not unit or not norm(claim.exact_quote) or norm(claim.exact_quote) not in norm(unit['text']):
            errors.append(dict(rule='EXACT_SOURCE_PROVENANCE_REQUIRED',evidence_id=claim.evidence_id));continue
        q=norm(claim.exact_quote)
        # Explicit acute features cannot be relabelled baseline by the model.
        if claim.temporal_class=='BASELINE' and re.search(r'neu aufgetreten|rezente|zucken des gesamten|starren blick',q):
            errors.append(dict(rule='NO_ACUTE_STATE_RECONSTRUCTION',evidence_id=claim.evidence_id));continue
        if claim.temporal_class=='BASELINE' and re.search(r'entlassen|Entlassung',unit['text'],re.I) and not re.search(r'vorbestehend|bis dato|vor dem Ereignis|pr[äa]morbid',q,re.I):
            errors.append(dict(rule='NO_DISCHARGE_TO_BASELINE_BACKPROPAGATION',evidence_id=claim.evidence_id));continue
        record=dict(**claim.model_dump(),source_page=unit['source_page'],source_section=unit['source_section'])
        # Domain labels describe the quoted concept only; they do not infer ADL
        # ability, continence or disability grade from diagnoses/devices.
        domain='MOBILITY' if re.search(r'hemipleg',q) else 'CONTINENCE' if re.search(r'katheter',q) else 'OTHER' if re.search(r'intracranielle.*blutung|intrakranielle.*blutung',q) else claim.functional_domain
        if domain!=claim.functional_domain:record.update(model_functional_domain=claim.functional_domain,functional_domain=domain,domain_validation_rule='EXPLICIT_QUOTED_CONCEPT_DOMAIN_ONLY')
        accepted.append(record)
    baseline={e['evidence_id'] for e in accepted if e['temporal_class']=='BASELINE'}
    mrs_candidate=p.pre_event_mrs.pre_event_mrs_candidate if p.pre_event_mrs.pre_event_mrs_candidate is not None else p.pre_event_mrs.candidate_range
    for name,candidate,ids in [('CFS',p.cfs.candidate_cfs,p.cfs.supporting_evidence_ids),('MRS',mrs_candidate,p.pre_event_mrs.supporting_evidence_ids)]:
        if candidate is not None and (not ids or not set(ids)<=baseline):errors.append(dict(rule='BASELINE_SUPPORT_IDS_REQUIRED',scale=name))
    # Reuse the existing Python mRS bounds/exact-versus-range validator.
    interval=p.pre_event_mrs.candidate_range
    if interval is not None and (len(interval)!=2 or any(type(x) is not int or not 0<=x<=5 for x in interval)):
        errors.append(dict(rule='MRS_RANGE_REQUIRES_TWO_BOUNDED_ENDPOINTS',scale='MRS'));interval=None
    try:MRSValue(score=p.pre_event_mrs.pre_event_mrs_candidate,min=interval[0] if interval else None,max=interval[1] if interval else None)
    except ValueError as exc:errors.append(dict(rule='EXISTING_MRSValue_NUMERIC_SHAPE',scale='MRS',detail=str(exc)))
    supporting=' '.join(norm(e['exact_quote']) for e in accepted if e['evidence_id'] in p.pre_event_mrs.supporting_evidence_ids and e['temporal_class']=='BASELINE')
    if p.pre_event_mrs.pre_event_mrs_candidate==5 and not all(re.search(rx,supporting) for rx in (r'bettl[äa]ger|bedridden',r'inkontinen|incontinen',r'st[äa]ndig.*pflege|constant.*nurs')):
        errors.append(dict(rule='MRS5_REQUIRES_BASELINE_BEDBOUND_INCONTINENCE_CONSTANT_CARE; RANGE_OR_ABSTENTION_REQUIRED',scale='MRS'))
    cfs_bad=any(e.get('scale')=='CFS' or 'scale' not in e for e in errors);mrs_bad=any(e.get('scale')=='MRS' or 'scale' not in e for e in errors)
    cfs=p.cfs.candidate_cfs if not cfs_bad else None;mrs=p.pre_event_mrs.pre_event_mrs_candidate if not mrs_bad else None;rng=interval if not mrs_bad else None
    return dict(validated_cfs=cfs,validated_cfs_source='RETROSPECTIVE_REVIEW_REQUIRED' if cfs is not None else 'NOT_ASSESSABLE',
        validated_pre_event_mrs=mrs,candidate_range=rng,validated_mrs_source='RETROSPECTIVE_REVIEW_REQUIRED' if mrs is not None or rng else 'NOT_ASSESSABLE',
        admission_mrs=None,review_required=True,validator_errors=errors,evidence=accepted,
        validator_policy=dict(ALLOW_RETROSPECTIVE_PRE_EVENT_FUNCTION_RECONSTRUCTION=True,NO_ACUTE_STATE_RECONSTRUCTION=True,
            REQUIRE_EXPLICIT_CFS=False,REQUIRE_COMPLETE_ADL_SET=False,admission_inference_allowed=False),existing_numeric_validator='encounter_granite_v2.MRSValue')

def personal_dependency_profile(phenotype):
    p=phenotype['functional_phenotype']
    return p['personal_ADL_dependency']=='LIKELY_DEPENDENT' and p['mobility_impairment']=='SEVERE' and p['caregiver_dependency'] in ('SUBSTANTIAL','LIKELY_CONTINUOUS')

class FunctionalPhenotype(Strict):
    mobility_impairment:Literal['NONE','MILD','MODERATE','SEVERE','UNKNOWN']
    personal_ADL_dependency:Literal['INDEPENDENT','SOME_HELP','SUBSTANTIAL_HELP','LIKELY_DEPENDENT','COMPLETELY_DEPENDENT','UNKNOWN']
    IADL_dependency:Literal['INDEPENDENT','PARTIAL','DEPENDENT','UNKNOWN']
    feeding_dependency:Literal['INDEPENDENT','ASSISTED','ENTERAL_FEEDING','UNKNOWN']
    caregiver_dependency:Literal['NONE','INTERMITTENT','DAILY','SUBSTANTIAL','LIKELY_CONTINUOUS','UNKNOWN']
    communication_cognitive_impairment:Literal['NONE','MILD','MODERATE','SEVERE','UNKNOWN']
    bedbound:Literal['YES','NO','UNKNOWN']
    terminal_state:Literal['YES','NO','UNKNOWN']

class PhenotypeProposal(Strict):
    evidence:list[Evidence]
    functional_phenotype:FunctionalPhenotype
    supporting_evidence_ids:list[str]
    limiting_missing_information:list[str]
    acute_features_excluded:list[str]
    rationale:str

class PhenotypeOnly(Strict):
    functional_phenotype:FunctionalPhenotype
    supporting_evidence_ids:list[str]
    limiting_missing_information:list[str]
    acute_features_excluded:list[str]
    rationale:str

class CFSBestEstimate(Strict):
    best_cfs:int|None=Field(ge=1,le=9)
    plausible_range_min:int|None=Field(ge=1,le=9)
    plausible_range_max:int|None=Field(ge=1,le=9)
    confidence:Literal['HIGH','MODERATE','LOW']
    review_required:bool
    functional_phenotype:FunctionalPhenotype
    supporting_evidence_ids:list[str]
    limiting_missing_information:list[str]
    acute_features_excluded:list[str]
    rationale:str

    @model_validator(mode='after')
    def interval(self):
        lo,hi=self.plausible_range_min,self.plausible_range_max
        if (lo is None)!=(hi is None):raise ValueError('Range requires both endpoints')
        if lo is not None and (lo>hi or self.best_cfs is None or not lo<=self.best_cfs<=hi):raise ValueError('Range must contain the best estimate')
        if self.best_cfs is not None and lo is None:raise ValueError('Best estimate requires plausible interval')
        return self

def age_context(dob,index_date,protocol_min_age=None,protocol_max_age=None):
    """User-requested tropical-year floor; age is never a score component."""
    birth,index=date.fromisoformat(dob),date.fromisoformat(index_date)
    if index<birth:raise ValueError('Index date precedes birth')
    years=math.floor((index-birth).days/365.2425)
    unspecified=protocol_min_age is None and protocol_max_age is None
    outside=(protocol_min_age is not None and years<protocol_min_age) or (protocol_max_age is not None and years>protocol_max_age)
    return dict(patient_age_years=years,age_at_index_date=years,index_date=index_date,date_of_birth=dob,
        age_formula='floor((index_date - date_of_birth).days / 365.2425)',age_context_only=True,
        CFS_APPLICABILITY_REVIEW_REQUIRED=bool(unspecified or outside),
        applicability_reason='PROTOCOL_AGE_BOUNDS_NOT_SPECIFIED' if unspecified else 'OUTSIDE_PROTOCOL_AGE_BOUNDS' if outside else 'WITHIN_PROTOCOL_AGE_BOUNDS',
        protocol_min_age=protocol_min_age,protocol_max_age=protocol_max_age)

def check_phenotype(raw,units):
    p=PhenotypeProposal.model_validate(raw)
    # Reuse the existing isolated quotation/temporal validator without assigning
    # any score yet. This stage validates facts before choosing a CFS anchor.
    adapter=dict(evidence=[e.model_dump() for e in p.evidence],cfs=dict(candidate_cfs=None,confidence='LOW',review_required=True,baseline_dependency_summary='',supporting_evidence_ids=[],missing_information=[],acute_features_excluded=p.acute_features_excluded),pre_event_mrs=dict(pre_event_mrs_candidate=None,candidate_range=None,confidence='LOW',review_required=True,supporting_evidence_ids=[],missing_information=[],rationale='Not reassessed in CFS patch'),admission_mrs=None)
    checked=validate_candidates(adapter,units);errors=list(checked['validator_errors'])
    baseline=[e for e in checked['evidence'] if e['temporal_class']=='BASELINE'];baseline_ids={e['evidence_id'] for e in baseline}
    if p.supporting_evidence_ids and not set(p.supporting_evidence_ids)<=baseline_ids:errors.append(dict(rule='PHENOTYPE_SUPPORT_MUST_BE_BASELINE'))
    # Domains count independent concordant clues, not paragraph identifiers:
    # several baseline facts can legitimately share one summary paragraph ID.
    support=[e for e in baseline if e['evidence_id'] in p.supporting_evidence_ids]
    signals=set()
    for e in support:
        q=norm(e['exact_quote'])
        if re.search(r'hemipleg|rollstuhl|nicht.*geh|geh.*hilfe',q):signals.add('MOBILITY')
        if re.search(r'gepflegt|pflege durch|h[äa]usliche pflege',q):signals.add('CAREGIVER_DEPENDENCY')
        if re.search(r'peg|enteral|beif[üu]tter',q):signals.add('FEEDING')
        if re.search(r'ja.*nein|kommunikation.*eingeschr',q):signals.add('COMMUNICATION')
        if re.search(r'k[öo]rperpflege|ankleiden|toilett',q):signals.add('PERSONAL_ADL')
    if p.functional_phenotype.bedbound=='YES' and not any(re.search(r'bettl[äa]ger|bedridden|bettgebunden',norm(e['exact_quote'])) for e in support):errors.append(dict(rule='BEDBOUND_REQUIRES_OWN_BASELINE_EVIDENCE'))
    if p.functional_phenotype.terminal_state=='YES' and not any(re.search(r'terminal|lebenserwartung|end.of.life',norm(e['exact_quote'])) for e in support):errors.append(dict(rule='TERMINAL_STATE_REQUIRES_OWN_BASELINE_EVIDENCE'))
    return dict(functional_phenotype=p.functional_phenotype.model_dump(),evidence=checked['evidence'],supporting_evidence_ids=p.supporting_evidence_ids,
        baseline_dependency_signals=sorted(signals),limiting_missing_information=p.limiting_missing_information,
        acute_features_excluded=p.acute_features_excluded,validator_errors=errors)

def check_estimate(raw,phenotype):
    p=CFSBestEstimate.model_validate(raw);errors=list(phenotype['validator_errors'])
    if p.functional_phenotype.model_dump()!=phenotype['functional_phenotype']:errors.append(dict(rule='ESTIMATE_MUST_RETAIN_VALIDATED_PHENOTYPE'))
    baseline_ids={e['evidence_id'] for e in phenotype['evidence'] if e['temporal_class']=='BASELINE'}
    if p.best_cfs is not None:
        if not p.supporting_evidence_ids or not set(p.supporting_evidence_ids)<=baseline_ids:errors.append(dict(rule='CFS_SUPPORT_MUST_BE_VALID_BASELINE_EVIDENCE'))
        if len(phenotype['baseline_dependency_signals'])<2:errors.append(dict(rule='AT_LEAST_TWO_CONCORDANT_BASELINE_DEPENDENCY_SIGNALS_REQUIRED'))
        if not p.acute_features_excluded:errors.append(dict(rule='ACUTE_FEATURE_EXCLUSION_MUST_BE_RECORDED'))
        if p.confidence=='HIGH':errors.append(dict(rule='ISOLATED_RETROSPECTIVE_CFS_CONFIDENCE_MUST_NOT_EXCEED_MODERATE'))
        if personal_dependency_profile(phenotype) and p.plausible_range_min is not None and p.plausible_range_min<6:
            errors.append(dict(rule='IADL_ONLY_RANGE_CONTRADICTS_LIKELY_PERSONAL_ADL_DEPENDENCY'))
        if p.best_cfs<=5 and p.functional_phenotype.personal_ADL_dependency in ('SUBSTANTIAL_HELP','LIKELY_DEPENDENT','COMPLETELY_DEPENDENT'):
            errors.append(dict(rule='IADL_ONLY_CFS_ANCHOR_CONTRADICTS_PERSONAL_ADL_DEPENDENCY_PHENOTYPE'))
        if p.confidence in ('LOW','MODERATE') and p.limiting_missing_information and p.plausible_range_min==p.plausible_range_max:
            errors.append(dict(rule='UNCERTAIN_GLOBAL_ESTIMATE_REQUIRES_NONDEGENERATE_PLAUSIBLE_RANGE'))
        supporting=' '.join(norm(e['exact_quote']) for e in phenotype['evidence'] if e['temporal_class']=='BASELINE' and e['evidence_id'] in p.supporting_evidence_ids)
        if p.best_cfs==9 and p.functional_phenotype.terminal_state!='YES':errors.append(dict(rule='CFS9_REQUIRES_BASELINE_TERMINAL_STATE'))
        if p.best_cfs==8 and not re.search(r'terminal|lebenserwartung|limited recovery reserve|kaum.*reserv|sehr.*gering.*erholungsreserv',supporting):errors.append(dict(rule='CFS8_REQUIRES_LIMITED_RECOVERY_RESERVE_BEYOND_NEUROLOGICAL_DISABILITY'))
    accepted=p.best_cfs is not None and not errors
    # Missing ADL detail limits certainty, never invalidates an otherwise
    # supported global estimate. All outputs in this experiment need review.
    result=dict(**p.model_dump(),validated_best_cfs=p.best_cfs if accepted else None,
        validated_plausible_range=[p.plausible_range_min,p.plausible_range_max] if accepted else None,
        source_type='RETROSPECTIVE_REVIEW_REQUIRED' if accepted else 'NOT_ASSESSABLE',
        validator_accepted=accepted,validator_errors=errors,review_required_effective=True,
        baseline_dependency_signals=phenotype['baseline_dependency_signals'],rule_version=VERSION)
    result.update(proposed_best_cfs=p.best_cfs,best_cfs=p.best_cfs if accepted else None,supporting_evidence_ids=sorted(set(p.supporting_evidence_ids)))
    return result
