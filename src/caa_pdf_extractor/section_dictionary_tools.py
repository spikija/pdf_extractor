"""Validate and export the runtime dictionary without extracting any patient data."""
import argparse
import csv
import json
from pathlib import Path

from .section_dictionary import CONFIG, classify_heading, load_dictionary, normalize_heading
from .page_boilerplate import load_boilerplate


def statistics(data=None):
    data = data if data is not None else load_dictionary()
    active = [e for e in data['sections'] if e['active']]
    return dict(manual_entries_ingested=len(data.get('manual_entries', [])),
        manual_unique_entries=len(set(data.get('manual_entries', []))),
        unique_variants=len({normalize_heading(v) for e in active for v in e['variants']}),
        canonical_sections=len({e['canonical_section'] for e in active}),
        canonical_section_families=len({e['section_family'] for e in active}),
        prefix_rules_created=sum(e['match_type']=='PREFIX' for e in active),
        prefix_variants=sum(len(e['variants']) for e in active if e['match_type']=='PREFIX'),
        contextual_rules_created=sum(e['boundary_strength']=='CONTEXTUAL' for e in active),
        page_boilerplate_patterns_created=len(load_boilerplate()['patterns']))


def known_heading_results(data=None):
    data = data if data is not None else load_dictionary()
    rows = []
    for source in dict.fromkeys(data.get('manual_entries', [])):
        text = source.rstrip('*')
        for suffix in ('', ' (06.02.2026)', ' vom 6.2.26'):
            found = classify_heading(text+suffix, data)
            rows.append(dict(source_variant=source, test_heading=text+suffix,
                passed=found is not None,
                canonical_section=found['canonical_section'] if found else None,
                section_family=found['section_family'] if found else None))
    return rows


def export(directory, data=None):
    data = data if data is not None else load_dictionary()
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    fields = ['canonical_section','variant','match_type','boundary_strength','module_scope','notes','active']
    with (directory/'section_dictionary_review.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for e in data['sections']:
            for variant in e['variants']:
                writer.writerow(dict(canonical_section=e['canonical_section'], variant=variant,
                    match_type=e['match_type'], boundary_strength=e['boundary_strength'],
                    module_scope=json.dumps(e['module_actions'], ensure_ascii=False), notes=e['notes'], active=e['active']))
    with (directory/'section_dictionary_ambiguous_entries.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['source_variant','proposed_family','reason_for_uncertainty']); writer.writeheader()
        for e in data['sections']:
            if e['boundary_strength']=='CONTEXTUAL':
                for variant in e['variants']:
                    writer.writerow(dict(source_variant=variant, proposed_family=e['section_family'],
                        reason_for_uncertainty='Family mapped; peer versus embedded section requires module context.'))
    return statistics(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check','export'])
    parser.add_argument('--dictionary', type=Path, default=CONFIG/'section_dictionary.yaml')
    parser.add_argument('--output', type=Path, default=CONFIG.parent/'docs')
    args = parser.parse_args(); data = load_dictionary(args.dictionary)
    results = known_heading_results(data)
    summary = export(args.output, data) if args.action=='export' else statistics(data)
    summary.update(known_heading_tests_passed=sum(r['passed'] for r in results),
                   known_heading_tests_failed=sum(not r['passed'] for r in results))
    print(json.dumps(summary, indent=2))
    if summary['known_heading_tests_failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
