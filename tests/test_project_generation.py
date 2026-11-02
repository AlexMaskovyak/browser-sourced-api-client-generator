"""Complete projects must build and rebuild without editing generated files."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from website_client.browser import BrowserError
from website_client.cli import main
from website_client.discovery_manifest import (
    artifact,
    new_bundle,
    service,
    write_bundle,
)
from website_client.project_generation import Project, build_project


def source(tmp_path):
    """Create a portable input with no service account or live credentials."""
    operation = artifact(
        "source.graphql",
        "graphql-operations",
        "query Options { options }\nmutation Create($input: Input!) { create(input: $input) }",
        "external",
    )
    path = tmp_path / "source.json"
    write_bundle(
        new_bundle(
            [operation],
            [
                service(
                    "graphql",
                    "https://api.example.test/graphql",
                    operations=[operation["id"]],
                )
            ],
        ),
        path,
    )
    return path


def tree(path):
    """Compare authored output files without interpreter caches from verification."""
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file() and "__pycache__" not in str(item)
    }


def test_complete_project_reproduces_from_its_own_published_inputs(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    project = Project(
        "complete-client",
        (source(tmp_path),),
        "https://app.example.test/home",
        ("x-app-context",),
    )
    build_project(project, first)
    build_project(Project.read(first / "client-project.json"), second)
    assert tree(first) == tree(second)
    assert "lineage" not in json.loads((first / "generation.json").read_text())
    assert "https://app.example.test/home" in (first / "README.md").read_text()
    assert (first / "LICENSE").read_text().startswith("MIT License")
    assert "pytest" in (first / "pyproject.toml").read_text()
    result = subprocess.run(
        [sys.executable, "-m", "complete_client._cli", "Options", "--help"],
        cwd=first,
        env={**__import__("os").environ, "PYTHONPATH": str(first)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "[--url URL]" in result.stdout
    assert (
        main(
            [
                "build",
                "--project",
                str(first / "client-project.json"),
                "--output",
                str(first),
            ]
        )
        == 1
    )


def test_generated_cli_applies_login_defaults_only_at_configured_endpoint(
    tmp_path, monkeypatch
):
    output = tmp_path / "output"
    build_project(
        Project(
            "defaulted-client",
            (source(tmp_path),),
            "https://app.example.test/home",
            ("x-app-context",),
        ),
        output,
    )
    monkeypatch.syspath_prepend(str(output))
    cli = importlib.import_module("defaulted_client._cli")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    seen = []
    monkeypatch.setattr(
        cli, "execute_operation", lambda options, *args: seen.append(options) or {}
    )
    assert cli.main(["Options", "--login"]) == 0
    assert seen[-1].url == "https://api.example.test/graphql"
    assert seen[-1].login_url == "https://app.example.test/home"
    assert seen[-1].session_header == ["x-app-context"]
    assert (
        cli.main(["Options", "--login", "--url", "https://other.example.test/graphql"])
        == 0
    )
    assert seen[-1].login_url is None and seen[-1].session_header == []


def test_lock_validation_failure_prevents_publication(tmp_path, monkeypatch):
    lock = tmp_path / "poetry.lock"
    lock.write_text("example lock")
    project = Project(
        "locked-client", (source(tmp_path),), "https://app.example.test", lockfile=lock
    )
    monkeypatch.setattr(
        "website_client.project_generation.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(BrowserError, match="lock does not match"):
        build_project(project, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_project_rejects_unknown_fields_and_credential_header_names(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "example",
                "manifests": ["source.json"],
                "loginUrl": "https://app.example.test",
                "token": "do-not-accept",
            }
        )
    )
    with pytest.raises(BrowserError, match="supported fields"):
        Project.read(path)
    with pytest.raises(BrowserError, match="header name"):
        Project("example", (source(tmp_path),), "https://app.example.test", ("Cookie",))


def test_cli_builds_complete_project_without_post_processing(tmp_path):
    path = source(tmp_path)
    output = tmp_path / "out"
    assert (
        main(
            [
                "build",
                "--manifest",
                str(path),
                "--name",
                "cli-project",
                "--login-url",
                "https://app.example.test",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "tests/test_client.py").is_file()
    assert (
        main(
            [
                "build",
                "--project",
                str(output / "client-project.json"),
                "--name",
                "override",
                "--output",
                str(tmp_path / "other"),
            ]
        )
        == 1
    )


def schema_source(tmp_path, kind, *, inferred=False):
    """Represent both schema protocols through the same portable input contract."""
    fixtures = Path(__file__).parent / "fixtures"
    if kind == "openapi":
        spec = json.loads((fixtures / "openapi.json").read_text())
        if inferred:
            spec["x-discovery-inferred"] = True
        definition = artifact("api.json", "openapi", json.dumps(spec), "external")
        documents = [definition]
        target = service("openapi", "https://api.example.test", definition["id"])
    else:
        definition = artifact(
            "schema.graphql",
            "graphql-schema",
            (fixtures / "schema.graphql").read_text(),
            "external",
        )
        operations = artifact(
            "operations.graphql",
            "graphql-operations",
            (fixtures / "operations.graphql").read_text(),
            "external",
        )
        documents = [definition, operations]
        target = service(
            "graphql",
            "https://api.example.test/graphql",
            definition["id"],
            [operations["id"]],
        )
    path = tmp_path / (kind + ".json")
    write_bundle(new_bundle(documents, [target]), path)
    return path


@pytest.mark.parametrize("kind", ["openapi", "graphql"])
def test_schema_backends_build_and_reproduce_complete_projects(tmp_path, kind):
    first, second = tmp_path / "first", tmp_path / "second"
    project = Project(
        "schema-" + kind, (schema_source(tmp_path, kind),), "https://app.example.test"
    )
    report = build_project(project, first)
    build_project(Project.read(first / "client-project.json"), second)
    assert tree(first) == tree(second)
    assert report["generationMode"] == (
        "openapi" if kind == "openapi" else "graphql-schema"
    )
    readme = (first / "README.md").read_text()
    assert "API operations" in readme
    assert "The command sends one read" not in readme  # Fixture reads need inputs.
    assert "allow_writes=True" not in readme  # Not part of these backend libraries.
    assert "schema-backed generation continues" not in readme
    environment = {**__import__("os").environ, "PYTHONPATH": str(first)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(first / "tests")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_inferred_openapi_requires_review_and_keeps_permission_on_rebuild(tmp_path):
    source = schema_source(tmp_path, "openapi", inferred=True)
    with pytest.raises(BrowserError, match="Select one service|Review inferred"):
        build_project(
            Project("reviewed-api", (source,), "https://app.example.test"),
            tmp_path / "blocked",
        )
    first, second = tmp_path / "first", tmp_path / "second"
    build_project(
        Project(
            "reviewed-api", (source,), "https://app.example.test", allow_inferred=True
        ),
        first,
    )
    recipe = Project.read(first / "client-project.json")
    assert recipe.allow_inferred is True
    build_project(recipe, second)
    assert tree(first) == tree(second)


@pytest.mark.parametrize("kind", ["openapi", "graphql"])
def test_schema_cli_uses_defaults_and_real_backend_request_builder(
    tmp_path, kind, monkeypatch, capsys
):
    """Exercise the complete CLI through backend input conversion and response models."""
    output = tmp_path / "client"
    name = "wire-" + kind
    build_project(
        Project(
            name,
            (schema_source(tmp_path, kind),),
            "https://app.example.test",
            ("x-context",),
        ),
        output,
    )
    package = name.replace("-", "_")
    monkeypatch.syspath_prepend(str(output))
    cli = importlib.import_module(package + "._cli")
    session = importlib.import_module(package + ".session_http")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    import httpx

    requests, settings = [], []

    class BrowserTransport(httpx.MockTransport):
        """Stand in for browser login while leaving the generated request builder intact."""

        def __init__(self, *args):
            settings.append(args)

            def respond(request):
                requests.append(request)
                payload = (
                    {"id": 7, "name": "sample"}
                    if kind == "openapi"
                    else {"data": {"item": {"id": "7", "name": "sample"}}}
                )
                return httpx.Response(200, json=payload)

            super().__init__(respond)

    monkeypatch.setattr(session, "BrowserLoginTransport", BrowserTransport)
    inputs = tmp_path / "arguments.json"
    inputs.write_text(json.dumps({"item_id": 7} if kind == "openapi" else {"id": "7"}))
    operation = "get_item" if kind == "openapi" else "GetItem"
    assert cli.main([operation, "--login", "--input", str(inputs)]) == 0
    assert "sample" in capsys.readouterr().out
    assert settings[0][2] == "https://app.example.test"
    assert settings[0][5] == ["x-context"]
    assert len(requests) == 1
    if kind == "openapi":
        assert requests[0].method == "GET"
        assert requests[0].url.path == "/items/7"
    else:
        assert json.loads(requests[0].content)["variables"] == {"id": "7"}


def test_missing_required_operation_does_not_publish_startup_only_client(tmp_path):
    manifest = source(tmp_path)
    with pytest.raises(
        BrowserError, match="Required operations were not discovered: Expenses"
    ):
        build_project(
            Project(
                "required-client",
                (manifest,),
                "https://app.example.test",
                required_operations=("Expenses",),
            ),
            tmp_path / "missing",
        )
    assert not (tmp_path / "missing").exists()
    output = tmp_path / "complete"
    build_project(
        Project(
            "required-client",
            (manifest,),
            "https://app.example.test",
            required_operations=("Options",),
        ),
        output,
    )
    assert Project.read(output / "client-project.json").required_operations == (
        "Options",
    )
