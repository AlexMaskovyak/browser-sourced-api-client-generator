"""Executable wire-feature policy shared by SDK and workflow admission."""

from jsonpointer import resolve_pointer, JsonPointerException

METHODS = frozenset({'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'})
CAPABILITIES = {
    'sdk': {'json': True, 'multipart_file': True, 'raw_binary': True, 'graphql_multipart': True,
            'graphql_nested_files': True, 'nonexploded_query_array': False},
    'workflow': {'json': True, 'flat_multipart': True, 'raw_binary': True,
                 'nonexploded_query_array': False, 'complex_parameters': False,
                 'dynamic_destinations': False, 'file_objects': False, 'json_null_body': True},
}


def resolved(document, value):
    seen = set()
    while isinstance(value, dict) and '$ref' in value:
        reference = value['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/'):
            raise ValueError('external_reference')
        if reference in seen:
            raise ValueError('unresolved_reference')
        seen.add(reference)
        try:
            value = resolve_pointer(document, reference[1:])
        except JsonPointerException:
            raise ValueError('unresolved_reference') from None
    return value


def request_features(document, path_item, operation, *, workflow=False):
    parameters = [resolved(document, item) for item in path_item.get('parameters', []) + operation.get('parameters', [])]
    parameters = {(item.get('in'), item.get('name')): item for item in parameters}
    for item in parameters.values():
        schema = resolved(document, item.get('schema', {}))
        location, kind = item.get('in'), schema.get('type')
        if any(key in schema for key in ('oneOf', 'anyOf', 'allOf', 'properties')) or isinstance(kind, list):
            raise ValueError('unsupported_serialization')
        style = item.get('style', 'form' if location in {'query', 'cookie'} else 'simple')
        if 'content' in item or kind == 'object' or (kind == 'array' and location != 'query'):
            raise ValueError('unsupported_serialization')
        if location == 'query' and (style != 'form' or (kind == 'array' and item.get('explode', True) is False)):
            raise ValueError('unsupported_serialization')
        if location != 'query' and style not in {'form', 'simple'}:
            raise ValueError('unsupported_serialization')
        if workflow and (location == 'cookie' or kind == 'boolean' or location == 'header' and kind != 'string'
                         or kind == 'array' and schema.get('items', {}).get('type') not in {'string', 'integer', 'number'}):
            raise ValueError('unsupported_serialization')
    if operation.get('callbacks'):
        raise ValueError('unsupported_callbacks')
