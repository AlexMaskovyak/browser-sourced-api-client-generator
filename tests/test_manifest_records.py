"""Domain records reject invalid construction and preserve the public JSON."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from website_client.browser import BrowserError
from website_client.discovery_manifest import artifact, new_bundle, service
from website_client.discovery_manifest.records import (
    Artifact, Manifest, Protocol, Service, Stage, Status,
)
from website_client.normalization import normalize_manifest


def example_manifest():
    item = artifact(
        "api.json", "openapi",
        (Path(__file__).parent / "fixtures/openapi.json").read_text(),
        "external",
    )
    return new_bundle(
        [item], [service("openapi", "https://example.test", item["id"])],
    )


def test_record_round_trip_preserves_optional_fields_and_source_bytes():
    raw = example_manifest()
    parsed = Manifest.from_dict(raw)
    assert parsed.to_dict() == raw
    assert parsed.stage is Stage.DISCOVERY
    assert parsed.services[0].protocol is Protocol.OPENAPI
    assert parsed.services[0].status is None
    assert "status" not in parsed.to_dict()["services"][0]


def test_parsed_records_do_not_share_mutable_input_or_output_data():
    raw = example_manifest()
    parsed = Manifest.from_dict(raw)
    raw["artifacts"][0]["provenance"]["producers"][0]["name"] = "changed"
    serialized = parsed.to_dict()
    serialized["artifacts"][0]["provenance"]["producers"].clear()
    assert parsed.artifacts[0].provenance.producers[0].name == "website-client"
    assert parsed.to_dict()["artifacts"][0]["provenance"]["producers"]


def test_records_and_nested_collections_are_immutable():
    parsed = Manifest.from_dict(example_manifest())
    with pytest.raises(FrozenInstanceError):
        parsed.artifacts[0].content = "changed"
    with pytest.raises(FrozenInstanceError):
        parsed.artifacts[0].provenance.producers[0].name = "changed"
    with pytest.raises(AttributeError):
        parsed.artifacts[0].provenance.locations.append("https://other.test/")


def test_direct_construction_rejects_plain_protocol_strings():
    with pytest.raises(BrowserError, match="Service.protocol"):
        Service("api", "openapi", "https://example.test/", None, (),
                Manifest.from_dict(example_manifest()).services[0].confidence)


def test_replacement_revalidates_hash_and_nested_collection_types():
    document = Manifest.from_dict(example_manifest()).artifacts[0]
    with pytest.raises(BrowserError, match="hash"):
        replace(document, content="tampered")
    with pytest.raises(BrowserError, match="Provenance.locations"):
        replace(document.provenance, locations=[])
    with pytest.raises(BrowserError, match="Artifact.provenance"):
        replace(document, provenance=document.provenance.to_dict())


def test_direct_service_replacement_rejects_invalid_status_and_endpoint():
    target = Manifest.from_dict(example_manifest()).services[0]
    with pytest.raises(BrowserError, match="Service.status"):
        replace(target, status="ready")
    with pytest.raises(BrowserError):
        replace(target, endpoint="https://EXAMPLE.test:443/")


def test_record_manifest_checks_cross_document_references_and_boolean_versions():
    parsed = Manifest.from_dict(example_manifest())
    with pytest.raises(BrowserError, match="missing artifact"):
        replace(parsed, services=(replace(parsed.services[0], schema_artifact="absent"),))
    with pytest.raises(BrowserError, match="Manifest.version"):
        replace(parsed, version=True)


def test_document_parser_checks_content_hash_after_structural_validation():
    raw = example_manifest()["artifacts"][0]
    raw["content"] = "changed without updating the hash"
    with pytest.raises(BrowserError, match="hash"):
        Artifact.from_dict(raw)


def test_typed_normalization_round_trips_all_metadata_and_is_idempotent():
    original = Manifest.from_dict(example_manifest())
    normalized = normalize_manifest(original)
    assert normalized.services[0].status is Status.READY
    assert normalized.normalization.policy_version == 1
    assert normalized.source_bundles[0].producer == original.producer
    assert Manifest.from_dict(normalized.to_dict()) == normalized
    assert normalize_manifest(normalized) == normalized
    assert original.stage is Stage.DISCOVERY


def test_typed_core_requires_the_validated_boundary():
    with pytest.raises(BrowserError, match="validated Manifest"):
        normalize_manifest(example_manifest())


def test_typed_merging_preserves_immutable_inputs_and_combines_provenance():
    from website_client.discovery_manifest.merging import merge_manifests

    first = Manifest.from_dict(example_manifest())
    evidence = replace(
        first.artifacts[0],
        provenance=replace(first.artifacts[0].provenance, locations=("https://source.test/",)),
    )
    second = replace(first, artifacts=(evidence,))
    merged = merge_manifests((first, second))
    assert merged.artifacts[0].provenance.locations == ("https://source.test/",)
    assert first.artifacts[0].provenance.locations == ()
    assert Manifest.from_dict(merged.to_dict()) == merged


def test_typed_merge_requires_records_and_single_input_keeps_metadata():
    from website_client.discovery_manifest.merging import merge_manifests

    manifest = normalize_manifest(Manifest.from_dict(example_manifest()))
    assert merge_manifests((manifest,)) == manifest
    with pytest.raises(BrowserError, match="validated Manifest"):
        merge_manifests((example_manifest(),))


def test_typed_factories_reject_mutable_or_mislabelled_inputs():
    from website_client.discovery_manifest.records import Kind, Source

    with pytest.raises(BrowserError, match="Provenance.locations"):
        Artifact.create("api.json", Kind.REPORT, "{}", Source.EXTERNAL, locations=[])
    with pytest.raises(BrowserError, match="Service.operation_artifacts"):
        Service.create(Protocol.GRAPHQL, "https://example.test/graphql", operations="abc")
    with pytest.raises(BrowserError):
        Manifest.create(created_at="")


def test_merge_rejects_colliding_document_ids_with_different_valid_hashes():
    from website_client.discovery_manifest.merging import merge_manifests
    from website_client.discovery_manifest import sha256

    first = Manifest.from_dict(example_manifest())
    different = "a different source document"
    collision = replace(first.artifacts[0], content=different, sha256=sha256(different))
    second = replace(first, artifacts=(collision,))
    with pytest.raises(BrowserError, match="Conflicting artifact IDs"):
        merge_manifests((first, second))


def test_merge_uses_published_evidence_for_equal_bytes_without_mutating_inputs():
    from website_client.discovery_manifest.merging import merge_manifests
    from website_client.discovery_manifest.records import Confidence

    published = Manifest.from_dict(example_manifest())
    inferred = replace(
        published, services=(replace(published.services[0], confidence=Confidence.INFERRED),),
    )
    for inputs in ((inferred, published), (published, inferred)):
        merged = merge_manifests(inputs)
        assert merged.services[0].confidence is Confidence.EXTERNAL
    assert inferred.services[0].confidence is Confidence.INFERRED


def test_merge_clears_computed_schema_selection_on_records():
    from website_client.discovery_manifest.merging import merge_manifests

    original = Manifest.from_dict(example_manifest())
    normalized = normalize_manifest(original)
    merged = merge_manifests((normalized, original))
    target = merged.services[0]
    assert target.schema_artifact == original.services[0].schema_artifact
    assert target.source_schema_artifact is None
    assert target.status is None
    assert normalized.services[0].status is Status.READY
    assert normalize_manifest(merged).services[0].status is Status.READY


def test_capture_transport_and_api_selection_exchange_records(tmp_path):
    from website_client.bundle_capture import capture_manifest
    from website_client.discovery import Discovery
    from website_client.discovery_manifest.transport import read_manifest, write_manifest
    from website_client.normalization import select_service

    spec = json.loads((Path(__file__).parent / "fixtures/openapi.json").read_text())
    spec["servers"] = [{"url": "https://example.test/"}]
    content = json.dumps(spec)
    captured = capture_manifest(Discovery(), {"openapi-downloaded-0001.json": content})
    assert isinstance(captured, Manifest)
    assert isinstance(captured.artifacts[0], Artifact)
    saved = tmp_path / "manifest.json"
    write_manifest(captured, saved)
    restored = read_manifest(saved)
    assert restored == captured
    target = select_service(normalize_manifest(restored))
    assert isinstance(target, Service)
    assert target.protocol is Protocol.OPENAPI
    assert target.status is Status.READY
