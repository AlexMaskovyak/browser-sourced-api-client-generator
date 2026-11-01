"""Verify installed commands against real terminal, browser and HTTPS behavior."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

from graphql import get_introspection_query
import website_client

from fixture_site import FIXTURES_DIRECTORY, Site
from terminal_command import Command, eventually

NETWORK_CAPTURE_SETTLE_SECONDS = 0.2
LOGIN_SCENARIOS = ("success", "unfinished", "cancelled", "closed", "terminated")
CLIENT_SCENARIOS = {
    "openapi": ("read", "write", "cancel", "redirect"),
    "graphql": ("read", "write", "closed"),
}


@dataclass(frozen=True)
class GeneratedClient:
    kind: str
    executable: Path


def run_program(arguments, *, check=True):
    """Capture build output; the interactive commands use Command instead."""
    return subprocess.run(
        arguments,
        check=check,
        capture_output=True,
        text=True,
    )


def complete_login(command, site):
    """Act as the human through the real password and verification forms."""
    command.fill('input[name="username"]', site.secrets["username"])
    command.fill('input[name="password"]', site.secrets["password"])
    command.click()
    command.fill('input[name="code"]', site.secrets["code"])
    command.click()
    eventually(lambda: command.node("h1"))


def assert_no_credentials(value, site):
    for secret in site.secrets.values():
        assert secret not in value


def close_browser(command):
    try:
        command.connection.call("Browser.close")
    except Exception:
        # Closing Chrome can disconnect the driver before the reply arrives.
        pass


def verify_login_scenario(site, scenario):
    site.counts = dict.fromkeys(site.counts, 0)
    command = Command(site)
    try:
        eventually(command.ready)
        command.connect()
        eventually(lambda: command.node('input[name="username"]'))

        if scenario == "success":
            complete_login(command, site)
            os.write(command.terminal, b"\n")
        elif scenario == "unfinished":
            os.write(command.terminal, b"\n")
        elif scenario == "cancelled":
            os.write(command.terminal, b"\x03")
        elif scenario == "terminated":
            os.kill(command.pid, signal.SIGTERM)
        else:
            close_browser(command)

        exit_code = command.wait()
        if scenario == "success":
            assert exit_code == 0, command.output
            assert '"sessionReady": true' in command.output
            assert '"apiVerified": false' in command.output
            assert site.counts == {"password": 1, "mfa": 1, "app": 1}
        else:
            assert exit_code != 0, command.output

        assert_no_credentials(command.output, site)
        command.cleanup_verified()
        print(f"PASS installed Python CLI: {scenario}", flush=True)
    finally:
        command.stop()


def capture_urls(site):
    base_url = site.url.rsplit("/", 1)[0]
    query_parameters = {
        "operationName": "GetItem",
        "query": "query GetItem($id: ID!) { item(id: $id) { id name } }",
        "variables": '{"id":"7"}',
    }
    introspection_parameters = {
        "operationName": "IntrospectionQuery",
        "query": get_introspection_query(),
    }
    return [
        base_url + "/items/7?search=" + site.secrets["password"],
        base_url + "/openapi.json",
        base_url + "/graphql?" + urlencode(query_parameters),
        base_url + "/graphql?" + urlencode(introspection_parameters),
    ]


def verify_capture(site, root):
    capture_output = root / "capture"
    command = Command(site, discover_output=capture_output)
    try:
        eventually(command.ready)
        command.connect()
        complete_login(command, site)
        os.write(command.terminal, b"\n")

        def capturing():
            command.pump()
            assert command.status is None, command.output
            return "Capture started." in command.output

        try:
            eventually(capturing)
        except AssertionError:
            raise AssertionError("Capture did not start: " + command.output) from None
        initial_read_count = site.api_reads
        urls = capture_urls(site)
        for expected_read_count, url in enumerate(urls, initial_read_count + 1):
            command.connection.call("Page.navigate", {"url": url})
            eventually(lambda: site.api_reads == expected_read_count)
            # Let completed-response events reach the recorder before navigating.
            time.sleep(NETWORK_CAPTURE_SETTLE_SECONDS)

        os.write(command.terminal, b"\n")
        assert command.wait() == 0, command.output
        assert site.api_reads == initial_read_count + len(urls), (
            "Discovery replayed an API request"
        )
        command.cleanup_verified()

        report = json.loads((capture_output / "discovery.json").read_text())
        assert report["source"] == "browser"
        assert report["liveVerified"] is False
        assert report["harEntries"] >= len(urls), report
        assert list(capture_output.glob("openapi-downloaded-*.json"))
        assert list(capture_output.glob("graphql-schema-downloaded-*.graphql"))
        assert list(capture_output.glob("operations-*.graphql"))
        for output_file in capture_output.iterdir():
            assert_no_credentials(output_file.read_text(), site)

        run_program(
            [
                shutil.which("website-client"),
                "discover",
                "--har",
                str(capture_output / "capture.har"),
                "--output",
                str(root / "offline"),
            ]
        )
        assert (root / "offline/discovery.json").exists()
        print(
            "PASS installed Python CLI: authenticated capture and offline HAR import",
            flush=True,
        )
        return capture_output
    finally:
        command.stop()


def generate_and_install_client(kind, capture_output, root):
    """Build a wheel, then install only its declared runtime dependencies."""
    project = root / (kind + "-client")
    name = "fixture-" + kind
    arguments = [
        shutil.which("website-client"),
        "generate",
        "--name",
        name,
        "--output",
        str(project),
    ]
    if kind == "openapi":
        definition = next(capture_output.glob("openapi-downloaded-*.json"))
        arguments += ["--openapi", str(definition)]
    else:
        schema = next(capture_output.glob("graphql-schema-downloaded-*.graphql"))
        arguments += [
            "--graphql-schema",
            str(schema),
            "--operations",
            str(FIXTURES_DIRECTORY / "operations.graphql"),
        ]
    run_program(arguments)
    run_program(
        [
            "poetry",
            "--directory",
            str(project),
            "build",
            "--format",
            "wheel",
        ]
    )

    environment = root / (kind + "-installed")
    run_program([sys.executable, "-m", "venv", str(environment)])
    runtime_python = environment / "bin/python"
    wheel = next((project / "dist").glob("*.whl"))
    run_program(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            "/opt/client-wheels",
            str(wheel),
        ]
    )

    # A generated client must run without the generator or either source tool.
    runtime_check = (
        "import importlib.util\n"
        "for package in (\n"
        "    'website_client', 'ariadne_codegen', 'openapi_python_client'\n"
        "):\n"
        "    assert importlib.util.find_spec(package) is None, package\n"
    )
    run_program([str(runtime_python), "-c", runtime_check])

    executable = environment / "bin" / name
    help_result = run_program([str(executable), "--help"])
    read_operation = "get_item" if kind == "openapi" else "GetItem"
    assert read_operation in help_result.stdout
    print(
        f"PASS installed generated Python client: {kind} wheel and endpoint help",
        flush=True,
    )
    return GeneratedClient(kind=kind, executable=executable)


def verify_self_discovery(site, root, *, disabled=False):
    output = root / ("automatic-disabled" if disabled else "automatic")
    site.self_discovery = True
    site.introspection_disabled = disabled
    writes_before = site.api_writes
    introspection_before = site.introspection_reads
    command = Command(
        site,
        args=[
            "discover",
            "--url",
            site.url,
            "--auto",
            "--observe-seconds",
            "1",
            "--max-probes",
            "8",
            "--output",
            str(output),
            "--browser",
            "chromium",
            "--browser-path",
            "/work/docker/test-chromium.sh",
        ],
    )
    try:
        eventually(command.ready)
        command.connect()
        complete_login(command, site)
        os.write(command.terminal, b"\n")
        # Automatic discovery needs only the login confirmation, unlike capture.
        assert command.wait() == 0, command.output
        command.cleanup_verified()
        report = json.loads((output / "self-discovery.json").read_text())
        assert len(report["probes"]) <= 8
        graphql_probe = next(
            item for item in report["probes"] if item["kind"] == "graphql"
        )
        assert graphql_probe["outcome"] == (
            "schema_unavailable" if disabled else "found"
        )
        assert (
            bool(list(output.glob("graphql-schema-downloaded-*.graphql")))
            is not disabled
        )
        assert list(output.glob("openapi-downloaded-*.json"))
        assert site.introspection_reads == introspection_before + 1
        assert site.api_writes == writes_before
        assert site.redirect_hits == 0
        for output_file in output.iterdir():
            assert_no_credentials(output_file.read_text(), site)
        print(
            "PASS automatic schema discovery: "
            + ("introspection disabled" if disabled else "OpenAPI and GraphQL"),
            flush=True,
        )
        return output
    finally:
        command.stop()
        site.self_discovery = False
        site.introspection_disabled = False


def operation_inputs(kind, scenario):
    if scenario == "write":
        if kind == "openapi":
            return "create_item", {"body": {"name": "Created"}}
        return "CreateItem", {"name": "Created"}

    if kind == "openapi":
        # Item 7 is the normal fixture; item 8 intentionally returns a redirect.
        item_id = 8 if scenario == "redirect" else 7
        return "get_item", {"item_id": item_id}
    return "GetItem", {"id": "7"}


def generated_arguments(client, site, scenario, root):
    operation, inputs = operation_inputs(client.kind, scenario)
    input_path = root / f"{client.kind}-{scenario}.json"
    input_path.write_text(json.dumps(inputs))
    base_url = site.url.rsplit("/", 1)[0]
    endpoint_url = base_url if client.kind == "openapi" else base_url + "/graphql"

    arguments = [
        operation,
        "--url",
        endpoint_url,
        "--input",
        str(input_path),
        "--login",
        "--login-url",
        site.url,
        "--browser",
        "chromium",
        "--browser-path",
        "/work/docker/test-chromium.sh",
    ]
    if scenario == "write":
        # This must fail before login, even with valid inputs and browser flags.
        denied = run_program([str(client.executable), *arguments], check=False)
        assert denied.returncode != 0
        assert "requires --confirm-write" in denied.stderr
        arguments += ["--confirm-write", operation]
    return arguments


def assert_generated_request_counts(site, scenario, reads_before, writes_before):
    expected_reads = 1 if scenario in {"read", "redirect"} else 0
    expected_writes = 1 if scenario == "write" else 0
    assert site.api_reads == reads_before + expected_reads
    assert site.api_writes == writes_before + expected_writes
    assert site.redirect_hits == 0


def verify_generated_scenario(client, site, scenario, root):
    arguments = generated_arguments(client, site, scenario, root)
    reads_before = site.api_reads
    writes_before = site.api_writes
    command = Command(site, executable=str(client.executable), args=arguments)
    try:
        eventually(command.ready)
        command.connect()
        if scenario == "cancel":
            os.write(command.terminal, b"\x03")
        elif scenario == "closed":
            close_browser(command)
        else:
            complete_login(command, site)
            os.write(command.terminal, b"\n")

        exit_code = command.wait()
        if scenario in {"read", "write"}:
            assert exit_code == 0, command.output
            assert '"id":' in command.output
        else:
            assert exit_code != 0, command.output

        assert_generated_request_counts(
            site,
            scenario,
            reads_before,
            writes_before,
        )
        assert_no_credentials(command.output, site)
        command.cleanup_verified()
        print(
            f"PASS generated browser login: {client.kind} {scenario}",
            flush=True,
        )
    finally:
        command.stop()


def assert_container_environment():
    assert sys.platform == "linux"
    assert os.getuid() != 0
    assert shutil.which("node") is None
    assert shutil.which("npm") is None
    assert not Path("/var/run/docker.sock").exists()
    assert not str(Path(website_client.__file__)).startswith("/work/src")


def main():
    assert_container_environment()
    with tempfile.TemporaryDirectory(prefix="website-client-fixture-") as directory:
        root = Path(directory)
        site = Site(root)
        try:
            for scenario in LOGIN_SCENARIOS:
                verify_login_scenario(site, scenario)

            verify_capture(site, root)
            capture_output = verify_self_discovery(site, root)
            verify_self_discovery(site, root, disabled=True)
            for kind, scenarios in CLIENT_SCENARIOS.items():
                client = generate_and_install_client(kind, capture_output, root)
                for scenario in scenarios:
                    verify_generated_scenario(client, site, scenario, root)
        finally:
            site.close()


if __name__ == "__main__":
    main()
