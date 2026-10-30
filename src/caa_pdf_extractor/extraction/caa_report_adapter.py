"""Source-report adapter for the shared CAA assembler, without dated snapshots."""
from .research_metadata_v2 import macro_location


def attach_source_report(scan, report):
    """Attach literal continuity evidence and source-derived lobar identities.

    No date, count or model feature is changed here. Unknown laterality cannot
    establish lesion identity. The assembler still requires an explicit report
    comparison and unchanged/resorbing hemorrhage before merging CT follow-ups.
    """
    identities = set()
    for finding in report['findings']:
        if finding.get('deleted') or finding['finding_type'] != 'ICH':
            continue
        location = macro_location(finding.get('location') or '')
        side = (finding.get('laterality') or '').lower()
        if location['macro_location'] == 'LOBAR' and side in ('left', 'right', 'bilateral'):
            identities.add((side, location['lobe']))
    return dict(scan, source_document_id=report['source_document_id'],
        header=report.get('header') or report.get('modality') or '', text=report['raw_report_text'],
        validated_positive=report['findings'], validated_negative=report.get('negative_findings', []),
        validated_ich_identity=';'.join(side + '_' + lobe for side, lobe in sorted(identities)) or None,
        complete_diagnostic_report=report.get('source_context', {}).get('discovery_kind') == 'FORMAL_REPORT')
