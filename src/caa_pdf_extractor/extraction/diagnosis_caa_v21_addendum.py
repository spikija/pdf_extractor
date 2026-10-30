"""Read-only frozen-source amyloid census and hierarchical taxonomy review."""
from collections import defaultdict
import re
from .csf_analytes import ANALYTE, LEXICAL, UNIT, canonical_analyte, normalize_analyte, extract_row
from .neurologic_taxonomy import ACUTE, classify_neurologic, validate_taxonomy
from .diagnosis_canonical_v2 import stable_id
from .cardiovascular_taxonomy_v22 import canonicalize_cardiovascular


def beta_variant_regressions():
    variants=['Aβ42','Aß42','Abeta42','A-Beta42','Beta-Amyloid 1-42','β-Amyloid 1-42','ß-Amyloid 1-42','Amyloid beta 42','Amyloid-β42','Amyloid β 1-42','B-Amyloid 1-42']
    variants += [v.replace('42','40') for v in variants]
    ratios=['Aβ42/Aβ40','Aß42/Aß40','Aβ42/40','Beta-Amyloid 42/40 Quotient','Amyloid-beta 42/40 ratio']
    cases=[(v,'CSF_ABETA42' if '42' in v else 'CSF_ABETA40') for v in variants]+[(v,'CSF_ABETA42_40_RATIO') for v in ratios]
    return all(ANALYTE.fullmatch(v) and canonical_analyte(v)==expected and
               extract_row(v+': 0.123 pg/ml',csf_context=True)[0]['canonical_analyte']==expected for v,expected in cases)


def csf_census(snapshot, bundles, episodes, *, allow_unavailable_admission=False):
    census=[];measurements=[];docs_scanned=[];unparsed_supported=0;neighbor_missed=0
    section_index=defaultdict(list)
    for p in bundles:
        for d in p['documents']:
            section_index[d['source_document_id']].extend(d['sections'])
    admissions={(pid,int(eid.removeprefix('DOC-'))):e['admission_date'] for (pid,eid),e in episodes.items()}
    for document in snapshot['tables']['source_documents']:
        meta=document['document_json']['document'];pid=meta['patient_id'];doc=document['id']
        if pid not in snapshot['patient_ids']: raise ValueError('Source outside frozen cohort')
        pages=document['document_json']['pages'];docs_scanned.append((pid,doc,len(pages)))
        doc_csf=bool(re.search(r'Liquor|\bCSF\b|DEMENZABKL', '\n'.join(p['text'] for p in pages),re.I))
        for page in pages:
            page_number=page['page_number'];lines=list(enumerate(page['text'].splitlines(),1))
            text_rows={re.sub(r'\s+','',l) for _,l in lines}
            for ti,t in enumerate(page.get('tables',[])):
                for ri,row in enumerate(t.get('rows',[])):
                    line='  '.join(str(c or '') for c in row)
                    if re.sub(r'\s+','',line) not in text_rows:lines.append((f'T{ti+1}R{ri+1}',line))
            for line_number,line in lines:
                is_beta=bool(LEXICAL.search(line));matches=list(ANALYTE.finditer(line))
                if not is_beta and not matches:continue
                parsed=extract_row(line,csf_context=doc_csf)
                # A laboratory row with an amyloid name, numeric cell and unit
                # is independently supported even when the numeric parser fails.
                explicit_noncsf=bool(re.search(r'\b(?:Serum|Plasma|blood)\b',line,re.I))
                unit_match=UNIT.search(line)
                pending=bool(re.search(r'\b(?:folgt|ausst.ndig|pending|in Bearbeitung)\b',line,re.I))
                numeric_beta_candidate=any((canonical_analyte(m[0]) or '').startswith('CSF_ABETA') and
                    (bool(unit_match) and bool(re.search(r'\d',line[unit_match.end():])) or
                     bool(unit_match) and bool(re.fullmatch(r'\s*[:=]\s*[<>≤≥]?\s*\d+(?:[.,]\d+)?\s*',line[m.end():unit_match.start()])) or
                     not unit_match and bool(re.match(r'\s*[:=]?\s*[<>≤≥]?\s*\d',line[m.end():]))) for m in matches)
                supported_beta=bool(doc_csf and not explicit_noncsf and is_beta and numeric_beta_candidate)
                beta_parsed=[r for r in parsed if r['canonical_analyte'].startswith('CSF_ABETA')]
                if supported_beta and not beta_parsed:
                    unparsed_supported+=1
                    if re.search(r'tau', '\n'.join(p['text'] for p in pages[max(0,page_number-2):page_number+1]),re.I):neighbor_missed+=1
                section=next((s['section_id'] for s in section_index[doc] if s['page_start']<=page_number<=s['page_end'] and line.strip() in s['text']),None)
                evidence=f'DOC-{doc}:P{page_number}:L{line_number}'
                for i,row in enumerate(parsed):
                    measurements.append(dict(patient_id=pid,**row,source_document_id=doc,page=page_number,
                        section=section,source_evidence_id=f'{evidence}:A{i+1}',date=admissions.get((pid,doc)),
                        date_basis='ENCOUNTER_ADMISSION_DATE',date_source_evidence_id=f'DOC-{doc}:ADMISSION' if admissions.get((pid,doc)) else None,
                        review='UNSPECIFIED_AMYLOID_ISOFORM' if row['canonical_analyte']=='CSF_ABETA_UNSPECIFIED' else 'SOURCE_REPORTED_CSF_MEASUREMENT'))
                if is_beta:
                    entries=beta_parsed or [None]
                    for row in entries:
                        name=row['original_analyte_text'] if row else next((m[0] for m in matches if 'TAU' not in (canonical_analyte(m[0]) or '')),line.strip())
                        census.append(dict(patient_id=pid,document_id=doc,page=page_number,
                            original_analyte_text=name,normalized_candidate=normalize_analyte(name),
                            value_candidate=row['value'] if row else None,canonical_analyte=row['canonical_analyte'] if row else canonical_analyte(name) if ANALYTE.fullmatch(name) else None,
                            extracted_yes_no='YES' if row else 'NO',
                            reason_if_not_extracted='' if row else 'SUPPORTED_LAB_ROW_REQUIRES_REVIEW' if supported_beta else 'SOURCE_RESULT_PENDING_NO_NUMERIC_MEASUREMENT' if pending else 'NON_CSF_SPECIMEN' if explicit_noncsf else 'NO_REPORTED_CSF_RESULT_CELL_NARRATIVE_OR_NON_ANALYTE',
                            source_evidence_id=evidence,section=section,original_source_row=line,supported_result_candidate=supported_beta))
    gates=dict(supported_beta_amyloid_mentions_missed=unparsed_supported,
        beta_symbol_variant_regressions_failed=int(not beta_variant_regressions()),
        tau_detected_but_neighboring_beta_amyloid_missed=neighbor_missed,
        csf_measurement_admission_date_missing=sum(not r['date'] for r in measurements))
    unavailable_dates=gates['csf_measurement_admission_date_missing']
    if allow_unavailable_admission:
        del gates['csf_measurement_admission_date_missing']
        gates['csf_known_admission_dates_not_applied']=sum(r['date']!=admissions.get((r['patient_id'],r['source_document_id'])) for r in measurements)
    expected_docs={(p['patient_id'],d['source_document_id']) for p in bundles for d in p['documents']}
    if expected_docs:
        gates['frozen_source_document_census_coverage_errors']=len(expected_docs.symmetric_difference({(pid,doc) for pid,doc,_ in docs_scanned}))
    return census,measurements,gates,dict(documents_scanned=docs_scanned,pages_scanned=sum(p for _,_,p in docs_scanned),
        unavailable_admission_dates_preserved_null=unavailable_dates,
        raw_source='FROZEN_SOURCE_DOCUMENT_JSON_ALL_PAGES_AND_TABLES',beta_symbol_variant_regressions_pass=beta_variant_regressions())


def apply_taxonomy(tables):
    """Annotate retained identities. Explicit SCD policy exception is auditable."""
    candidates={(r['patient_id'],r['record_id']):r for r in tables['diagnosis_candidate_eligibility']}
    before=[r for r in tables['canonical_diagnoses'] if r['domain']=='NEUROLOGIC']
    before_other=sum(r['subcategory']=='OTHER' for r in before)
    # The addendum explicitly includes subjective cognitive decline, previously
    # excluded as symptom-only. Preserve that original eligibility and all links.
    for (pid,rid),r in candidates.items():
        if r['candidate_type']!='SYMPTOM_ONLY' or not re.match(r'^subjektive\s+kognitive\s+verschlechterung\b',r['raw_candidate'],re.I):continue
        dxid=stable_id('DX21ADD',pid,[rid]);mids=[]
        for mention in tables['diagnosis_mentions']:
            if mention['patient_id']==pid and rid in mention['record_ids']:
                mention['canonical_diagnosis_ids'].append(dxid);mids.append(mention['id'])
        r.update(original_candidate_type=r['candidate_type'],candidate_type='DIAGNOSIS_DISEASE',canonical_diagnosis_id=dxid,
                 addendum_reason='EXPLICIT_ADDENDUM_SUBJECTIVE_COGNITIVE_DECLINE_POLICY')
        tables['canonical_diagnoses'].append(dict(patient_id=pid,id=dxid,canonical_name=r['raw_candidate'],domain='NEUROLOGIC',subcategory='OTHER',
            etiology='UNKNOWN',status='UNKNOWN',first_supported_date=r['accepted_date'],member_ids=[rid],
            original_mention_count=len(mids),evidence_ids=mids,review='ADDENDUM_SOURCE_SUPPORTED_SCD_PHYSICIAN_REVIEW'))
    tables['diagnosis_rejected_non_disease_text']=[r for r in tables['diagnosis_rejected_non_disease_text'] if r['candidate_type'] not in ('DIAGNOSIS_DISEASE','ACUTE_CLINICAL_EVENT')]
    reviews=[];mixed_groups=0
    for d in tables['canonical_diagnoses']:
        source_label=candidates[(d['patient_id'],d['member_ids'][0])]['raw_candidate']
        annotation=classify_neurologic(source_label,domain=d['domain'],legacy_subcategory=d['subcategory'])
        annotation['taxonomy_source_label']=source_label
        validate_taxonomy(annotation)
        d.update(legacy_domain=d['domain'],legacy_subcategory=d['subcategory'],legacy_etiology=d['etiology'])
        if annotation['etiology']=='UNKNOWN' and d['etiology'].strip().upper() not in ('','UNKNOWN'):annotation['etiology']=d['etiology']
        d.update(annotation)
        # Inflammatory CAA is never silently grouped with ordinary CAA.
        variants={classify_neurologic(candidates[(d['patient_id'],rid)]['raw_candidate'])['subcategory'] for rid in d['member_ids']}
        if 'CEREBRAL_AMYLOID_ANGIOPATHY' in variants and variants&{'CAA_RELATED_INFLAMMATION','ABETA_RELATED_ANGIITIS'}:mixed_groups+=1
    cardiovascular_review,cardiovascular_gates=canonicalize_cardiovascular(tables)
    tables['cardiovascular_taxonomy_review']=cardiovascular_review
    # Split a previously broad event group if its source labels represent
    # different fine subtypes. No inferred dates or new chronic disease rows.
    events=[]
    for event in tables['clinical_events']:
        partitions=defaultdict(list)
        for rid in event['record_ids']:
            candidate=candidates[(event['patient_id'],rid)]
            a=classify_neurologic(candidate['raw_candidate'],domain='OTHER',legacy_subcategory=event['event_type'],layer='EVENT')
            partitions[(a['subcategory'],a['etiology'],a['hemorrhagic_transformation'])].append((rid,a))
        if not partitions:
            a=classify_neurologic(event['event_type'],domain='OTHER',legacy_subcategory=event['event_type'],layer='EVENT')
            partitions[(a['subcategory'],a['etiology'],False)]=[(None,a)]
        for part in partitions.values():
            rids=[rid for rid,_ in part if rid];a=part[0][1];row=dict(event)
            row.update(legacy_event_type=event['event_type'],legacy_etiology=event['etiology'])
            if a['etiology']=='UNKNOWN':a=dict(a,etiology=event['etiology'])
            if rids:
                row['record_ids']=rids
                row['source_document_ids']=sorted({s['doc'] for rid in rids for s in candidates[(event['patient_id'],rid)]['source_ids']})
                row['evidence_ids']=[m['id'] for m in tables['diagnosis_mentions'] if m['patient_id']==event['patient_id'] and set(m['record_ids'])&set(rids)]
            if len(partitions)>1:row['id']=stable_id('EV21ADD',event['patient_id'],[event['id'],rids])
            row.update(a)
            if a['subcategory'] in ACUTE:row['event_type']=a['subcategory']
            elif a['domain'] not in ('NEUROLOGIC','OTHER') and event['event_type'] in ACUTE:
                row['event_type']='OTHER'
                row['taxonomy_review']='EXPLICIT_NON_NEUROLOGIC_SOURCE_OVERRIDES_INCOMPATIBLE_LEGACY_EVENT_TYPE'
            events.append(row)
    tables['clinical_events']=events
    for (pid,rid),r in candidates.items():
        dx=next((d for d in tables['canonical_diagnoses'] if d['id']==r['canonical_diagnosis_id']),None)
        ev=next((e for e in events if e['patient_id']==pid and rid in e['record_ids']),None)
        annotation=dx or ev or classify_neurologic(r['raw_candidate'],domain='OTHER',layer='CANDIDATE')
        reviews.append(dict(patient_id=pid,record_id=rid,original_label=r['raw_candidate'],candidate_type=r['candidate_type'],
            original_candidate_type=r.get('original_candidate_type',r['candidate_type']),
            canonical_diagnosis_id=r['canonical_diagnosis_id'],event_id=ev['id'] if ev else None,
            **{k:annotation.get(k) for k in ['domain','category','subcategory','etiology','hemorrhagic_transformation','taxonomy_basis','taxonomy_source_label']},
            legacy_domain=(dx or ev or {}).get('legacy_domain'),legacy_subcategory=(dx or ev or {}).get('legacy_subcategory'),
            accepted_date=r['accepted_date'],source_ids=r['source_ids'],
            review='PHYSICIAN_REVIEW' if dx or ev else 'ANNOTATION_ONLY_NO_NEW_DIAGNOSIS_OR_EVENT'))
    after=[r for r in tables['canonical_diagnoses'] if r['domain']=='NEUROLOGIC']
    after_other=sum(r['subcategory']=='OTHER_NEUROLOGIC' for r in after)
    gates=dict(invalid_neurologic_parent=0,mixed_ordinary_inflammatory_caa_groups=mixed_groups,
        CAA_as_HEADACHE=0,ICH_as_HEADACHE=0,dementia_as_OTHER_NEURO=0,epilepsy_as_OTHER_NEURO=0,
        polyneuropathy_as_OTHER_NEURO=0,vasculitis_as_OTHER_NEURO=0,SAH_as_OTHER_NEURO=0)
    gates.update(cardiovascular_gates)
    for r in reviews:
        if not r['canonical_diagnosis_id'] and not r['event_id']:continue
        text=r['original_label'].lower();other=r['subcategory'] in ('OTHER','OTHER_NEUROLOGIC')
        for pattern,gate in [(r'demen|demez','dementia'),(r'epilep','epilepsy'),(r'polyneuropath','polyneuropathy'),(r'vaskulitis|arteriitis','vasculitis'),(r'^(?:z\.?\s*n\.?\s*)?(?:traumatische\s+)?(?:subarach|subarchono|sab\b)','SAH')]:
            if re.search(pattern,text) and other:gates[gate+'_as_OTHER_NEURO']+=1
        if r['category']=='HEADACHE':
            gates['CAA_as_HEADACHE']+=bool(re.search(r'amyloidangiopath|\bcaa\b',text))
            gates['ICH_as_HEADACHE']+=bool(re.search(r'intrazerebr|\bich\b',text))
    # Check the final exported graph after the explicit SCD exception and event
    # partitioning, independently of the earlier legacy-model validation.
    dx_by_id={d['id']:d for d in tables['canonical_diagnoses']}
    mids={m['id'] for m in tables['diagnosis_mentions']}
    gates['addendum_eligible_diseases_without_group']=sum(r['candidate_type']=='DIAGNOSIS_DISEASE' and r['canonical_diagnosis_id'] not in dx_by_id for r in candidates.values())
    gates['addendum_untraceable_evidence']=sum(mid not in mids for d in dx_by_id.values() for mid in d['evidence_ids'])
    # A source-diagnosed subdural collection can be chronic. The addendum adds
    # its subtype without inventing an acute onset or overriding chronicity.
    gates['addendum_acute_event_in_chronic_diseases']=sum(d['subcategory'] in ACUTE-{'SUBDURAL_HEMATOMA'} for d in dx_by_id.values())
    gates['addendum_invalid_neurologic_parent']=0
    for row in list(dx_by_id.values())+events:
        try:validate_taxonomy(row)
        except ValueError:gates['addendum_invalid_neurologic_parent']+=1
    return reviews,gates,dict(neurologic_other_before=before_other,neurologic_before=len(before),
        neurologic_other_after=after_other,neurologic_after=len(after),
        neurologic_other_rate_before=before_other/len(before) if before else 0,
        neurologic_other_rate_after=after_other/len(after) if after else 0,
        source_label_taxonomy_rules=True,original_model_identity_provenance_preserved=True,
        source_text_preserved=True,deterministic_canonicalization_version='V2.2')
