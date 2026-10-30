# Ranked patient-bound gender

`patient_bound_gender_v2_ranked` establishes identity from DOB/name headers or
an explicit report-about introduction. Names bind evidence to the patient;
they never determine gender.

Evidence priority is:

1. Full patient name and title in a DOB identity header, or an explicit patient
   demographic field.
2. A report-about introduction with the patient's full name and an agreeing
   relative pronoun.
3. An agreeing age plus Patient/Patientin in an anchored clinical narrative.
4. Local patient grammar or a surname-bound title in an anchored narrative.

One tier 1–3 assertion can qualify. Tier 4 requires two distinct supporting
assertions. Identical copied phrases do not become independent evidence.
The strongest available tier decides; lower-tier contradictions remain in
`rejected_markers` as `REJECTED_LOWER_TIER_CONTRADICTION`, classified
`NON_PATIENT_OR_TEMPLATE_CONFLICT`. Contradictions within the strongest tier
block. Conflicting weak evidence without an identity-level winner also stays
blocked; it is never resolved by voting. Explicit other/unspecified/unknown
values are not replaced by binary grammatical inference.

Recipient blocks, relatives, informants, quoted text and intervening other
people cannot supply patient grammar. An explicit patient name can re-anchor
the narrative. Ambiguous multi-page prepared sections are excluded. Every
accepted or rejected span retains its document, section, page, offset, tier,
binding method and reason.

The case source fingerprint includes this implementation, so previous case
artifacts require regeneration and review before any future commit. A
gender-only validation artifact is for review and does not authorize a live
case write. No model inference is involved.
