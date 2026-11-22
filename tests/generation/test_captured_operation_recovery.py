"""Saved evidence must produce a client even when independent REST inference fails."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from graphql import parse
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import UnresolvableParameterError

from website_client.browser import origin
from website_client.bundle_capture import capture_manifest
from website_client.cli import main
from website_client.discovery import Discovery
from website_client.discovery_manifest.records import Kind
from website_client.exploration_policy import request_allowed
from website_client.privacy import sanitized_manifest
from website_client.har_inference import infer_har
from website_client.self_discovery import DiscoveryObserver

FIXTURE = Path(__file__).parents[1] / "fixtures" / "captured_operation_recovery"
WEBSITE = "https://payroll.example.test"
GRAPHQL_ENDPOINT = "https://graphql.payroll.example.test/"
EXPECTED_OPERATIONS = json.loads((FIXTURE / "expected_operations.json").read_text())


def captured_entries() -> list[dict]:
    return json.loads((FIXTURE / "capture.har").read_text())["log"]["entries"]


def test_fixture_preserves_missing_responses_without_private_values():
    entries = captured_entries()
    assert len(entries) == 12
    graphql_payloads = []
    for entry in entries:
        request = entry["request"]
        assert urlsplit(request["url"]).hostname in {
            "payroll.example.test",
            "graphql.payroll.example.test",
        }
        assert request["headers"] == []
        assert request["cookies"] == []
        if request["method"] == "POST":
            payload = json.loads(request["postData"]["text"])
            graphql_payloads.append(payload)
            assert "variables" not in payload, (
                "Real variable values must not be fixtures"
            )
            assert entry["response"]["status"] == 0
            assert entry["response"]["content"]["text"] == ""

    assert sorted(p["operationName"] for p in graphql_payloads) == EXPECTED_OPERATIONS


def test_sanitization_retains_operations_and_placeholder_across_saved_round_trip():
    discovery = Discovery()
    for original_entry in captured_entries():
        entry = deepcopy(original_entry)
        # Synthetic secrets test the real sanitizer without storing private data.
        entry["request"]["headers"] = [
            {"name": "Authorization", "value": "Bearer CANARY_PRIVATE_TOKEN"}
        ]
        entry["request"]["cookies"] = [
            {"name": "sid", "value": "CANARY_PRIVATE_COOKIE"}
        ]
        if entry["request"]["method"] == "POST":
            payload = json.loads(entry["request"]["postData"]["text"])
            payload["variables"] = {"field2": "CANARY_PRIVATE_ACCOUNT"}
            entry["request"]["postData"]["text"] = json.dumps(payload)
        discovery.add(entry)

    # Match the normal pipeline boundary: native REST inference owns OpenAPI.
    captured_files = {
        name: content
        for name, content in discovery.files("har").items()
        if not name.startswith("openapi-inferred")
    }
    manifest = capture_manifest(discovery, captured_files)
    for _round_trip in range(2):
        manifest = sanitized_manifest(manifest)
        assert "CANARY_PRIVATE" not in json.dumps(manifest.to_dict())
        assert len(manifest.services) == 1
        service = manifest.services[0]
        assert service.endpoint == GRAPHQL_ENDPOINT
        captured_names = {
            definition.name.value
            for artifact in manifest.artifacts
            if artifact.id in service.operation_artifacts
            for definition in parse(artifact.content).definitions
            if definition.kind == "operation_definition"
        }
        assert sorted(captured_names) == EXPECTED_OPERATIONS
        assert service.schema_artifact is None, "A failed response is not a schema"
        captured_har = next(a for a in manifest.artifacts if a.kind is Kind.HAR)
        entries = json.loads(captured_har.content)["log"]["entries"]
        rest_entry = next(e for e in entries if e["request"]["method"] == "GET")
        assert "%7Bid_3%7D" in rest_entry["request"]["url"]


def test_saved_rest_response_reproduces_native_converter_failure():
    rest_entries = [
        entry for entry in captured_entries() if entry["request"]["method"] == "GET"
    ]
    safe_har = json.dumps({"log": {"version": "1.2", "entries": rest_entries}})

    # Use the actual dependency. A mocked invalid document would miss changes in
    # how the captured placeholder is interpreted by har2sdk.
    inferred_definition, _diagnostics = infer_har(safe_har)

    assert "/api/accounts/{id_3}/setup/states" in inferred_definition["paths"]
    with pytest.raises(UnresolvableParameterError, match="id_3"):
        validate(inferred_definition)


def test_normal_cli_retains_client_and_regenerates_from_saved_manifest(
    tmp_path, capsys
):
    output = tmp_path / "generated"

    exit_code = main(
        [
            "run",
            str(FIXTURE / "capture.har"),
            "--output",
            str(output),
            "--name",
            "saved-payroll",
        ]
    )

    assert exit_code == 0, capsys.readouterr().err
    assert_generated_client(output, expected_exclusion="har_inference_failed")

    # The saved manifest is the same recovery entry point used for the real run.
    regenerated = tmp_path / "regenerated"
    exit_code = main(
        [
            "run",
            str(output / "manifest.json"),
            "--output",
            str(regenerated),
            "--name",
            "saved-payroll",
        ]
    )

    assert exit_code == 0, capsys.readouterr().err
    assert_generated_client(regenerated, expected_exclusion="har_inference_failed")


def test_missing_converter_prerequisite_still_generates_captured_client(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("website_client.har_inference.shutil.which", lambda _name: None)
    output = tmp_path / "without-node"

    exit_code = main(["run", str(FIXTURE / "capture.har"), "--output", str(output)])

    assert exit_code == 0, capsys.readouterr().err
    assert_generated_client(output, expected_exclusion="har_dependency_required")


def test_captured_reads_admit_separate_host_but_mutation_alone_does_not():
    graphql_requests = [
        entry["request"]
        for entry in captured_entries()
        if entry["request"]["url"] == GRAPHQL_ENDPOINT
    ]
    for request in graphql_requests:
        allowed_origins = {origin(WEBSITE)}
        admitted_endpoints = []

        def admit(endpoint):
            admitted_endpoints.append(endpoint)
            allowed_origins.add(origin(endpoint))
            return True

        observer = DiscoveryObserver(
            WEBSITE, allowed_origins, admit_graphql_origin=admit
        )
        payload = json.loads(request["postData"]["text"])
        operation_name = payload["operationName"]
        event = {
            "documentURL": WEBSITE + "/dashboard",
            "type": "Fetch",
            "request": {
                **request,
                "headers": {},
                "postData": request["postData"]["text"],
            },
        }

        observer.event("Network.requestWillBeSent", event)

        expected_endpoints = [] if "Mutation" in operation_name else [GRAPHQL_ENDPOINT]
        assert admitted_endpoints == expected_endpoints, operation_name
        browser_request = SimpleNamespace(
            url=request["url"],
            method=request["method"],
            resource_type="fetch",
            post_data=request["postData"]["text"],
            is_navigation_request=lambda: False,
        )
        blocked_reason = request_allowed(
            browser_request,
            origin(WEBSITE),
            strict_origin=True,
            api_origins=allowed_origins,
        )
        if expected_endpoints:
            assert blocked_reason is None, (operation_name, blocked_reason)
        else:
            assert blocked_reason is not None, (
                "Mutation evidence must not authorize dispatch"
            )
        assert observer.headers == {}, "Saved fixtures must not introduce credentials"


def assert_generated_client(output: Path, *, expected_exclusion: str) -> None:
    report = json.loads((output / "coverage.json").read_text())
    assert report["coverage"] == "partial", report
    assert len(report["services"]) == 1, report["exclusions"]
    service = report["services"][0]
    assert service["operations"] == EXPECTED_OPERATIONS
    reasons = {item["reason"] for item in report["exclusions"]}
    assert expected_exclusion in reasons
    other_failure = (
        "har_dependency_required"
        if expected_exclusion == "har_inference_failed"
        else "har_inference_failed"
    )
    assert other_failure not in reasons, reasons
    assert "undeclared_origin" not in reasons, reasons

    project = output / service["directory"]
    module = service["package"].replace("-", "_")
    assert (project / "README.md").is_file(), (
        "Generated service needs usage instructions"
    )
    assert (project / module / "client.py").is_file(), "Expected a real GraphQL client"
    assert not (output / "client.py").exists(), (
        "Usable operations must not become a stub"
    )

    help_result = subprocess.run(
        [sys.executable, "-m", module + "._cli", "--help"],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_result.returncode == 0, help_result.stderr
    for operation_name in EXPECTED_OPERATIONS:
        assert operation_name in help_result.stdout, operation_name
