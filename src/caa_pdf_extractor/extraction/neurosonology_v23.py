"""Conservative vessel-scoped annotations of frozen ultrasound reports."""
import re

VESSELS={'ICA':r'(?:A\.?\s*carotis\s*interna|\bACI\b|\bICA\b)',
 'CCA':r'(?:carotis\s*communis|\bACC\b|\bCCA\b)','ECA':r'(?:carotis\s*externa|\bACE\b|\bECA\b)',
 'VERTEBRAL':r'(?:vertebralis|\bVA\b|\bV[124]\b)','MCA':r'(?:cerebri\s*media|\bACM\b|\bMCA\b)',
 'ACA':r'(?:cerebri\s*anterior|\bACA\b)','PCA':r'(?:cerebri\s*posterior|\bPCA\b)',
 'BASILAR':r'(?:basilaris|\bBA\b)'}

def categorize_stenosis(value):
    return 'UNKNOWN' if value is None else 'OCCLUSION' if value==100 else 'PCT_70_99' if value>=70 else 'PCT_50_69' if value>=50 else 'LT_50'

def parse_exam(exam):
    text=exam.get('report') or '';rows=[]
    extra=bool(re.search(r'extrakrani|extracrani|carotis|vertebralis',text,re.I))
    intra=bool(re.search(r'intrakrani|intracrani|transkrani|transcrani|cerebri|basilaris',text,re.I))
    typ='COMBINED_NEUROSONOLOGY' if extra and intra else 'EXTRACRANIAL_DUPLEX' if extra else 'TRANSCRANIAL_COLOR_CODED_SONOGRAPHY' if intra and re.search(r'farb|color',text,re.I) else 'TRANSCRANIAL_DOPPLER' if intra else 'UNKNOWN'
    quality='LIMITED' if re.search(r'eingeschr[äa]nkt|kein.*schallfenster|nicht.*beurteil|limited',text,re.I) else 'ADEQUATE' if re.search(r'gut\w*\s+(?:beurteil|schallfenster)|adequate',text,re.I) else 'UNKNOWN'
    for n,line in enumerate(text.splitlines()):
        hits=[v for v,rx in VESSELS.items() if re.search(rx,line,re.I)]
        if not hits:continue
        # Multiple vessels in one sentence cannot share an unscoped percentage.
        ambiguous=len(hits)>1
        for v in hits:
            sides=['LEFT','RIGHT'] if re.search(r'\bbds\b|beidseit|bilateral',line,re.I) else ['LEFT'] if re.search(r'links|sinistra|\bli\b|\bleft\b',line,re.I) else ['RIGHT'] if re.search(r'rechts|dextra|\bre\b|\bright\b',line,re.I) else ['UNKNOWN']
            nums=re.findall(r'(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*%',line)
            percent=int(nums[0][0]) if len(nums)==1 and not nums[0][1] and not ambiguous and 'NASCET' in line.upper() else None
            if percent is not None and percent>100:percent=None
            occlusion=True if re.search(r'(?:verschluss|okklusion|occlusion)',line,re.I) and not re.search(r'kein|ohne|no ',line,re.I) else None
            if occlusion:percent=100
            plaque=None if re.search(r'ohne\s+relevante\s+plaque',line,re.I) else False if re.search(r'kein\w*\s+plaque|ohne\s+plaque',line,re.I) else True if re.search(r'plaque',line,re.I) else None
            def velocity(name):
                m=re.search(name+r'\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(cm/s|m/s)',line,re.I)
                return (float(m[1].replace(',','.'))*(100 if m[2].lower()=='m/s' else 1)) if m and not ambiguous else None
            for side in sides:
                rows.append(dict(patient_id=exam['patient_id'],exam_id=exam['id'],exam_date=exam.get('date'),exam_type=typ,report_quality=quality,
                    source_document_id=exam['source_document_id'],page=exam.get('page'),section_id=exam.get('section'),
                    evidence_id=f"{exam['id']}:line{n}",vessel=v,side=side,plaque=plaque,nascet_percent=percent,
                    stenosis_category=categorize_stenosis(percent),occlusion=occlusion,psv_cm_s=velocity('PSV'),edv_cm_s=velocity('EDV'),
                    vertebral_flow='ANTEGRADE' if v=='VERTEBRAL' and re.search(r'orthograd|antegrad',line,re.I) else 'RETROGRADE' if v=='VERTEBRAL' and re.search(r'retrograd',line,re.I) else 'UNKNOWN',
                    intracranial_stenosis=True if v in ('MCA','ACA','PCA','BASILAR') and re.search(r'stenos',line,re.I) and not re.search(r'kein|ohne',line,re.I) else None,
                    evidence_quote=line[:160],review_required=ambiguous or side=='UNKNOWN' or quality!='ADEQUATE',rule_version='NEUROSONOLOGY_V2.3'))
    if not rows:
        rows=[dict(patient_id=exam['patient_id'],exam_id=exam['id'],exam_date=exam.get('date'),exam_type=typ,report_quality=quality,
            source_document_id=exam['source_document_id'],page=exam.get('page'),section_id=exam.get('section'),evidence_id=exam['id'],
            vessel='UNKNOWN',side='UNKNOWN',plaque=None,nascet_percent=None,stenosis_category='UNKNOWN',review_required=True,rule_version='NEUROSONOLOGY_V2.3')]
    for row in rows:
        row['technical_quality']=quality if quality!='UNKNOWN' else 'NOT_ASSESSABLE'
        row['quality_basis']='EXPLICIT' if quality!='UNKNOWN' else 'NOT_REPORTED'
        row['plaque_present']='YES' if row.get('plaque') is True else 'NO' if row.get('plaque') is False else 'UNKNOWN'
        row['plaque_burden']='UNKNOWN';row['plaque_morphology']='UNKNOWN'
        row['ICA_CCA_PSV_ratio']=None
        row['stenosis_percent']=row.get('nascet_percent')
        row['classification_basis']='SOURCE_REPORTED_NASCET' if row.get('nascet_percent') is not None and row.get('nascet_percent')!=100 else 'SOURCE_REPORTED_OCCLUSION' if row.get('occlusion') else 'UNKNOWN'
        row['flow_direction']=row.get('vertebral_flow','UNKNOWN');row['dominance']='UNKNOWN';row['hypoplasia']='UNKNOWN'
        row['vertebral_stenosis']='OCCLUDED' if row.get('vessel')=='VERTEBRAL' and row.get('occlusion') else 'UNKNOWN'
        row['intracranial_stenosis_status']='CONFIRMED' if row.get('intracranial_stenosis') else 'UNKNOWN'
        row['flow_abnormality']='UNKNOWN'
        quote=row.get('evidence_quote','')
        row['atherosclerosis']=True if re.search(r'atheroskler|atheroscler',quote,re.I) and not re.search(r'kein|ohne|no ',quote,re.I) else True if row.get('plaque') is True else None
        if row.get('plaque') is False:row['plaque_burden']='NONE'
        elif row.get('plaque') is True:
            row['plaque_burden']='MILD' if re.search(r'leicht|gering',quote,re.I) else 'MODERATE' if re.search(r'mittelgrad|m[äa]ßig',quote,re.I) else 'SEVERE' if re.search(r'ausgepr[äa]gt|hochgrad',quote,re.I) else 'UNKNOWN'
            row['plaque_morphology']='ULCERATED' if re.search(r'ulzer|ulcer',quote,re.I) else 'CALCIFIED' if re.search(r'verkalk|kalzifiz|calcif',quote,re.I) else 'ECHOLUCENT' if re.search(r'echoarm|echolucent',quote,re.I) else 'ECHOGENIC' if re.search(r'echoreich|echogen',quote,re.I) else 'MIXED' if re.search(r'gemischt|mixed',quote,re.I) else 'UNKNOWN'
        if row.get('vessel')=='BASILAR':row['side']='MIDLINE'
        ratio=re.search(r'ICA\s*[/:-]\s*CCA\s*(?:PSV\s*)?(?:ratio|Quotient)?\s*[:=]\s*(\d+(?:[.,]\d+)?)',quote,re.I)
        if ratio:row['ICA_CCA_PSV_ratio']=float(ratio[1].replace(',','.'))
        if row.get('vessel')=='VERTEBRAL':
            if re.search(r'bidirektion|bidirection',quote,re.I):row['flow_direction']='BIDIRECTIONAL'
            if re.search(r'hypoplas',quote,re.I):row['hypoplasia']='NO' if re.search(r'kein|ohne',quote,re.I) else 'YES'
            if re.search(r'dominan',quote,re.I):row['dominance']=row['side'] if row['side'] in ('LEFT','RIGHT') else 'UNKNOWN'
            if re.search(r'stenos',quote,re.I):row['vertebral_stenosis']='NONE' if re.search(r'kein|ohne',quote,re.I) else 'PRESENT'
        if row.get('intracranial_stenosis'):
            if re.search(r'Verdacht|\bV\.?a\.?|suspect',quote,re.I):row['intracranial_stenosis_status']='SUSPECTED'
    return rows

def summarize(rows):
    def known_any(key):
        vals=[r.get(key) for r in rows if r.get(key) is not None]
        # A negative in one vessel cannot exclude disease in unexamined vessels.
        return True if any(vals) else None
    return dict(patient_id=rows[0]['patient_id'],exam_ids=sorted({r['exam_id'] for r in rows}),any_carotid_plaque=known_any('plaque'),
        any_ica_stenosis_ge50=True if any(r.get('vessel')=='ICA' and r.get('nascet_percent') is not None and r['nascet_percent']>=50 for r in rows) else None,
        any_ica_occlusion=True if any(r.get('vessel')=='ICA' and r.get('occlusion') is True for r in rows) else None,
        any_carotid_atherosclerosis=True if any(r.get('vessel') in ('CCA','ICA','ECA') and r.get('atherosclerosis') is True for r in rows) else None,
        any_ica_stenosis_ge70=True if any(r.get('vessel')=='ICA' and r.get('nascet_percent') is not None and r['nascet_percent']>=70 for r in rows) else None,
        vertebral_abnormality=True if any(r.get('vessel')=='VERTEBRAL' and (r.get('occlusion') or r.get('vertebral_flow')=='RETROGRADE') for r in rows) else None,
        intracranial_occlusion=True if any(r.get('vessel') in ('MCA','ACA','PCA','BASILAR') and r.get('occlusion') for r in rows) else None,
        intracranial_stenosis=known_any('intracranial_stenosis'),review_required=any(r.get('review_required') for r in rows))
