"""Privacy boundaries for supplied definitions, manifests and portable reports."""

from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from graphql import parse, print_ast, visit, separate_operations
from graphql.language import DocumentNode, Node, Visitor

from .admission import admit_openapi, sensitive, scrub_document
from .browser import BrowserError
from .discovery_manifest.model import endpoint_url, sha256
from .discovery_manifest.records import (
    Artifact,
    Kind,
    Manifest,
    Producer,
    Provenance,
    Service,
    Source,
)
from .graphql_admission import safe_schema

DefinitionKey = tuple[str, str]
HASH = re.compile(r"[a-f0-9]{64}\Z")
ARTIFACT_ID = re.compile(r"artifact-[a-f0-9]{64}\Z")
REPORT_REASONS = frozenset(
    {
        "unsupported_serialization",
        "external_reference",
        "unresolved_reference",
        "sensitive_structure",
        "unsupported_callbacks",
        "unsupported_destination",
        "invalid_contract",
        "upstream_generation_warning",
        "unsupported_selection",
        "unsupported_abstract_selection",
        "ambiguous_native_name",
        "unknown_request_body",
        "cookie_origin_isolation",
        "har_dependency_required",
        "har_inference_failed",
        "response_body_unknown",
        "unsupported_subscription",
        "unnamed_operation",
        "conflicting_operation",
        "privacy_or_capture_omission",
        "upstream_inference_warning",
        "upstream_leak_detected",
        "conflicting_contracts",
        "conflicting_artifact_identity",
        "unresolved_source",
        "ambiguous_source",
        "invalid_source_binding",
        "invalid_workflow_structure",
        "unsupported_workflow_structure",
        "unresolved_source_destination",
        "ambiguous_workflow_namespace",
    }
)
REPORT_TEXT_FIELDS = frozenset(
    {
        "method",
        "operation",
        "path",
        "workflow",
        "service",
        "level",
        "type",
        "backend",
        "version",
        "artifact",
    }
)


class _FragmentSpreads(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []

    def enter_fragment_spread(self, node: Node, *_: Any) -> None:
        self.names.append(node.name.value)


def _index_definitions(
    document: DocumentNode,
) -> tuple[dict[DefinitionKey, Node], set[DefinitionKey]]:
    definitions, conflicts = {}, set()
    for item in document.definitions:
        if item.name is None:
            continue
        key = (item.kind, item.name.value)
        if key in definitions and print_ast(definitions[key]) != print_ast(item):
            conflicts.add(key)
        definitions[key] = item
    return definitions, conflicts


def _unsafe_definitions(
    definitions: dict[DefinitionKey, Node], conflicts: set[DefinitionKey]
) -> set[DefinitionKey]:
    unsafe = {
        key for key, item in definitions.items() if sensitive(print_ast(item))
    } | conflicts
    changed = True
    while changed:
        changed = False
        for key, item in definitions.items():
            spreads = _FragmentSpreads()
            visit(item, spreads)
            if key not in unsafe and any(
                ("fragment_definition", name) in unsafe for name in spreads.names
            ):
                unsafe.add(key)
                changed = True
    return unsafe


def _operation_reason(
    item: Node, conflicts: set[DefinitionKey], unsafe: set[DefinitionKey]
) -> str | None:
    name = item.name.value if item.name else None
    key = (item.kind, name)
    if name is None:
        return "unnamed_operation"
    if key in conflicts:
        return "conflicting_operation"
    if key in unsafe:
        return "sensitive_structure"
    if item.operation.value == "subscription":
        return "unsupported_subscription"
    return None


def _filter_operations(
    document: DocumentNode, conflicts: set[DefinitionKey], unsafe: set[DefinitionKey]
) -> list[dict]:
    exclusions = []
    for item in document.definitions:
        if item.kind != "operation_definition":
            continue
        reason = _operation_reason(item, conflicts, unsafe)
        if reason:
            name = item.name.value if item.name else None
            exclusions.append(
                {
                    "operation": "[omitted]" if sensitive(name) else name,
                    "reason": reason,
                }
            )
            if name is not None:
                unsafe.add((item.kind, name))
    return exclusions


def _remove_orphan_fragments(document: DocumentNode) -> None:
    # Native GraphQL dependency separation preserves only fragments reachable from
    # admitted operations. A removed subscription cannot invalidate a safe query.
    reachable = set()
    for operation in separate_operations(document).values():
        reachable.update(
            (node.kind, node.name.value)
            for node in operation.definitions
            if node.name is not None
        )
    document.definitions = tuple(
        node
        for node in document.definitions
        if (node.kind, node.name.value) in reachable
    )


def safe_operations(
    source: str, with_exclusions: bool = False
) -> str | tuple[str, list[dict]]:
    """Exclude unsafe operations and dependent fragments without rewriting wire names."""
    try:
        document = parse(source)
    except Exception:
        raise BrowserError("invalid_graphql_operations") from None
    definitions, conflicts = _index_definitions(document)
    unsafe = _unsafe_definitions(definitions, conflicts)
    exclusions = _filter_operations(document, conflicts, unsafe)
    document.definitions = tuple(
        item for key, item in definitions.items() if key not in unsafe
    )
    _remove_orphan_fragments(document)
    text = print_ast(document) + "\n"
    return (text, exclusions) if with_exclusions else text


def _workflow_item(item: dict) -> dict:
    item = deepcopy(item)
    item.pop("description", None)
    item.pop("summary", None)
    if "inputs" in item:
        item["inputs"] = scrub_document(item["inputs"])
    for step in item.get("steps", []):
        step.pop("description", None)
    return item


def _workflow_sources(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        selected = {key: item.get(key) for key in ("name", "type", "url")}
        portable = selected.get("url")
        portable_reference = isinstance(portable, str) and re.fullmatch(
            r"sources/artifact-[a-f0-9]{64}\.json", portable
        )
        inspected = (
            {key: value for key, value in selected.items() if key != "url"}
            if portable_reference
            else selected
        )
        if not sensitive(json.dumps(inspected)):
            result.append({key: item[key] for key in selected if key in item})
    return result


def safe_workflow(value: dict[str, Any]) -> dict[str, Any]:
    """Retain explicit safe workflow evidence without evaluating or executing it."""
    value = deepcopy(value)
    if value.get("arazzo") != "1.0.0":
        raise BrowserError("unsupported_workflow_version")
    if set(value) - {
        "arazzo",
        "info",
        "sourceDescriptions",
        "workflows",
        "components",
        "x-discovery-exclusions",
    }:
        raise BrowserError("unsupported_workflow_structure")
    if "info" in value:
        value["info"] = {"title": "Explicit workflows", "version": "1.0.0"}
    prior = value.pop("x-discovery-exclusions", [])
    excluded = (
        [
            {"workflow": "[omitted]", "reason": "sensitive_structure"}
            for record in prior
            if isinstance(record, dict)
            and record.get("reason") == "sensitive_structure"
        ]
        if isinstance(prior, list)
        else []
    )
    workflows = []
    for original in value.get("workflows", []):
        item = _workflow_item(original)
        if sensitive(json.dumps(item)):
            excluded.append({"workflow": "[omitted]", "reason": "sensitive_structure"})
        else:
            workflows.append(item)
    value["workflows"] = workflows
    value["sourceDescriptions"] = _workflow_sources(value.get("sourceDescriptions", []))
    if "components" in value and sensitive(json.dumps(value["components"])):
        value.pop("components")
        value["workflows"] = []
        excluded.append({"workflow": "[omitted]", "reason": "sensitive_structure"})
    if excluded:
        value["x-discovery-exclusions"] = excluded
    return value


def safe_location(value: str) -> str | None:
    """Retain only a fixed non-sensitive endpoint, with query and fragment removed."""
    try:
        parsed = urlsplit(value)
        if sensitive(parsed.path):
            return None
        return endpoint_url(urlunsplit(parsed._replace(query="", fragment="")))
    except (BrowserError, ValueError):
        return None


def _clean_report_fields(item: Any) -> Any:
    if isinstance(item, list):
        return [
            _clean_report_fields(element)
            for element in item
            if isinstance(element, (dict, list))
        ]
    if not isinstance(item, dict):
        return {}
    result = {}
    for key, element in item.items():
        if (
            key in {"sourceSha256", "sanitizedSha256"}
            and isinstance(element, str)
            and HASH.fullmatch(element)
        ):
            result[key] = element
        elif key in {"exclusions", "warnings", "leaks", "diagnostics"} and isinstance(
            element, list
        ):
            result[key] = _clean_report_fields(element)
        elif key == "reason" and element in REPORT_REASONS:
            result[key] = element
        elif key in REPORT_TEXT_FIELDS and isinstance(element, str):
            if not sensitive(element) or (
                key == "artifact" and ARTIFACT_ID.fullmatch(element)
            ):
                result[key] = element
        elif key == "count" and type(element) is int:
            result[key] = element
    return result


def _report_profiles(value: dict) -> dict[str, str]:
    from .runtime_profile import service_origin

    profiles = {}
    for api_origin, identifier in value["runtimeProfiles"].items():
        selected = service_origin(api_origin)
        expected = "origin_" + hashlib.sha256(selected.encode()).hexdigest()[:24]
        if identifier == expected:
            profiles[selected] = identifier
    return profiles


def _report_graphql_endpoints(items: list) -> list[dict]:
    from .discovery import route, FIELD

    endpoints = []
    for item in items:
        try:
            operation = item.get("graphql") or {}
            name = operation.get("name")
            if (
                not isinstance(name, str)
                or sensitive(name)
                or item.get("method") not in {"GET", "POST"}
            ):
                continue
            base, path, _ = route(item["origin"] + item["path"])
            parameters = [
                key
                for key in item.get("queryParameters", [])
                if isinstance(key, str) and FIELD.fullmatch(key) and not sensitive(key)
            ]
            endpoints.append(
                {
                    "origin": base,
                    "path": path,
                    "method": item["method"],
                    "queryParameters": parameters,
                    "graphql": {"name": name},
                }
            )
        except (AttributeError, KeyError, TypeError, ValueError, BrowserError):
            continue
    return endpoints


def safe_report(text: str) -> str:
    """Parse report data, retaining approved fields and protocol-specific evidence."""
    value = json.loads(text)
    if isinstance(value, dict) and "openapi" in value:
        return encoded_json(admit_openapi(value)[0])
    if isinstance(value, dict) and "runtimeProfiles" in value:
        return encoded_json({"runtimeProfiles": _report_profiles(value)})
    if isinstance(value, dict) and any(
        key in value for key in ("workflowBindings", "workflowBindingConflicts")
    ):
        from .workflow_binding import clean_binding_report, clean_binding_conflicts

        associations = {}
        if "workflowBindings" in value:
            associations["workflowBindings"] = clean_binding_report(
                value["workflowBindings"]
            )
        if "workflowBindingConflicts" in value:
            associations["workflowBindingConflicts"] = clean_binding_conflicts(
                value["workflowBindingConflicts"]
            )
        return encoded_json(associations)
    cleaned = _clean_report_fields(value)
    if isinstance(value, dict) and isinstance(value.get("endpoints"), list):
        cleaned["endpoints"] = _report_graphql_endpoints(value["endpoints"])
    return encoded_json(cleaned)


def encoded_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2) + "\n"


def safe_ui_report(value: Any) -> Any:
    """Remove known sensitive values from portable exploration reports."""
    if isinstance(value, dict):
        return {
            key: safe_ui_report(item)
            for key, item in value.items()
            if not sensitive(key)
        }
    if isinstance(value, list):
        return [safe_ui_report(item) for item in value]
    return "[omitted]" if sensitive(value) else value


def _safe_producer(producer: Producer) -> Producer:
    if sensitive(producer.name + producer.version):
        return Producer("external-input", "unspecified")
    return producer


def _safe_identifiers(artifacts: tuple[Artifact, ...]) -> dict[str, str]:
    return {
        item.id: "artifact-" + sha256(item.id)
        if sensitive(item.id) and not ARTIFACT_ID.fullmatch(item.id)
        else item.id
        for item in artifacts
    }


def _safe_locations(locations: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted({location for raw in locations if (location := safe_location(raw))})
    )


def _safe_provenance(provenance: Provenance, identifiers: dict[str, str]) -> Provenance:
    return replace(
        provenance,
        derived_from=tuple(
            identifiers[reference] for reference in provenance.derived_from
        ),
        locations=_safe_locations(provenance.locations),
        producers=tuple(_safe_producer(producer) for producer in provenance.producers),
    )


def _openapi_content(
    item: Artifact, index: int, identifier: str
) -> tuple[str, list[Artifact], list[dict]]:
    safe, executable, exclusions = admit_openapi(item.content)
    text = encoded_json(executable if executable["paths"] else safe)
    if not exclusions:
        return text, [], []
    source = Artifact.create(
        f"source-input-{index:04d}.json", Kind.REPORT, encoded_json(safe), Source.REPORT
    )
    return text, [source], [{"artifact": identifier, "exclusions": exclusions}]


def _graphql_content(
    item: Artifact, identifier: str
) -> tuple[str, list[Artifact], list[dict]]:
    if item.kind is Kind.GRAPHQL_SCHEMA:
        text, exclusions = safe_schema(item.content)
    else:
        text, exclusions = safe_operations(item.content, with_exclusions=True)
    audit = [{"artifact": identifier, "exclusions": exclusions}] if exclusions else []
    return text, [], audit


def _har_content(text: str) -> str:
    from .discovery import Discovery

    discovery = Discovery()
    for entry in json.loads(text)["log"]["entries"]:
        discovery.add(entry)
    return discovery.files("har")["capture.har"]


def _artifact_content(
    item: Artifact, index: int, identifier: str
) -> tuple[str, list[Artifact], list[dict]]:
    if item.kind is Kind.OPENAPI:
        return _openapi_content(item, index, identifier)
    if item.kind in {Kind.GRAPHQL_SCHEMA, Kind.GRAPHQL_OPERATIONS}:
        return _graphql_content(item, identifier)
    if item.kind is Kind.HAR:
        return _har_content(item.content), [], []
    if item.kind is Kind.ARAZZO:
        return encoded_json(safe_workflow(json.loads(item.content))), [], []
    return safe_report(item.content), [], []


def _sanitized_artifact(
    item: Artifact, index: int, identifiers: dict[str, str]
) -> tuple[list[Artifact], list[dict]]:
    kind, identifier = item.kind, identifiers[item.id]
    try:
        text, additional, audit = _artifact_content(item, index, identifier)
    except Exception:
        # Parsers and validators can echo private source snippets. Demote invalid
        # evidence to a bounded report without retaining exception text or chains.
        kind, text = Kind.REPORT, '{"reason":"unsupported_private_or_invalid_source"}\n'
        additional, audit = [], []
    digest = sha256(text)
    name = f"input-{index:04d}.{kind.value}" if sensitive(item.name) else item.name
    result = replace(
        item,
        id=identifier,
        name=name,
        kind=kind,
        content=text,
        sha256=digest,
        provenance=_safe_provenance(item.provenance, identifiers),
    )
    audit.append(
        {"artifact": identifier, "sourceSha256": item.sha256, "sanitizedSha256": digest}
    )
    return [*additional, result], audit


def _safe_service(target: Service, identifiers: dict[str, str]) -> Service:
    return replace(
        target,
        id="service-" + sha256(target.id)[:16] if sensitive(target.id) else target.id,
        schema_artifact=identifiers.get(target.schema_artifact),
        source_schema_artifact=identifiers.get(target.source_schema_artifact),
        operation_artifacts=tuple(
            identifiers[reference] for reference in target.operation_artifacts
        ),
        compiled_operations_artifact=identifiers.get(
            target.compiled_operations_artifact
        ),
        issues=(),
        operation_names=(),
        status=None,
    )


def _safe_services(
    manifest: Manifest, identifiers: dict[str, str]
) -> tuple[tuple[Service, ...], list[dict]]:
    services, audit = [], []
    for target in manifest.services:
        if safe_location(target.endpoint) == target.endpoint:
            services.append(_safe_service(target, identifiers))
        else:
            audit.append(
                {
                    "exclusions": [
                        {
                            "service": "service-" + sha256(target.id)[:16],
                            "reason": "unsupported_destination",
                        }
                    ]
                }
            )
    return tuple(services), audit


def sanitized_manifest(manifest: Manifest) -> Manifest:
    """Filter each artifact before persistence, retaining safe ownership and audit hashes.

    The returned content hashes describe sanitized bytes. Separate audit records
    retain original hashes without copying raw names, samples, or diagnostics.
    """
    identifiers = _safe_identifiers(manifest.artifacts)
    artifacts, audit = [], []
    for index, item in enumerate(manifest.artifacts):
        selected, records = _sanitized_artifact(item, index, identifiers)
        artifacts.extend(selected)
        audit.extend(records)
    services, service_audit = _safe_services(manifest, identifiers)
    audit.extend(service_audit)
    artifacts.append(
        Artifact.create(
            "privacy-audit.json",
            Kind.REPORT,
            json.dumps(audit, sort_keys=True) + "\n",
            Source.REPORT,
        )
    )
    bundles = tuple(
        replace(source, producer=_safe_producer(source.producer))
        for source in (manifest.source_bundles or ())
    )
    return replace(
        manifest,
        producer=_safe_producer(manifest.producer),
        artifacts=tuple(artifacts),
        services=services,
        source_bundles=bundles,
    )
