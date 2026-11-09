"""Conservative source classification, physical-space QC and no-clobber T2 recovery."""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path

import nibabel as nib
import numpy as np
import pydicom

VERSION='1.0'
ROOT=Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY=Path(r'E:\caa_nifti_batch\inventory.sqlite')
DEFAULT_DICOM=Path(r'E:\caa_mri')
DEFAULT_DESTINATION=Path(r'E:\caa_nifti_t2_display\T2')
HARD_EXCLUDE=re.compile(r'flair|dark[ _-]*fluid|tirm|stir|diffusion|\bdwi\b|\bdti\b|\badc\b|trace|swi|swip|haem|hemo|t2[ _-]*\*|t2star|\bgre\b|\bffe\b|fl2d|me2d|localiz|localis|scout|survey|topogram|posdisp|screenshot|secondary|projection|\bmip\b|\bt1w?\b',re.I)
SPINE=re.compile(r'spine|spinal|cervic|lumbar|thorac|\blws\b|\bhws\b|\bbws\b|mobi[ _-]*view',re.I)
OTHER_BODY=re.compile(r'knee|knie|shoulder|schulter|ankle|sprung|abdomen|pelvis|becken|prostat|thorax|carotid|neck|hals|extremit|hand|wrist|ellbogen|elbow|foot|fuss|hip\b|hüft',re.I)
BRAIN=re.compile(r'brain|head|sch[aä]del|schaedel|gehirn|cerebr|hippocamp',re.I)
T2=re.compile(r'(?:^|[^a-z0-9])(?:e?cs[ _-]*)?e?t2(?:w|[ _-]|$)|dual[ /_-]*tse|tse2[/ _-]*sag',re.I)
CONVENTIONAL=re.compile(r't2w?[ _-]*(?:tse|fse|frfse|space|spc)|(?:ax|cor|sag)[ _-]+t2[ _-]+frfse|t2[ _-]+(?:ax|cor|sag|tra)|dual[ /_-]*tse',re.I)
UNCERTAIN=re.compile(r'drive|haste|trufi|spair|spir|dixon|\bfs\b|nerv|fusion|mvxd|mv_xd|warp|tse2/',re.I)
SOURCE_COLUMNS='study series subtype echo geometry'.split()
RESULT_FIELDS='group_id study_uid series_uid series_description subtype echo geometry dicom_instance_count source_unique_image_count nifti_frame_count classification classification_reason recovery_status qc_status qc_issues existing_output_directories nifti_paths nifti_sha256 source_identity_sha256 source_qc_path output_qc_path'.split()


def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def write_csv(path,rows,fields):
    with Path(path).open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


@contextmanager
def lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0)
        if not f.read(1):f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def connect_inventory(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    return db


def group_id(row):return hashlib.sha256('\x1f'.join(str(row[k]) for k in SOURCE_COLUMNS).encode()).hexdigest()[:20]


def groups(db):
    return [dict(r) for r in db.execute('SELECT study,series,subtype,echo,geometry,COUNT(*) AS n,MIN(modality) AS modality,MIN(description) AS description,COUNT(DISTINCT description) AS description_count,COUNT(DISTINCT modality) AS modality_count FROM instances GROUP BY study,series,subtype,echo,geometry ORDER BY study,series,subtype,echo,geometry')]


def paths_for(db,row):
    return [Path(r[0]) for r in db.execute('SELECT path FROM instances WHERE study=? AND series=? AND subtype=? AND echo=? AND geometry=? ORDER BY path',[row[k] for k in SOURCE_COLUMNS])]


def classify(row,header=None):
    desc=row.get('description') or '';subtype=row.get('subtype') or ''
    words=desc.replace('_',' ')
    if row.get('modality')!='MR':return 'EXCLUDED','NON_MR'
    if HARD_EXCLUDE.search(words) or SPINE.search(words) or OTHER_BODY.search(words):return 'EXCLUDED','EXCLUDED_SEQUENCE_OR_ANATOMY'
    if re.search(r'DERIVED|SECONDARY|PROJECTION|LOCALIZER',subtype,re.I):return 'EXCLUDED','DERIVED_SECONDARY_OR_LOCALIZER'
    if not T2.search(desc):
        if not desc.strip():return 'AMBIGUOUS','UNLABELED_SEQUENCE'
        if re.search(r'\btse\b|\bfse\b|spin[ _-]*echo',words,re.I):return 'AMBIGUOUS','UNTYPED_SPIN_ECHO_SEQUENCE'
        return 'EXCLUDED','NO_T2_SEQUENCE_SIGNAL'
    if not header:return 'AMBIGUOUS','DICOM_HEADER_REQUIRED'
    image_type=' '.join(str(v) for v in header.get('ImageType',[]))
    if re.search(r'DERIVED|SECONDARY|PROJECTION|LOCALIZER',image_type,re.I) or str(header.get('SOPClassUID','')).startswith('1.2.840.10008.5.1.4.1.1.7'):return 'EXCLUDED','SECONDARY_SCREENSHOT_OR_DERIVED'
    body=str(header.get('BodyPartExamined',''));study=str(header.get('StudyDescription',''));protocol=str(header.get('ProtocolName',''))
    if SPINE.search(body) or OTHER_BODY.search(body):return 'EXCLUDED','DICOM_NON_BRAIN_ANATOMY'
    if not BRAIN.search(body) and (SPINE.search(study) or OTHER_BODY.search(study)):return 'AMBIGUOUS','MIXED_OR_NON_BRAIN_STUDY_CONTEXT'
    if not BRAIN.search(' '.join((body,study,desc,protocol))):return 'AMBIGUOUS','BRAIN_ANATOMY_NOT_ESTABLISHED'
    if HARD_EXCLUDE.search(protocol.replace('_',' ')):return 'EXCLUDED','DICOM_EXCLUDED_PROTOCOL'
    if subtype.lower()=='unknown' or row.get('description_count',1)>1 or row.get('modality_count',1)>1:return 'AMBIGUOUS','UNKNOWN_OR_MIXED_INVENTORY_METADATA'
    if int(header.get('NumberOfFrames',1))>1:return 'AMBIGUOUS','ENHANCED_MULTIFRAME_REQUIRES_REVIEW'
    try:te=float(header.get('EchoTime',0));tr=float(header.get('RepetitionTime',0));ti=float(header.get('InversionTime',0))
    except (TypeError,ValueError):return 'AMBIGUOUS','UNREADABLE_SEQUENCE_TIMING'
    if 'dual' in desc.lower() and te and te<50:return 'EXCLUDED','DUAL_ECHO_PD_COMPONENT_NOT_T2'
    if UNCERTAIN.search(words) or not CONVENTIONAL.search(desc):return 'AMBIGUOUS','SPECIALIZED_OR_UNRECOGNIZED_T2_VARIANT'
    if not (te>=50 and tr>=1000) or ti>0 or re.search(r'\b(?:IR|GR)\b',str(header.get('ScanningSequence',''))):return 'AMBIGUOUS','TIMING_OR_ACQUISITION_NOT_CONVENTIONAL_T2'
    if row['n']<12:return 'AMBIGUOUS','SMALL_OR_POSSIBLY_INCOMPLETE_SERIES'
    return 'CANDIDATE','CONVENTIONAL_BRAIN_T2_SOURCE_CONFIRMED'


def identity(ds):return {k:str(ds.get(k,'')) for k in ('PatientID','PatientName','PatientBirthDate','StudyDate','StudyInstanceUID','SeriesInstanceUID')}


def source_qc(paths,row,dicom_root):
    problems=[];positions=[];orientations=[];spacings=[];dims=[];sops={};identities=set();declared=set();signatures={};first_identity=None;unique_paths=[];copies=0
    for path in paths:
        if not path.resolve().is_relative_to(Path(dicom_root).resolve()):problems.append('SOURCE_OUTSIDE_DICOM_ROOT');continue
        try:
            stat=path.stat();signatures[str(path)]=[stat.st_size,stat.st_mtime_ns]
            ds=pydicom.dcmread(path,stop_before_pixels=True)
            if classify(row,ds)[0]!='CANDIDATE':problems.append('SOURCE_SEQUENCE_CLASSIFICATION_CONFLICT')
            current=identity(ds);first_identity=first_identity or current;identities.add(json.dumps(current,sort_keys=True))
            if current['StudyInstanceUID']!=row['study'] or current['SeriesInstanceUID']!=row['series']:problems.append('SOURCE_UID_MISMATCH')
            sop=str(ds.get('SOPInstanceUID',''))
            if not sop:problems.append('MISSING_SOP_UID')
            if sop in sops:
                if sha(path)!=sha(sops[sop]):problems.append('CONFLICTING_DUPLICATE_SOP_UID')
                else:copies+=1
                continue
            sops[sop]=path;unique_paths.append(str(path))
            if int(ds.get('NumberOfFrames',1))!=1:problems.append('MULTIFRAME_GEOMETRY_NOT_VALIDATED');continue
            positions.append([float(v) for v in ds.ImagePositionPatient]);orientations.append([float(v) for v in ds.ImageOrientationPatient]);spacings.append([float(v) for v in ds.PixelSpacing]);dims.append([int(ds.Rows),int(ds.Columns)])
            if ds.get('ImagesInAcquisition'):declared.add(int(ds.ImagesInAcquisition))
        except (OSError,ValueError,AttributeError,TypeError,OverflowError):problems.append('MISSING_OR_UNREADABLE_DICOM_GEOMETRY')
    if len(paths)!=row['n'] or len(positions)!=len(unique_paths):problems.append('INCOMPLETE_SOURCE_FILES_OR_FRAMES')
    if len(identities)>1:problems.append('SOURCE_IDENTITY_OR_DATE_CONFLICT')
    geometry={}
    if positions:
        try:
            orient=np.asarray(orientations);space=np.asarray(spacings);pos=np.asarray(positions);size=np.asarray(dims)
            if orient.shape[1]!=6 or pos.shape[1]!=3 or space.shape[1]!=2 or not all(np.isfinite(v).all() for v in (orient,space,pos)):raise ValueError()
            if not np.all(space>0) or not np.all(size>1):problems.append('INVALID_PIXEL_GEOMETRY')
            if not np.allclose(orient,orient[0],atol=1e-3) or not np.allclose(space,space[0],rtol=1e-4) or not np.all(size==size[0]):problems.append('INCONSISTENT_SOURCE_GEOMETRY')
            x,y=orient[0,:3],orient[0,3:];normal=np.cross(x,y)
            if abs(np.linalg.norm(x)-1)>1e-3 or abs(np.linalg.norm(y)-1)>1e-3 or abs(np.dot(x,y))>1e-3:problems.append('INVALID_DICOM_ORIENTATION')
            projected=pos@normal;order=np.argsort(projected);steps=np.diff(projected[order]);step=float(np.median(steps)) if len(steps) else 0
            if len(steps)==0 or np.any(steps<0.01):problems.append('DUPLICATE_POSITIONS_OR_TOO_FEW_SLICES')
            elif np.max(steps)>step*1.5:problems.append('INCOMPLETE_SERIES_SLICE_GAP')
            elif not np.allclose(steps,step,rtol=.05,atol=.05):problems.append('IRREGULAR_SLICE_SPACING')
            if declared and declared!={len(unique_paths)}:problems.append('DECLARED_IMAGE_COUNT_DIFFERS_REVIEW_COMPLETENESS')
            # Expected physical voxel-center corners, DICOM LPS -> NIfTI RAS.
            corners=[]
            for origin in (pos[order[0]],pos[order[-1]]):
                for i in (0,int(size[0,1])-1):
                    for j in (0,int(size[0,0])-1):corners.append(((origin+x*i*space[0,1]+y*j*space[0,0])*[-1,-1,1]).tolist())
            geometry=dict(shape=[int(size[0,1]),int(size[0,0]),len(positions)],zooms=[float(space[0,1]),float(space[0,0]),step],corners_ras=corners)
        except (ValueError,IndexError,TypeError):problems.append('INVALID_SOURCE_GEOMETRY')
    return dict(passed=not problems,issues=sorted(set(problems)),source_files=len(paths),expected_frames=len(unique_paths),unique_sop_instances=len(sops),identical_duplicate_source_copies=copies,conversion_paths=unique_paths,geometry=geometry,
        identity=first_identity or {},identity_sha256=hashlib.sha256(json.dumps(first_identity,sort_keys=True).encode()).hexdigest(),source_file_signatures=signatures,
        completeness_limit='Detects absent files, duplicate instances/positions, internal gaps and declared-count mismatch. An entirely absent terminal slice without a declared count cannot be proven from this inventory.')


def nifti_qc(directory,source):
    paths=sorted(list(Path(directory).glob('*.nii'))+list(Path(directory).glob('*.nii.gz')));issues=[];details=[];frames=0
    if not paths:issues.append('NO_NIFTI')
    for path in paths:
        try:
            image=nib.load(str(path));shape=image.shape;affine=image.affine
            if len(shape)<3 or len(shape)>4 or any(d<2 for d in shape[:3]) or any(d<1 for d in shape):raise ValueError('INVALID_DIMENSIONS')
            if np.prod(shape,dtype=np.int64)*image.get_data_dtype().itemsize>2*1024**3:raise ValueError('LARGE_VOLUME_REQUIRES_REVIEW')
            data=np.asanyarray(image.dataobj)
            if not np.isfinite(data).all():issues.append('NONFINITE_VOXELS')
            if not np.any(data):issues.append('EMPTY_ZERO_VOLUME')
            zooms=np.linalg.norm(affine[:3,:3],axis=0)
            if not np.isfinite(affine).all() or np.any(zooms<=0) or abs(np.linalg.det(affine[:3,:3]))<1e-6:issues.append('INVALID_AFFINE')
            if not int(image.header['sform_code']) and not int(image.header['qform_code']):issues.append('MISSING_SPATIAL_TRANSFORM')
            if image.header.get_xyzt_units()[0]!='mm':issues.append('SPATIAL_UNITS_NOT_MM')
            q,qcode=image.get_qform(coded=True);s,scode=image.get_sform(coded=True)
            if qcode and scode and not np.allclose(q,s,atol=.05):issues.append('CONFLICTING_QFORM_SFORM')
            geometry=source.get('geometry',{})
            count=int(np.prod(shape[2:]));frames+=count
            if geometry:
                expected=sorted(zip(geometry['shape'],geometry['zooms']));actual=sorted(zip(shape[:3],zooms.tolist()))
                if any(a[0]!=b[0] or not np.isclose(a[1],b[1],rtol=.02,atol=.05) for a,b in zip(actual,expected)):issues.append('DIMENSIONS_OR_SPACING_DIFFER_FROM_DICOM')
                corners=np.array([nib.affines.apply_affine(affine,[i,j,k]) for i in (0,shape[0]-1) for j in (0,shape[1]-1) for k in (0,shape[2]-1)])
                reference=np.asarray(geometry['corners_ras'])
                if max(min(np.linalg.norm(point-other) for other in reference) for point in corners)>.5:issues.append('PHYSICAL_GEOMETRY_DIFFERS_FROM_DICOM')
            sidecar=path.with_name(path.name.removesuffix('.nii.gz').removesuffix('.nii')+'.json')
            metadata=json.loads(sidecar.read_text('utf-8-sig')) if sidecar.exists() else {}
            if not metadata:issues.append('MISSING_CONVERSION_SIDECAR')
            if metadata.get('UsePhilipsFloatNotDisplayScaling') not in (None,0,False):issues.append('PHILIPS_DISPLAY_SCALING_NOT_USED')
            if str(metadata.get('Manufacturer','')).lower().startswith('philips') and 'UsePhilipsFloatNotDisplayScaling' not in metadata:issues.append('PHILIPS_SCALING_NOT_DOCUMENTED')
            details.append(dict(path=str(path.resolve()),shape=list(shape),zooms=zooms.tolist(),frames=count,sha256=sha(path)))
        except Exception as exc:issues.append('UNREADABLE_NIFTI:'+type(exc).__name__)
    if frames!=source['expected_frames']:issues.append('NIFTI_FRAME_COUNT_DIFFERS_FROM_SOURCE')
    log=Path(directory)/'conversion.log'
    if log.exists() and re.search(r'error:|missing images|interslice distance varies|unable to rotate|missing slice|slice timing.*inconsistent',log.read_text('utf-8',errors='replace'),re.I):issues.append('DCM2NIIX_GEOMETRY_OR_COMPLETENESS_WARNING')
    return dict(passed=not issues and source['passed'],issues=sorted(set(issues+([] if source['passed'] else ['SOURCE_QC_FAILED']))),nifti_count=len(paths),frames=frames,files=details)


def convert(paths,row,source,args,run_directory):
    gid=group_id(row);target=args.destination/gid
    with lock(args.destination.parent/'_t2_recovery_locks'/(gid+'.lock')):
        existing=[root/gid for root in [args.destination]+getattr(args,'existing_t2_root',[]) if (root/gid).exists()]
        if existing:
            checks=[nifti_qc(p,source) for p in existing]
            qc=dict(passed=all(q['passed'] for q in checks),issues=sorted({v for q in checks for v in q['issues']}),files=[f for q in checks for f in q['files']])
            return ('ALREADY_VALID' if qc['passed'] else 'EXISTING_REVIEW'),qc
        if not source['passed']:return 'SOURCE_REVIEW',dict(passed=False,issues=source['issues'],files=[])
        paths=[Path(p) for p in source['conversion_paths']]
        if not args.dcm2niix.is_file():return 'CONVERSION_FAILED',dict(passed=False,issues=['MISSING_DCM2NIIX'],files=[])
        scratch=args.destination.parent/'_staging_t2_archive';scratch.mkdir(parents=True,exist_ok=True)
        required=sum(p.stat().st_size for p in paths)*3+int(args.reserve_gb*1024**3)
        if shutil.disk_usage(scratch).free<required:return 'CONVERSION_FAILED',dict(passed=False,issues=['INSUFFICIENT_FREE_SPACE'],files=[])
        job=scratch/(gid+'_'+uuid.uuid4().hex);inputs=job/'dicom';output=job/'output';inputs.mkdir(parents=True);output.mkdir()
        try:
            for i,path in enumerate(paths):
                stat=path.stat()
                if [stat.st_size,stat.st_mtime_ns]!=source['source_file_signatures'][str(path)]:raise ValueError('SOURCE_CHANGED_SINCE_QC')
                shutil.copyfile(path,inputs/f'I{i:06}.dcm')
            cmd=[str(args.dcm2niix),'-d','0','-z','y','-b','y','-ba','y','-p','n','-m','n','-w','0','-f','image_%s_%e','-o',str(output),str(inputs)]
            proc=subprocess.run(cmd,capture_output=True,text=True,errors='replace',timeout=args.timeout,
                **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            (output/'conversion.log').write_text((proc.stdout or '')+'\n'+(proc.stderr or ''),encoding='utf-8')
            qc=nifti_qc(output,source);qc['command']=cmd;qc['returncode']=proc.returncode
            if proc.returncode:qc['passed']=False;qc['issues'].append('NONZERO_CONVERTER_EXIT')
            if not qc['passed']:
                qc['retained_staging_output']=str(output);return 'CONVERSION_QC_REVIEW',qc
            args.destination.mkdir(parents=True,exist_ok=True)
            os.rename(output,target) # Windows no-clobber directory rename; never os.replace.
            for f in qc['files']:f['path']=str(target/Path(f['path']).name)
            return 'CONVERTED_QC_PASS',qc
        except Exception as exc:
            return 'CONVERSION_FAILED',dict(passed=False,issues=[type(exc).__name__+':'+str(exc)],files=[],retained_staging_output=str(output))
        finally:
            # Only disposable copies in this exact, fresh, owned staging directory.
            resolved=inputs.resolve();boundary=scratch.resolve()
            if resolved.is_relative_to(boundary) and resolved.parent==job.resolve() and inputs.is_dir() and not inputs.is_symlink():shutil.rmtree(resolved)


def run(args):
    args.inventory=args.inventory.resolve();args.dicom_root=args.dicom_root.resolve();args.destination=args.destination.resolve()
    if not args.inventory.is_file() or not args.dicom_root.is_dir():raise ValueError('Inventory or original DICOM root missing')
    if args.limit<1 or args.reserve_gb<0 or args.timeout<=0:raise ValueError('--limit and --timeout must be positive and --reserve-gb >=0')
    if args.destination.is_relative_to(args.dicom_root) or args.dicom_root.is_relative_to(args.destination):raise ValueError('DICOM and output trees must be separate')
    output=(args.report_dir or ROOT/'data/debug'/('t2_archive_recovery_'+stamp())).resolve()
    if output.is_relative_to(args.dicom_root):raise ValueError('Reports must not be written inside original DICOM archive')
    output.mkdir(parents=True,exist_ok=False)
    (output/'source_qc').mkdir();(output/'output_qc').mkdir()
    inventory_sha=sha(args.inventory);results=[];attempts=0;source_count=0
    roots=list(dict.fromkeys([args.destination]+[p.resolve() for p in args.existing_t2_root]))
    with connect_inventory(args.inventory) as db:
        all_groups=groups(db);source_count=db.execute('SELECT COUNT(DISTINCT study) FROM instances').fetchone()[0]
        for i,row in enumerate(all_groups,1):
            gid=group_id(row);decision,reason=classify(row);source=None;qc={};existing=[r/gid for r in roots if (r/gid).exists()]
            paths=[]
            if decision!='EXCLUDED':
                paths=paths_for(db,row)
                try:
                    if not paths or not paths[0].resolve().is_relative_to(args.dicom_root):raise ValueError('Unsafe or absent source')
                    header=pydicom.dcmread(paths[0],stop_before_pixels=True);decision,reason=classify(row,header)
                except Exception:decision,reason='AMBIGUOUS','MISSING_OR_UNREADABLE_SOURCE_HEADER'
            status=decision
            if decision=='CANDIDATE':
                source=source_qc(paths,row,args.dicom_root);write_json(output/'source_qc'/(gid+'.json'),source)
                if existing:
                    checks=[nifti_qc(p,source) for p in existing]
                    qc=dict(passed=all(q['passed'] for q in checks),issues=sorted({v for q in checks for v in q['issues']}),files=[f for q in checks for f in q['files']],directories=[str(p) for p in existing])
                    status='ALREADY_VALID' if qc['passed'] else 'EXISTING_REVIEW'
                elif not source['passed']:status='SOURCE_REVIEW';qc=dict(passed=False,issues=source['issues'],files=[])
                else:
                    status='MISSING';qc=dict(passed=False,issues=['NOT_CONVERTED'],files=[])
                    if args.convert and attempts<args.limit:
                        attempts+=1;status,qc=convert(paths,row,source,args,output)
                write_json(output/'output_qc'/(gid+'.json'),qc)
            elif existing:
                qc=dict(passed=False,issues=['EXISTING_OUTPUT_SEQUENCE_NOT_CONFIRMED_CONVENTIONAL_BRAIN_T2'],files=[dict(path=str(p)) for d in existing for p in list(d.glob('*.nii'))+list(d.glob('*.nii.gz'))])
                status='EXISTING_REVIEW'
            rec=dict(group_id=gid,study_uid=row['study'],series_uid=row['series'],series_description=row['description'] or '',subtype=row['subtype'],echo=row['echo'],geometry=row['geometry'],dicom_instance_count=row['n'],source_unique_image_count=source['expected_frames'] if source else '',nifti_frame_count=sum(f.get('frames',0) for f in qc.get('files',[])) if source else '',classification=decision,classification_reason=reason,recovery_status=status,
                qc_status='QC_PASS_NOT_CLINICALLY_VALIDATED' if qc.get('passed') else 'REVIEW_REQUIRED' if decision!='EXCLUDED' or existing else 'NOT_APPLICABLE',qc_issues=';'.join(qc.get('issues',[])),existing_output_directories=' | '.join(str(p) for p in existing),
                nifti_paths=' | '.join(f['path'] for f in qc.get('files',[])),nifti_sha256=json.dumps({f['path']:f['sha256'] for f in qc.get('files',[]) if f.get('sha256')}),
                source_identity_sha256=source['identity_sha256'] if source else '',source_qc_path=str(output/'source_qc'/(gid+'.json')) if source else '',output_qc_path=str(output/'output_qc'/(gid+'.json')) if source else '')
            results.append(rec)
            if i%500==0:print(f'Scanned {i}/{len(all_groups)} archive groups',flush=True)
    fields=RESULT_FIELDS
    write_csv(output/'t2_recovery_results.csv',results,fields)
    write_csv(output/'t2_ambiguous_review.csv',[r for r in results if r['classification']=='AMBIGUOUS' or r['recovery_status'] in ('SOURCE_REVIEW','EXISTING_REVIEW','CONVERSION_QC_REVIEW','CONVERSION_FAILED')],fields)
    known={r['group_id'] for r in results};orphans=[str(p) for root in roots if root.exists() for p in root.iterdir() if p.is_dir() and re.fullmatch('[a-f0-9]{20}',p.name) and p.name not in known]
    summary=dict(version=VERSION,mode='CONVERT' if args.convert else 'DRY_RUN',study_count=source_count,acquisition_groups=len(results),classification_counts=dict(Counter(r['classification'] for r in results)),status_counts=dict(Counter(r['recovery_status'] for r in results)),
        already_converted_valid_groups=sum(r['recovery_status']=='ALREADY_VALID' for r in results),missing_eligible_groups=sum(r['recovery_status']=='MISSING' for r in results),
        excluded_groups=sum(r['classification']=='EXCLUDED' for r in results),ambiguous_groups=sum(r['classification']=='AMBIGUOUS' for r in results),
        existing_output_groups=sum(bool(r['existing_output_directories']) for r in results),existing_output_review_groups=sum(r['recovery_status']=='EXISTING_REVIEW' for r in results),
        conversion_attempts=attempts,limit=args.limit,source_inventory=str(args.inventory),source_inventory_sha256=inventory_sha,source_inventory_unchanged=sha(args.inventory)==inventory_sha,
        destination=str(args.destination),existing_t2_roots=[str(p) for p in roots],orphan_existing_group_directories=orphans,postgresql_calls=0,clinical_validation=False,results_csv=str(output/'t2_recovery_results.csv'),
        pilot=next((r for r in results if r['group_id']=='d9ccd6bbc6ed10d1db03'),None),qc_geometry_tolerance_mm=.5,code_sha256=sha(__file__),dcm2niix_sha256=sha(args.dcm2niix) if args.dcm2niix.is_file() else None)
    write_json(output/'summary.json',summary)
    lines=['# Archive-wide T2 recovery','',f"Mode: {summary['mode']}. Studies: {source_count}. Acquisition groups: {len(results)}.",'','| Recovery status | Groups |','| --- | ---: |']
    lines += [f'| {k} | {v} |' for k,v in sorted(summary['status_counts'].items())]
    lines += ['','[All acquisition groups and QC](t2_recovery_results.csv) · [Uncertain sequences / QC review](t2_ambiguous_review.csv)','',
        'Only source-confirmed conventional brain T2 is eligible. Existing output directories are never overwritten or reconverted, including invalid/partial output; these require separate review. QC_PASS is technical QC, not clinical validation.',
        'DICOM completeness checks detect internal gaps, missing files, duplicate positions/instances and declared-count mismatches. Without an independent acquisition count, absent terminal slices cannot be ruled out.',
        'Dry-run creates reports only. No PostgreSQL connection or identity-inventory write occurs. Conversion preserves original DICOM. Incremental identity inventory publication and PostgreSQL refresh are separate explicit steps.','']
    (output/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    if not summary['source_inventory_unchanged']:raise ValueError('Source inventory changed during scan; report is not a stable snapshot')
    print(json.dumps({k:v for k,v in summary.items() if k!='pilot'},indent=2));print('REPORT:',output/'REPORT.md',flush=True)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',type=Path,default=DEFAULT_INVENTORY);p.add_argument('--dicom-root',type=Path,default=DEFAULT_DICOM)
    p.add_argument('--destination',type=Path,default=DEFAULT_DESTINATION,help='Actual T2 group directory, including the T2 suffix')
    p.add_argument('--existing-t2-root',type=Path,action='append',default=[],help='Additional T2 group directory to check before converting')
    p.add_argument('--dcm2niix',type=Path,default=Path(r'C:\app\dcm2niix\dcm2niix.exe'))
    p.add_argument('--convert',action='store_true');p.add_argument('--limit',type=int,default=1,help='Maximum conversion attempts, including failures; default 1')
    p.add_argument('--reserve-gb',type=float,default=10);p.add_argument('--timeout',type=int,default=300);p.add_argument('--report-dir',type=Path)
    args=p.parse_args()
    try:run(args)
    except Exception as exc:p.exit(1,'STOPPED: '+str(exc)+'\n')
