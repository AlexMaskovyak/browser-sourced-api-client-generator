"""Build the portable handoff from discovery's evidence and exported files."""

from dataclasses import replace
import json
from urllib.parse import urljoin

from .browser import BrowserError
from .discovery_manifest import observed_endpoint, sha256
from .discovery_manifest.records import (
    Artifact, Confidence, Kind, Manifest, Protocol, Service, Source,
)
from .definitions import openapi_document, graphql_schema


def locations(values):
    result = []
    for value in values:
        try:
            result.append(observed_endpoint(value))
        except (BrowserError, ValueError):
            continue
    return sorted(set(result))


def capture_manifest(discovery, files) -> Manifest:
    artifacts: dict[str, Artifact] = {}
    targets: dict[str, Service] = {}
    gql_endpoints = locations(discovery.graphql_endpoints)
    gql_schemas, operations = [], []
    for name, content in files.items():
        source, sources, declarations = Source.REPORT, [], []
        if name.startswith("openapi-"):
            kind = Kind.OPENAPI
            spec = openapi_document(content)
            inferred = bool(spec.get("x-discovery-inferred"))
            source = Source.INFERRED if inferred else Source.DOWNLOADED
            canonical = json.dumps(spec, indent=2) + "\n"
            sources = locations(
                discovery.definition_sources.get(("openapi", sha256(canonical)), ())
            )
        elif name.startswith("graphql-schema-"):
            kind, source = Kind.GRAPHQL_SCHEMA, Source.DOWNLOADED
            sources = locations(
                discovery.definition_sources.get(
                    (kind.value, sha256(graphql_schema(content))), ()
                )
            )
        elif name.startswith("operations") and name.endswith(".graphql"):
            kind = Kind.GRAPHQL_OPERATIONS
            key = sha256(content.rstrip("\n"))
            sources = locations(discovery.document_sources.get(key, ()))
            declarations = locations(discovery.document_declarations.get(key, ()))
            source = Source.CAPTURED if sources else Source.FRONTEND
        else:
            kind = Kind.HAR if name.endswith(".har") else Kind.REPORT
        item = Artifact.create(name, kind, content, source, locations=tuple(sources))
        if kind is Kind.GRAPHQL_OPERATIONS and declarations:
            item = replace(item, provenance=replace(
                item.provenance, locations=tuple(sorted(set(sources + declarations))),
            ))
        artifacts[item.id] = item
        if kind is Kind.OPENAPI:
            # Published server URLs own the API address; relative URLs require
            # the observed specification location. Unresolved definitions stay
            # in the bundle for explicit binding instead of guessed endpoints.
            bases = sources or [""]
            for server in spec.get("servers", [{"url": "/"}]):
                for base in bases:
                    try:
                        endpoint = observed_endpoint(urljoin(base, server["url"]))
                        target = Service.create(
                            Protocol.OPENAPI,
                            endpoint,
                            item.id,
                            confidence=Confidence.INFERRED if inferred else Confidence.PUBLISHED,
                        )
                    except (BrowserError, KeyError, ValueError):
                        continue
                    prior = targets.get(target.id)
                    if prior and prior.schema_artifact != target.schema_artifact:
                        if prior.confidence is Confidence.INFERRED and not inferred:
                            targets[target.id] = target
                        elif not inferred and prior.confidence is not Confidence.INFERRED:
                            # Preserve both versions as explicit choices.
                            target = replace(target, id=target.id + "-" + item.sha256[:8])
                            targets[target.id] = target
                    else:
                        targets[target.id] = target
        elif kind is Kind.GRAPHQL_SCHEMA:
            gql_schemas.append((item, sources))
        elif kind is Kind.GRAPHQL_OPERATIONS and name != "operations.graphql":
            operations.append((item, sources))
    for endpoint in gql_endpoints:
        schemas = [item for item, sources in gql_schemas if endpoint in sources]
        if not schemas:
            schemas = [None]
        for schema in schemas:
            bound = tuple(item.id for item, sources in operations if endpoint in sources)
            target = Service.create(
                Protocol.GRAPHQL,
                endpoint,
                schema.id if schema else None,
                bound,
                Confidence.PUBLISHED if schema else Confidence.OBSERVED,
            )
            if len(schemas) > 1:
                target = replace(target, id=target.id + "-" + schema.sha256[:8])
            targets[target.id] = target
    individual_ids = tuple(item.id for item, _sources in operations)
    for item in tuple(artifacts.values()):
        if item.name == "operations.graphql":
            artifacts[item.id] = replace(
                item, provenance=replace(item.provenance, derived_from=individual_ids),
            )
    return Manifest.create(tuple(artifacts.values()), tuple(targets.values()))


def capture_bundle(discovery, files):
    """JSON compatibility adapter for older capture consumers."""
    return capture_manifest(discovery, files).to_dict()
