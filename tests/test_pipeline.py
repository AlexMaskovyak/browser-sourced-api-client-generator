import copy
import json
from pathlib import Path
import subprocess
import sys

from graphql import build_schema, get_introspection_query, graphql_sync
import pytest

from website_client.browser import BrowserError
from website_client.bundle import (
    artifact,
    digest,
    encoded,
    merge_bundles,
    new_bundle,
    read_bundle,
    service,
    sha256,
    validate_bundle,
    write_bundle,
)
from website_client.bundle_capture import capture_bundle
from website_client.discovery import Discovery
from website_client.normalization import choose_service, normalize_bundle
from website_client.pipeline import generate_bundle

FIXTURES = Path(__file__).parent / "fixtures"


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


def openapi_bundle(*, inferred=False):
    spec = json.loads((FIXTURES / "openapi.json").read_text())
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


def command(arguments, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "website_client.cli", *arguments],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=40,
    )


@pytest.mark.parametrize("version", [True, 2, "1", 1.0])
def test_unsupported_versions_are_rejected(version):
    bundle = openapi_bundle()
    bundle["version"] = version
    with pytest.raises(BrowserError, match="version"):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "change",
    [
        "hash",
        "missing-reference",
        "duplicate-id",
        "unknown-field",
        "unsafe-name",
        "unsafe-endpoint",
    ],
)
def test_invalid_bundles_are_rejected(change):
    bundle = openapi_bundle()
    if change == "hash":
        bundle["artifacts"][0]["content"] += "changed"
    elif change == "missing-reference":
        bundle["services"][0]["schemaArtifact"] = "missing"
    elif change == "duplicate-id":
        bundle["artifacts"].append(copy.deepcopy(bundle["artifacts"][0]))
    elif change == "unknown-field":
        bundle["loadFromPath"] = "/private/file"
    elif change == "unsafe-name":
        bundle["artifacts"][0]["name"] = "../../outside"
    else:
        bundle["services"][0]["endpoint"] = "https://user:secret@example.test"
    with pytest.raises(BrowserError):
        validate_bundle(bundle)


def test_manifest_transport_is_private_self_contained_and_exclusive(tmp_path):
    original = graphql_bundle()
    output = tmp_path / "manifest.json"
    write_bundle(original, output)
    assert read_bundle(tmp_path) == original
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(BrowserError):
        write_bundle(original, output)
    assert read_bundle(output) == original


def test_normalization_is_idempotent_and_preserves_source_artifacts():
    source = graphql_bundle()
    first = normalize_bundle(source)
    assert encoded(first) == encoded(normalize_bundle(first))
    assert source["stage"] == "discovery"
    assert first["services"][0]["status"] == "ready"
    assert first["services"][0]["operationNames"] == ["CreateItem", "GetItem"]
    source_artifacts = {item["id"]: item for item in source["artifacts"]}
    assert all(
        item == next(value for value in first["artifacts"] if value["id"] == key)
        for key, item in source_artifacts.items()
    )


def test_external_schema_producer_completes_a_capture():
    walk = graphql_bundle(schema=False)
    upstream = graphql_bundle(operations=False)
    upstream["producer"] = {"name": "independent-schema-registry", "version": "2026-11"}
    merged = normalize_bundle(merge_bundles([walk, upstream]))
    assert merged["services"][0]["status"] == "ready"
    assert {item["sha256"] for item in merged["sourceBundles"]} >= {
        digest(walk),
        digest(upstream),
    }
    assert len(merged["services"][0]["operationArtifacts"]) == 1


def test_operations_are_not_bound_by_schema_similarity():
    bundle = graphql_bundle()
    other = copy.deepcopy(bundle["services"][0])
    other.update(
        id="other-api", endpoint="https://other.test/graphql", operationArtifacts=[]
    )
    bundle["services"].append(other)
    normalized = normalize_bundle(bundle)
    assert normalized["services"][0]["status"] == "ready"
    assert normalized["services"][1]["status"] == "blocked"


def test_multiple_ready_services_require_selection():
    normalized = normalize_bundle(merge_bundles([openapi_bundle(), graphql_bundle()]))
    with pytest.raises(BrowserError, match="--service"):
        choose_service(normalized)
    assert (
        choose_service(normalized, normalized["services"][1]["id"])["protocol"]
        == "graphql"
    )


def test_inferred_contract_requires_explicit_acceptance():
    normalized = normalize_bundle(openapi_bundle(inferred=True))
    assert normalized["services"][0]["status"] == "review_required"
    with pytest.raises(BrowserError):
        choose_service(normalized, normalized["services"][0]["id"])
    assert (
        choose_service(normalized, allow_inferred=True)["status"] == "review_required"
    )


def test_conflicting_operations_block_one_service_without_discarding_evidence():
    bundle = graphql_bundle()
    conflict = artifact(
        "conflict.graphql",
        "graphql-operations",
        "query GetItem($id: ID!) { item(id: $id) { id } }",
        "frontend",
    )
    bundle["artifacts"].append(conflict)
    bundle["services"][0]["operationArtifacts"].append(conflict["id"])
    normalized = normalize_bundle(bundle)
    assert normalized["services"][0]["status"] == "blocked"
    assert "Conflicting" in normalized["services"][0]["issues"][0]
    assert any(item["id"] == conflict["id"] for item in normalized["artifacts"])


def test_unbound_frontend_write_can_be_explicitly_associated():
    bundle = graphql_bundle(operations=False)
    document = artifact(
        "declared.graphql",
        "graphql-operations",
        (FIXTURES / "operations.graphql").read_text(),
        "frontend",
    )
    bundle["artifacts"].append(document)
    incomplete = normalize_bundle(bundle)
    assert document["id"] in incomplete["normalization"]["unassignedArtifacts"]
    complete = normalize_bundle(
        bundle, bind_operations=[bundle["services"][0]["id"] + "=declared.graphql"]
    )
    assert complete["services"][0]["status"] == "ready"
    assert complete["artifacts"][1]["provenance"]["source"] == "frontend"


def test_form_inventory_is_retained_without_fabricating_a_callable_contract():
    item = artifact(
        "exploration.json",
        "report",
        json.dumps({"forms": [{"action": "/submit", "method": "POST"}]}),
        "report",
    )
    normalized = normalize_bundle(new_bundle([item]))
    assert not normalized["services"]
    assert normalized["artifacts"][0]["content"] == item["content"]
    with pytest.raises(BrowserError):
        choose_service(normalized)


def test_cli_import_and_normalize_accept_standard_input_and_merge(tmp_path):
    imported = command(
        [
            "import",
            "--openapi",
            "-",
            "--endpoint",
            "https://example.test",
            "--producer",
            "upstream-tool",
        ],
        (FIXTURES / "openapi.json").read_text(),
    )
    assert imported.returncode == 0, imported.stderr
    raw = json.loads(imported.stdout)
    assert raw["producer"]["name"] == "upstream-tool"
    normalized = command(["normalize", "--input", "-"], imported.stdout)
    assert normalized.returncode == 0, normalized.stderr
    assert json.loads(normalized.stdout)["services"][0]["status"] == "ready"
    assert (
        "Normalized services" in normalized.stderr
        and "Normalized services" not in normalized.stdout
    )


def test_generation_from_only_a_manifest_is_offline_and_records_hashes(tmp_path):
    raw = graphql_bundle()
    manifest = tmp_path / "manifest.json"
    write_bundle(raw, manifest)
    report = generate_bundle(manifest, tmp_path / "client", "bundle-client")
    assert report["operations"] == ["CreateItem", "GetItem"]
    assert report["lineage"]["inputBundleSha256"] == digest(raw)
    assert report["operationsSha256"] == sha256(
        (tmp_path / "client/operations.graphql").read_text()
    )
    assert (
        report["lineage"]["sourceSchemaArtifact"]["sha256"]
        == raw["artifacts"][0]["sha256"]
    )
    assert not (tmp_path / "client/exploration.json").exists()


def test_generate_cli_reads_normalized_bundle_from_stdin(tmp_path):
    result = command(
        [
            "generate",
            "--bundle",
            "-",
            "--name",
            "pipe-client",
            "--output",
            str(tmp_path / "client"),
        ],
        encoded(normalize_bundle(openapi_bundle())),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["operations"] == ["create_item", "get_item"]
    assert (tmp_path / "client/generation.json").exists()


def test_tampered_bundle_does_not_publish_a_client(tmp_path):
    bundle = openapi_bundle()
    bundle["artifacts"][0]["content"] += "tampered"
    result = command(
        ["generate", "--bundle", "-", "--output", str(tmp_path / "client")],
        encoded(bundle),
    )
    assert result.returncode != 0
    assert not (tmp_path / "client").exists()


def test_discovery_manifest_preserves_schemas_and_frontend_documents_beyond_har():
    discovery = Discovery()
    endpoint = "https://example.test/graphql"
    query = get_introspection_query()
    data = graphql_sync(
        build_schema((FIXTURES / "schema.graphql").read_text()), query
    ).formatted
    discovery.add(
        {
            "request": {
                "url": endpoint,
                "method": "POST",
                "postData": {
                    "text": json.dumps(
                        {"query": query, "operationName": "IntrospectionQuery"}
                    )
                },
            },
            "response": {
                "status": 200,
                "content": {"mimeType": "application/json", "text": json.dumps(data)},
            },
        }
    )
    text = (FIXTURES / "operations.graphql").read_text()
    discovery.documents[sha256(text)] = text
    discovery.document_declarations[sha256(text)] = {"https://example.test/app.js"}
    files = discovery.files("browser")
    bundle = capture_bundle(discovery, files)
    assert {item["name"] for item in bundle["artifacts"]} == set(files)
    assert any(item["kind"] == "graphql-schema" for item in bundle["artifacts"])
    target = bundle["services"][0]
    assert normalize_bundle(bundle)["services"][0]["status"] == "blocked"
    normalized = normalize_bundle(
        bundle, bind_operations=[target["id"] + "=operations-0001.graphql"]
    )
    assert normalized["services"][0]["status"] == "ready"
