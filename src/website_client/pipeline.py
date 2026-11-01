"""Offline command orchestration for interoperable discovery producers."""

from pathlib import Path
import sys
import tempfile

from .browser import BrowserError
from .discovery_manifest import (
    artifact,
    digest,
    encoded,
    endpoint_url,
    merge_bundles,
    new_bundle,
    read_bundle,
    read_text,
    service,
    write_bundle,
)
from .definitions import MAX_DOCUMENT, graphql_schema, openapi_document
from .discovery_manifest.model import MAX_BUNDLE_BYTES
from .normalization import choose_service, normalize_bundle


def add_commands(commands, generate_command, definition):
    definition.add_argument(
        "--manifest",
        "--bundle",
        dest="bundle",
        help="Discovery manifest JSON file, directory containing manifest.json, or '-' for stdin. --bundle is an alias.",
    )
    generate_command.add_argument(
        "--service", help="Service ID when more than one API is generation-ready."
    )
    generate_command.add_argument(
        "--allow-inferred",
        action="store_true",
        help="Generate a reviewed inferred OpenAPI contract.",
    )
    normalize = commands.add_parser(
        "normalize",
        help="Validate and combine discovery manifests without opening a browser.",
        description="Validate definitions and assign generation status to each API. Reads discovery manifests and writes one manifest JSON document. Sends no API requests; blocked APIs remain in the output for inspection.",
    )
    normalize.add_argument(
        "--input",
        action="append",
        help="Manifest file/directory or '-'; repeat to combine inputs. Default stdin.",
    )
    normalize.add_argument(
        "--output", default="-", help="New normalized JSON file; default stdout."
    )
    normalize.add_argument(
        "--bind-schema",
        action="append",
        default=[],
        help="Assign a schema document with SERVICE_ID=DOCUMENT_ID_OR_NAME. IDs and names are in the manifest.",
    )
    normalize.add_argument(
        "--bind-operations",
        action="append",
        default=[],
        help="Assign GraphQL operations with SERVICE_ID=DOCUMENT_ID_OR_NAME. IDs and names are in the manifest.",
    )
    imported = commands.add_parser(
        "import",
        help="Create a discovery manifest from another tool's API definition.",
        description="Read a local OpenAPI definition or GraphQL schema and write manifest JSON for normalization and client generation. Sends no API requests. Use --endpoint for GraphQL or OpenAPI without absolute server URLs.",
    )
    source = imported.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--openapi", help="Local OpenAPI JSON/YAML file, or '-' for stdin."
    )
    source.add_argument(
        "--graphql-schema",
        help="GraphQL SDL/introspection JSON file, or '-' for stdin.",
    )
    imported.add_argument(
        "--operations",
        help="Optional GraphQL operation file, or '-' for stdin. Required for generation, not schema-only import.",
    )
    imported.add_argument(
        "--endpoint",
        help="HTTPS API base URL or GraphQL endpoint. Required for GraphQL; overrides OpenAPI server selection.",
    )
    imported.add_argument(
        "--service-id",
        help="Stable service ID; default derived from protocol and endpoint.",
    )
    imported.add_argument(
        "--producer",
        default="external-definition",
        help="Name of the upstream producer.",
    )
    imported.add_argument(
        "--producer-version",
        default="unspecified",
        help="Upstream producer version or release identifier.",
    )
    imported.add_argument(
        "--output", default="-", help="New manifest JSON file; default stdout."
    )


def import_definition(options):
    source_path = options.openapi or options.graphql_schema
    if options.operations and options.openapi:
        raise BrowserError("--operations is only used with GraphQL.")
    if source_path == "-" and options.operations == "-":
        raise BrowserError("Only one imported document can use stdin.")
    source = read_text(source_path, MAX_DOCUMENT)
    producer = {"name": options.producer, "version": options.producer_version}
    if options.openapi:
        spec = openapi_document(source)
        endpoints = (
            [options.endpoint]
            if options.endpoint
            else [item["url"] for item in spec.get("servers", [])]
        )
        if not endpoints:
            raise BrowserError(
                "Supply --endpoint when the OpenAPI document has no absolute server URL."
            )
        if options.service_id and len(endpoints) != 1:
            raise BrowserError("Use --endpoint to select one server with --service-id.")
        endpoints = [endpoint_url(value) for value in endpoints]
        item = artifact(
            "imported-openapi.yaml",
            "openapi",
            source,
            "external",
            locations=endpoints,
            producer=producer,
        )
        return new_bundle(
            [item],
            [
                service("openapi", endpoint, item["id"], identifier=options.service_id)
                for endpoint in endpoints
            ],
            producer,
        )
    if not options.endpoint:
        raise BrowserError(
            "GraphQL import requires --endpoint for schema/operation association."
        )
    endpoint = endpoint_url(options.endpoint)
    graphql_schema(source)
    schema = artifact(
        "imported-schema.graphql",
        "graphql-schema",
        source,
        "external",
        locations=[endpoint],
        producer=producer,
    )
    artifacts, documents = [schema], []
    if options.operations:
        document = artifact(
            "imported-operations.graphql",
            "graphql-operations",
            read_text(options.operations, MAX_DOCUMENT),
            "external",
            locations=[endpoint],
            producer=producer,
        )
        artifacts.append(document)
        documents.append(document["id"])
    return new_bundle(
        artifacts,
        [
            service(
                "graphql",
                endpoint,
                schema["id"],
                documents,
                identifier=options.service_id,
            )
        ],
        producer,
    )


def run_offline(options):
    if options.command == "import":
        value = import_definition(options)
    else:
        sources = options.input or ["-"]
        if len(sources) > 20:
            raise BrowserError("Merge at most 20 bundles totaling at most 64 MiB.")
        if sources.count("-") > 1:
            raise BrowserError("Only one bundle input can use stdin.")
        inputs, remaining = [], MAX_BUNDLE_BYTES
        for source in sources:
            document = read_bundle(source, limit=remaining)
            remaining -= len(encoded(document).encode("utf-8"))
            inputs.append(document)
        value = normalize_bundle(
            merge_bundles(inputs),
            bind_schema=options.bind_schema,
            bind_operations=options.bind_operations,
        )
        summary = "; ".join(
            item["id"]
            + " ["
            + item["protocol"]
            + " "
            + item["endpoint"]
            + "]:"
            + item["status"]
            for item in value["services"]
        )
        print("Normalized services: " + (summary or "none"), file=sys.stderr)
    write_bundle(value, options.output)


def generate_bundle(source, output, name, identifier=None, allow_inferred=False):
    from .generation import generate

    original = read_bundle(source)
    normalized = normalize_bundle(original)
    target = choose_service(normalized, identifier, allow_inferred=allow_inferred)
    artifacts = {item["id"]: item for item in normalized["artifacts"]}
    schema = artifacts[target["schemaArtifact"]]
    operations = artifacts.get(target.get("compiledOperationsArtifact"))
    lineage = {
        "inputBundleSha256": digest(original),
        "normalizedBundleSha256": digest(normalized),
        "producer": original["producer"],
        "sourceBundles": normalized.get("sourceBundles", []),
        "bundleHashFormat": "website-client-canonical-json-v1",
        "service": {
            key: target[key]
            for key in ("id", "protocol", "endpoint", "confidence", "status")
        },
        "schemaArtifact": {
            "id": schema["id"],
            "sha256": schema["sha256"],
            "provenance": schema["provenance"],
        },
        "sourceSchemaArtifact": {
            key: artifacts[target["sourceSchemaArtifact"]][key]
            for key in ("id", "sha256", "provenance")
        },
        "operationArtifacts": [
            {
                "id": reference,
                "sha256": artifacts[reference]["sha256"],
                "provenance": artifacts[reference]["provenance"],
            }
            for reference in target["operationArtifacts"]
        ],
        "unassignedArtifacts": normalized["normalization"]["unassignedArtifacts"],
        "otherServices": [
            {"id": item["id"], "status": item["status"]}
            for item in normalized["services"]
            if item["id"] != target["id"]
        ],
    }
    print(
        "Generating service "
        + target["id"]
        + "; unassigned contract artifacts: "
        + str(len(lineage["unassignedArtifacts"])),
        file=sys.stderr,
    )
    with tempfile.TemporaryDirectory(prefix="website-client-contract-") as directory:
        root = Path(directory)
        schema_path = root / "schema"
        schema_path.write_text(schema["content"], encoding="utf-8")
        if target["protocol"] == "openapi":
            return generate(output, name, openapi=schema_path, lineage=lineage)
        operations_path = root / "operations.graphql"
        operations_path.write_text(operations["content"], encoding="utf-8")
        return generate(
            output,
            name,
            schema=schema_path,
            operations=operations_path,
            lineage=lineage,
        )
