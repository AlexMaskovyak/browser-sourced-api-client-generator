"""Admit only explicit linear Arazzo contracts before constructing the native runner."""

from copy import deepcopy
from collections import Counter
import re
from urllib.parse import urlsplit

from jsonpointer import JsonPointer
from jsonschema import Draft202012Validator

from .capabilities import METHODS, CAPABILITIES, request_features, resolved
from .runtime_profile import service_origin
from .workflow_schema import WorkflowRejected, local_schema

NAME = r'[A-Za-z][A-Za-z0-9_]*'
IDENTIFIER = re.compile(NAME + r'\Z')
INPUT = re.compile(r'\$inputs\.(' + NAME + r')\Z')
STEP_OUTPUT = re.compile(r'\$steps\.(' + NAME + r')\.(' + NAME + r')\Z')
POINTER_PREFIX = '$response.body#'
STATUS = re.compile(r'\$statusCode == ([1-5][0-9][0-9])\Z')
BOOLEAN = re.compile(r'\$response\.body\.(' + NAME + r') == (true|false)\Z')
MAX_STEPS = 100

PARAMETER_SCHEMA = {
    'type': 'object', 'required': ['name', 'in', 'value'], 'additionalProperties': False,
    'properties': {'name': {'type': 'string'}, 'in': {'enum': ['path', 'query', 'header']}, 'value': {}},
}
STEP_SCHEMA = {
    'type': 'object', 'required': ['stepId', 'operationId'], 'additionalProperties': False,
    'properties': {
        'stepId': {'type': 'string', 'pattern': '^' + NAME + '$'}, 'operationId': {'type': 'string'},
        'parameters': {'type': 'array', 'items': PARAMETER_SCHEMA},
        'requestBody': {'type': 'object', 'required': ['contentType', 'payload'], 'additionalProperties': False,
                        'properties': {'contentType': {'type': 'string'}, 'payload': {}}},
        'outputs': {'type': 'object', 'additionalProperties': {'type': 'string'}},
        'successCriteria': {'type': 'array', 'items': {'type': 'object', 'required': ['condition'], 'additionalProperties': False,
                                                    'properties': {'condition': {'type': 'string'}}}},
    },
}
WORKFLOW_SCHEMA = {
    'type': 'object', 'required': ['workflowId', 'steps'], 'additionalProperties': False,
    'properties': {
        'workflowId': {'type': 'string', 'pattern': '^' + NAME + '$'}, 'inputs': {'type': 'object'},
        'outputs': {'type': 'object', 'additionalProperties': {'type': 'string'}},
        'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS, 'items': STEP_SCHEMA},
    },
}
PROFILE_SCHEMA = {
    'type': 'object', 'required': ['arazzo', 'info', 'sourceDescriptions', 'workflows'], 'additionalProperties': False,
    'properties': {
        'arazzo': {'const': '1.0.0'}, 'info': {'type': 'object'},
        'sourceDescriptions': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'required': ['name', 'type', 'url'], 'additionalProperties': False,
            'properties': {'name': {'type': 'string', 'pattern': '^' + NAME + '$'}, 'type': {'const': 'openapi'}, 'url': {'type': 'string'}}}},
        'workflows': {'type': 'array', 'items': WORKFLOW_SCHEMA},
    },
}


def operation_index(sources):
    index, duplicates = {}, set()
    for source_name, document in sources.items():
        servers = document.get('servers', [])
        if not servers or not isinstance(servers[0], dict):
            raise WorkflowRejected('unsupported_destination')
        base = servers[0].get('url')
        service_origin(base)
        parsed = urlsplit(base)
        if parsed.query or parsed.fragment or '{' in base or '}' in base or servers[0].get('variables'):
            raise WorkflowRejected('unsupported_destination')
        for path, item in document.get('paths', {}).items():
            for method, operation in item.items():
                if method not in METHODS or not isinstance(operation, dict):
                    continue
                identifier = operation.get('operationId')
                if not isinstance(identifier, str):
                    continue
                if identifier in index:
                    duplicates.add(identifier)
                index[identifier] = {'source': source_name, 'document': document, 'path': path, 'path_item': item,
                                     'method': method.upper(), 'operation': operation, 'base': base}
    return index, duplicates


def check_reference(value, inputs, previous, *, response=False):
    if isinstance(value, dict):
        for item in value.values():
            check_reference(item, inputs, previous, response=response)
    elif isinstance(value, list):
        for item in value:
            check_reference(item, inputs, previous, response=response)
    elif isinstance(value, str) and '$' in value:
        if match := INPUT.fullmatch(value):
            if inputs is not None and match[1] not in inputs:
                raise WorkflowRejected('unknown_input_reference')
        elif match := STEP_OUTPUT.fullmatch(value):
            if match[1] not in previous or match[2] not in previous[match[1]]:
                raise WorkflowRejected('unknown_step_output_reference')
        elif response and value.startswith(POINTER_PREFIX):
            try:
                pointer = JsonPointer(value[len(POINTER_PREFIX):])
                if not pointer.parts or any(token == '' for token in pointer.parts):
                    raise ValueError()
            except Exception:
                raise WorkflowRejected('unsupported_empty_pointer_token') from None
        else:
            raise WorkflowRejected('unsupported_expression')


def check_workflow(document, workflow, sources):
    candidate = {key: value for key, value in document.items() if key != 'x-discovery-exclusions'}
    candidate['workflows'] = [workflow]
    if next(Draft202012Validator(PROFILE_SCHEMA).iter_errors(candidate), None) is not None:
        raise WorkflowRejected('unsupported_workflow_structure')
    names = [item['name'] for item in document['sourceDescriptions']]
    if len(names) != len(set(names)) or set(names) != set(sources):
        raise WorkflowRejected('unresolved_source')
    schema = workflow.get('inputs', {})
    local_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise WorkflowRejected('invalid_input_schema') from None
    inputs = set(schema.get('properties', {})) if schema.get('additionalProperties') is False else None
    operations, duplicates = operation_index(sources)
    previous, selected = {}, {}
    for step in workflow['steps']:
        identifier = step['stepId']
        if identifier in previous:
            raise WorkflowRejected('duplicate_step_id')
        operation_id = step['operationId']
        if operation_id in duplicates:
            raise WorkflowRejected('ambiguous_operation')
        if operation_id not in operations:
            raise WorkflowRejected('unresolved_operation')
        info = operations[operation_id]
        try:
            request_features(info['document'], info['path_item'], info['operation'], workflow=True)
        except ValueError:
            raise WorkflowRejected('unsupported_serialization') from None
        if info['method'] not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise WorkflowRejected('unsupported_method')
        check_reference(step.get('parameters', []), inputs, previous)
        check_reference(step.get('requestBody', {}), inputs, previous)
        parameter_values = [resolved(info['document'], item) for item in info['path_item'].get('parameters', []) + info['operation'].get('parameters', [])]
        parameters = {(item.get('in'), item.get('name')): item for item in parameter_values}
        for parameter in step.get('parameters', []):
            if (parameter['in'], parameter['name']) not in parameters:
                raise WorkflowRejected('unknown_parameter')
        for criterion in step.get('successCriteria', []):
            if not (STATUS.fullmatch(criterion['condition']) or BOOLEAN.fullmatch(criterion['condition'])):
                raise WorkflowRejected('unsupported_criterion')
        for name, expression in step.get('outputs', {}).items():
            if not IDENTIFIER.fullmatch(name) or not expression.startswith(POINTER_PREFIX):
                raise WorkflowRejected('unsupported_output')
            check_reference(expression, inputs, previous, response=True)
        local_schema({key: info["operation"][key] for key in ("requestBody", "responses", "parameters") if key in info["operation"]}, document=info["document"])
        local_schema(info["path_item"].get("parameters", []), document=info["document"])
        body = step.get('requestBody')
        if body:
            definition = resolved(info['document'], info['operation'].get('requestBody', {}))
            if body['contentType'] not in definition.get('content', {}):
                raise WorkflowRejected('unsupported_content_type')
            media = body['contentType']
            body_schema = resolved(info['document'], definition['content'][media].get('schema', {}))
            if media == 'multipart/form-data':
                if not CAPABILITIES['workflow']['flat_multipart'] or body_schema.get('type') != 'object':
                    raise WorkflowRejected('unsupported_multipart')
                for field in body_schema.get('properties', {}).values():
                    field = resolved(info['document'], field)
                    if field.get('type') not in {'string', 'integer', 'number', 'boolean'}:
                        raise WorkflowRejected('unsupported_multipart')
                if any(set(encoding) - {'contentType'} for encoding in definition['content'][media].get('encoding', {}).values()):
                    raise WorkflowRejected('unsupported_multipart')
            elif 'json' not in media:
                if body_schema.get('type') != 'string' or body_schema.get('format') != 'binary':
                    raise WorkflowRejected('unsupported_content_type')
            if body['payload'] is None and not CAPABILITIES['workflow']['json_null_body']:
                raise WorkflowRejected('unsupported_serialization')
        previous[identifier] = set(step.get('outputs', {}))
        selected[identifier] = info
    for name, expression in workflow.get('outputs', {}).items():
        if not IDENTIFIER.fullmatch(name) or not STEP_OUTPUT.fullmatch(expression):
            raise WorkflowRejected('unsupported_output')
        check_reference(expression, inputs, previous)
    return selected


def admit_workflows(document, sources):
    """Exclude unsupported workflows independently and keep the safe source separate."""
    if not isinstance(document, dict) or not isinstance(document.get('workflows'), list):
        return None, [{'workflow': '[omitted]', 'reason': 'unsupported_workflow_structure'}]
    accepted, exclusions = [], list(document.get("x-discovery-exclusions", []))
    counts = Counter(item.get("workflowId") for item in document["workflows"] if isinstance(item, dict) and isinstance(item.get("workflowId"), str))
    for workflow in document['workflows']:
        identifier = workflow.get('workflowId') if isinstance(workflow, dict) else None
        try:
            if isinstance(identifier, str) and counts[identifier] > 1:
                raise WorkflowRejected('duplicate_workflow_id')
            check_workflow(document, workflow, sources)
            accepted.append(deepcopy(workflow))
        except (WorkflowRejected, ValueError, KeyError, TypeError, AttributeError) as error:
            reason = str(error) if isinstance(error, WorkflowRejected) else 'unsupported_workflow_structure'
            exclusions.append({'workflow': identifier if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier) else '[omitted]', 'reason': reason})
    result = {key: deepcopy(value) for key, value in document.items() if key != 'x-discovery-exclusions'}
    result['workflows'] = accepted
    return result, exclusions
