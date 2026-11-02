"""Exercise real installed command pipes without shared application memory."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from terminal_command import Command, eventually


def pipeline(commands):
    """Quote argv arrays for a real shell pipe rather than joining unsafe arguments."""
    return " | ".join(shlex.join(command) for command in commands)


def verify_external_pipeline(root):
    """Prove an independent stdlib producer feeds normalization and installed generation."""
    executable = shutil.which("website-client")
    output = root / "external-client"
    script = pipeline(
        [
            [
                sys.executable,
                "-S",
                "/work/examples/create_discovery_manifest.py",
                "--protocol",
                "graphql",
                "--schema",
                "/work/tests/fixtures/schema.graphql",
                "--operations",
                "/work/tests/fixtures/operations.graphql",
                "--endpoint",
                "https://example.test/graphql",
            ],
            [executable, "normalize", "--input", "-"],
            [
                executable,
                "generate",
                "--manifest",
                "-",
                "--name",
                "external-client",
                "--output",
                str(output),
            ],
        ]
    )
    result = subprocess.run(
        ["/bin/bash", "-o", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["lineage"]["producer"]["name"] == "standalone-example-producer"
    assert set(report["operations"]) == {"GetItem", "CreateItem"}
    assert report["operationsSha256"]
    assert (output / "pyproject.toml").exists()
    print(
        "PASS portable pipeline: independent stdlib producer to normalized bundle to client",
        flush=True,
    )


def verify_browser_pipeline(site, root, complete_login, assert_no_credentials):
    """Exercise login-to-generation across actual stdin/stdout command boundaries."""
    executable = shutil.which("website-client")
    output, capture = root / "browser-pipe-client", root / "browser-pipe-capture"
    endpoint = site.url.rsplit("/", 1)[0] + "/graphql"
    identifier = (
        "service-" + hashlib.sha256(("graphql\0" + endpoint).encode()).hexdigest()[:16]
    )
    script = pipeline(
        [
            [
                executable,
                "discover",
                "--url",
                site.url,
                "--explore",
                "--max-pages",
                "8",
                "--max-actions",
                "12",
                "--explore-seconds",
                "20",
                "--max-probes",
                "8",
                "--emit-manifest",
                "--output",
                str(capture),
                "--browser",
                "chromium",
                "--browser-path",
                "/work/docker/test-chromium.sh",
            ],
            [executable, "normalize", "--input", "-"],
            [
                executable,
                "generate",
                "--manifest",
                "-",
                "--service",
                identifier,
                "--name",
                "browser-pipe-client",
                "--output",
                str(output),
            ],
        ]
    )
    site.exploration = True
    site.introspection_disabled = True
    writes_before = site.api_writes
    command = Command(
        site, executable="/bin/bash", args=["-o", "pipefail", "-c", script]
    )
    try:
        eventually(command.ready)
        command.connect()
        complete_login(command, site)
        os.write(command.terminal, b"\n")
        assert command.wait(timeout_seconds=60) == 0, command.output
        command.cleanup_verified()
        assert_no_credentials(command.output, site)
        report = json.loads((output / "generation.json").read_text())
        assert report["generationMode"] == "operation-documents"
        assert report["schemaValidated"] is False
        assert report["lineage"]["service"]["id"] == identifier
        assert report["lineage"]["sourceBundles"]
        assert site.api_writes == writes_before
        assert (capture / "manifest.json").exists()
        print(
            "PASS portable pipeline: real browser login through stdout/stdin to generated client",
            flush=True,
        )
    finally:
        command.stop()
        site.exploration = False
        site.introspection_disabled = False


def verify_complete_project(root, kind="operations"):
    """Install and test untouched complete output, then reproduce it from shipped inputs."""
    from website_client.discovery_manifest import (
        artifact,
        new_bundle,
        service,
        write_bundle,
    )

    if kind == "operations":
        document = artifact(
            "operations.graphql",
            "graphql-operations",
            "query Read { value }\nmutation Change($input: Input!) { change(input: $input) }",
            "external",
        )
        documents = [document]
        selected = service(
            "graphql", "https://example.test/graphql", operations=[document["id"]]
        )
        read = "Read"
    elif kind == "openapi":
        document = artifact(
            "openapi.json",
            "openapi",
            Path("/work/tests/fixtures/openapi.json").read_text(),
            "external",
        )
        documents = [document]
        selected = service("openapi", "https://example.test", document["id"])
        read = "get_item"
    else:
        schema = artifact(
            "schema.graphql",
            "graphql-schema",
            Path("/work/tests/fixtures/schema.graphql").read_text(),
            "external",
        )
        document = artifact(
            "operations.graphql",
            "graphql-operations",
            Path("/work/tests/fixtures/operations.graphql").read_text(),
            "external",
        )
        documents = [schema, document]
        selected = service(
            "graphql", "https://example.test/graphql", schema["id"], [document["id"]]
        )
        read = "GetItem"
    source = root / ("project-source-" + kind + ".json")
    write_bundle(new_bundle(documents, [selected]), source)
    output, rebuilt = (
        root / ("complete-project-" + kind),
        root / ("rebuilt-project-" + kind),
    )
    subprocess.run(
        [
            "website-client",
            "build",
            "--manifest",
            str(source),
            "--name",
            "complete-project",
            "--login-url",
            "https://example.test/login",
            "--session-header",
            "x-app-context",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "website-client",
            "build",
            "--project",
            str(output / "client-project.json"),
            "--output",
            str(rebuilt),
        ],
        check=True,
        capture_output=True,
    )
    snapshot = lambda directory: {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    assert snapshot(output) == snapshot(rebuilt)
    env = root / ("project-env-" + kind)
    subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    python = str(env / "bin/python")
    environment = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": "/opt/client-wheels",
    }
    subprocess.run(
        ["poetry", "--directory", str(output), "build", "--format", "wheel"],
        check=True,
        capture_output=True,
    )
    wheel = next((output / "dist").glob("*.whl"))
    subprocess.run(
        [python, "-m", "pip", "install", str(wheel), "pytest"],
        env=environment,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [python, "-m", "pytest", "-q", str(output / "tests")],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [str(env / "bin/complete-project"), read, "--help"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    print(
        "PASS complete project ("
        + kind
        + "): untouched install, generated tests and byte-identical rebuild",
        flush=True,
    )


def verify_page_record_discovery(site, root, complete_login, assert_no_credentials):
    """A delayed listing on the starting page must reach the generated client's output."""
    from graphql import build_schema, print_schema
    from website_client.discovery_manifest import read_manifest
    from website_client.discovery_manifest.records import Protocol

    schema, disabled = site.schema, site.introspection_disabled
    site.schema = build_schema(
        print_schema(schema)
        + "\nextend type Query { records(first: Int, includeDetails: Boolean, search: String): RecordConnection! }\ntype RecordConnection { nodes: [Record!]! pageInfo: RecordPageInfo! }\ntype Record { id: ID! status: String! }\ntype RecordPageInfo { endCursor: String hasNextPage: Boolean! }"
    )
    site.page_records = True
    site.introspection_disabled = True
    output = root / "page-record-discovery"
    command = Command(
        site,
        args=[
            "discover",
            "--url",
            site.url,
            "--explore",
            "--max-pages",
            "1",
            "--max-actions",
            "1",
            "--max-depth",
            "0",
            "--explore-seconds",
            "15",
            "--max-probes",
            "1",
            "--output",
            str(output),
            "--browser",
            "chromium",
            "--browser-path",
            "/work/docker/test-chromium.sh",
        ],
    )
    before = site.api_writes
    try:
        eventually(command.ready)
        command.connect()
        complete_login(command, site)
        command.connection.call(
            "Runtime.evaluate",
            {"expression": "sessionStorage.setItem('record-scope','selected')"},
        )
        os.write(command.terminal, b"\n")
        assert command.wait(timeout_seconds=60) == 0, command.output
        command.cleanup_verified()
        command.driver.stop()
        command.driver = None
        manifest = read_manifest(output / "manifest.json")
        target = next(
            item for item in manifest.services if item.protocol is Protocol.GRAPHQL
        )
        documents = [
            item for item in manifest.artifacts if item.id in target.operation_artifacts
        ]
        assert any("query ListRecords" in item.content for item in documents)
        report = json.loads((output / "self-discovery.json").read_text())["exploration"]
        assert any(
            "localhost" in item["url"] and item.get("sourceInspected")
            for item in report["frontendModules"]
        )
        project = root / "page-record-client"
        subprocess.run(
            [
                "website-client",
                "build",
                "--manifest",
                str(output / "manifest.json"),
                "--service",
                target.id,
                "--name",
                "page-record-client",
                "--login-url",
                site.url,
                "--output",
                str(project),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["poetry", "--directory", str(project), "build", "--format", "wheel"],
            check=True,
            capture_output=True,
        )
        env = root / "page-record-env"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        subprocess.run(
            [
                str(env / "bin/python"),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                "/opt/client-wheels",
                str(next((project / "dist").glob("*.whl"))),
            ],
            check=True,
            capture_output=True,
        )
        result = root / "page-record-result.json"
        call = Command(
            site,
            executable=str(env / "bin/page-record-client"),
            args=[
                "ListRecords",
                "--login",
                "--output",
                str(result),
                "--browser",
                "chromium",
                "--browser-path",
                "/work/docker/test-chromium.sh",
            ],
        )
        try:
            eventually(call.ready)
            call.connect()
            complete_login(call, site)
            os.write(call.terminal, b"\n")
            assert call.wait() == 0, call.output
            call.cleanup_verified()
            records = json.loads(result.read_text())["records"]["nodes"]
            assert records == [{"id": "record-7", "status": "SUBMITTED"}]
            assert_no_credentials(call.output, site)
        finally:
            call.stop()
        assert site.api_writes == before
        print(
            "PASS page discovery: delayed asset-origin listing through independently generated client",
            flush=True,
        )
    finally:
        command.stop()
        site.page_records = False
        site.introspection_disabled = disabled
        site.schema = schema
