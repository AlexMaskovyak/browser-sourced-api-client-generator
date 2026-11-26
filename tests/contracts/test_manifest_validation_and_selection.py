"""Validate records, merge evidence and determine which services can generate a client."""

import copy
import json

import pytest

from tests.support.api_bundles import FIXTURES, graphql_bundle, openapi_bundle
from website_client.browser import BrowserError
from website_client.discovery_manifest import (
    artifact,
    digest,
    encoded,
    merge_bundles,
    new_bundle,
    read_bundle,
    service,
    validate_bundle,
    write_bundle,
)
from website_client.normalization import choose_service, normalize_bundle


def test_endpoint_identity_normalizes_default_port_and_hostname():
    first = service("graphql", "https://EXAMPLE.test:443/graphql")
    second = service("graphql", "https://example.test/graphql")
    assert first == second
    assert service("openapi", "https://example.test")["endpoint"].endswith("/")


def test_provenance_cycles_are_rejected():
    value = graphql_bundle()
    first, second = value["artifacts"]
    first["provenance"]["derivedFrom"] = [second["id"]]
    second["provenance"]["derivedFrom"] = [first["id"]]
    with pytest.raises(BrowserError, match="cycle"):
        validate_bundle(value)


def test_merge_validates_timestamp_before_using_it():
    first, second = graphql_bundle(), graphql_bundle()
    second["createdAt"] = "not a timestamp"
    with pytest.raises(BrowserError):
        merge_bundles([first, second])


def test_merge_uses_timestamp_instant_instead_of_timezone_text_order():
    first, second = graphql_bundle(), graphql_bundle()
    first["createdAt"] = "2026-11-01T01:00:00+02:00"
    second["createdAt"] = "2026-11-01T00:00:00Z"
    assert merge_bundles([first, second])["createdAt"] == "2026-11-01T00:00:00+00:00"


def test_producer_readiness_is_recomputed():
    value = graphql_bundle(schema=False, operations=False)
    value["stage"] = "normalized"
    value["services"][0]["status"] = "ready"
    normalized = normalize_bundle(value)
    assert normalized["services"][0]["status"] == "blocked"
    with pytest.raises(BrowserError):
        choose_service(normalized)


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


@pytest.mark.parametrize("reverse", [False, True])
def test_merge_published_schema_replaces_previously_normalized_inference(reverse):
    inputs = [normalize_bundle(openapi_bundle(inferred=True)), openapi_bundle()]
    if reverse:
        inputs.reverse()
    normalized = normalize_bundle(merge_bundles(inputs))
    target = normalized["services"][0]
    assert target["status"] == "ready"
    definitions = {item["id"]: item for item in normalized["artifacts"]}
    spec = json.loads(definitions[target["schemaArtifact"]]["content"])
    assert not spec.get("x-discovery-inferred")
    assert target["sourceSchemaArtifact"] == openapi_bundle()["artifacts"][0]["id"]


def test_merge_accepts_rfc3339_lowercase_timestamp():
    first, second = graphql_bundle(), graphql_bundle()
    first["createdAt"] = "2026-10-31T00:00:00Z"
    second["createdAt"] = "2026-11-01t00:00:00z"
    assert merge_bundles([first, second])["createdAt"] == "2026-11-01T00:00:00+00:00"


def test_conflicting_inferred_schemas_do_not_depend_on_input_order():
    first, second = openapi_bundle(inferred=True), openapi_bundle(inferred=True)
    spec = json.loads(second["artifacts"][0]["content"])
    spec["info"]["title"] = "A different sample"
    replacement = artifact("other.json", "openapi", encoded(spec), "inferred")
    second["artifacts"] = [replacement]
    second["services"][0]["schemaArtifact"] = replacement["id"]
    for inputs in ([first, second], [second, first]):
        with pytest.raises(BrowserError, match="Conflicting schemas"):
            merge_bundles(inputs)


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
