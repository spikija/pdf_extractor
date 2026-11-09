"""Merge checked T2 outputs into a complete private CSV, never a partial rebuild.

Plan creation is read-only for the live inventory. Atomic application is separate.
No PostgreSQL imports or connections. Existing image paths and untouched rows survive.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
import pydicom
from .mri_t2_recovery import ROOT,DEFAULT_INVENTORY,connect_inventory,groups,group_id,sha,stamp,write_json,write_csv,identity,lock

DEFAULT_IDENTITY=ROOT/'caa_private_linkage/mri_identity_series_PRIVATE.csv'
REQUIRED=set('dicom_patient_id patient_name_dicom birth_date_yyyymmdd study_date_yyyymmdd study_uid series_uid group_id series_description modality subtype echo geometry dicom_instance_count nifti_category nifti_paths dicom_representative_path identity_qc'.split())


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
    if not fields or len(fields)!=len(set(fields)) or any(None in r or any(v is None for v in r.values()) for r in rows):raise ValueError('Malformed/truncated CSV structure')
    return fields,rows


def image_paths(row):return {p.strip() for p in row['nifti_paths'].split(' | ') if p.strip()}


def validate_complete(rows,source_groups,minimum):
    if len(rows)<minimum:raise ValueError(f'Incomplete inventory: {len(rows)} rows below safety minimum {minimum}; never overwrite a complete inventory with partial output')
    ids={r['group_id'] for r in rows}
    if missing:=set(source_groups)-ids:raise ValueError(f'Incomplete identity inventory: {len(missing)} source groups missing; refuse partial baseline')
    for row in rows:
        source=source_groups.get(row['group_id'])
        if source and any(row[k]!=str(source[s]) for k,s in [('study_uid','study'),('series_uid','series'),('subtype','subtype'),('echo','echo'),('geometry','geometry')]):raise ValueError('Existing identity inventory group metadata conflicts with SQLite')


def preserve(before,after):
    if len(after)<len(before):raise ValueError('Inventory row count would shrink')
    old=defaultdict(set);new=defaultdict(set)
    for rows,lookup in ((before,old),(after,new)):
        for r in rows:lookup[(r['group_id'],r['study_uid'],r['series_uid'],r['nifti_category'])]|=image_paths(r)
    if not {r['group_id'] for r in before}<={r['group_id'] for r in after}:raise ValueError('Existing source group lost')
    if any(paths-new.get(key,set()) for key,paths in old.items()):raise ValueError('Existing converted image paths would be lost')


def validate_proof(row):
    source=json.loads(Path(row['source_qc_path']).read_text('utf-8'));output=json.loads(Path(row['output_qc_path']).read_text('utf-8'))
    if not source['passed'] or not output['passed'] or not output['files']:raise ValueError('Technical QC proof does not pass')
    digest=hashlib.sha256(json.dumps(source['identity'],sort_keys=True).encode()).hexdigest()
    if digest!=row['source_identity_sha256'] or digest!=source['identity_sha256']:raise ValueError('Source identity QC proof changed')
    expected=json.loads(row['nifti_sha256'])
    if expected!={f['path']:f['sha256'] for f in output['files']} or set(expected)!=image_paths(row):raise ValueError('Output file proof differs from result CSV')
    for path,expected_sha in expected.items():
        if sha(path)!=expected_sha:raise ValueError('NIfTI changed since technical QC')
    for path,sig in source['source_file_signatures'].items():
        stat=Path(path).stat()
        if [stat.st_size,stat.st_mtime_ns]!=sig:raise ValueError('Source DICOM changed since QC')
    return source,output


def create_plan(results,inventory=DEFAULT_IDENTITY,sqlite_inventory=DEFAULT_INVENTORY,report_dir=None,minimum=1206):
    inventory=Path(inventory).resolve();results=Path(results).resolve();sqlite_inventory=Path(sqlite_inventory).resolve()
    base_sha=sha(inventory);source_sha=sha(sqlite_inventory);fields,before=read_csv(inventory)
    if not REQUIRED<=set(fields):raise ValueError('Identity inventory schema missing required fields')
    _,recovery=read_csv(results)
    with connect_inventory(sqlite_inventory) as db:source_groups={group_id(r):r for r in groups(db)}
    validate_complete(before,source_groups,minimum)
    if len(recovery)!=len(source_groups) or len({r['group_id'] for r in recovery})!=len(recovery) or {r['group_id'] for r in recovery}!=set(source_groups):raise ValueError('Archive-wide complete recovery results required; partial results cannot drive publication')
    summary=json.loads((results.parent/'summary.json').read_text('utf-8'))
    if summary['source_inventory_sha256']!=source_sha or not summary['source_inventory_unchanged']:raise ValueError('Source inventory differs from recovery run')
    after=[dict(r) for r in before];changes=[];checks=[]
    for row in recovery:
        if row['recovery_status'] not in ('ALREADY_VALID','CONVERTED_QC_PASS') or row['qc_status']!='QC_PASS_NOT_CLINICALLY_VALIDATED':continue
        gid=row['group_id'];index=[i for i,r in enumerate(after) if r['group_id']==gid]
        if not index:raise ValueError('Unknown group lacks full baseline identity provenance')
        t2=[i for i in index if after[i]['nifti_category']=='T2']
        if len(t2)>1:raise ValueError('Duplicate baseline T2 rows require manual reconciliation')
        current_paths=set().union(*(image_paths(after[i]) for i in t2))
        additions=image_paths(row)-current_paths
        if not additions:continue
        source,proof=validate_proof(row);original=after[index[0]];person=source['identity']
        pairs={'dicom_patient_id':'PatientID','patient_name_dicom':'PatientName','birth_date_yyyymmdd':'PatientBirthDate','study_date_yyyymmdd':'StudyDate','study_uid':'StudyInstanceUID','series_uid':'SeriesInstanceUID'}
        if original['identity_qc'] or any(not person.get(k) for k in pairs.values()) or any(original[col]!=person[key] for col,key in pairs.items()):raise ValueError('Existing identity is unclean or differs from new DICOM source; review instead of updating')
        ds=pydicom.dcmread(original['dicom_representative_path'],stop_before_pixels=True)
        if identity(ds)!=person:raise ValueError('Existing representative DICOM provenance conflicts')
        checks.append(dict(result_row=row,source_qc_sha256=sha(row['source_qc_path']),output_qc_sha256=sha(row['output_qc_path'])))
        if t2:
            at=t2[0];updated=dict(after[at]);updated['nifti_paths']=' | '.join(sorted(current_paths|additions));after[at]=updated;kind='EXTEND_T2_PATHS'
        else:
            unconverted=[i for i in index if after[i]['nifti_category']=='NOT_CONVERTED' and not image_paths(after[i])]
            updated=dict(original);updated.update(nifti_category='T2',nifti_paths=' | '.join(sorted(additions)))
            if len(unconverted)==1:after[unconverted[0]]=updated;kind='REPLACE_EMPTY_NOT_CONVERTED'
            else:after.append(updated);kind='APPEND_T2_ROW_PRESERVE_OTHER_CATEGORIES'
        changes.append(dict(group_id=gid,action=kind,added_paths=' | '.join(sorted(additions))))
    preserve(before,after);validate_complete(after,source_groups,minimum)
    directory=Path(report_dir or ROOT/'data/debug'/('t2_identity_update_'+stamp())).resolve();directory.mkdir(parents=True,exist_ok=False)
    staged=directory/'complete_inventory_proposed_PRIVATE.csv';write_csv(staged,after,fields)
    sf,sr=read_csv(staged)
    if sf!=fields or sr!=after:raise ValueError('Complete staged inventory failed serialization round-trip')
    write_csv(directory/'incremental_changes_PRIVATE.csv',changes,['group_id','action','added_paths'])
    plan=dict(schema_version=1,created_utc=stamp(),inventory=str(inventory),baseline_sha256=base_sha,baseline_rows=len(before),baseline_groups=len({r['group_id'] for r in before}),
        source_inventory=str(sqlite_inventory),source_inventory_sha256=source_sha,minimum_rows=minimum,source_group_count=len(source_groups),
        results_csv=str(results),results_sha256=sha(results),staged_inventory=str(staged),staged_sha256=sha(staged),proposed_rows=len(after),
        changed_groups=len(changes),proofs=checks,postgresql_calls=0,applied=False)
    write_json(directory/'update_plan_PRIVATE.json',plan)
    print(json.dumps({k:v for k,v in plan.items() if k!='proofs'},indent=2));print('PLAN ONLY: live identity inventory unchanged.')
    return directory/'update_plan_PRIVATE.json'


def apply_plan(path):
    path=Path(path).resolve();plan=json.loads(path.read_text('utf-8'));target=Path(plan['inventory']);staged=Path(plan['staged_inventory'])
    with lock(target.with_name(target.name+'.incremental.lock')):
        if sha(target)!=plan['baseline_sha256']:raise ValueError('Live inventory changed since plan; prepare a new plan rather than overwriting changes')
        if sha(staged)!=plan['staged_sha256'] or sha(plan['results_csv'])!=plan['results_sha256']:raise ValueError('Plan inputs changed')
        if sha(plan['source_inventory'])!=plan['source_inventory_sha256']:raise ValueError('SQLite inventory changed since plan')
        old_fields,before=read_csv(target);fields,after=read_csv(staged)
        if fields!=old_fields or len(before)!=plan['baseline_rows'] or len(after)!=plan['proposed_rows']:raise ValueError('Plan row count/schema mismatch')
        with connect_inventory(plan['source_inventory']) as db:source_groups={group_id(r):r for r in groups(db)}
        validate_complete(before,source_groups,plan['minimum_rows']);validate_complete(after,source_groups,plan['minimum_rows']);preserve(before,after)
        for proof in plan['proofs']:
            row=proof['result_row']
            if sha(row['source_qc_path'])!=proof['source_qc_sha256'] or sha(row['output_qc_path'])!=proof['output_qc_sha256']:raise ValueError('QC proof changed')
            validate_proof(row)
        if not plan['changed_groups']:print('NO CHANGES: live identity inventory not replaced.');return
        backup=target.with_name(target.name+'.backup_'+stamp())
        with backup.open('xb') as f:f.write(target.read_bytes());f.flush();os.fsync(f.fileno())
        if sha(backup)!=plan['baseline_sha256']:raise ValueError('Backup validation failed; original inventory untouched')
        fd,temp=tempfile.mkstemp(prefix='.t2-incremental-',suffix='.csv',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(staged.read_bytes());f.flush();os.fsync(f.fileno())
            _,reread=read_csv(temp)
            if len(reread)!=plan['proposed_rows'] or sha(temp)!=plan['staged_sha256']:raise ValueError('Temporary complete inventory validation failed')
            if sha(target)!=plan['baseline_sha256']:raise ValueError('Concurrent inventory change; original not replaced')
            os.replace(temp,target)
        finally:
            if Path(temp).exists():Path(temp).unlink()
        write_json(path.parent/('applied_receipt_'+stamp()+'.json'),dict(inventory=str(target),rows=len(after),sha256=sha(target),backup=str(backup),postgresql_calls=0))
        print(f'APPLIED ATOMICALLY: {len(before)} -> {len(after)} rows; {plan["changed_groups"]} updated groups. Backup: {backup}. PostgreSQL unchanged.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path);p.add_argument('--inventory',type=Path,default=DEFAULT_IDENTITY)
    p.add_argument('--sqlite-inventory',type=Path,default=DEFAULT_INVENTORY);p.add_argument('--report-dir',type=Path)
    p.add_argument('--minimum-rows',type=int,default=1206,help='Additional truncation guard; full SQLite group coverage is always required')
    p.add_argument('--apply-plan',type=Path,help='Explicitly apply a reviewed full-inventory plan atomically; never touches PostgreSQL')
    args=p.parse_args()
    try:
        if args.minimum_rows<1:raise ValueError('--minimum-rows must be positive')
        if args.apply_plan:
            if args.results:p.error('--results and --apply-plan are mutually exclusive')
            apply_plan(args.apply_plan)
        elif args.results:create_plan(args.results,args.inventory,args.sqlite_inventory,args.report_dir,args.minimum_rows)
        else:p.error('Provide --results to prepare a plan, or --apply-plan to publish a reviewed plan')
    except Exception as exc:p.exit(1,'STOPPED: '+str(exc)+'\n')
