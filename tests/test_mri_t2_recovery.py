"""Synthetic MRI tests: real archive/identity files and PostgreSQL are never changed."""
import csv
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import nibabel as nib
import numpy as np
import pydicom
import pytest
from pydicom.dataset import FileDataset,FileMetaDataset,Dataset
from pydicom.uid import MRImageStorage,ExplicitVRLittleEndian,generate_uid
from caa_pdf_extractor import mri_t2_recovery as t2
from caa_pdf_extractor import mri_t2_inventory as incremental


@pytest.fixture
def archive(tmp_path):
    source=tmp_path/'dicom';source.mkdir();dbpath=tmp_path/'inventory.sqlite'
    study=generate_uid();series=generate_uid();paths=[]
    for i in range(12):
        meta=FileMetaDataset();meta.TransferSyntaxUID=ExplicitVRLittleEndian;meta.MediaStorageSOPClassUID=MRImageStorage;meta.MediaStorageSOPInstanceUID=generate_uid()
        path=source/f'{i}.dcm';ds=FileDataset(str(path),{},file_meta=meta,preamble=b'\0'*128)
        ds.SOPClassUID=MRImageStorage;ds.SOPInstanceUID=meta.MediaStorageSOPInstanceUID;ds.StudyInstanceUID=study;ds.SeriesInstanceUID=series
        ds.PatientID='SYNTHETIC';ds.PatientName='Synthetic^Only';ds.PatientBirthDate='19600101';ds.StudyDate='20200101'
        ds.ImageType=['ORIGINAL','PRIMARY'];ds.Modality='MR';ds.SeriesDescription='csT2W_TSE tra 4mm';ds.BodyPartExamined='BRAIN';ds.StudyDescription='Brain'
        ds.EchoTime=80;ds.RepetitionTime=3000;ds.ScanningSequence='SE';ds.Manufacturer='Philips'
        ds.Rows=8;ds.Columns=10;ds.PixelSpacing=[2,3];ds.ImageOrientationPatient=[1,0,0,0,1,0];ds.ImagePositionPatient=[0,0,i*5]
        ds.SamplesPerPixel=1;ds.PhotometricInterpretation='MONOCHROME2';ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=0;ds.PixelData=np.ones((8,10),dtype='<u2').tobytes()
        ds.save_as(path,enforce_file_format=True);paths.append(path)
    db=sqlite3.connect(dbpath);db.execute('CREATE TABLE instances(path TEXT PRIMARY KEY,study TEXT,series TEXT,subtype TEXT,echo TEXT,geometry TEXT,modality TEXT,description TEXT,series_number TEXT)')
    for path in paths:db.execute('INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?)',(str(path),study,series,'ORIGINAL_PRIMARY','1','10x8|1,0,0,0,1,0','MR','csT2W_TSE tra 4mm','1'))
    db.commit();db.close()
    with t2.connect_inventory(dbpath) as db:row=t2.groups(db)[0]
    return SimpleNamespace(source=source,db=dbpath,row=row,paths=paths,gid=t2.group_id(row),tmp=tmp_path)


def make_nifti(directory,shape=(10,8,12),bad_affine=False):
    directory.mkdir(parents=True,exist_ok=True)
    affine=np.diag([-3.,-2.,5.,1.]);affine[0,3]=20 if bad_affine else 0
    image=nib.Nifti1Image(np.ones(shape,dtype=np.int16),affine);image.header.set_xyzt_units('mm')
    nib.save(image,str(directory/'image.nii.gz'))
    (directory/'image.json').write_text(json.dumps(dict(Manufacturer='Philips',UsePhilipsFloatNotDisplayScaling=0)))


@pytest.mark.parametrize('description',['T2W_TSE','csT2W_TSE tra 4mm','cs T2W_TSE QS','t2_tse_tra','Brain *dual/tse +','Ax T2 FRFSE','t2_cor_Hippocampus_Blade'])
def test_conventional_brain_variations(archive,description):
    row={**archive.row,'description':description};header=pydicom.dcmread(archive.paths[0],stop_before_pixels=True)
    assert t2.classify(row,header)[0]=='CANDIDATE'


@pytest.mark.parametrize('description',['T2W_FLAIR','t2_tirm_dark-fluid','T2* GRE','SWI','T2 DWI','T2 DTI','Cervical t2tse','T2 localizer','PosDisp t2_tse_tra','T2 screenshot','t2_me2d_tra','t2_fl2d_hemo'])
def test_excluded_sequences(archive,description):
    assert t2.classify({**archive.row,'description':description},pydicom.dcmread(archive.paths[0]))[0]=='EXCLUDED'


@pytest.mark.parametrize('change,reason',[
    ({'description':'T2W_DRIVE'},'SPECIALIZED'),({'subtype':'unknown'},'UNKNOWN'),({'n':4},'SMALL'),({'description':''},'UNLABELED')])
def test_uncertainty_is_explicit(archive,change,reason):
    decision,why=t2.classify({**archive.row,**change},pydicom.dcmread(archive.paths[0]))
    assert decision=='AMBIGUOUS' and reason in why


def test_dual_short_echo_and_nonbrain_header_are_not_t2(archive):
    d=pydicom.dcmread(archive.paths[0]);d.EchoTime=9.5
    assert t2.classify({**archive.row,'description':'Brain dual/tse'},d)==('EXCLUDED','DUAL_ECHO_PD_COMPONENT_NOT_T2')
    d.BodyPartExamined='KNEE'
    assert t2.classify(archive.row,d)[0]=='EXCLUDED'


def test_qc_reads_full_payload_and_checks_geometry_count_and_gaps(archive):
    source=t2.source_qc(archive.paths,archive.row,archive.source);assert source['passed']
    out=archive.tmp/'out';make_nifti(out);assert t2.nifti_qc(out,source)['passed']
    make_nifti(out,bad_affine=True);assert 'PHYSICAL_GEOMETRY_DIFFERS_FROM_DICOM' in t2.nifti_qc(out,source)['issues']
    make_nifti(out,shape=(10,8,11));assert 'NIFTI_FRAME_COUNT_DIFFERS_FROM_SOURCE' in t2.nifti_qc(out,source)['issues']
    (out/'image.nii.gz').write_bytes(b'broken gzip');assert not t2.nifti_qc(out,source)['passed']
    d=pydicom.dcmread(archive.paths[5]);d.ImagePositionPatient=[0,0,26];d.save_as(archive.paths[5],enforce_file_format=True)
    assert 'IRREGULAR_SLICE_SPACING' in t2.source_qc(archive.paths,archive.row,archive.source)['issues']
    d.ImagePositionPatient=[0,0,50];d.save_as(archive.paths[5],enforce_file_format=True)
    assert not t2.source_qc(archive.paths,archive.row,archive.source)['passed']


def test_identical_source_copies_are_not_missing_images_but_conflicts_fail(archive):
    copy=archive.source/'copy.dcm';shutil.copyfile(archive.paths[0],copy)
    row={**archive.row,'n':13};source=t2.source_qc(archive.paths+[copy],row,archive.source)
    assert source['passed'] and source['expected_frames']==12 and source['identical_duplicate_source_copies']==1
    d=pydicom.dcmread(copy);d.PixelData=np.full((8,10),2,dtype='<u2').tobytes();d.save_as(copy,enforce_file_format=True)
    assert 'CONFLICTING_DUPLICATE_SOP_UID' in t2.source_qc(archive.paths+[copy],row,archive.source)['issues']


def args(archive,**changes):
    return SimpleNamespace(**(dict(inventory=archive.db,dicom_root=archive.source,destination=archive.tmp/'T2',existing_t2_root=[],dcm2niix=archive.tmp/'converter.exe',report_dir=None,limit=1,convert=False,reserve_gb=0,timeout=10)|changes))


def test_conversion_is_idempotent_preserves_dicom_and_uses_pilot_flags(archive,monkeypatch):
    settings=args(archive);settings.dcm2niix.write_bytes(b'synthetic converter marker');calls=[]
    def fake(cmd,**kwargs):
        calls.append(cmd);make_nifti(Path(cmd[cmd.index('-o')+1]));return SimpleNamespace(returncode=0,stdout='Converted',stderr='')
    monkeypatch.setattr(t2.subprocess,'run',fake)
    before={p:t2.sha(p) for p in archive.paths};source=t2.source_qc(archive.paths,archive.row,archive.source)
    status,qc=t2.convert(archive.paths,archive.row,source,settings,archive.tmp)
    assert status=='CONVERTED_QC_PASS' and qc['passed']
    assert calls[0][calls[0].index('-p')+1]=='n' and calls[0][calls[0].index('-w')+1]=='0'
    assert t2.convert(archive.paths,archive.row,source,settings,archive.tmp)[0]=='ALREADY_VALID'
    assert len(calls)==1 and all(t2.sha(p)==h for p,h in before.items())


def test_partial_existing_output_is_never_overwritten(archive,monkeypatch):
    settings=args(archive);out=settings.destination/archive.gid;out.mkdir(parents=True);bad=out/'image.nii.gz';bad.write_bytes(b'original partial file')
    monkeypatch.setattr(t2.subprocess,'run',lambda *a,**k:pytest.fail('Must not reconvert existing output'))
    source=t2.source_qc(archive.paths,archive.row,archive.source)
    status,qc=t2.convert(archive.paths,archive.row,source,settings,archive.tmp)
    assert status=='EXISTING_REVIEW' and bad.read_bytes()==b'original partial file'


def test_incomplete_new_conversion_is_retained_for_review_not_published(archive,monkeypatch):
    settings=args(archive);settings.dcm2niix.write_bytes(b'test')
    def fake(cmd,**kw):
        make_nifti(Path(cmd[cmd.index('-o')+1]),shape=(10,8,11));return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(t2.subprocess,'run',fake)
    source=t2.source_qc(archive.paths,archive.row,archive.source)
    status,qc=t2.convert(archive.paths,archive.row,source,settings,archive.tmp)
    assert status=='CONVERSION_QC_REVIEW' and not (settings.destination/archive.gid).exists()
    assert Path(qc['retained_staging_output']).is_dir()


def test_an_additional_t2_root_also_prevents_reconversion(archive,monkeypatch):
    other=archive.tmp/'previous_T2';make_nifti(other/archive.gid);settings=args(archive,existing_t2_root=[other])
    monkeypatch.setattr(t2.subprocess,'run',lambda *a,**k:pytest.fail('Existing alternate output must be reused'))
    source=t2.source_qc(archive.paths,archive.row,archive.source)
    assert t2.convert(archive.paths,archive.row,source,settings,archive.tmp)[0]=='ALREADY_VALID'


def test_mixed_source_anatomy_is_blocked_even_if_first_header_is_brain(archive):
    ds=pydicom.dcmread(archive.paths[-1]);ds.BodyPartExamined='KNEE';ds.save_as(archive.paths[-1],enforce_file_format=True)
    source=t2.source_qc(archive.paths,archive.row,archive.source)
    assert not source['passed'] and 'SOURCE_SEQUENCE_CLASSIFICATION_CONFLICT' in source['issues']


def test_limit_caps_failed_attempts_and_dry_run_does_not_convert(archive,monkeypatch):
    settings=args(archive,report_dir=archive.tmp/'dry');called=[]
    monkeypatch.setattr(t2,'convert',lambda *a:(called.append(a) or ('CONVERSION_FAILED',dict(passed=False,issues=['TEST'],files=[]))))
    before=t2.sha(archive.db);output=t2.run(settings)
    assert not called and not settings.destination.exists() and t2.sha(archive.db)==before
    assert json.loads((output/'summary.json').read_text())['status_counts']=={'MISSING':1}
    # Two additional true source groups ensure a failed first attempt consumes the limit.
    db=sqlite3.connect(archive.db)
    for number in (1,2):
        series=generate_uid()
        for i,oldpath in enumerate(archive.paths):
            path=archive.source/f'new{number}_{i}.dcm';ds=pydicom.dcmread(oldpath);ds.SeriesInstanceUID=series;ds.SOPInstanceUID=generate_uid();ds.file_meta.MediaStorageSOPInstanceUID=ds.SOPInstanceUID;ds.save_as(path,enforce_file_format=True)
            db.execute('INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?)',(str(path),archive.row['study'],series,archive.row['subtype'],archive.row['echo'],archive.row['geometry'],'MR',archive.row['description'],'1'))
    db.commit();db.close()
    settings=args(archive,convert=True,report_dir=archive.tmp/'converted');output=t2.run(settings);assert len(called)==1
    counts=json.loads((output/'summary.json').read_text())
    assert counts['conversion_attempts']==1 and counts['status_counts']=={'CONVERSION_FAILED':1,'MISSING':2}


def identity_csv(archive,path):
    ds=pydicom.dcmread(archive.paths[0],stop_before_pixels=True);row={k:'' for k in incremental.REQUIRED}
    row.update(dicom_patient_id=str(ds.PatientID),patient_name_dicom=str(ds.PatientName),birth_date_yyyymmdd=str(ds.PatientBirthDate),study_date_yyyymmdd=str(ds.StudyDate),study_uid=archive.row['study'],series_uid=archive.row['series'],group_id=archive.gid,
        series_description=archive.row['description'],modality='MR',subtype=archive.row['subtype'],echo=archive.row['echo'],geometry=archive.row['geometry'],dicom_instance_count='12',nifti_category='NOT_CONVERTED',dicom_representative_path=str(archive.paths[0]))
    t2.write_csv(path,[row],sorted(incremental.REQUIRED));return row


def checked_run(archive):
    settings=args(archive,report_dir=archive.tmp/'recovery');make_nifti(settings.destination/archive.gid)
    return t2.run(settings)


def test_incremental_plan_preserves_complete_inventory_and_apply_is_atomic(archive):
    inv=archive.tmp/'identity.csv';old=identity_csv(archive,inv);before=inv.read_bytes();run=checked_run(archive)
    plan=incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,archive.tmp/'plan',minimum=1)
    assert inv.read_bytes()==before
    p=json.loads(plan.read_text());assert p['baseline_rows']==p['proposed_rows']==1 and p['changed_groups']==1
    incremental.apply_plan(plan)
    _,after=incremental.read_csv(inv);assert after[0]['nifti_category']=='T2'
    assert all(after[0][k]==v for k,v in old.items() if k not in ('nifti_category','nifti_paths'))
    assert next(inv.parent.glob('identity.csv.backup_*')).read_bytes()==before
    with pytest.raises(ValueError,match='changed since plan'):incremental.apply_plan(plan)
    newer=incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,archive.tmp/'plan2',minimum=1)
    assert json.loads(newer.read_text())['changed_groups']==0


def test_truncated_inventory_and_tampered_output_cannot_be_published(archive):
    inv=archive.tmp/'identity.csv';identity_csv(archive,inv);run=checked_run(archive)
    with pytest.raises(ValueError,match='below safety minimum'):incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,minimum=1206)
    with t2.connect_inventory(archive.db) as db:source={t2.group_id(r):r for r in t2.groups(db)}
    with pytest.raises(ValueError,match='source groups missing'):incremental.validate_complete([{'group_id':'other'}],source,1)
    plan=incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,archive.tmp/'plan',minimum=1);before=inv.read_bytes()
    next((archive.tmp/'T2'/archive.gid).glob('*.nii.gz')).write_bytes(b'changed')
    with pytest.raises(ValueError,match='NIfTI changed'):incremental.apply_plan(plan)
    assert inv.read_bytes()==before


def test_failed_atomic_replace_keeps_original_and_backup(archive,monkeypatch):
    inv=archive.tmp/'identity.csv';identity_csv(archive,inv);run=checked_run(archive)
    plan=incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,archive.tmp/'plan',minimum=1);before=inv.read_bytes()
    def fail(*a):raise OSError('simulated interrupted replacement')
    monkeypatch.setattr(incremental.os,'replace',fail)
    with pytest.raises(OSError):incremental.apply_plan(plan)
    assert inv.read_bytes()==before and next(inv.parent.glob('identity.csv.backup_*')).read_bytes()==before


def test_incremental_t2_addition_preserves_existing_other_category_row(archive):
    inv=archive.tmp/'identity.csv';old=identity_csv(archive,inv);inv.unlink()
    old.update(nifti_category='T1',nifti_paths=str(archive.tmp/'original_T1.nii.gz'))
    t2.write_csv(inv,[old],sorted(incremental.REQUIRED));run=checked_run(archive)
    plan=incremental.create_plan(run/'t2_recovery_results.csv',inv,archive.db,archive.tmp/'plan',minimum=1)
    incremental.apply_plan(plan);_,rows=incremental.read_csv(inv)
    assert len(rows)==2 and rows[0]==old and rows[1]['nifti_category']=='T2'


def test_historical_two_hundred_row_partial_inventory_is_rejected():
    with pytest.raises(ValueError,match='200 rows below safety minimum 1206'):
        incremental.validate_complete([dict(group_id=str(i)) for i in range(200)],{},1206)
