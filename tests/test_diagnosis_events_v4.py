"""Tests use synthetic source text; no clinical data, medication inference or DB."""
import copy
import json
from pathlib import Path

import pytest

from caa_pdf_extractor.extraction import diagnosis_events_v4 as dx
from caa_pdf_extractor import diagnosis_v4_regression as runner
from caa_pdf_extractor.module_artifacts import ModuleStore, sha, select_latest_validated


def sources(documents):
    bundle = {'patient_id':'p','documents':[]}
    for i, (date, heading, text) in enumerate(documents,1):
        bundle['documents'].append(dict(source_document_id=i, document_date=date,
            sections=[dict(section_id=f'd{i}',heading=heading,text=text,page_start=1)]))
    return bundle, {'mentions':[], 'audit_only':[]}


def assemble(documents):
    return dx.assemble('p',*sources(documents))


def test_two_stroke_episodes_and_post_lysis_complication():
    result = assemble([
        ('2015-09-07','Diagnosen bei Entlassung',
         '1. Ischämischer Infarkt im Stromgebiet der A. cerebri media rechts\n'
         '- Ätiologie: kardioembolisch bei Vorhofflimmern\n'
         '- Therapie: systemische Lyse am 29.8.2015\n'
         '- Komplikation: Parietookzipitales intraparenchymales Hämatom links nach Lyse\n'
         '2. Mediateilinfarkt rechts 2006\n3. Vorhofflimmern'),
        ('2015-10-20','Diagnosen',
         'Ischämischer Arteria cerebri media-Infarkt rechts (cardio-embolisch bei Vorhofflimmern)\n'
         'Einblutung parieto-occipital links nach Lyse\n'
         'Mediateilinfarkt rechts 2006'),
        ('2015-10-30','Diagnosen',
         'Ischämischer Arteria cerebri media-Infarkt rechts am 29.8.2015 (cardio-embolisch bei Vorhofflimmern)\n'
         'Einblutung parieto-occipital links nach Lyse\nMediateilinfarkt rechts 2006'),
    ])
    events = result['canonical_clinical_events']
    strokes = [e for e in events if e['event_type']=='ISCHEMIC_STROKE']
    assert {e['event_date'] for e in strokes} == {'2006','2015-08-29'}
    current = next(e for e in strokes if e['event_date']=='2015-08-29')
    assert current['mention_count']==3
    assert current['acute_treatment']['IV_THROMBOLYSIS'] is True
    assert current['etiology']=='CARDIOEMBOLIC'
    assert current['side']=='RIGHT' and current['territory']=='MCA'
    assert current['etiology_context']=='ATRIAL_FIBRILLATION'
    hemorrhage = [e for e in events if e['event_type']=='TREATMENT_ASSOCIATED_ICH']
    assert len(hemorrhage)==1 and hemorrhage[0]['mention_count']==3
    assert not any(e['event_type']=='PRIMARY_ICH' for e in events)
    assert sum(d['canonical_name']=='ATRIAL_FIBRILLATION' for d in result['canonical_disease_identities'])==1
    assert sum(r['record_type']=='CLINICAL_EVENT' and r['canonical_name']=='ISCHEMIC_STROKE' for r in result['patient_clinical_diagnoses'])==2


def test_imaging_date_is_report_date_not_comparison_scan():
    result = assemble([
        ('2021-03-09','Befunde',
         'Magnetresonanztomografie: Schädel mit Sequenz (08.03.2021)\n'
         'Abgesehen von einer rezenten punktförmigen ischämischen Läsion links mesiotemporal '
         'unveränderte Befundverhältnisse zum letzten MRI vom 15.10.2020.'),
        ('2022-01-01','Diagnosen',
         '- cMRT-Kontrolle 03/2021:\n-- rezente kleine ischämischeLäsion links mesiotemporal'),
    ])
    events=result['canonical_clinical_events']
    assert len(events)==1
    assert events[0]['event_date']=='2021-03-08'
    assert events[0]['event_date_is_approximate']
    assert events[0]['location']=='MESIOTEMPORAL' and events[0]['side']=='LEFT'
    assert events[0]['source_type']=='IMAGING_CONFIRMED'
    assert events[0]['mention_count']==2


def test_differential_and_established_assertions_do_not_flatten():
    result=assemble([
        ('2020-01-16','Diagnosen','Verdacht auf cerebrale Vaskulitis in Abklärung, Erstdiagnose 2004\nDD: Zerebrale Amyloidangiopathie'),
        ('2021-03-09','Diagnosen','Zerebrale Vaskulitis\nDD: Zerebrale Amyloidangiopathie'),
        ('2025-11-14','Diagnosen','Zerebrale Vaskulitis\nAmyloidangiopathie (stabiler Befund vom Oktober 2025)'),
    ])
    by_name={d['canonical_name']:d for d in result['canonical_disease_identities']}
    assert by_name['CEREBRAL_AMYLOID_ANGIOPATHY']['first_suspicion_date']=='2020-01-16'
    assert by_name['CEREBRAL_AMYLOID_ANGIOPATHY']['first_explicit_diagnosis_date']=='2025-11-14'
    assert by_name['CNS_VASCULITIS']['first_suspicion_date']=='2004'
    assert by_name['CNS_VASCULITIS']['first_explicit_diagnosis_date']=='2021-03-09'
    assert {a['assertion_status'] for a in result['diagnostic_assertion_timeline']}=={'SUSPECTED','ESTABLISHED'}


@pytest.mark.parametrize('text',[
    'Internistischer Befund','Kardio-pulmonal kompensiert','Untersuchungstechnik',
    'Diffusionsstudie mit ADCMap, T2 axial, FLAIR axial', 'Rechtes Herz:',
    'Vena cava inferior normal weit','Keine epileptiformen Potenziale',
    'Thrombophilie-Screening','PET','keine Klappenvitien','kein Perikarderguss',
    'keine rezente Ischämie','Keine rezente ischämische Läsion links mesiotemporal',
    'Cyclophosphamid-Therapie 2015 bis 2017','Rituximab-Therapie','Infliximab-Therapie','CPAP Therapie',
])
def test_junk_and_negation_never_promote_even_in_diagnosis_sections(text):
    bundle,history=sources([('2021-03-09','Diagnosen',text)])
    history['audit_only']=[dict(item_id='legacy',section_id='d1',source_document_id=1,evidence_text=text,start=0,end=len(text))]
    result=dx.assemble('p',bundle,history)
    assert not result['diagnosis_mentions']
    assert not result['canonical_disease_identities']
    assert not result['canonical_clinical_events']
    assert result['raw_source_spans'][0]['original_source']['item_id']=='legacy'
    assert result['diagnosis_rejected_spans']


def test_disease_local_assertion_scope():
    result=assemble([('2021-03-09','Zusammenfassung',
        'Bei Frau A besteht eine zerebrale Vaskulitis, wobei über lange Jahre die Differentialdiagnose einer Amyloidangiopathie bestand. '
        'Kein Perikarderguss.')])
    assert {d['canonical_name'] for d in result['canonical_disease_identities']}=={'CNS_VASCULITIS'}
    timeline=result['diagnostic_assertion_timeline']
    assert next(a for a in timeline if a['canonical_name']=='CEREBRAL_AMYLOID_ANGIOPATHY')['assertion_status']=='SUSPECTED'


def test_partial_dates_and_anatomy_preserve_distinct_events():
    result=assemble([
        ('2021-12-01','Diagnosen','Ischämischer Mediainfarkt rechts 2006\nIschämischer Mediainfarkt rechts 2015\nIschämischer Mediainfarkt links 2015'),
    ])
    events=result['canonical_clinical_events']
    assert len(events)==3
    assert all(e['event_date_precision']=='YEAR' for e in events)
    assert {e['event_date'] for e in events}=={'2006','2015'}


def test_undated_ambiguous_reference_cannot_bridge_two_strokes():
    result=assemble([
        ('2020-01-01','Diagnosen','Ischämischer Mediainfarkt rechts 2006\nIschämischer Mediainfarkt rechts 2015'),
        (None,'Anamnese','Ischämischer Mediainfarkt rechts'),
    ])
    assert len(result['canonical_clinical_events'])==2
    assert any(m['reason']=='AMBIGUOUS_OR_UNDATED_EVENT_REFERENCE' for m in result['unresolved_candidates'])


def test_dvt_date_bound_to_embolism_not_earlier_stroke():
    result=assemble([('2015-01-01','Vorerkrankungen',
       'Ischämischer Mediainfarkt rechts 2006, pulmonalarterielle Embolie 2003 bei tiefer Beinvenenthrombose rechts.')])
    dvt=next(e for e in result['canonical_clinical_events'] if e['event_type']=='DVT')
    assert dvt['event_date']=='2003'
    assert dvt['side']=='RIGHT'


def test_failed_regression_publishes_no_validated_diagnoses_and_preserves_other_modules(tmp_path):
    source=tmp_path/'source'
    prior=ModuleStore(tmp_path/'previous')
    expected={}
    for pid in runner.PATIENTS:
        folder=source/pid;folder.mkdir(parents=True)
        bundle,history=sources([('2020-01-01','Diagnosen','Hypothyreose')])
        (folder/'source_bundle.json').write_text(json.dumps(bundle),encoding='utf-8')
        (folder/'source_mentions.json').write_text(json.dumps(history),encoding='utf-8')
        manifest={name:prior.publish(pid,name,'3.0', {name+'.json':[]},'source') for name in ['medications','imaging','ORBIT']}
        prior.save_manifest(pid,manifest);expected[pid]=manifest
    hashes={str(p):sha(p) for p in prior.root.rglob('*') if p.is_file()}
    report=runner.run(source,prior.root,tmp_path/'output',protect=False)
    assert report['DIAGNOSIS_V4_READY_FOR_PHYSICIAN_REVIEW']=='NO'
    assert report['MEDICATION_V3_PRESERVED']=='YES'
    assert report['medication_module_runs']==0
    for pid in runner.PATIENTS:
        manifest=runner.load(tmp_path/'output/patient_artifacts'/pid/'module_manifest.json')
        assert manifest['diagnoses']['status']=='FAILED_VALIDATION'
        assert select_latest_validated([manifest],'diagnoses') is None
        assert manifest['medications']['artifacts']==expected[pid]['medications']['artifacts']
        assert manifest['medications']['status']=='VALIDATED'
        assert manifest['medications']['preservation_status']=='PRESERVED'
        assert select_latest_validated([expected[pid],manifest],'medications') is not None
        assert manifest['ORBIT']['status']=='STALE_DEPENDENCY'
    assert all(sha(p)==h for p,h in hashes.items())


def test_html_escapes_source_text():
    result=assemble([('2020-01-01','Diagnosen','Hypothyreose <script>alert(1)</script>')])
    _,page=runner.render({'p':result})
    assert '<script>' not in page
    assert '&lt;script&gt;' in page


def test_therapy_disease_reference_alone_is_evidence_only():
    result=assemble([('2020-01-01','Diagnosen','Cyclophosphamid-Therapie bei zerebraler Vaskulitis')])
    assert not result['diagnosis_mentions']
    assert not result['canonical_disease_identities']
    assert result['diagnosis_rejected_spans'][0]['semantic_class']=='THERAPY'


def test_investigation_question_does_not_establish_a_disease():
    result=assemble([('2020-01-01','Befunde','Fragestellung: zerebrale Vaskulitis')])
    assert not result['diagnosis_mentions']
    assert not result['canonical_disease_identities']
    assert result['diagnostic_assertion_timeline'][0]['assertion_status']=='SUSPECTED'
