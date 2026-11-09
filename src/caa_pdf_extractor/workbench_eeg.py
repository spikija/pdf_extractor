"""Bounded EEG candidate storage and review projection; no clinical database access."""
import copy
import re
from datetime import date
from .workbench_store import WorkbenchStore,read,candidate_entities,file_lock
from .extraction.eeg_schema import GROUPS,defaults,validate_value
from .extraction.eeg_extraction_v1 import clinical_summary


class EEGWorkbenchStore(WorkbenchStore):
    eeg_only=True

    def enabled_patients(self):
        ids=read(self.root/'eeg_validation_cohort.json')['patient_ids']
        if not 1<=len(ids)<=20 or len(set(ids))!=len(ids) or not all(isinstance(p,str) and re.fullmatch(r'[0-9]{1,32}',p) for p in ids):
            raise ValueError('Invalid bounded EEG cohort')
        return set(ids)

    def publish(self,patient,module,version,artifacts,metadata,**kwargs):
        if module!='eeg' or kwargs.get('validated'):raise ValueError('EEG validation publishes candidates only')
        return super().publish(patient,module,version,artifacts,metadata,**kwargs)

    def promote(self,*args,**kwargs):
        raise ValueError('EEG is in validation; promotion is disabled')

    def _commit(self,patient,manifest,action,module,previous,entry):
        metadata=read(entry['metadata_path'])
        manifest['eeg_source_context']=dict(directory=metadata['eeg_source_directory'],input_hashes=metadata['source_input_hashes'])
        super()._commit(patient,manifest,action,module,previous,entry)

    def supersede_qa(self,patient,old_version,new_version,reason):
        with file_lock(self.patient_dir(patient)/'.manifest.lock'):
            manifest=self.manifest(patient);group=manifest['modules']['eeg']
            if old_version==new_version or group['versions'][old_version]['status']!='CANDIDATE':raise ValueError('Only a previous EEG candidate may be superseded')
            group.setdefault('candidate_qa_holds',{})[old_version]=reason
            self._commit(patient,manifest,'QA_SUPERSEDE','eeg',old_version,group['versions'][new_version])


def validate_edits(raw,edits):
    if raw.get('eeg_schema_version')=='1.2':
        from .workbench_eeg_v12 import validate_edits as validate_v12
        return validate_v12(raw,edits)
    if raw.get('eeg_schema_version')=='1.1':
        from .workbench_eeg_v11 import validate_edits as validate_v11
        return validate_v11(raw,edits)
    if not isinstance(edits,dict):raise ValueError('Corrections must be a JSON object')
    if 'finding_id' in raw:
        if set(edits)!={'value'}:raise ValueError('A finding correction requires only value')
        validate_value(raw['field'],edits['value'])
    else:
        for field,value in edits.items():
            if field=='eeg_date':
                if value is not None:
                    if not isinstance(value,str) or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',value):raise ValueError('EEG date requires ISO year, month or day, or null')
                    date.fromisoformat(value+'-01-01' if len(value)==4 else value+'-01' if len(value)==7 else value)
            elif field=='eeg_date_precision':
                if value not in ('DAY','MONTH','YEAR','UNKNOWN'):raise ValueError('Invalid EEG date precision')
            else:validate_value(field,value)
    return edits


def reviewed_exam(exam,findings,records,fingerprint):
    """Apply current explicit actions in timestamp order; rejection means unknown, never absent."""
    if exam.get('eeg_schema_version')=='1.2':
        from .workbench_eeg_v12 import reviewed_exam as review_v12
        return review_v12(exam,findings,records,fingerprint)
    if exam.get('eeg_schema_version')=='1.1':
        from .workbench_eeg_v11 import reviewed_exam as review_v11
        return review_v11(exam,findings,records,fingerprint)
    result=copy.deepcopy(exam);unknown=defaults()
    result.pop('eeg_date_end',None)  # Ignore legacy metadata without rewriting artifacts.
    entities={exam['eeg_exam_id']:exam,**{f['finding_id']:f for f in findings}}
    actions=[r for eid,r in records.items() if eid in entities and r.get('source_artifact_hash')==fingerprint and r['action']!='RESET']
    for record in sorted(actions,key=lambda r:r['updated_at']):
        raw=entities[record['entity_id']];action=record['action']
        if action=='ACCEPT':
            fields=[raw['field']] if 'finding_id' in raw else list(unknown)
            for field in fields:result[field]=raw['value'] if 'finding_id' in raw else raw[field]
        elif action=='EDIT':
            saved=record['physician_value']
            if 'finding_id' not in raw:saved={k:v for k,v in saved.items() if k!='eeg_date_end'}
            edits=validate_edits(raw,saved)
            if 'finding_id' in raw:result[raw['field']]=edits['value']
            else:result.update(edits)
        elif action in ('REJECT','UNCERTAIN'):
            fields=[raw['field']] if 'finding_id' in raw else list(unknown)
            for field in fields:result[field]=unknown[field]
    summary=clinical_summary(result)
    result.update(eeg_primary_finding=summary['eeg_primary_finding'],clinical_phenotype=summary['clinical_phenotype'])
    return result


def groups_for(exam):
    if exam.get('eeg_schema_version')=='1.2':
        from .workbench_eeg_v12 import GROUPS as groups
        return groups
    if exam.get('eeg_schema_version')=='1.1':
        from .workbench_eeg_v11 import GROUPS as groups
        return groups
    return GROUPS
