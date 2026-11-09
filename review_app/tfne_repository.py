"""Isolated, artifact-hash-scoped TFNE physician overlays; raw candidates immutable."""
import copy
import json
import uuid
from datetime import datetime,timezone
from pathlib import Path

from review_app.config import STABLE_ROOT,REVIEW_ROOT
from review_app.artifact_repository import read_json,sha
from caa_pdf_extractor.workbench_store import atomic,file_lock
from caa_pdf_extractor.tfne_pilot import ARTIFACT
from caa_pdf_extractor.tfne_schema import defaults,validate
from caa_pdf_extractor.tfne_screen import derive


ARTIFACT_VERSIONS={'0.1':ARTIFACT,'0.2':'tfne_neurological_deficit_v0_2.json'}


def available_versions(patient,root=STABLE_ROOT):
    if not str(patient).isdigit():raise ValueError('Numeric TFNE patient ID required')
    return [v for v,name in ARTIFACT_VERSIONS.items() if (Path(root)/str(patient)/name).exists()]


def load_artifact(patient,root=STABLE_ROOT,version='0.1'):
    if not str(patient).isdigit():raise ValueError('Numeric TFNE patient ID required')
    if version not in ARTIFACT_VERSIONS:raise ValueError('Unsupported TFNE artifact version')
    path=Path(root)/str(patient)/ARTIFACT_VERSIONS[version]
    if not path.exists():return None,None
    artifact=read_json(path);registry=read_json(Path(artifact['pilot_directory'])/'published_artifacts.json')
    if sha(path)!=registry[patient]['sha256']:raise ValueError('TFNE candidate checksum changed')
    return artifact,sha(path)


def validate_changes(raw,changes):
    allowed=set(defaults())-{'episode_index','evidence'}
    if not isinstance(changes,dict) or set(changes)-allowed:raise ValueError('Edit clinical source fields; evidence, identities and derived screens stay protected')
    values={k:copy.deepcopy(raw.get(k,v)) for k,v in defaults().items()};values['evidence']={};values.update(changes)
    validate(dict(episodes=[values]))
    return copy.deepcopy(changes)


def project(artifact,events):
    episodes={e['episode_id']:dict(copy.deepcopy(e),review_status='UNREVIEWED') for e in artifact['episodes']}
    for record in events:
        ids=record['target_ids']
        if any(eid not in episodes for eid in ids):continue
        action=record['action'];sources=[episodes[eid] for eid in ids]
        if action in ('ACCEPT','REJECT','UNCERTAIN'):
            episodes[ids[0]]['review_status']=action
        elif action=='EDIT':
            episodes[ids[0]].update(record['changes'],review_status='EDIT',review_source='PHYSICIAN_OVERLAY')
            if artifact.get('module_version')=='0.2':
                episodes[ids[0]].setdefault('field_validation_status',{}).update({f:'PHYSICIAN_OVERLAY' for f in record['changes']})
        elif action=='SPLIT':
            del episodes[ids[0]]
            for n,changes in enumerate(record['changes'],1):
                child=dict(copy.deepcopy(sources[0]),**changes)
                child.update(episode_id=ids[0]+':SPLIT:'+record['review_id']+':'+str(n),review_parent_ids=ids,review_status='EDIT',review_source='PHYSICIAN_OVERLAY')
                if artifact.get('module_version')=='0.2':child.setdefault('field_validation_status',{}).update({f:'PHYSICIAN_OVERLAY' for f in changes})
                episodes[child['episode_id']]=child
        elif action=='MERGE':
            merged=defaults();merged.update(symptom_description='; '.join(e['symptom_description'] for e in sources),
                symptom_domains=list(dict.fromkeys(d for e in sources for d in e['symptom_domains'])),
                evidence=[copy.deepcopy(e) for source in sources for e in source['evidence']],
                field_evidence={k:[copy.deepcopy(e) for s in sources for e in s.get('field_evidence',{}).get(k,[])] for k in set().union(*(s.get('field_evidence',{}) for s in sources))})
            labels={s['historical_diagnosis'] for s in sources}
            merged['historical_diagnosis']=next(iter(labels)) if len(labels)==1 else 'OTHER'
            merged.update(record['changes']);merged.update(episode_id='TFNE:MERGE:'+record['review_id'],review_parent_ids=ids,review_status='EDIT',review_source='PHYSICIAN_OVERLAY',
                merged_historical_diagnoses=[s['historical_diagnosis'] for s in sources])
            for eid in ids:del episodes[eid]
            episodes[merged['episode_id']]=merged
    for episode in episodes.values():episode.update(derive(episode))
    return list(episodes.values())


class TFNEReviewRepository:
    def __init__(self,root=None,version='0.1'):
        if version not in ARTIFACT_VERSIONS:raise ValueError('Unsupported TFNE review version')
        self.root=Path(root if root is not None else REVIEW_ROOT/('tfne_neurological_deficit_v'+version.replace('.','_'))).resolve()

    def folder(self,patient,fingerprint):
        if not str(patient).isdigit() or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):raise ValueError('Invalid TFNE review scope')
        return self.root/str(patient)/fingerprint

    def load(self,patient,fingerprint):
        path=self.folder(patient,fingerprint)/'events.json'
        return read_json(path) if path.exists() else []

    def save(self,artifact,fingerprint,target_ids,action,changes=None):
        if action not in ('ACCEPT','EDIT','REJECT','UNCERTAIN','SPLIT','MERGE'):raise ValueError('Unsupported TFNE action')
        folder=self.folder(artifact['patient_id'],fingerprint);folder.mkdir(parents=True,exist_ok=True)
        with file_lock(folder/'.review.lock'):
            events=self.load(artifact['patient_id'],fingerprint);current={e['episode_id']:e for e in project(artifact,events)}
            if not target_ids or len(set(target_ids))!=len(target_ids) or any(e not in current for e in target_ids):raise ValueError('Select current episode identities')
            if action=='MERGE' and len(target_ids)<2:raise ValueError('Merge requires at least two episodes')
            if action!='MERGE' and len(target_ids)!=1:raise ValueError('Action requires one episode')
            if action=='EDIT':changes=validate_changes(current[target_ids[0]],changes)
            elif action=='SPLIT':
                if not isinstance(changes,list) or len(changes)<2:raise ValueError('Split requires at least two correction objects')
                changes=[validate_changes(current[target_ids[0]],c) for c in changes]
            elif action=='MERGE':changes=validate_changes(defaults(),changes or {})
            else:changes={}
            record=dict(review_id=uuid.uuid4().hex,patient_id=artifact['patient_id'],module='TFNE_NEUROLOGICAL_DEFICIT',module_version=artifact['module_version'],source_artifact_hash=fingerprint,
                target_ids=target_ids,action=action,changes=changes,reviewer='PHYSICIAN',timestamp=datetime.now(timezone.utc).isoformat())
            events.append(record);atomic(folder/'events.json',events)
        return record
