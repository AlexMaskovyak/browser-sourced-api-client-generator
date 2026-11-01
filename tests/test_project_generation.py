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
