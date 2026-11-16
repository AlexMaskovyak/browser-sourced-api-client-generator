"""Automatic admission tests use independent standard documents and safe HARs."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from website_client.admission import admit_openapi
from website_client.discovery import Discovery
from website_client.har_inference import infer_har


def contract():
    return {'openapi': '3.0.3', 'info': {'title': 'Input', 'version': '1'}, 'servers': [{'url': 'https://neutral.invalid'}],
            'paths': {'/read': {'get': {'operationId': 'read', 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}}}}}}}}}}


def test_inherited_referenced_nonexploded_arrays_excluded_without_losing_other_methods():
    spec = contract()
    spec['components'] = {'parameters': {'Tags': {'name': 'tag', 'in': 'query', 'style': 'form', 'explode': False, 'schema': {'type': 'array', 'items': {'type': 'string'}}}}}
    spec['paths']['/tags'] = {'parameters': [{'$ref': '#/components/parameters/Tags'}], 'get': {'operationId': 'list_tags', 'responses': {'200': {'description': 'ok'}}}}
    safe, executable, excluded = admit_openapi(spec)
    assert set(executable['paths']) == {'/read'}
    assert excluded == [{'operation': 'list_tags', 'method': 'GET', 'path': '/tags', 'reason': 'unsupported_serialization'}]
    assert '/tags' in safe['paths']


def test_external_reference_excludes_only_dependent_operation_without_fetch():
    spec = contract()
    spec['components'] = {'schemas': {'Remote': {'$ref': 'https://must-not-fetch.invalid/remote.json'}}}
    spec['paths']['/blocked'] = {'get': {'operationId': 'blocked', 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Remote'}}}}}}}
    safe, executable, excluded = admit_openapi(spec)
    assert set(executable['paths']) == {'/read'}
    assert excluded[0]['reason'] == 'external_reference'
    assert 'components' not in executable


def test_privacy_removes_samples_prose_and_excludes_sensitive_wire_structure():
    spec = contract()
    spec['info']['description'] = 'CANARY_DESCRIPTION'
    schema = spec['paths']['/read']['get']['responses']['200']['content']['application/json']['schema']
    schema['properties']['value'].update(example='CANARY_EXAMPLE', default='CANARY_DEFAULT')
    spec['paths']['/CANARY_CAPABILITY'] = {'get': {'operationId': 'private_read', 'responses': {'200': {'description': 'ok'}}}}
    safe, executable, excluded = admit_openapi(spec)
    assert 'CANARY' not in json.dumps(safe)
    assert 'CANARY' not in json.dumps(executable)
    assert excluded[0]['reason'] == 'sensitive_structure'
    assert set(executable['paths']) == {'/read'}


def test_structured_har_backend_retains_repetition_and_response_variants():
    discovery = Discovery()
    for status, body in [(200, {'value': 7}), (404, {'missing': True})]:
        discovery.add({'request': {'method': 'GET', 'url': 'https://neutral.invalid/items?tag=PRIVATE_ONE&tag=PRIVATE_TWO', 'headers': [{'name': 'Authorization', 'value': 'Bearer CANARY_TOKEN'}]}, 'response': {'status': status, 'content': {'mimeType': 'application/json', 'text': json.dumps(body)}}})
    safe = discovery.files('har')['capture.har']
    assert 'PRIVATE' not in safe and 'CANARY' not in safe
    assert len(json.loads(safe)['log']['entries'][0]['request']['queryString']) == 2
    spec, report = infer_har(safe)
    operation = next(iter(spec['paths'].values()))['get']
    parameter = next(item for item in operation['parameters'] if item['name'] == 'tag')
    assert parameter['schema']['type'] == 'array'
    assert set(operation['responses']) == {'200', '404'}
    assert report['backend'] == 'har2sdk' and report['version'] == '0.6.15'
    assert 'PRIVATE' not in json.dumps(spec)


def test_missing_node_is_terminal_dependency_reason(monkeypatch):
    monkeypatch.setattr('website_client.har_inference.shutil.which', lambda _: None)
    with pytest.raises(Exception, match='har_dependency_required'):
        infer_har('{"log":{"version":"1.2","entries":[]}}')


def test_generated_package_removes_nonexploded_operation_and_preserves_source(tmp_path):
    from website_client.generation import generate
    spec = contract()
    spec['paths']['/tags'] = {'get': {'operationId': 'list_tags', 'parameters': [{'in': 'query', 'name': 'tag', 'schema': {'type': 'array', 'items': {'type': 'string'}}, 'style': 'form', 'explode': False}], 'responses': {'200': {'description': 'ok'}}}}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(spec))
    output = tmp_path / 'client'
    report = generate(output, 'admitted-client', openapi=source)
    operations = json.loads((output / 'admitted_client' / '_operations.json').read_text())
    assert set(operations) == {'read'}
    assert report['coverage']['exclusions'][0]['reason'] == 'unsupported_serialization'
    assert '/tags' in json.loads((output / 'source-openapi.json').read_text())['paths']
    assert '/tags' not in json.loads((output / 'openapi.json').read_text())['paths']


def test_backend_warning_is_attributed_without_losing_independent_operation(tmp_path):
    from website_client.generation import generate
    spec = contract()
    spec['components'] = {'responses': {'First': {'$ref': '#/components/responses/Second'}, 'Second': {'description': 'ok'}}}
    spec['paths']['/unsupported'] = {'get': {'operationId': 'unsupported', 'responses': {'200': {'$ref': '#/components/responses/First'}}}}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(spec))
    output = tmp_path / 'client'
    report = generate(output, 'warning-client', openapi=source)
    operations = json.loads((output / 'warning_client' / '_operations.json').read_text())
    assert set(operations) == {'read'}
    assert report['coverage']['exclusions'][0]['operation'] == 'unsupported'
    assert report['coverage']['exclusions'][0]['reason'] == 'upstream_generation_warning'
