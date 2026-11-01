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
        assert command.wait() == 0, command.output
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
