"""Version 0.2 model contracts, isolated from the preserved 0.1 schema."""
import copy
from .tfne_schema import defaults, response_schema as old_schema, validate as old_validate

VERSION='0.2'
FIELDS=sorted(defaults().keys()-{'episode_index','evidence'})


def detection_schema(span_ids,anchors=None):
    episode=dict(type='object',additionalProperties=False,
        required=['episode_index','source_span_ids','short_exact_description','episode_date_text'],properties=dict(
            episode_index=dict(type='integer',minimum=1),
            source_span_ids=dict(type='array',minItems=1,items={'enum':span_ids}),
            short_exact_description=dict(type='string',minLength=1),episode_date_text=dict(type=['string','null'])))
    if anchors:
        episode['properties']['short_exact_description']={'enum':list(dict.fromkeys(a['text'] for a in anchors))}
    return dict(type='object',additionalProperties=False,required=['episodes'],properties=dict(episodes=dict(type='array',items=episode)))


def phenotype_schema(span_ids):
    schema=old_schema(span_ids)
    schema['properties']['episodes'].update(minItems=1,maxItems=1)
    evidence=schema['properties']['episodes']['items']['properties']['evidence']['properties']
    for field,rule in evidence.items():
        rule['required'].append('quote');rule['properties']['quote']=dict(type=['string','null'])
    return schema


def validate_phenotype(raw):
    # v0.1's structural checker ignores evidence entry shapes; v0.2 checks these
    # itself and applies evidence gates rather than accepting empty provenance.
    return old_validate(raw)
