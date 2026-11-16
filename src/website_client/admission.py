"""Automatic privacy and wire-feature admission for standard OpenAPI inputs."""

from copy import deepcopy
import json
import re
from urllib.parse import unquote, urlsplit

from jsonpointer import resolve_pointer, JsonPointerException, JsonPointer
import yaml

from .browser import BrowserError
from .definitions import openapi_document

from .capabilities import METHODS, resolved, request_features

# Conservative known-value detection, not a universal secret detector.
SENSITIVE = re.compile(r'@|(?:bearer\s+)|(?:[?&](?:token|signature|key|credential)=)|(?:AKIA[A-Z0-9]{16})|(?:eyJ[A-Za-z0-9_-]{12,}\.)|(?:[a-f0-9]{32,})|(?:\bSECRET\d+\b)|(?:CANARY|SYNTHETIC_(?:TOKEN|SECRET|CAPABILITY))', re.I)
DROP = frozenset({'example', 'examples', 'default', 'description', 'summary', 'externalDocs', 'contact', 'license'})


def sensitive(value):
    return isinstance(value, str) and bool(SENSITIVE.search(unquote(value)))


def scrub_document(value, context=None):
    """Filter metadata without changing declared wire properties or literal values."""
    maps = {'properties', 'patternProperties', 'schemas', 'parameters', 'responses', 'requestBodies',
            'headers', 'callbacks', 'securitySchemes', 'paths', 'definitions', '$defs', 'dependentSchemas'}
    if isinstance(value, dict):
        if context in maps:
            return {key: scrub_document(item) for key, item in value.items()}
        return {key: ('API definition.' if key == 'description' else scrub_document(item, key)) for key, item in value.items()
                if (key not in DROP or key == 'description') and not (key.startswith('x-') and not key.startswith('x-discovery-'))}
    if isinstance(value, list):
        return [scrub_document(item) for item in value]
    return value


def walk_contract(document, value, seen=None, context=None):
    """Follow only local references. Never allow a validator to fetch a URL."""
    seen = set() if seen is None else seen
    if isinstance(value, dict):
        if context in {"properties", "patternProperties", "$defs", "definitions"}:
            for key, item in value.items():
                if sensitive(key):
                    raise ValueError("sensitive_structure")
                walk_contract(document, item, seen)
            return
        for key, item in value.items():
            if key in {'$id', '$dynamicRef', '$dynamicAnchor', '$recursiveRef', '$recursiveAnchor'}:
                raise ValueError('unsupported_schema_reference')
            if sensitive(key) or (key in {'name', 'operationId', 'enum', 'const', 'url'} and sensitive(item)):
                raise ValueError('sensitive_structure')
            if key == 'enum' and any(sensitive(v) for v in item):
                raise ValueError('sensitive_structure')
            if key == '$ref':
                if item in seen:
                    continue
                seen.add(item)
                walk_contract(document, resolved(document, value), seen)
            else:
                walk_contract(document, item, seen, key)
    elif isinstance(value, list):
        for item in value:
            walk_contract(document, item, seen)
    elif sensitive(value):
        raise ValueError("sensitive_structure")


def prune_components(document):
    """Retain only transitively referenced models and declared security schemes."""
    original = deepcopy(document.get('components', {}))
    used = set()
    def visit(value):
        if isinstance(value, dict):
            reference = value.get('$ref')
            if isinstance(reference, str) and reference.startswith('#/components/') and reference not in used:
                used.add(reference)
                visit(resolved({'components': original}, value))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(document.get('paths', {}))
    components = {}
    for reference in sorted(used, key=lambda item: len(JsonPointer(item[1:]).parts)):
        parts = JsonPointer(reference[1:]).parts[1:]
        cursor = components
        for index, part in enumerate(parts[:-1]):
            following = parts[index + 1]
            if isinstance(cursor, list):
                while len(cursor) <= int(part):
                    cursor.append({})
                cursor = cursor[int(part)]
            else:
                cursor = cursor.setdefault(part, [] if following.isdigit() else {})
        value = deepcopy(resolve_pointer({'components': original}, reference[1:]))
        if isinstance(cursor, list):
            while len(cursor) <= int(parts[-1]):
                cursor.append({})
            cursor[int(parts[-1])] = value
        else:
            cursor[parts[-1]] = value
    security_names = set()
    for item in document.get('paths', {}).values():
        for method, operation in item.items():
            if method in METHODS:
                for requirement in operation.get('security', document.get('security', [])):
                    security_names.update(requirement)
    if security_names:
        components['securitySchemes'] = {name: original.get('securitySchemes', {})[name] for name in sorted(security_names)}
    if components:
        document['components'] = components
    else:
        document.pop('components', None)


def admit_openapi(source):
    """Return safe source, executable definition and per-operation exclusion reasons."""
    try:
        document = yaml.safe_load(source) if isinstance(source, str) else deepcopy(source)
    except Exception:
        raise BrowserError("invalid_openapi") from None
    if not isinstance(document, dict) or not str(document.get('openapi', '')).startswith(('3.0.', '3.1.')):
        raise BrowserError('Supply an OpenAPI 3.0 or 3.1 definition.')
    safe = scrub_document(document)
    safe['info'] = {'title': 'Service API', 'version': '1.0.0'}
    safe.pop('externalDocs', None)
    executable = deepcopy(safe)
    executable['paths'] = {}
    exclusions = []
    for path, path_item in sorted(safe.get('paths', {}).items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if method not in METHODS:
                continue
            reason = None
            try:
                if not isinstance(operation, dict):
                    raise ValueError('invalid_contract')
                security_names = {name for requirement in operation.get('security', safe.get('security', [])) for name in requirement}
                security_schemes = {name: safe.get('components', {}).get('securitySchemes', {})[name] for name in security_names}
                if sensitive(path):
                    raise ValueError('sensitive_structure')
                walk_contract(safe, {'parameters': path_item.get('parameters', []), 'operation': operation,
                                     'servers': safe.get('servers', []), 'securitySchemes': security_schemes})
                for server in safe.get('servers', []):
                    parsed = urlsplit(server.get('url', ''))
                    if parsed.username or parsed.password or parsed.query or parsed.fragment or server.get('variables'):
                        raise ValueError('unsupported_destination')
                if operation.get('servers') or path_item.get('servers'):
                    raise ValueError('unsupported_destination')
                request_features(safe, path_item, operation)
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
                reason = str(error) if str(error) in {'external_reference', 'unresolved_reference', 'sensitive_structure', 'unsupported_serialization', 'unsupported_callbacks', 'unsupported_destination', 'unsupported_schema_reference'} else 'invalid_contract'
            if reason:
                exclusions.append({'operation': operation.get('operationId') if isinstance(operation, dict) and not sensitive(operation.get('operationId')) else None,
                                   'method': method.upper(), 'path': path if not sensitive(path) else '[omitted]', 'reason': reason})
                continue
            selected = executable['paths'].setdefault(path, {key: deepcopy(value) for key, value in path_item.items() if key not in METHODS})
            selected[method] = deepcopy(operation)
    # Excluded sensitive declarations may not be retained even as "source".
    for path in list(safe.get('paths', {})):
        if sensitive(path):
            del safe['paths'][path]
    safe = _remove_sensitive_source(safe)
    used_tags = {tag for item in executable["paths"].values() for method, operation in item.items() if method in METHODS for tag in operation.get("tags", [])}
    if "tags" in executable:
        executable["tags"] = [tag for tag in executable["tags"] if tag.get("name") in used_tags and not sensitive(tag.get("name"))]
    prune_components(executable)
    if executable['paths']:
        openapi_document(executable)
    return safe, executable, exclusions


def _remove_sensitive_source(value):
    if isinstance(value, dict):
        return {key: _remove_sensitive_source(item) for key, item in value.items() if not sensitive(key) and not sensitive(item)}
    if isinstance(value, list):
        return [_remove_sensitive_source(item) for item in value if not sensitive(item)]
    return value
