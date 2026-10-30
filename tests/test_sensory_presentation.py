"""Structural/provenance regressions; clinical regressions run against local Granite."""
from copy import deepcopy
from dataclasses import replace

import pytest

from caa_pdf_extractor.extraction import sensory_presentation as s


@pytest.fixture
def sources():
    return [s.Snippet('A1-001', '123', 1, 's', 'Anamnese', None, 1, 1, 0, 5, 'test.')]


@pytest.fixture
def episode():
    return dict(current_episode_evidence_ids=['A1-001'],
                explicit_sensory_symptom_evidence_ids=['A1-001'], sensory_present=True,
                current_sensory_evidence_ids=['A1-001'], excluded_historical_or_chronic_ids=[],
                evidence_roles=[dict(evidence_id='A1-001', role='CURRENT_PRESENTING_EPISODE')])


@pytest.fixture
def features():
    return dict(sensory_polarity='NEGATIVE', polarity_evidence_ids=['A1-001'],
                sensory_laterality='LEFT', sensory_distribution='UPPER_EXTREMITY',
                localization_evidence_ids=['A1-001'], sensory_spreading=None, spread_evidence_ids=[],
                sensory_transient=True, sensory_duration_minutes=None, transient_evidence_ids=['A1-001'],
                sensory_stereotyped_recurrent=None, recurrent_evidence_ids=[])


@pytest.mark.parametrize('field', ['current_episode_evidence_ids', 'explicit_sensory_symptom_evidence_ids',
                                  'current_sensory_evidence_ids', 'excluded_historical_or_chronic_ids'])
def test_every_episode_id_list_is_provenance_checked(episode, sources, field):
    episode[field] = ['not-supplied']
    result = s.validate('episode', episode, sources, '123', 1)
    assert result['status'] == 'TECHNICAL_FAILURE'
    assert result['errors'] == [{'path': [field, 0], 'code': 'MISSING_SNIPPET_ID', 'id': 'not-supplied'}]


@pytest.mark.parametrize('field', ['polarity_evidence_ids', 'localization_evidence_ids',
                                  'spread_evidence_ids', 'transient_evidence_ids', 'recurrent_evidence_ids'])
def test_every_feature_has_its_own_provenance_check(features, sources, field):
    features[field] = ['not-supplied']
    assert s.validate('features', features, sources, '123', 1)['errors'][0]['path'] == [field, 0]


def test_exclusion_roles_cannot_cite_foreign_patient_or_document(episode, sources):
    for source in (replace(sources[0], patient_id='456'), replace(sources[0], source_document_id=2)):
        result = s.validate('episode', episode, [source], '123', 1)
        assert result['errors'][-1]['code'] == 'WRONG_PATIENT_OR_DOCUMENT'
    episode['evidence_roles'][0]['evidence_id'] = 'invalid-role-source'
    assert s.validate('episode', episode, sources, '123', 1)['errors'][0]['path'] == ['evidence_roles', 0]


@pytest.mark.parametrize('value', [True, -1, float('inf'), float('nan'), 'five'])
def test_non_duration_numbers_rejected(features, sources, value):
    features['sensory_duration_minutes'] = value
    assert s.validate('features', features, sources, '123', 1)['status'] == 'TECHNICAL_FAILURE'


@pytest.mark.parametrize('present', [True, False, None])
def test_model_semantics_are_retained_without_python_correction(episode, sources, present):
    episode['sensory_present'] = present
    # One passage can contain current and historical clauses. Never mask it globally.
    episode['excluded_historical_or_chronic_ids'] = ['A1-001']
    before = deepcopy(episode)
    result = s.validate('episode', episode, sources, '123', 1)
    assert result['value'] == before == episode


def test_stage_failure_does_not_mutate_other_raw_components(episode, features, sources):
    episode['current_sensory_evidence_ids'] = ['bad']
    original = deepcopy(features)
    assert s.validate('episode', episode, sources, '123', 1)['value'] is None
    assert s.validate('features', features, sources, '123', 1)['value'] == original


def test_dependent_prompts_require_prior_stages(sources, episode):
    with pytest.raises(ValueError):
        s.prompt('features', sources, '123', 1)
    with pytest.raises(ValueError):
        s.prompt('phenotype', sources, '123', 1, episode)


def test_assembly_does_not_reclassify_semantically_inconsistent_model_values(episode, features):
    phenotype = {'primary_reason': 'OTHER', 'flags': [], 'phenotype_evidence_ids': ['A1-001']}
    result = s.assemble(episode, features, phenotype)
    assert result['sensory_present'] is True and result['flags'] == []
    assert result['primary_reason'] == 'OTHER'


def test_strict_schema_rejects_unknown_fields(features, sources):
    features['derived_mrs'] = 2
    assert s.validate('features', features, sources, '123', 1)['status'] == 'TECHNICAL_FAILURE'


def test_each_clinical_dimension_has_a_separate_model_schema(sources):
    assert s.STAGES == ('episode', 'polarity', 'localization', 'spread', 'transience', 'recurrence', 'phenotype')
    for stage in s.STAGES:
        schema = s.schema(stage, sources)
        for key, value in schema['properties'].items():
            if key.endswith('_ids'):
                assert value['items']['enum'] == ['A1-001']
                assert value['maxItems'] == 1
    role = s.schema('episode', sources)['$defs']['EvidenceRole']
    assert role['properties']['evidence_id']['enum'] == ['A1-001']


def test_reference_schema_is_generated_fresh_for_each_document(sources):
    other = [replace(sources[0], snippet_id='A2-001', source_document_id=2)]
    a, b = s.schema('spread', sources), s.schema('spread', other)
    assert a['properties']['spread_evidence_ids']['items']['enum'] == ['A1-001']
    assert b['properties']['spread_evidence_ids']['items']['enum'] == ['A2-001']
