"""The public producer contract works without discovery-specific structures."""

import hashlib
import json

import pytest

from website_client.browser import BrowserError
from website_client.bundle import read_bundle, read_text, validate_bundle


def independent_bundle():
    content = '{"forms": []}'
    producer = {"name": "separate-tool", "version": "1"}
    return {
        "format": "website-client-discovery",
        "version": 1,
        "stage": "discovery",
        "producer": producer,
        "createdAt": "2026-11-01T00:00:00Z",
        "services": [],
        "artifacts": [
            {
                "id": "evidence",
                "name": "forms.json",
                "kind": "report",
                "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "provenance": {
                    "source": "external",
                    "locations": [],
                    "derivedFrom": [],
                    "producers": [producer],
                },
            }
        ],
    }


def test_independent_producer_document_is_accepted(tmp_path):
    bundle = independent_bundle()
    path = tmp_path / "input.json"
    path.write_text(json.dumps(bundle))
    assert read_bundle(path) == bundle


def test_tampering_is_detected():
    bundle = independent_bundle()
    bundle["artifacts"][0]["content"] = "changed"
    with pytest.raises(BrowserError, match="hash"):
        validate_bundle(bundle)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"version":1,"version":2}')
    with pytest.raises(BrowserError):
        read_bundle(path)


def test_read_limit_counts_bytes(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes("é".encode() * 10)
    with pytest.raises(BrowserError, match="size limit"):
        read_text(path, limit=15)
