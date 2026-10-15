"""Plan event reuse before writes; never distinguish events by result wording."""
from collections import defaultdict
from .examination_validators import record_key, whitespace


class ExaminationIdentityError(ValueError):
    status = 'DUPLICATE_IDENTITY_AMBIGUOUS'

    def __init__(self, details):
        self.details = details
        super().__init__(self.status)


def row_key(row):
    return record_key({'date_examination': row.date_examination.isoformat() if row.date_examination else None,
                      'type': row.type, 'location': row.location, 'neck_vessel_result': row.neck_vessel_result})


def procedure_identity(context):
    return context['source_document_id'], context['examination_id']


def plan_reconciliation(events, rows, audits):
    existing, intended = defaultdict(list), defaultdict(list)
    for row in rows:
        existing[row_key(row)].append(row)
    for event in events:
        for record in event['records']:
            intended[record_key(record)].append((event, record))
    links = defaultdict(set)
    for run in audits:
        output = run.raw_output or {}
        if (run.status != 'SUCCESS' or output.get('operation') != 'COMMIT_VERIFIED_EXTRACTION'
                or output.get('extraction_type') != 'examinations' or not output.get('examination')):
            continue
        for row_id in output.get('matched_examination_ids', []) + output.get('inserted_examination_ids', []):
            links[procedure_identity(output['examination'])].add(row_id)
    plan, collisions, unresolved = {}, [], []
    for key, items in intended.items():
        candidates = existing.get(key, [])
        if len(items) == 1 and len(candidates) <= 1:
            event, record = items[0]
            plan[event['examination_id']] = (candidates[0] if candidates else None, 'identity_fields')
            continue
        detail = {'identity': {'date_examination': key[0], 'type': key[1], 'location': key[2],
                              'neck_vessel_result': key[3]},
                  'procedures': [{'source_document_id': e['source_document_id'],
                                  'examination_id': e['examination_id'], 'label': e['header']} for e, r in items],
                  'existing_row_ids': [r.id for r in candidates]}
        matched_ids = set()
        ambiguous = False
        for event, record in items:
            linked = links.get(procedure_identity(event), set())
            matches = [r for r in candidates if r.id in linked]
            reason = 'audited_procedure_identity'
            if not matches:
                # Legacy full reports contain an explicit examination label.
                # Compare that metadata prefix only, never body/result wording.
                label = whitespace(event['header'])
                matches = [r for r in candidates if label and whitespace(r.result or '').startswith(label)]
                reason = 'explicit_examination_label'
            if len(matches) != 1 or matches[0].id in matched_ids:
                ambiguous = True
                continue
            matched_ids.add(matches[0].id)
            plan[event['examination_id']] = (matches[0], reason)
        if ambiguous:
            detail['resolution'] = 'UNRESOLVED: schema fields cannot distinguish the examinations safely'
            unresolved.append(detail)
        else:
            detail['resolution'] = 'RESOLVED_USING_DETERMINISTIC_METADATA'
            detail['matched_row_ids'] = sorted(matched_ids)
            collisions.append(detail)
    if unresolved:
        raise ExaminationIdentityError(unresolved)
    return plan, collisions
