"""Normalize validated records, retaining the version 1 JSON command interface."""

from dataclasses import dataclass, replace

from graphql import GraphQLError, build_schema, parse, print_ast, validate

from .browser import BrowserError
from .discovery_manifest import digest, encoded
from .discovery_manifest.records import (
    Artifact, Confidence, Kind, Manifest, Normalization, Protocol,
    Service, Source, SourceBundle, Stage, Status,
)
from .definitions import MAX_DOCUMENT, graphql_schema, openapi_document
from .operation_contracts import compile_operations


@dataclass(slots=True)
class ArtifactIndex:
    """Index immutable documents and validate every derived replacement."""

    items: dict[str, Artifact]

    def derive(self, name: str, kind: Kind, content: str, parents: tuple[str, ...]) -> str:
        item = Artifact.create(
            name, kind, content, Source.NORMALIZED, derived_from=parents
        )
        prior = self.items.get(item.id)
        if prior is not None:
            if (prior.kind, prior.content) != (item.kind, item.content):
                raise BrowserError("A producer artifact collides with a normalized artifact ID.")
            if item.id in parents:
                # Keep an already canonical source without a self dependency.
                return item.id
            provenance = replace(
                prior.provenance,
                derived_from=tuple(sorted(set(prior.provenance.derived_from + parents))),
            )
            item = replace(prior, provenance=provenance)
        self.items[item.id] = item
        return item.id


def resolve_artifact(artifacts: ArtifactIndex, reference: str, kind: Kind) -> Artifact:
    matches = [
        item for item in artifacts.items.values() if reference in {item.id, item.name}
    ]
    if len(matches) != 1 or matches[0].kind is not kind:
        raise BrowserError("Artifact selection is missing, ambiguous or has the wrong kind.")
    return matches[0]


def apply_bindings(
    targets: dict[str, Service], artifacts: ArtifactIndex, bindings, *, schema: bool
) -> None:
    for binding in bindings:
        identifier, separator, reference = binding.partition("=")
        if not separator or identifier not in targets:
            raise BrowserError("Use an existing SERVICE_ID=ARTIFACT_ID_OR_NAME binding.")
        target = targets[identifier]
        expected = (
            (Kind.OPENAPI if target.protocol is Protocol.OPENAPI else Kind.GRAPHQL_SCHEMA)
            if schema else Kind.GRAPHQL_OPERATIONS
        )
        item = resolve_artifact(artifacts, reference, expected)
        if schema:
            target = replace(
                target, schema_artifact=item.id, source_schema_artifact=None,
                confidence=(Confidence.INFERRED if item.provenance.source is Source.INFERRED
                            else Confidence.EXTERNAL),
            )
        elif target.protocol is not Protocol.GRAPHQL:
            raise BrowserError("Operation documents can only be bound to GraphQL services.")
        elif item.id not in target.operation_artifacts:
            target = replace(target, operation_artifacts=target.operation_artifacts + (item.id,))
        targets[identifier] = target


def combined_operations(items: tuple[Artifact, ...], schema_sdl: str):
    definitions = {}
    for item in items:
        if len(item.content.encode()) > MAX_DOCUMENT:
            raise BrowserError("Operation document exceeds the 8 MiB limit.")
        document = parse(item.content, max_tokens=20000)
        for definition in document.definitions:
            if (
                definition.kind not in {"fragment_definition", "operation_definition"}
                or definition.name is None
            ):
                raise BrowserError(
                    "GraphQL documents must contain named operations and fragments."
                )
            if (
                definition.kind == "operation_definition"
                and definition.operation.value == "subscription"
            ):
                raise BrowserError(
                    "Subscriptions are not supported by this client backend."
                )
            key = (definition.kind, definition.name.value)
            text = print_ast(definition)
            if key in definitions and definitions[key] != text:
                raise BrowserError(
                    "Conflicting GraphQL definitions; select the intended source documents."
                )
            definitions[key] = text
    text = "\n\n".join(definitions[key] for key in sorted(definitions)) + "\n"
    document = parse(text, max_tokens=20000)
    names = [
        item.name.value
        for item in document.definitions
        if item.kind == "operation_definition"
    ]
    if not names or validate(build_schema(schema_sdl), document):
        raise BrowserError(
            "GraphQL operations do not match this schema or contain no operations."
        )
    return text, names


def normalize_openapi(target: Service, source: Artifact, artifacts: ArtifactIndex) -> Service:
    if target.operation_artifacts:
        raise BrowserError("OpenAPI services must not contain GraphQL operation documents.")
    spec = openapi_document(source.content)
    methods = [
        definition
        for path in spec.get("paths", {}).values()
        for method, definition in path.items()
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    ]
    if not methods:
        raise BrowserError("OpenAPI contains no endpoint operations.")
    schema_id = artifacts.derive(
        "normalized-openapi.json", Kind.OPENAPI, encoded(spec), (source.id,),
    )
    return replace(
        target, schema_artifact=schema_id,
        operation_names=tuple(sorted(item["operationId"] for item in methods if item.get("operationId"))),
        status=(Status.REVIEW_REQUIRED
                if spec.get("x-discovery-inferred") or target.confidence is Confidence.INFERRED
                else Status.READY),
    )


def normalize_graphql(target: Service, source: Artifact, artifacts: ArtifactIndex) -> Service:
    schema_sdl = graphql_schema(source.content)
    schema_id = artifacts.derive(
        "normalized-schema.graphql", Kind.GRAPHQL_SCHEMA, schema_sdl, (source.id,),
    )
    documents = tuple(artifacts.items[ref] for ref in dict.fromkeys(target.operation_artifacts))
    if any(item.kind is not Kind.GRAPHQL_OPERATIONS for item in documents):
        raise BrowserError("A GraphQL service references a non-operation artifact.")
    if not documents:
        raise BrowserError("Missing operation documents for this GraphQL endpoint.")
    text, names = combined_operations(documents, schema_sdl)
    compiled_id = artifacts.derive(
        "normalized-operations.graphql", Kind.GRAPHQL_OPERATIONS, text,
        tuple(item.id for item in documents),
    )
    return replace(
        target, schema_artifact=schema_id, compiled_operations_artifact=compiled_id,
        operation_names=tuple(names), status=Status.READY,
    )


def normalize_service(target: Service, artifacts: ArtifactIndex) -> Service:
    source_id = target.source_schema_artifact or target.schema_artifact
    target = replace(
        target, compiled_operations_artifact=None, status=Status.BLOCKED,
        issues=(), operation_names=(), source_schema_artifact=source_id,
    )
    if source_id is None:
        if target.protocol is Protocol.GRAPHQL and target.operation_artifacts:
            try:
                documents = tuple(artifacts.items[ref] for ref in target.operation_artifacts)
                if any(item.kind is not Kind.GRAPHQL_OPERATIONS for item in documents):
                    raise ValueError("A GraphQL service references a non-operation artifact.")
                operations = compile_operations(tuple(item.content for item in documents))
                return replace(
                    target, status=Status.READY,
                    operation_names=tuple(item.name for item in operations),
                )
            except ValueError as error:
                return replace(target, issues=(str(error),))
        return replace(target, issues=("Missing complete API schema.",))
    source = artifacts.items[source_id]
    expected = Kind.OPENAPI if target.protocol is Protocol.OPENAPI else Kind.GRAPHQL_SCHEMA
    try:
        if source.kind is not expected or len(source.content.encode()) > MAX_DOCUMENT:
            raise BrowserError("Wrong schema kind or schema larger than 8 MiB.")
        if target.protocol is Protocol.OPENAPI:
            return normalize_openapi(target, source, artifacts)
        return normalize_graphql(target, source, artifacts)
    except BrowserError as error:
        return replace(target, issues=(str(error),))
    except GraphQLError:
        return replace(target, issues=("GraphQL document parsing failed; inspect the source definitions.",))


def unassigned_documents(artifacts: ArtifactIndex, services: tuple[Service, ...]) -> tuple[str, ...]:
    used = set()
    for target in services:
        used.update(target.operation_artifacts)
        used.update(
            reference for reference in (
                target.schema_artifact, target.source_schema_artifact,
                target.compiled_operations_artifact,
            ) if reference is not None
        )
    return tuple(sorted(
        item.id for item in artifacts.items.values()
        if item.kind in {Kind.OPENAPI, Kind.GRAPHQL_SCHEMA, Kind.GRAPHQL_OPERATIONS}
        and item.id not in used
        and not (item.provenance.derived_from
                 and all(parent in used for parent in item.provenance.derived_from))
    ))


def normalize_manifest(manifest: Manifest, *, bind_schema=(), bind_operations=()) -> Manifest:
    if type(manifest) is not Manifest:
        raise BrowserError("Normalize requires a validated Manifest record.")
    sources = manifest.source_bundles
    if manifest.stage is Stage.DISCOVERY:
        source = SourceBundle(digest(manifest.to_dict()), manifest.producer, manifest.created_at)
        sources = sources or ()
        if source not in sources:
            sources += (source,)
    artifacts = ArtifactIndex({item.id: item for item in manifest.artifacts})
    targets = {item.id: item for item in manifest.services}
    apply_bindings(targets, artifacts, bind_schema, schema=True)
    apply_bindings(targets, artifacts, bind_operations, schema=False)
    services = tuple(normalize_service(target, artifacts) for target in targets.values())
    return replace(
        manifest, stage=Stage.NORMALIZED, source_bundles=sources,
        artifacts=tuple(artifacts.items.values()), services=services,
        normalization=Normalization(
            1, unassigned_documents(artifacts, services),
            ("Form inventories and blocked request shapes remain evidence, not manufactured API contracts.",
             "Readiness means a structurally valid generation input, not live compatibility."),
        ),
    )


def normalize_bundle(value, *, bind_schema=(), bind_operations=()):
    """JSON compatibility boundary for existing producers and command pipelines."""
    return normalize_manifest(
        Manifest.from_dict(value), bind_schema=bind_schema, bind_operations=bind_operations,
    ).to_dict()


def select_service(manifest: Manifest, identifier=None, *, allow_inferred=False) -> Service:
    if type(manifest) is not Manifest:
        raise BrowserError("Selection requires a validated Manifest record.")
    targets = manifest.services
    if identifier:
        candidates = [item for item in targets if item.id == identifier]
    else:
        candidates = [
            item for item in targets
            if item.status is Status.READY
            or allow_inferred and item.status is Status.REVIEW_REQUIRED
        ]
    if len(candidates) != 1:
        choices = ", ".join(item.id + ":" + (item.status.value if item.status else "not_normalized")
                            for item in targets)
        raise BrowserError(
            "Select one service with --service. Available services: "
            + (choices or "none; import or bind an API contract first")
        )
    target = candidates[0]
    if target.status is None:
        raise BrowserError("Normalize the manifest before selecting a service.")
    if target.status is Status.BLOCKED:
        raise BrowserError("Selected service cannot generate: " + "; ".join(target.issues or ()))
    if target.status is Status.REVIEW_REQUIRED and not allow_inferred:
        raise BrowserError("Review inferred definitions, then use --allow-inferred to generate them.")
    return target


def choose_service(bundle, identifier=None, *, allow_inferred=False):
    """Compatibility adapter for callers supplying manifest JSON."""
    return select_service(
        Manifest.from_dict(bundle), identifier, allow_inferred=allow_inferred,
    ).to_dict()
