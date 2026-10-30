"""Review-only full local PDF cohort orchestration. Never commits clinical rows."""
import argparse,contextlib,io,json,shutil,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .full_cohort_sources import prepare,discover
from .full_cohort_extraction import extract
from .full_cohort_enrichment import enrich
from .full_cohort_report import assemble,generate_pdf,validate_pdf,load_csv,EPILEPSY
from .diagnosis_caa_v21_review import main as diagnosis_review,read,write,sha,csvfile,guard_network

BASELINE=Path('data/debug/diagnosis_caa_v21_20261022T200324+0200')


def finalize(root):
    sys.addaudithook(guard_network)
    results=enrich(root)
    ids=[r['patient_id'] for r in results]
    frozen=read(root/'cohort_freeze.json')
    if len(ids)!=frozen['patients'] or len(set(ids))!=len(ids):raise ValueError('Patient artifact cohort mismatch')
    diagnosis_root=root/'diagnosis_caa_v21_full';diagnosis_root.mkdir(exist_ok=True)
    model=diagnosis_root/'model';model.mkdir(exist_ok=True)
    for p in (BASELINE/'model').iterdir():
        if p.is_file() and not (model/p.name).exists():shutil.copyfile(p,model/p.name)
    inputs={name:root/file for name,file in dict(history='history.json',sensory='sensory.json',image='image.json',snapshot='snapshot.json',
        old_assertions='caa_source_assertions.csv',admission_events='validated_admission_events.csv',bundles='bundles.json').items()}
    verification=diagnosis_review(['--output',str(diagnosis_root),'--infer'],inputs=inputs,expected_patient_ids=ids)
    patients,statistics,panels=assemble(root,diagnosis_root)
    target,ranges=generate_pdf(root,patients,statistics,panels)
    pdf_validation=validate_pdf(root,target,ranges)
    current_manifest,current_documents=discover(Path('pdf_db'))
    source_set_same=current_documents==read(root/'source_manifest.json')
    manifest=load_csv(root/'cohort_manifest.csv')
    pids={p['patient_id'] for p in patients};folders={m['patient_id'] for m in current_manifest};manifest_ids={m['patient_id'] for m in manifest}
    gates=dict(verification['gates'])
    gates.update(folder_manifest_patient_mismatch=len(folders.symmetric_difference(manifest_ids)),
        manifest_summary_patient_mismatch=len(manifest_ids.symmetric_difference(pids)),
        summary_PDF_patient_mismatch=len(pids.symmetric_difference(ranges)),source_manifest_changed=int(not source_set_same),
        supported_source_documents_missing=len(current_documents)-len(read(root/'sources.json')['tables']['source_documents']),
        PDF_layout_validation_errors=len(pdf_validation['errors']),duplicate_CAA_patient_counting=0,duplicate_epilepsy_patient_counting=0,
        isolated_seizure_counted_as_chronic_epilepsy=0,CAA_source_vs_computed_conflation=0,clinical_writes=0,cloud_calls=0)
    gates['source_processing_errors']=sum(bool(m['error_status']) for m in manifest)
    canonical=load_csv(root/'canonical_diagnoses.csv')
    gates['isolated_seizure_counted_as_chronic_epilepsy']=sum(p['epilepsy_yes_no'] and not any(d['patient_id']==p['patient_id'] and d['subcategory'] in EPILEPSY for d in canonical) for p in patients)
    metrics={s['metric']:s for s in statistics}
    gates['duplicate_CAA_patient_counting']=abs(metrics['ANY_CAA_POSITIVE']['n']-len({p['patient_id'] for p in patients if p['caa_positive_yes_no']}))
    gates['duplicate_epilepsy_patient_counting']=abs(metrics['CHRONIC_EPILEPSY']['n']-len({p['patient_id'] for p in patients if p['epilepsy_yes_no']}))
    gates['Edinburgh_with_adequate_MRI']=sum(e.get('adequate_mri_available') is True and any(e.get(k) not in ('NOT_APPLICABLE','',None) for k in ('original_edinburgh','simplified_edinburgh')) for e in load_csv(root/'edinburgh_results.csv'))
    for pid,value in [('2684469520','425'),('2826947422','269')]:
        gates['known_beta_regression_'+pid]=int(not any(r['patient_id']==pid and r['canonical_analyte']=='CSF_ABETA_UNSPECIFIED' and r['value']==value for r in load_csv(root/'csf_biomarkers.csv')))
    for row in manifest:
        p=next(p for p in patients if p['patient_id']==row['patient_id'])
        row['processing_status']=p['overall_review_status'] if not row['error_status'] else 'SOURCE_ERROR'
    csvfile(root,'cohort_manifest',manifest)
    decision='FULL_COHORT_PHYSICIAN_REVIEW_READY' if all(v==0 for v in gates.values()) else 'FULL_COHORT_PHYSICIAN_REVIEW_INCOMPLETE'
    result=dict(decision=decision,patients=len(patients),pdfs=frozen['pdfs'],source_pages=frozen['pages'],PDF_pages=pdf_validation['pages'],
        PDF=str(target.resolve()),csv_folder=str(root.resolve()),gates=gates,statistics=statistics,
        review_holds=sum(p['review_hold_count'] for p in patients),clinical_database_writes=0,database_connections=0,cloud_calls=0,
        frozen_source_manifest_sha256=sha(root/'source_manifest.json'),PDF_sha256=sha(target),clinical_approval=False,
        code_sha256={str(p):sha(p) for p in Path('src/caa_pdf_extractor').rglob('*.py')})
    write(root/'final_verification.json',result);write(root/'csv_manifest.json',{p.name:sha(p) for p in root.glob('*.csv')})
    lines=[decision,f'Patients: {len(patients)}',f'PDFs: {frozen["pdfs"]}',f'Source pages: {frozen["pages"]}',f'Review PDF pages: {pdf_validation["pages"]}']
    for name,title in [('ANY_CAA_POSITIVE','CAA positive'),('CLINICIAN_DIAGNOSED_CAA','  Clinician diagnosed'),('COMPUTED_BOSTON_PROBABLE','  Boston probable'),('COMPUTED_BOSTON_POSSIBLE','  Boston possible'),('EDINBURGH_POSITIVE','  Edinburgh positive'),('CAA_SUSPICION_ONLY','  Suspicion only'),('CHRONIC_EPILEPSY','Chronic epilepsy'),('ANY_SEIZURE_EVENT','Any seizure history'),('ANY_STATUS_EPILEPTICUS','Status epilepticus'),('ATRIAL_FIBRILLATION','Atrial fibrillation')]:
        s=metrics[name];lines.append(f'{title}: {s["n"]} / {s["denominator"]} ({s["percent"]:.1f}%)')
    lines += [f'Review holds: {result["review_holds"]}',f'PDF: {target.resolve()}',f'CSV folder: {root.resolve()}']
    (root/'terminal_summary.txt').write_text('\n'.join(lines),encoding='utf-8');print('\n'.join(lines),flush=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);p.add_argument('--finalize-only',action='store_true');a=p.parse_args()
    root=a.output or Path('data/debug')/('full_cohort_'+datetime.now(ZoneInfo('Europe/Vienna')).strftime('%Y%m%dT%H%M%S%z'))
    if root.resolve().parent!=Path('data/debug').resolve() or not root.name.startswith('full_cohort_'):raise ValueError('Output must be a full_cohort_ folder under data/debug')
    if not a.finalize_only:prepare(root,Path('pdf_db'));extract(root)
    finalize(root)


if __name__=='__main__':main()
