"""Non-fetching, dialect-correct schema validation at workflow wire boundaries."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
from openapi_schema_validator import OAS30Validator, OAS31Validator
from referencing import Registry
from referencing.exceptions import NoSuchResource


class WorkflowRejected(ValueError):
    """A fixed reason code safe to return without source values or validator details."""


def no_fetch(uri):
    raise NoSuchResource(ref=uri)


REGISTRY = Registry(retrieve=no_fetch)


def local_schema(value, depth=0, context=None):
    if depth > 80:
        raise WorkflowRejected('unsupported_schema_depth')
    if context in {"enum", "const", "default", "example", "examples"}:
        return
    maps = {"properties", "patternProperties", "$defs", "definitions", "schemas", "parameters", "responses", "headers", "paths", "requestBodies", "securitySchemes"}
    if isinstance(value, dict):
        if context in maps:
            for item in value.values():
                local_schema(item, depth + 1)
            return
        for key, item in value.items():
            if key in {'$id', '$dynamicRef', '$dynamicAnchor', '$recursiveRef', '$recursiveAnchor', '$vocabulary', '$anchor'}:
                raise WorkflowRejected('unsupported_schema_reference')
            if key == '$ref' and (not isinstance(item, str) or not item.startswith('#/')):
                raise WorkflowRejected('external_reference')
            local_schema(item, depth + 1, key)
    elif isinstance(value, list):
        for item in value:
            local_schema(item, depth + 1)


def validate_value(value, schema, *, document=None, dialect='2020-12', reason='invalid_inputs'):
    """Resolve only bundled local pointers through standard schema validators."""
    try:
        local_schema(schema)
        if document is not None:
            local_schema(document)
            root = deepcopy(document)
            root['x-website-client-validation'] = schema
            root['$ref'] = '#/x-website-client-validation'
        else:
            root = schema
        validator = OAS30Validator if dialect.startswith('3.0.') else OAS31Validator if dialect.startswith('3.1.') else Draft202012Validator
        if next(validator(root, registry=REGISTRY).iter_errors(value), None) is not None:
            raise WorkflowRejected(reason)
    except WorkflowRejected:
        raise
    except Exception:
        raise WorkflowRejected(reason) from None


def strict_json(raw, reason='malformed_json'):
    def invalid_constant(_):
        raise ValueError()
    try:
        return json.loads(raw, parse_constant=invalid_constant)
    except Exception:
        raise WorkflowRejected(reason) from None
