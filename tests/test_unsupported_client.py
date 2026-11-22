"""Unsupported discovery still produces useful, executable local guidance."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from website_client.autonomous import run
from website_client.browser import BrowserError


def test_empty_discovery_writes_helpful_readme_and_offline_client(tmp_path):
    source = tmp_path / "empty.har"
    source.write_text(json.dumps({"log": {"version": "1.2", "entries": []}}))
    output = tmp_path / "result"
    report = run(source, output)

    assert report["coverage"] == "unsupported"
    assert report["services"] == []
    assert report["workflows"] == []
    readme = (output / "README.md").read_text()
    assert "No callable API operations were found" in readme
    assert "python3 client.py --help" in readme
    assert "new output directory" in readme
    assert "saved manifest" in readme
    assert not list((output / "services").iterdir())

    # Isolated Python requires no installed generator, dependencies, or credentials.
    script = output / "client.py"
    help_result = invoke(script, "--help")
    assert help_result.returncode == 0
    assert "no network requests" in help_result.stdout
    result = invoke(script)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "coverage": "unsupported",
        "reason": "no_executable_operations",
        "message": "No callable API operations were found. Read README.md for recovery steps.",
        "requestsSent": 0,
    }

    # Execute under an audit hook to prove that the stub cannot silently dispatch.
    audit_script = Path(__file__).parent / "helpers" / "reject_network.py"
    audited = subprocess.run(
        [sys.executable, "-I", str(audit_script), str(script)],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert audited.returncode == 1, audited.stderr
    assert audited.stdout == result.stdout
    assert audited.stderr == ""


def invoke(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(script), *args],
        cwd=script.parent,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "error, reason",
    [
        (BrowserError("CANARY_PRIVATE_INPUT"), "invalid_or_unsupported_input"),
        (
            BrowserError("har_dependency_required: CANARY_PRIVATE_PATH"),
            "har_dependency_required",
        ),
        (RuntimeError("CANARY_INTERNAL_DETAIL"), "generation_failed"),
        (KeyboardInterrupt(), "cancelled"),
    ],
)
def test_accepted_run_always_leaves_assets_when_collection_fails(
    tmp_path, monkeypatch, error, reason
):
    def fail(_request):
        raise error

    monkeypatch.setattr("website_client.autonomous._collect_inputs", fail)
    output = tmp_path / "failed"
    with pytest.raises(type(error)):
        run("https://neutral.invalid", output)
    assert (output / "README.md").is_file()
    report = json.loads((output / "coverage.json").read_text())
    assert report["reason"] == reason
    result = invoke(output / "client.py")
    assert result.returncode == (130 if reason == "cancelled" else 1)
    assert json.loads(result.stdout)["reason"] == reason
    assert not (output / "manifest.json").exists()
    for path in output.iterdir():
        if path.is_file():
            assert "CANARY" not in path.read_text()


def test_invalid_saved_input_still_leaves_client_assets(tmp_path):
    source = tmp_path / "invalid.yaml"
    source.write_text("openapi: [ invalid")
    output = tmp_path / "failed"
    with pytest.raises(BrowserError):
        run(source, output)
    assert (output / "README.md").is_file()
    assert invoke(output / "client.py").returncode == 1


def test_existing_output_is_never_replaced_by_failure_assets(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    readme = output / "README.md"
    readme.write_text("User-owned instructions")
    with pytest.raises(BrowserError):
        run("missing.json", output)
    assert readme.read_text() == "User-owned instructions"
    assert list(output.iterdir()) == [readme]


def test_failure_readme_explains_actual_exclusions_without_copying_private_details(
    tmp_path,
):
    from website_client.unsupported_output import write_unsupported_output

    report = {
        "coverage": "unsupported",
        "reason": "no_executable_operations",
        "exclusions": [
            {"reason": "undeclared_origin", "service": "CANARY_SERVICE"},
            {"reason": "har_dependency_required"},
            {"reason": "CANARY_UNKNOWN"},
        ],
        "discovery": {
            "probes": [{"outcome": "request_failed", "endpoint": "CANARY_URL"}],
            "exploration": {"blockedRequests": [{"graphql": {"kind": "query"}}]},
        },
    }
    write_unsupported_output(tmp_path, report)
    readme = (tmp_path / "README.md").read_text()
    assert "separate GraphQL host" in readme
    assert "node --version" in readme and "npm --version" in readme
    assert "cannot distinguish authentication" in readme
    assert "CANARY" not in readme


@pytest.mark.parametrize(
    "kind", ["openapi", "schema", "operations", "manifest", "workflow", "har"]
)
def test_successful_input_routes_always_write_readme_and_python_client(tmp_path, kind):
    fixtures = Path(__file__).parent / "fixtures"
    sources = {
        "openapi": fixtures / "openapi.json",
        "schema": fixtures / "schema.graphql",
        "operations": fixtures / "operations.graphql",
        "workflow": fixtures / "workflow" / "workflow.json",
    }
    if kind == "manifest":
        initial = tmp_path / "initial"
        run(fixtures / "openapi.json", initial, endpoint="https://neutral.invalid")
        sources[kind] = initial / "manifest.json"
    elif kind == "har":
        sources[kind] = tmp_path / "input.har"
        sources[kind].write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "entries": [
                            {
                                "request": {
                                    "method": "GET",
                                    "url": "https://neutral.invalid/read",
                                },
                                "response": {
                                    "status": 200,
                                    "content": {
                                        "mimeType": "application/json",
                                        "text": '{"value":7}',
                                    },
                                },
                            }
                        ],
                    }
                }
            )
        )
    output = tmp_path / "result"
    report = run(sources[kind], output, endpoint="https://neutral.invalid")
    assert report["coverage"] in {"complete", "partial"}
    projects = [output / item["directory"] for item in report["services"]]
    if report.get("workflowPackage"):
        projects.append(output / report["workflowPackage"]["directory"])
    assert projects
    for project in projects:
        assert (project / "README.md").stat().st_size > 0
        assert list(project.rglob("*.py"))


def test_failed_packaging_also_leaves_readable_runnable_output(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("CANARY_GENERATOR_DETAIL")

    monkeypatch.setattr("website_client.autonomous._generate_service", fail)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        run(
            Path(__file__).parent / "fixtures" / "openapi.json",
            output,
            endpoint="https://neutral.invalid",
        )
    assert (output / "README.md").is_file()
    result = invoke(output / "client.py")
    assert result.returncode == 1
    assert json.loads(result.stdout)["reason"] == "generation_failed"
    assert "CANARY" not in (output / "README.md").read_text() + result.stdout
