"""Pure, review-only criteria calculators. No source-diagnosis or SQL mutation.

Boston: Charidimou et al., Lancet Neurology 2022, Table 2 (PMID 35841910).
Edinburgh ORIGINAL: Rodrigues et al. 2018, Figure 3 (PMID 29331631).
SIMPLIFIED: Schwarz et al. 2022 (PMID 35314501). Versions are explicit.
UNKNOWN is represented by None, never interpreted as an absent lesion.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Inputs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class BostonFeatures(Inputs):
    age: int | None = Field(default=None,ge=0,le=130)
    qualifying_presentation: bool | None = None
    mri_adequate: bool | None = None
    blood_sensitive_sequence: bool | None = None
    lobar_ich: int | None = Field(default=None,ge=0)
    lobar_cmb: int | None = Field(default=None,ge=0)
    css_csah_foci: int | None = Field(default=None,ge=0)
    hemorrhagic_count_complete: bool | None = None
    severe_cso_pvs: bool | None = None
    multispot_wmh: bool | None = None
    deep_ich: bool | None = None
    deep_cmb: bool | None = None
    alternative_cause: bool | None = None
    pathology: Literal['AUTOPSY_SEVERE_CAA_VASCULOPATHY','BIOPSY_OR_HEMATOMA_SOME_CAA','UNKNOWN'] = 'UNKNOWN'
    pathology_no_other_diagnostic_lesion: bool | None = None
    sources: dict[str,list[str]] = Field(default_factory=dict)

def boston_v2(f: BostonFeatures):
    holds=[]
    def result(value):
        return dict(classification=value,assessable=value!='NOT_ASSESSABLE',holds=holds,
                    rule_version='BOSTON_V2_2022_TABLE2')
    if f.qualifying_presentation is True and f.pathology_no_other_diagnostic_lesion is True:
        if f.pathology=='AUTOPSY_SEVERE_CAA_VASCULOPATHY': return result('DEFINITE_CAA')
        if f.pathology=='BIOPSY_OR_HEMATOMA_SOME_CAA': return result('PROBABLE_WITH_SUPPORTING_PATHOLOGY')
    # Known disqualifiers can be assessed without inventing missing MRI findings.
    if (f.age is not None and f.age<50) or f.qualifying_presentation is False or f.alternative_cause is True or f.deep_ich is True or f.deep_cmb is True:
        return result('DOES_NOT_MEET')
    for key in ('age','qualifying_presentation','mri_adequate','blood_sensitive_sequence','deep_ich','deep_cmb','alternative_cause'):
        if getattr(f,key) is None: holds.append('UNKNOWN_'+key.upper())
    if f.mri_adequate is False: holds.append('INADEQUATE_MRI')
    if f.blood_sensitive_sequence is False: holds.append('NO_BLOOD_SENSITIVE_SEQUENCE')
    if holds: return result('NOT_ASSESSABLE')
    # Counts are distinct lesions on the same adequate study; cSS/cSAH overlap
    # must be resolved upstream and must not be counted twice.
    lower=sum(v or 0 for v in (f.lobar_ich,f.lobar_cmb,f.css_csah_foci))
    wm=f.severe_cso_pvs is True or f.multispot_wmh is True
    if lower>=2 or (lower>=1 and wm): return result('PROBABLE_CAA')
    if f.hemorrhagic_count_complete is not True:
        holds.append('INCOMPLETE_DISTINCT_HEMORRHAGIC_COUNTS');return result('NOT_ASSESSABLE')
    if any(v is None for v in (f.lobar_ich,f.lobar_cmb,f.css_csah_foci)):
        holds.append('UNKNOWN_HEMORRHAGIC_COUNT');return result('NOT_ASSESSABLE')
    if lower==1 or wm: return result('POSSIBLE_CAA')
    if f.severe_cso_pvs is None or f.multispot_wmh is None:
        holds.append('UNKNOWN_QUALIFYING_WHITE_MATTER_FEATURES');return result('NOT_ASSESSABLE')
    return result('DOES_NOT_MEET')

class EdinburghFeatures(Inputs):
    # Patient/timepoint assessment, not mere MRI existence or sequence wording.
    adequate_mri_available: bool | None = None
    ct_available: bool | None = None
    lobar_ich: bool | None = None
    spontaneous_ich: bool | None = None
    appropriate_context: bool | None = None
    finger_like_projections: bool | None = None
    subarachnoid_extension: bool | None = None
    apoe_e4_status: Literal['ZERO','ONE_OR_MORE','UNKNOWN'] = 'UNKNOWN'
    sources: dict[str,list[str]] = Field(default_factory=dict)

def edinburgh_ct(f: EdinburghFeatures):
    # Route before inspecting CT morphology or genotype. A failed/incomplete
    # Boston result does not authorize falling back from an adequate MRI.
    if f.adequate_mri_available is not False:
        adequate = f.adequate_mri_available is True
        return dict(edinburgh_ct_applicable=False,
                    applicability_uncertain=not adequate, version='UNKNOWN',
                    rule_version='UNKNOWN', classification='NOT_APPLICABLE' if adequate else 'NOT_ASSESSABLE',
                    assessable=False, holds=['ADEQUATE_MRI_USE_BOSTON_V2' if adequate else 'UNKNOWN_MRI_ADEQUACY'])
    eligibility=[f.ct_available,f.lobar_ich,f.spontaneous_ich,f.appropriate_context]
    morphology_known=f.finger_like_projections is not None and f.subarachnoid_extension is not None
    version='ORIGINAL' if f.apoe_e4_status!='UNKNOWN' else 'SIMPLIFIED' if morphology_known else 'UNKNOWN'
    result=dict(edinburgh_ct_applicable=all(v is True for v in eligibility),applicability_uncertain=any(v is None for v in eligibility),
                version=version,rule_version='EDINBURGH_2018_FIG3' if version=='ORIGINAL' else 'EDINBURGH_SIMPLIFIED_2022' if version=='SIMPLIFIED' else 'UNKNOWN',holds=[])
    if any(v is False for v in eligibility):
        return dict(result,classification='NOT_APPLICABLE',assessable=False)
    if any(v is None for v in eligibility):
        return dict(result,classification='NOT_ASSESSABLE',assessable=False,holds=['APPLICABILITY_REQUIRES_REVIEW'])
    flp,sah=f.finger_like_projections,f.subarachnoid_extension
    if version=='ORIGINAL' and sah is True and f.apoe_e4_status=='ONE_OR_MORE':
        return dict(result,classification='HIGH_PROBABILITY',assessable=True)
    if flp is None or sah is None:
        return dict(result,classification='NOT_ASSESSABLE',assessable=False,holds=['UNKNOWN_CT_MORPHOLOGY'])
    if version=='SIMPLIFIED':
        category='HIGH_PROBABILITY' if flp and sah else 'INTERMEDIATE_OR_UNCERTAIN' if flp or sah else 'LOW_PROBABILITY'
    else:
        e4=f.apoe_e4_status=='ONE_OR_MORE'
        if sah and (e4 or flp): category='HIGH_PROBABILITY'
        elif not flp: category='INTERMEDIATE_OR_UNCERTAIN' if sah or e4 else 'LOW_PROBABILITY'
        else:
            # FLP without SAH is not represented in original Figure 3; do not
            # silently replace the original criteria with a later 2-of-3 rule.
            return dict(result,classification='NOT_ASSESSABLE',assessable=False,holds=['ORIGINAL_UNREPRESENTED_FLP_WITHOUT_SAH'])
    return dict(result,classification=category,assessable=True)


def caa_imaging_assessment(boston: BostonFeatures, edinburgh: EdinburghFeatures):
    """Route already assembled patient/timepoint evidence to one framework.

    MRI adequacy must include interpretability and sufficient susceptibility
    information; supported indirect evidence is valid without SWI/GRE wording.
    None means unresolved availability/adequacy, not confirmed absence of MRI.
    """
    if edinburgh.adequate_mri_available is True:
        if boston.mri_adequate is not True:
            raise ValueError('Conflicting patient/timepoint MRI adequacy')
        return dict(framework='BOSTON_V2', **boston_v2(boston))
    if boston.mri_adequate is True:
        raise ValueError('Conflicting patient/timepoint MRI adequacy')
    result = edinburgh_ct(edinburgh)
    if result['edinburgh_ct_applicable']:
        return dict(framework='EDINBURGH', **result)
    return dict(result, framework='NONE', classification='NOT_ASSESSABLE')

def require_provenance(features,allowed_ids=None):
    """Every supplied non-UNKNOWN input needs an explicit reference registry entry."""
    missing=[]
    for key,value in features.model_dump().items():
        if key=='sources' or value is None or value=='UNKNOWN': continue
        if not features.sources.get(key): missing.append(key)
    if missing: raise ValueError('Missing feature provenance: '+','.join(missing))
    if allowed_ids is not None:
        unknown={ref for refs in features.sources.values() for ref in refs if ref not in allowed_ids}
        if unknown: raise ValueError('Unknown source IDs: '+','.join(sorted(unknown)))
    return features
