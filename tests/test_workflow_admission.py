"""Restricted Arazzo admission rejects unsafe semantics before constructing a runner."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from website_client.workflow_admission import check_workflow, admit_workflows
from website_client.workflow_schema import WorkflowRejected, validate_value

FIXTURES = Path(__file__).parent / 'fixtures' / 'workflow'


def documents():
    return json.loads((FIXTURES / 'workflow.json').read_text()), {'api': json.loads((FIXTURES / 'openapi.json').read_text())}


def test_original_research_sequence_without_input_schema_is_admitted():
    document, sources = documents()
    admitted, exclusions = admit_workflows(document, sources)
    assert not exclusions
    assert admitted['workflows'][0]['workflowId'] == 'upload'
    assert set(check_workflow(document, document['workflows'][0], sources)) == {'prepare', 'bytes', 'finish'}


@pytest.mark.parametrize('change,reason', [
    (lambda w: w.update(dependsOn=['other']), 'unsupported_workflow_structure'),
    (lambda w: w['steps'][0].update(onFailure=[{'type': 'retry'}]), 'unsupported_workflow_structure'),
    (lambda w: w['steps'][0].update(workflowId='other'), 'unsupported_workflow_structure'),
    (lambda w: w['steps'][0]['successCriteria'][0].update(type='regex'), 'unsupported_workflow_structure'),
    (lambda w: w['steps'][0]['successCriteria'][0].update(condition='$statusCode != 200'), 'unsupported_criterion'),
    (lambda w: w['steps'][0]['outputs'].update(ticket='$response.body#/'), 'unsupported_empty_pointer_token'),
    (lambda w: w['steps'][0]['outputs'].update(ticket='$response.body#/a//b'), 'unsupported_empty_pointer_token'),
    (lambda w: w['steps'][0]['requestBody'].update(payload='$steps.finish.complete'), 'unknown_step_output_reference'),
    (lambda w: w['steps'][2]['requestBody']['payload'].update(ticket='$steps.prepare.absent'), 'unknown_step_output_reference'),
    (lambda w: w['steps'][1]['requestBody'].update(payload='$inputs.absent'), 'unknown_input_reference'),
    (lambda w: w['steps'][1]['requestBody'].update(payload='prefix {$inputs.file}'), 'unsupported_expression'),
    (lambda w: w['steps'][1].update(stepId='prepare'), 'duplicate_step_id'),
])
def test_unsupported_semantics_exclude_only_affected_workflow(change, reason):
    document, sources = documents()
    valid = deepcopy(document['workflows'][0])
    valid['workflowId'] = 'valid'
    document['workflows'].append(valid)
    document['workflows'][0]['inputs'] = {'type': 'object', 'properties': {'file': {}}, 'additionalProperties': False}
    change(document['workflows'][0])
    admitted, exclusions = admit_workflows(document, sources)
    assert [item['workflowId'] for item in admitted['workflows']] == ['valid']
    assert exclusions[0]['reason'] == reason


def test_inherited_referenced_query_array_is_rejected():
    document, sources = documents()
    source = sources['api']
    source['components']['parameters'] = {'Tags': {'name': 'tag', 'in': 'query', 'style': 'form', 'explode': False, 'schema': {'type': 'array', 'items': {'type': 'string'}}}}
    source['paths']['/prepare']['parameters'] = [{'$ref': '#/components/parameters/Tags'}]
    _, exclusions = admit_workflows(document, sources)
    assert exclusions[0]['reason'] == 'unsupported_serialization'


@pytest.mark.parametrize('dialect,schema,valid,invalid', [
    ('3.0.3', {'type': 'string', 'nullable': True}, None, 1),
    ('3.1.0', {'type': ['string', 'null']}, None, 1),
    ('2020-12', {'type': 'boolean'}, True, 1),
])
def test_dialect_correct_validation(dialect, schema, valid, invalid):
    validate_value(valid, schema, dialect=dialect)
    with pytest.raises(WorkflowRejected, match='invalid_inputs'):
        validate_value(invalid, schema, dialect=dialect)


@pytest.mark.parametrize('schema', [
    {'$ref': 'https://must-not-fetch.invalid/schema.json'},
    {'$dynamicRef': 'https://must-not-fetch.invalid/schema.json'},
    {'$id': 'https://must-not-fetch.invalid/base', 'type': 'string'},
])
def test_reference_features_cannot_fetch(schema, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *_: pytest.fail('schema validator opened network'))
    with pytest.raises(WorkflowRejected):
        validate_value('value', schema)


def test_openapi_local_component_reference_uses_correct_root():
    document = {'openapi': '3.0.3', 'components': {'schemas': {'Value': {'type': 'integer'}}}}
    schema = {'$ref': '#/components/schemas/Value'}
    validate_value(7, schema, document=document, dialect='3.0.3')
    with pytest.raises(WorkflowRejected):
        validate_value('wrong', schema, document=document, dialect='3.0.3')


def test_duplicate_workflow_ids_are_all_excluded_and_metadata_is_not_executable():
    document, sources = documents()
    document['workflows'].append(deepcopy(document['workflows'][0]))
    admitted, exclusions = admit_workflows(document, sources)
    assert not admitted['workflows']
    assert len(exclusions) == 2
    document, sources = documents()
    document['x-discovery-exclusions'] = [{'workflow': '[omitted]', 'reason': 'sensitive_structure'}]
    admitted, exclusions = admit_workflows(document, sources)
    assert len(admitted['workflows']) == 1
    assert 'x-discovery-exclusions' not in admitted
    assert exclusions[0]['reason'] == 'sensitive_structure'


def test_unused_external_component_does_not_exclude_selected_workflow():
    document, sources = documents()
    sources['api']['components']['schemas']['Unused'] = {'$ref': 'https://must-not-fetch.invalid/schema.json'}
    admitted, exclusions = admit_workflows(document, sources)
    assert len(admitted['workflows']) == 1 and not exclusions
