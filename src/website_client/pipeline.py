"""Offline command orchestration for interoperable discovery producers."""

from pathlib import Path
import sys
import tempfile

from .browser import BrowserError
from .discovery_manifest import (
    digest, encoded, endpoint_url, merge_manifests, read_manifest, read_text, write_manifest,
)
from .discovery_manifest.records import (
    Artifact, Kind, Manifest, Producer, Protocol, Service, Source,
)
from .definitions import MAX_DOCUMENT, graphql_schema, openapi_document
from .discovery_manifest.model import MAX_BUNDLE_BYTES
from .normalization import normalize_manifest, select_service


def add_commands(commands, generate_command, definition):
    """Register import/normalize commands and manifest-based generation options."""
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
        help="Create a manifest from operations or an API definition.",
        description="Read OpenAPI, GraphQL operations, or an optional GraphQL schema and write manifest JSON. Sends no API requests. Use --endpoint for GraphQL or OpenAPI without absolute server URLs.",
    )
    source = imported.add_mutually_exclusive_group()
    source.add_argument(
        "--openapi", help="Local OpenAPI JSON/YAML file, or '-' for stdin."
    )
    source.add_argument(
        "--graphql-schema",
        help="GraphQL SDL/introspection JSON file, or '-' for stdin.",
    )
    imported.add_argument(
        "--operations",
        help="GraphQL operation file, or '-' for stdin. Can be supplied alone with --endpoint; a schema is optional.",
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


def import_definition(options) -> Manifest:
    """Convert local definitions into a portable manifest with explicit endpoint ownership.

    GraphQL operations alone are supported. A full schema is optional enrichment.
    Validate documents before emitting them, and allow only one stdin source."""
    if not (options.openapi or options.graphql_schema or options.operations):
        raise BrowserError("Supply --operations, --openapi or --graphql-schema.")
    if options.operations and not options.openapi and not options.graphql_schema:
        from .operation_contracts import compile_operations

        if not options.endpoint:
            raise BrowserError("Operation import requires --endpoint.")
        text = read_text(options.operations, MAX_DOCUMENT)
        try:
            compile_operations((text,))
        except ValueError as error:
            raise BrowserError(str(error)) from None
        producer = Producer(options.producer, options.producer_version)
        endpoint = endpoint_url(options.endpoint)
        document = Artifact.create(
            "imported-operations.graphql", Kind.GRAPHQL_OPERATIONS, text,
            Source.EXTERNAL, locations=(endpoint,), producer=producer,
        )
        return Manifest.create(
            (document,), (Service.create(Protocol.GRAPHQL, endpoint, operations=(document.id,)),),
            producer,
        )
    source_path = options.openapi or options.graphql_schema
    if options.operations and options.openapi:
        raise BrowserError("--operations is only used with GraphQL.")
    if source_path == "-" and options.operations == "-":
        raise BrowserError("Only one imported document can use stdin.")
    source = read_text(source_path, MAX_DOCUMENT)
    producer = Producer(options.producer, options.producer_version)
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
        item = Artifact.create(
            "imported-openapi.yaml",
            Kind.OPENAPI,
            source,
            Source.EXTERNAL,
            locations=tuple(endpoints),
            producer=producer,
        )
        return Manifest.create(
            (item,),
            tuple(Service.create(Protocol.OPENAPI, endpoint, item.id, identifier=options.service_id)
                  for endpoint in endpoints),
            producer,
        )
    if not options.endpoint:
        raise BrowserError(
            "GraphQL import requires --endpoint for schema/operation association."
        )
    endpoint = endpoint_url(options.endpoint)
    graphql_schema(source)
    schema = Artifact.create(
        "imported-schema.graphql",
        Kind.GRAPHQL_SCHEMA,
        source,
        Source.EXTERNAL,
        locations=(endpoint,),
        producer=producer,
    )
    artifacts, documents = (schema,), ()
    if options.operations:
        document = Artifact.create(
            "imported-operations.graphql",
            Kind.GRAPHQL_OPERATIONS,
            read_text(options.operations, MAX_DOCUMENT),
            Source.EXTERNAL,
            locations=(endpoint,),
            producer=producer,
        )
        artifacts += (document,)
        documents += (document.id,)
    return Manifest.create(
        artifacts,
        (Service.create(Protocol.GRAPHQL, endpoint, schema.id, documents),),
        producer,
    )


def run_offline(options):
    """Import or merge bounded manifest inputs and write one JSON document.

    Write progress to stderr so stdout can feed the next command unchanged. This
    path never opens a browser or sends service requests."""
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
            document = read_manifest(source, limit=remaining)
            remaining -= len(encoded(document.to_dict()).encode("utf-8"))
            inputs.append(document)
        value = normalize_manifest(
            merge_manifests(tuple(inputs)),
            bind_schema=options.bind_schema,
            bind_operations=options.bind_operations,
        )
        summary = "; ".join(
            f"{item.id} [{item.protocol.value} {item.endpoint}]:{item.status.value}"
            for item in value.services
        )
        print("Normalized services: " + (summary or "none"), file=sys.stderr)
    write_manifest(value, options.output)


def artifact_reference(document: Artifact):
    """Serialize selected source metadata into the generation report."""
    return {
        "id": document.id, "sha256": document.sha256,
        "provenance": document.provenance.to_dict(),
    }


def generate_bundle(source, output, name, identifier=None, allow_inferred=False):
    """Normalize saved inputs, select one service and dispatch its SDK backend.

    Without a GraphQL schema, compile captured operation documents directly. With
    a full schema, use the schema-backed generator. Preserve source hashes in the
    generation report; refuse unusable or ambiguous services before publication."""
    from .generation import generate

    original = read_manifest(source)
    normalized = normalize_manifest(original)
    target = select_service(normalized, identifier, allow_inferred=allow_inferred)
    artifacts = {item.id: item for item in normalized.artifacts}
    schema = artifacts.get(target.schema_artifact)
    operations = artifacts.get(target.compiled_operations_artifact)
    if normalized.normalization is None:
        raise BrowserError("Selected manifest has no normalization result.")
    lineage = {
        "inputBundleSha256": digest(original.to_dict()),
        "normalizedBundleSha256": digest(normalized.to_dict()),
        "producer": original.producer.to_dict(),
        "sourceBundles": [item.to_dict() for item in normalized.source_bundles or ()],
        "bundleHashFormat": "website-client-canonical-json-v1",
        "service": {
            "id": target.id, "protocol": target.protocol.value,
            "endpoint": target.endpoint, "confidence": target.confidence.value,
            "status": target.status.value,
        },
        "schemaArtifact": artifact_reference(schema) if schema else None,
        "sourceSchemaArtifact": (
            artifact_reference(artifacts[target.source_schema_artifact])
            if target.source_schema_artifact else None
        ),
        "operationArtifacts": [artifact_reference(artifacts[ref]) for ref in target.operation_artifacts],
        "unassignedArtifacts": list(normalized.normalization.unassigned_artifacts),
        "otherServices": [
            {"id": item.id, "status": item.status.value}
            for item in normalized.services if item.id != target.id
        ],
    }
    print(
        "Generating service " + target.id + "; unassigned contract artifacts: "
        + str(len(normalized.normalization.unassigned_artifacts)),
        file=sys.stderr,
    )
    if target.protocol is Protocol.GRAPHQL and schema is None:
        from .operation_generation import generate_operation_client

        return generate_operation_client(original, target, output, name, lineage)
    if schema is None:
        raise BrowserError("Selected API has no usable HTTP definition.")
    with tempfile.TemporaryDirectory(prefix="website-client-contract-") as directory:
        root = Path(directory)
        schema_path = root / "schema"
        schema_path.write_text(schema.content, encoding="utf-8")
        if target.protocol is Protocol.OPENAPI:
            return generate(output, name, openapi=schema_path, lineage=lineage)
        if operations is None:
            raise BrowserError("Selected GraphQL API has no validated operation document.")
        operations_path = root / "operations.graphql"
        operations_path.write_text(operations.content, encoding="utf-8")
        return generate(
            output, name, schema=schema_path, operations=operations_path, lineage=lineage,
        )
