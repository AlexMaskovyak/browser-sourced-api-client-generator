"""Domain records reject invalid construction and preserve the public JSON."""

from dataclasses import FrozenInstanceError, replace
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
