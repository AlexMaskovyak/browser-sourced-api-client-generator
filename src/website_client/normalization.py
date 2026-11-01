"""Validate endpoint-specific contracts without inventing missing API evidence."""

import copy

from graphql import build_schema, parse, print_ast, validate

from .browser import BrowserError
from .bundle import artifact, digest, encoded, validate_bundle
from .definitions import MAX_DOCUMENT, graphql_schema, openapi_document


def resolve_artifact(artifacts, reference, kind=None):
    matches = [
        item for item in artifacts.values() if reference in {item["id"], item["name"]}
    ]
    if len(matches) != 1 or kind and matches[0]["kind"] != kind:
        raise BrowserError(
            "Artifact selection is missing, ambiguous or has the wrong kind."
        )
    return matches[0]


def apply_bindings(bundle, artifacts, bindings, *, schema):
    targets = {item["id"]: item for item in bundle["services"]}
    for binding in bindings:
        identifier, separator, reference = binding.partition("=")
        if not separator or identifier not in targets:
            raise BrowserError(
                "Use an existing SERVICE_ID=ARTIFACT_ID_OR_NAME binding."
            )
        target = targets[identifier]
        expected = (
            ("openapi" if target["protocol"] == "openapi" else "graphql-schema")
            if schema
            else "graphql-operations"
        )
        item = resolve_artifact(artifacts, reference, expected)
        if schema:
            target["schemaArtifact"] = item["id"]
            target.pop("sourceSchemaArtifact", None)
            target["confidence"] = (
                "inferred" if item["provenance"]["source"] == "inferred" else "external"
            )
        elif target["protocol"] != "graphql":
            raise BrowserError(
                "Operation documents can only be bound to GraphQL services."
            )
        elif item["id"] not in target["operationArtifacts"]:
            target["operationArtifacts"].append(item["id"])


def combined_operations(items, schema_sdl):
    definitions = {}
    for item in items:
        if len(item["content"].encode()) > MAX_DOCUMENT:
            raise BrowserError("Operation document exceeds the 8 MiB limit.")
        document = parse(item["content"], max_tokens=20000)
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


def normalize_bundle(value, *, bind_schema=(), bind_operations=()):
    bundle = copy.deepcopy(validate_bundle(value))
    if bundle["stage"] == "discovery":
        sources = bundle.setdefault("sourceBundles", [])
        source = {
            "sha256": digest(value),
            "producer": value["producer"],
            "createdAt": value["createdAt"],
        }
        if source not in sources:
            sources.append(source)
    artifacts = {item["id"]: item for item in bundle["artifacts"]}
    apply_bindings(bundle, artifacts, bind_schema, schema=True)
    apply_bindings(bundle, artifacts, bind_operations, schema=False)

    def derived(name, kind, content, parents):
        item = artifact(name, kind, content, "normalized", derived_from=parents)
        if item["id"] in parents:
            # A producer may already supply this canonical document/name. Keep
            # the source artifact intact instead of adding a self dependency.
            return item["id"]
        if item["id"] in artifacts:
            prior = artifacts[item["id"]]
            if (prior["kind"], prior["content"]) != (item["kind"], item["content"]):
                raise BrowserError(
                    "A producer artifact collides with a normalized artifact ID."
                )
            prior["provenance"]["derivedFrom"] = sorted(
                set(prior["provenance"]["derivedFrom"] + list(parents))
            )
        else:
            artifacts[item["id"]] = item
        return item["id"]

    for target in bundle["services"]:
        source_id = target.get("sourceSchemaArtifact", target["schemaArtifact"])
        target.pop("compiledOperationsArtifact", None)
        target["status"], target["issues"], target["operationNames"] = "blocked", [], []
        if not source_id:
            target["issues"].append("Missing complete API schema.")
            continue
        target["sourceSchemaArtifact"] = source_id
        source = artifacts[source_id]
        expected = "openapi" if target["protocol"] == "openapi" else "graphql-schema"
        try:
            if (
                source["kind"] != expected
                or len(source["content"].encode()) > MAX_DOCUMENT
            ):
                raise BrowserError("Wrong schema kind or schema larger than 8 MiB.")
            if expected == "openapi":
                if target["operationArtifacts"]:
                    raise BrowserError(
                        "OpenAPI services must not contain GraphQL operation documents."
                    )
                spec = openapi_document(source["content"])
                methods = [
                    definition
                    for path in spec.get("paths", {}).values()
                    for method, definition in path.items()
                    if method.lower()
                    in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "head",
                        "options",
                        "trace",
                    }
                ]
                if not methods:
                    raise BrowserError("OpenAPI contains no endpoint operations.")
                target["schemaArtifact"] = derived(
                    "normalized-openapi.json", "openapi", encoded(spec), [source_id]
                )
                target["operationNames"] = sorted(
                    item["operationId"] for item in methods if item.get("operationId")
                )
                target["status"] = (
                    "review_required"
                    if spec.get("x-discovery-inferred")
                    or target["confidence"] == "inferred"
                    else "ready"
                )
            else:
                schema_sdl = graphql_schema(source["content"])
                target["schemaArtifact"] = derived(
                    "normalized-schema.graphql",
                    "graphql-schema",
                    schema_sdl,
                    [source_id],
                )
                documents = []
                for reference in dict.fromkeys(target["operationArtifacts"]):
                    item = artifacts[reference]
                    if item["kind"] != "graphql-operations":
                        raise BrowserError(
                            "A GraphQL service references a non-operation artifact."
                        )
                    documents.append(item)
                if not documents:
                    raise BrowserError(
                        "Missing operation documents for this GraphQL endpoint."
                    )
                text, names = combined_operations(documents, schema_sdl)
                target["compiledOperationsArtifact"] = derived(
                    "normalized-operations.graphql",
                    "graphql-operations",
                    text,
                    [item["id"] for item in documents],
                )
                target["operationNames"], target["status"] = names, "ready"
        except BrowserError as error:
            target["issues"].append(str(error))
        except Exception:
            target["issues"].append(
                "Contract validation failed; inspect the source definitions."
            )
    used = set()
    for target in bundle["services"]:
        used.update(target["operationArtifacts"])
        used.update(
            target[key]
            for key in (
                "schemaArtifact",
                "sourceSchemaArtifact",
                "compiledOperationsArtifact",
            )
            if target.get(key)
        )
    unassigned = []
    for item in artifacts.values():
        parents = item["provenance"]["derivedFrom"]
        if (
            item["kind"] in {"openapi", "graphql-schema", "graphql-operations"}
            and item["id"] not in used
            and not (parents and all(parent in used for parent in parents))
        ):
            unassigned.append(item["id"])
    bundle["artifacts"] = list(artifacts.values())
    bundle["stage"] = "normalized"
    bundle["normalization"] = {
        "policyVersion": 1,
        "unassignedArtifacts": sorted(unassigned),
        "notes": [
            "Form inventories and blocked request shapes remain evidence, not manufactured API contracts.",
            "Readiness means a structurally valid generation input, not live compatibility.",
        ],
    }
    return validate_bundle(bundle)


def choose_service(bundle, identifier=None, *, allow_inferred=False):
    targets = bundle["services"]
    if identifier:
        candidates = [item for item in targets if item["id"] == identifier]
    else:
        candidates = [
            item
            for item in targets
            if item["status"] == "ready"
            or allow_inferred
            and item["status"] == "review_required"
        ]
    if len(candidates) != 1:
        choices = ", ".join(item["id"] + ":" + item["status"] for item in targets)
        raise BrowserError(
            "Select one service with --service. Available services: "
            + (choices or "none; import or bind an API contract first")
        )
    target = candidates[0]
    if target["status"] == "blocked":
        raise BrowserError(
            "Selected service cannot generate: " + "; ".join(target["issues"])
        )
    if target["status"] == "review_required" and not allow_inferred:
        raise BrowserError(
            "Review inferred definitions, then use --allow-inferred to generate them."
        )
    return target
