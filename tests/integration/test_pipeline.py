"""Exercise command boundaries from saved manifests to generated packages."""

import json
import subprocess
import sys

import pytest
from graphql import build_schema, get_introspection_query, graphql_sync

from tests.support.api_bundles import FIXTURES, graphql_bundle, openapi_bundle
from website_client.browser import BrowserError
from website_client.bundle_capture import capture_bundle
from website_client.discovery import Discovery
from website_client.discovery_manifest import (
    digest,
    encoded,
    sha256,
    write_bundle,
)
from website_client.normalization import normalize_bundle
from website_client.pipeline import generate_bundle


def command(arguments, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "website_client.cli", *arguments],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=40,
    )


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


def test_generate_from_manifest_preserves_an_api_with_zero_endpoints(tmp_path):
    manifest = tmp_path / "manifest.json"
    write_bundle(openapi_bundle(paths={}), manifest)
    output = tmp_path / "empty-client"

    report = generate_bundle(manifest, output, "empty-client")

    assert report["generator"] == "openapi-python-client"
    assert report["operations"] == []
    assert (output / "empty_client/client.py").is_file()
    assert (output / "empty_client/_login.py").is_file()


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


def run_producer_pipeline(tmp_path, payload, exit_code):
    """Use separate processes and pipefail, not a simulated stream handoff."""
    import shlex

    producer = tmp_path / "producer.py"
    producer.write_text(
        "import sys\nsys.stdout.write(" + repr(payload)
        + ")\nsys.exit(" + str(exit_code) + ")\n"
    )
    commands = [
        [sys.executable, str(producer)],
        [sys.executable, "-m", "website_client.cli", "normalize", "--input", "-"],
        [
            sys.executable, "-m", "website_client.cli", "generate",
            "--manifest", "-", "--output", str(tmp_path / "client"),
        ],
    ]
    return subprocess.run(
        [
            "/bin/bash", "-o", "pipefail", "-c",
            " | ".join(shlex.join(c) for c in commands),
        ],
        capture_output=True, text=True, timeout=40,
    )


def test_truncated_producer_output_stops_generation(tmp_path):
    result = run_producer_pipeline(tmp_path, encoded(openapi_bundle())[:-20], 7)
    assert result.returncode != 0
    assert result.stdout == ""
    assert not (tmp_path / "client").exists()


def test_pipefail_reports_late_producer_failure_but_cannot_undo_generation(tmp_path):
    result = run_producer_pipeline(tmp_path, encoded(openapi_bundle()), 7)
    assert result.returncode == 7
    assert (tmp_path / "client/generation.json").exists()


def test_normalize_success_reports_blocked_apis_without_claiming_generation(tmp_path):
    result = command(["normalize"], encoded(graphql_bundle(schema=False, operations=False)))
    assert result.returncode == 0
    assert json.loads(result.stdout)["services"][0]["status"] == "blocked"
    assert not (tmp_path / "client").exists()


def test_cli_combines_piped_operations_with_a_file_schema(tmp_path):
    source = tmp_path / "registry.json"
    write_bundle(graphql_bundle(operations=False), source)
    result = command(
        ["normalize", "--input", "-", "--input", str(source)],
        encoded(graphql_bundle(schema=False)),
    )
    assert result.returncode == 0, result.stderr
    target = json.loads(result.stdout)["services"][0]
    assert target["status"] == "ready"
    assert target["operationNames"] == ["CreateItem", "GetItem"]


def test_merge_input_budget_stops_reading_before_all_inputs_accumulate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from website_client import pipeline

    content = encoded(graphql_bundle())
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(content)
    second.write_text(content)
    monkeypatch.setattr(pipeline, "MAX_BUNDLE_BYTES", len(content.encode()) + 10)
    output = tmp_path / "merged.json"
    options = SimpleNamespace(
        command="normalize", input=[str(first), str(second)],
        output=str(output), bind_schema=[], bind_operations=[],
    )
    with pytest.raises(BrowserError, match="size limit"):
        pipeline.run_offline(options)
    assert not output.exists()


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
