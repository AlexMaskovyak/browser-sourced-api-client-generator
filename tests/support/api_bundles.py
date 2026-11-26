"""Build API manifests from the shared synthetic API definitions."""

import json
from pathlib import Path

from website_client.discovery_manifest import (
    artifact,
    encoded,
    new_bundle,
    service,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def graphql_bundle(
    endpoint="https://example.test/graphql", *, schema=True, operations=True
):
    artifacts = []
    definition = (
        artifact(
            "schema.graphql",
            "graphql-schema",
            (FIXTURES / "schema.graphql").read_text(),
            "external",
        )
        if schema
        else None
    )
    document = (
        artifact(
            "operations.graphql",
            "graphql-operations",
            (FIXTURES / "operations.graphql").read_text(),
            "external",
        )
        if operations
        else None
    )
    artifacts += [item for item in (definition, document) if item]
    return new_bundle(
        artifacts,
        [
            service(
                "graphql",
                endpoint,
                definition["id"] if definition else None,
                [document["id"]] if document else [],
            )
        ],
    )


def openapi_bundle(*, inferred=False, paths=None):
    spec = json.loads((FIXTURES / "openapi.json").read_text())
    if paths is not None:
        spec["paths"] = paths
    if inferred:
        spec["x-discovery-inferred"] = True
    item = artifact(
        "openapi.json", "openapi", encoded(spec), "inferred" if inferred else "external"
    )
    return new_bundle(
        [item],
        [
            service(
                "openapi",
                "https://example.test",
                item["id"],
                confidence="inferred" if inferred else "external",
            )
        ],
    )
