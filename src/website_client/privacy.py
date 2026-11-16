"""Privacy boundaries for supplied definitions, manifests and portable reports."""

from dataclasses import replace
from copy import deepcopy
import json
import re
import hashlib
from urllib.parse import urlsplit, urlunsplit

from graphql import parse, print_ast, visit, separate_operations
from graphql.language import Visitor

from .admission import admit_openapi, sensitive, scrub_document, _remove_sensitive_source
from .browser import BrowserError
from .graphql_admission import safe_schema


def safe_operations(source, with_exclusions=False):
    """Exclude sensitive named operations and their fragments without wire rewriting."""
    try:
        document = parse(source)
    except Exception:
        raise BrowserError("invalid_graphql_operations") from None
    definitions, conflicts = {}, set()
    for item in document.definitions:
        if item.name is None:
            continue
        key = (item.kind, item.name.value)
        if key in definitions and print_ast(definitions[key]) != print_ast(item):
            conflicts.add(key)
        definitions[key] = item
    unsafe = {key for key, item in definitions.items() if sensitive(print_ast(item))} | conflicts
    changed = True
    while changed:
        changed = False
        for key, item in definitions.items():
            spreads = []
            class Spreads(Visitor):
                def enter_fragment_spread(self, node, *_):
                    spreads.append(node.name.value)
            visit(item, Spreads())
            if key not in unsafe and any(("fragment_definition", name) in unsafe for name in spreads):
                unsafe.add(key)
                changed = True
    exclusions = []
    for item in document.definitions:
        if item.kind != 'operation_definition':
            continue
        name = item.name.value if item.name else None
        key = (item.kind, name)
        reason = ('unnamed_operation' if name is None else 'conflicting_operation' if key in conflicts
                  else 'sensitive_structure' if key in unsafe else 'unsupported_subscription' if item.operation.value == 'subscription' else None)
        if reason:
            exclusions.append({'operation': '[omitted]' if sensitive(name) else name, 'reason': reason})
            if name is not None:
                unsafe.add(key)
    document.definitions = tuple(item for key, item in definitions.items() if key not in unsafe)
    # Standard GraphQL dependency separation removes orphan fragments after exclusion.
    reachable = {(node.kind, node.name.value) for operation in separate_operations(document).values() for node in operation.definitions if node.name is not None}
    document.definitions = tuple(node for node in document.definitions if (node.kind, node.name.value) in reachable)
    # The existing GraphQL parser/compiler still owns fragments and variable semantics.
    text = print_ast(document) + '\n'
    return (text, exclusions) if with_exclusions else text


def safe_workflow(value):
    """Preserve explicit safe workflows for the restricted runner; never execute here."""
    value = deepcopy(value)
    if value.get('arazzo') != '1.0.0':
        raise BrowserError('unsupported_workflow_version')
    if set(value) - {'arazzo', 'info', 'sourceDescriptions', 'workflows', 'components'}:
        raise BrowserError('unsupported_workflow_structure')
    if 'info' in value:
        value['info'] = {'title': 'Explicit workflows', 'version': '1.0.0'}
    excluded, workflows = [], []
    for item in value.get('workflows', []):
        item = deepcopy(item)
        item.pop('description', None)
        item.pop('summary', None)
        if 'inputs' in item:
            item['inputs'] = scrub_document(item['inputs'])
        for step in item.get('steps', []):
            step.pop('description', None)
        if sensitive(json.dumps(item)):
            excluded.append({'workflow': '[omitted]', 'reason': 'sensitive_structure'})
        else:
            workflows.append(item)
    value['workflows'] = workflows
    # Source definitions may be retained only with non-sensitive fixed references.
    value['sourceDescriptions'] = [{key: item[key] for key in ('name', 'type', 'url') if key in item}
                                   for item in value.get('sourceDescriptions', []) if not sensitive(json.dumps({key: item.get(key) for key in ('name', 'type', 'url')}))]
    if 'components' in value and sensitive(json.dumps(value['components'])):
        value.pop('components')
        value['workflows'] = []
        excluded.append({'workflow': '[omitted]', 'reason': 'sensitive_structure'})
    if excluded:
        value['x-discovery-exclusions'] = excluded
    return value


def safe_location(value):
    from .discovery_manifest.model import endpoint_url
    try:
        parsed = urlsplit(value)
        if sensitive(parsed.path):
            return None
        return endpoint_url(urlunsplit(parsed._replace(query='', fragment='')))
    except (BrowserError, ValueError):
        return None


def safe_report(text):
    """Retain known reason records and verified hashes, never arbitrary source prose."""
    value = json.loads(text)
    reasons = {'unsupported_serialization', 'external_reference', 'unresolved_reference', 'sensitive_structure',
               'unsupported_callbacks', 'unsupported_destination', 'invalid_contract', 'upstream_generation_warning',
               'unsupported_selection', 'unsupported_abstract_selection', 'ambiguous_native_name', 'unknown_request_body',
               'cookie_origin_isolation', 'har_dependency_required', 'response_body_unknown', 'unsupported_subscription', 'unnamed_operation', 'conflicting_operation', 'privacy_or_capture_omission', 'upstream_inference_warning', 'upstream_leak_detected', 'conflicting_contracts'}
    if isinstance(value, dict) and 'openapi' in value:
        return encoded_json(admit_openapi(value)[0])
    if isinstance(value, dict) and 'runtimeProfiles' in value:
        from .runtime_profile import service_origin
        profiles = {}
        for api_origin, identifier in value['runtimeProfiles'].items():
            selected = service_origin(api_origin)
            if identifier == 'origin_' + hashlib.sha256(selected.encode()).hexdigest()[:24]:
                profiles[selected] = identifier
        return encoded_json({'runtimeProfiles': profiles})
    def clean(item):
        if isinstance(item, list):
            return [clean(element) for element in item if isinstance(element, (dict, list))]
        if not isinstance(item, dict):
            return {}
        result = {}
        for key, element in item.items():
            if key in {'sourceSha256', 'sanitizedSha256'} and isinstance(element, str) and re.fullmatch(r'[a-f0-9]{64}', element):
                result[key] = element
            elif key in {'exclusions', 'warnings', 'leaks', 'diagnostics'} and isinstance(element, list):
                result[key] = clean(element)
            elif key == 'reason' and element in reasons:
                result[key] = element
            elif key in {'method', 'operation', 'path', 'workflow', 'service', 'level', 'type', 'backend', 'version', 'artifact'} and isinstance(element, str) and (not sensitive(element) or key == 'artifact' and re.fullmatch(r'artifact-[a-f0-9]{64}', element)):
                result[key] = element
            elif key == 'count' and type(element) is int:
                result[key] = element
        return result
    cleaned = clean(value)
    if isinstance(value, dict) and isinstance(value.get('endpoints'), list):
        endpoints = []
        from .discovery import route, FIELD
        for item in value['endpoints']:
            try:
                operation = item.get('graphql') or {}
                name = operation.get('name')
                if not isinstance(name, str) or sensitive(name) or item.get('method') not in {'GET', 'POST'}:
                    continue
                base, path, _ = route(item['origin'] + item['path'])
                parameters = [key for key in item.get('queryParameters', []) if isinstance(key, str) and FIELD.fullmatch(key) and not sensitive(key)]
                endpoints.append({'origin': base, 'path': path, 'method': item['method'],
                                  'queryParameters': parameters, 'graphql': {'name': name}})
            except (AttributeError, KeyError, TypeError, ValueError, BrowserError):
                continue
        cleaned['endpoints'] = endpoints
    return encoded_json(cleaned)


def encoded_json(value):
    return json.dumps(value, sort_keys=True, indent=2) + '\n'


def safe_ui_report(value):
    if isinstance(value, dict):
        return {key: safe_ui_report(item) for key, item in value.items() if not sensitive(key)}
    if isinstance(value, list):
        return [safe_ui_report(item) for item in value]
    return '[omitted]' if sensitive(value) else value


def sanitized_manifest(manifest):
    """Replace unsafe content before any normalized/imported manifest is persisted.

    Content SHA values describe the safe content. A separate audit preserves the
    supplied content hashes without copying names, sample values or diagnostics.
    """
    from .discovery import Discovery
    from .discovery_manifest.records import Artifact, Kind, Producer, Source
    from .discovery_manifest.model import sha256
    artifacts, audit = [], []
    identifiers = {item.id: ("artifact-" + sha256(item.id) if sensitive(item.id) and not re.fullmatch(r"artifact-[a-f0-9]{64}", item.id) else item.id) for item in manifest.artifacts}
    for index, item in enumerate(manifest.artifacts):
        kind, text = item.kind, item.content
        reason = None
        try:
            if kind is Kind.OPENAPI:
                safe, executable, exclusions = admit_openapi(text)
                text = json.dumps(executable if executable['paths'] else safe, sort_keys=True, indent=2) + '\n'
                if exclusions:
                    audit.append({'artifact': identifiers[item.id], 'exclusions': exclusions})
                    artifacts.append(Artifact.create(f'source-input-{index:04d}.json', Kind.REPORT, encoded_json(safe), Source.REPORT))
            elif kind is Kind.GRAPHQL_SCHEMA:
                text, exclusions = safe_schema(text)
                if exclusions:
                    audit.append({'artifact': identifiers[item.id], 'exclusions': exclusions})
            elif kind is Kind.GRAPHQL_OPERATIONS:
                text, exclusions = safe_operations(text, with_exclusions=True)
                if exclusions:
                    audit.append({"artifact": identifiers[item.id], "exclusions": exclusions})
            elif kind is Kind.HAR:
                discovery = Discovery()
                value = json.loads(text)
                for entry in value['log']['entries']:
                    discovery.add(entry)
                text = discovery.files('har')['capture.har']
            elif kind is Kind.ARAZZO:
                text = json.dumps(safe_workflow(json.loads(text)), sort_keys=True, indent=2) + '\n'
            else:
                text = safe_report(text)
        except Exception:
            kind, text = Kind.REPORT, '{"reason":"unsupported_private_or_invalid_source"}\n'
        provenance = replace(item.provenance,
                             derived_from=tuple(identifiers[reference] for reference in item.provenance.derived_from),
                             locations=tuple(sorted({location for raw in item.provenance.locations if (location := safe_location(raw))})),
                             producers=tuple(Producer('external-input', 'unspecified') if sensitive(producer.name + producer.version) else producer for producer in item.provenance.producers))
        artifacts.append(replace(item, id=identifiers[item.id], name=(f'input-{index:04d}.{kind.value}' if sensitive(item.name) else item.name), kind=kind,
                                 content=text, sha256=sha256(text), provenance=provenance))
        audit.append({'artifact': identifiers[item.id], 'sourceSha256': item.sha256, 'sanitizedSha256': sha256(text)})
    producer = Producer('external-input', 'unspecified') if sensitive(manifest.producer.name + manifest.producer.version) else manifest.producer
    services = tuple(replace(target, id=("service-" + sha256(target.id)[:16] if sensitive(target.id) else target.id),
                             schema_artifact=identifiers.get(target.schema_artifact), source_schema_artifact=identifiers.get(target.source_schema_artifact),
                             operation_artifacts=tuple(identifiers[reference] for reference in target.operation_artifacts),
                             compiled_operations_artifact=identifiers.get(target.compiled_operations_artifact),
                             issues=(), operation_names=(), status=None) for target in manifest.services if safe_location(target.endpoint) == target.endpoint)
    for target in manifest.services:
        if safe_location(target.endpoint) != target.endpoint:
            audit.append({'exclusions': [{'service': 'service-' + sha256(target.id)[:16], 'reason': 'unsupported_destination'}]})
    artifacts.append(Artifact.create('privacy-audit.json', Kind.REPORT, json.dumps(audit, sort_keys=True) + '\n', Source.REPORT))
    return replace(manifest, producer=producer, artifacts=tuple(artifacts), services=services,
                   source_bundles=tuple(replace(source, producer=Producer("external-input", "unspecified")) if sensitive(source.producer.name + source.producer.version) else source for source in (manifest.source_bundles or ())))
