"""Build the portable handoff from discovery's evidence and exported files."""

import json
from urllib.parse import urljoin

from .browser import BrowserError
from .bundle import (
    artifact,
    new_bundle,
    observed_endpoint,
    service,
    sha256,
    validate_bundle,
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


def capture_bundle(discovery, files):
    artifacts, targets = {}, {}
    gql_endpoints = locations(discovery.graphql_endpoints)
    gql_schemas, operations = [], []
    for name, content in files.items():
        source, sources = "report", []
        if name.startswith("openapi-"):
            kind = "openapi"
            spec = openapi_document(content)
            inferred = bool(spec.get("x-discovery-inferred"))
            source = "inferred" if inferred else "downloaded"
            canonical = json.dumps(spec, indent=2) + "\n"
            sources = locations(
                discovery.definition_sources.get(("openapi", sha256(canonical)), ())
            )
        elif name.startswith("graphql-schema-"):
            kind, source = "graphql-schema", "downloaded"
            sources = locations(
                discovery.definition_sources.get(
                    (kind, sha256(graphql_schema(content))), ()
                )
            )
        elif name.startswith("operations") and name.endswith(".graphql"):
            kind = "graphql-operations"
            key = sha256(content.rstrip("\n"))
            sources = locations(discovery.document_sources.get(key, ()))
            declarations = locations(discovery.document_declarations.get(key, ()))
            source = "captured" if sources else "frontend"
        else:
            kind = "har" if name.endswith(".har") else "report"
        item = artifact(name, kind, content, source, locations=sources)
        if kind == "graphql-operations" and declarations:
            item["provenance"]["locations"] = sorted(set(sources + declarations))
        artifacts[item["id"]] = item
        if kind == "openapi":
            # Published server URLs own the API address; relative URLs require
            # the observed specification location. Unresolved definitions stay
            # in the bundle for explicit binding instead of guessed endpoints.
            bases = sources or [""]
            for server in spec.get("servers", [{"url": "/"}]):
                for base in bases:
                    try:
                        endpoint = observed_endpoint(urljoin(base, server["url"]))
                        target = service(
                            "openapi",
                            endpoint,
                            item["id"],
                            confidence="inferred" if inferred else "published",
                        )
                    except (BrowserError, KeyError, ValueError):
                        continue
                    prior = targets.get(target["id"])
                    if prior and prior["schemaArtifact"] != target["schemaArtifact"]:
                        if prior["confidence"] == "inferred" and not inferred:
                            targets[target["id"]] = target
                        elif not inferred and prior["confidence"] != "inferred":
                            # Preserve both versions as explicit choices.
                            target["id"] += "-" + item["sha256"][:8]
                            targets[target["id"]] = target
                    else:
                        targets[target["id"]] = target
        elif kind == "graphql-schema":
            gql_schemas.append((item, sources))
        elif kind == "graphql-operations" and name != "operations.graphql":
            operations.append((item, sources))
    for endpoint in gql_endpoints:
        schemas = [item for item, sources in gql_schemas if endpoint in sources]
        if not schemas:
            schemas = [None]
        for schema in schemas:
            bound = [item["id"] for item, sources in operations if endpoint in sources]
            target = service(
                "graphql",
                endpoint,
                schema["id"] if schema else None,
                bound,
                "published" if schema else "observed",
            )
            if len(schemas) > 1:
                target["id"] += "-" + schema["sha256"][:8]
            targets[target["id"]] = target
    individual_ids = [item["id"] for item, _sources in operations]
    for item in artifacts.values():
        if item["name"] == "operations.graphql":
            item["provenance"]["derivedFrom"] = individual_ids
    return validate_bundle(new_bundle(artifacts.values(), targets.values()))
