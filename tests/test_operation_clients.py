"""Captured operations generate independently installable clients without schemas."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from website_client.discovery_manifest import (
    artifact,
    new_bundle,
    service,
    write_bundle,
)
from website_client.pipeline import generate_bundle

DOC = (Path(__file__).parent / "fixtures/captured-operations.graphql").read_text()


def manifest(tmp_path, text=DOC, *, observed=False):
    item = artifact("operations.graphql", "graphql-operations", text, "external")
    documents = [item]
    if observed:
        data = {
            "endpoints": [
                {
                    "origin": "https://example.test",
                    "path": "/graphql",
                    "method": "POST",
                    "queryParameters": ["operationName"],
                    "graphql": {"name": "Find"},
                }
            ]
        }
        documents.append(
            artifact("discovery.json", "report", json.dumps(data), "report")
        )
    path = tmp_path / "manifest.json"
    write_bundle(
        new_bundle(
            documents,
            [
                service(
                    "graphql", "https://example.test/graphql", operations=[item["id"]]
                )
            ],
        ),
        path,
    )
    return path


def test_generated_methods_send_exact_requests_and_guard_mutations(
    tmp_path, monkeypatch
):
    output = tmp_path / "client"
    report = generate_bundle(
        manifest(tmp_path, observed=True), output, "captured-client"
    )
    assert report["generationMode"] == "operation-documents"
    assert report["schemaValidated"] is False
    assert report["lineage"]["schemaArtifact"] is None
    monkeypatch.syspath_prepend(str(output))
    module = importlib.import_module("captured_client.client")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {"result": {"id": "7"}}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with module.Client(
            "https://example.test/graphql", http_client=transport
        ) as client:
            assert client.find(id="7") == {"result": {"id": "7"}}
            first = json.loads(calls[-1].content)
            assert first["variables"] == {"id": "7"}
            assert calls[-1].url.params["operationName"] == "Find"
            client.find(id="7", label=None)
            assert json.loads(calls[-1].content)["variables"]["label"] is None
            with pytest.raises(ValueError, match="allow_writes"):
                client.create(input={"name": "Example"})
            assert len(calls) == 2
            with pytest.raises(ValueError):
                client.find(id="7", limit=None)
            assert len(calls) == 2
        with module.Client(
            "https://example.test/graphql", http_client=transport, allow_writes=True
        ) as client:
            client.create(input={"name": "Example"})
            assert json.loads(calls[-1].content)["operationName"] == "Create"
    env = {**os.environ, "PYTHONPATH": str(output)}
    result = subprocess.run(
        [sys.executable, "-m", "captured_client._cli", "Find", "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "id: ID! (required)" in result.stdout
    assert "limit: Int! (optional" in result.stdout
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "captured_client._cli",
            "Create",
            "--url",
            "https://example.test/graphql",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "--confirm-write Create" in result.stderr


def test_operation_only_import_normalize_generate_pipe(tmp_path):
    cli = [sys.executable, "-m", "website_client.cli"]
    imported = subprocess.run(
        cli
        + ["import", "--operations", "-", "--endpoint", "https://example.test/graphql"],
        input=DOC,
        text=True,
        capture_output=True,
    )
    assert imported.returncode == 0, imported.stderr
    normalized = subprocess.run(
        cli + ["normalize", "--input", "-"],
        input=imported.stdout,
        text=True,
        capture_output=True,
    )
    assert normalized.returncode == 0, normalized.stderr
    generated = subprocess.run(
        cli
        + [
            "generate",
            "--manifest",
            "-",
            "--name",
            "piped-operations",
            "--output",
            str(tmp_path / "client"),
        ],
        input=normalized.stdout,
        text=True,
        capture_output=True,
    )
    assert generated.returncode == 0, generated.stderr
    assert json.loads(generated.stdout)["generationMode"] == "operation-documents"


def test_get_requests_preserve_variables_and_do_not_follow_redirects(
    tmp_path, monkeypatch
):
    source = manifest(tmp_path, observed=True)
    data = json.loads(source.read_text())
    report = next(
        item for item in data["artifacts"] if item["name"] == "discovery.json"
    )
    evidence = json.loads(report["content"])
    evidence["endpoints"][0]["method"] = "GET"
    replacement = artifact("discovery.json", "report", json.dumps(evidence), "report")
    data["artifacts"] = [
        replacement if item["id"] == report["id"] else item
        for item in data["artifacts"]
    ]
    source.write_text(json.dumps(data))
    output = tmp_path / "get-client"
    generate_bundle(source, output, "observed-get")
    monkeypatch.syspath_prepend(str(output))
    Client = importlib.import_module("observed_get.client").Client
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.test/"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with Client("https://example.test/graphql", http_client=http) as client:
            with pytest.raises(RuntimeError, match="non-success"):
                client.find(id="7")
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert json.loads(calls[0].url.params["variables"]) == {"id": "7"}
    assert calls[0].url.params["operationName"] == "Find"


def test_partial_graphql_errors_fail_without_retry_or_server_message_leaks(
    tmp_path, monkeypatch
):
    output = tmp_path / "error-client"
    generate_bundle(manifest(tmp_path), output, "failed-operation")
    monkeypatch.syspath_prepend(str(output))
    Client = importlib.import_module("failed_operation.client").Client
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": {"partial": True},
                "errors": [{"message": "do-not-print-private-server-data"}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with Client("https://example.test/graphql", http_client=http) as client:
            with pytest.raises(RuntimeError) as error:
                client.find(id="7")
    assert len(calls) == 1
    assert "do-not-print" not in str(error.value)
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": null}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "failed_operation._cli",
            "Find",
            "--url",
            "http://127.0.0.1:1",
            "--input",
            str(bad),
        ],
        env={**os.environ, "PYTHONPATH": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Input must satisfy its non-null" in result.stderr
    assert "may have succeeded" not in result.stderr


def test_cli_failure_reports_safe_http_status_and_browser_reason(capsys):
    from website_client.client_cli import RequestState, show_failure
    from website_client.operation_runtime import OperationError
    from website_client.browser import BrowserError

    state = RequestState()
    state.started = True
    show_failure(OperationError("private-response", http_status=403), state)
    output = capsys.readouterr().err
    assert "403" in output and "private-response" not in output
    show_failure(BrowserError("Browser request redirects are blocked."), state)
    assert "redirects are blocked" in capsys.readouterr().err


def test_generation_documents_public_methods_and_repeats_exactly(tmp_path):
    import ast

    source = manifest(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    generate_bundle(source, first, "documented-client")
    generate_bundle(source, second, "documented-client")
    files = lambda root: {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert files(first) == files(second)
    tree = ast.parse((first / "documented_client/client.py").read_text())
    client = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert "context manager" in ast.get_docstring(client)
    methods = {
        node.name: ast.get_docstring(node)
        for node in client.body
        if isinstance(node, ast.FunctionDef)
    }
    assert all(methods.values())
    assert "GraphQL $id: ID!. Required." in methods["find"]
    assert "Defaults to UNSET" in methods["find"]
    assert "not validated without a server schema" in methods["find"]
    assert "allow_writes=True" in methods["create"]
    assert "No automatic retry" in methods["create"]
