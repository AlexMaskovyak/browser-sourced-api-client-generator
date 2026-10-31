import http.server
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from graphql import build_schema, graphql_sync
import pytest

from website_client.browser import BrowserError
from website_client.generation import generate


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    root = tmp_path_factory.mktemp("real-generators")
    rest, graphql = root / "rest", root / "graphql"
    rest_report = generate(rest, "sample-rest", openapi=FIXTURES / "openapi.json")
    graphql_report = generate(
        graphql,
        "sample-graphql",
        schema=FIXTURES / "schema.graphql",
        operations=FIXTURES / "operations.graphql",
    )
    return rest, graphql, rest_report, graphql_report


@pytest.fixture
def service():
    counts = {"reads": 0, "writes": 0}
    schema = build_schema((FIXTURES / "schema.graphql").read_text())
    root = {
        "item": lambda _info, id: {"id": id, "name": "Example"},
        "createItem": lambda _info, name: {"id": "2", "name": name},
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, value, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_GET(self):
            counts["reads"] += 1
            self.send({"id": int(self.path.rsplit("/", 1)[-1]), "name": "Example"})

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/graphql":
                key = "writes" if "mutation" in value["query"] else "reads"
                counts[key] += 1
                self.send(
                    graphql_sync(
                        schema,
                        value["query"],
                        root_value=root,
                        variable_values=value.get("variables"),
                    ).formatted
                )
            else:
                assert value == {"name": "Created"}
                counts["writes"] += 1
                self.send({"id": 2}, 201)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", counts
    server.shutdown()
    server.server_close()


def command(project, package, args, stdin=None):
    environment = {**os.environ, "PYTHONPATH": str(project)}
    return subprocess.run(
        [sys.executable, "-m", package + "._cli", *args],
        env=environment,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=20,
    )


def test_real_upstream_generators_emit_standalone_packages(generated):
    rest, graphql, rest_report, graphql_report = generated
    assert rest_report["generator"] == "openapi-python-client"
    assert graphql_report["generator"] == "ariadne-codegen"
    for project in (rest, graphql):
        assert (project / "pyproject.toml").exists()
        assert not list(project.rglob("*.js"))
        assert all(
            "import website_client" not in path.read_text()
            for path in project.rglob("*.py")
        )
        assert "websocket-client" in (project / "pyproject.toml").read_text()
    assert (graphql / "sample_graphql/client.py").exists()
    assert (rest / "sample_rest/api/default/get_item.py").exists()


def test_generated_help_has_actual_inputs_and_write_labels(generated):
    rest, graphql, *_ = generated
    for project, package, operation, field in (
        (rest, "sample_rest", "get_item", "item_id"),
        (graphql, "sample_graphql", "GetItem", "id"),
    ):
        result = command(project, package, [operation, "--help"])
        assert result.returncode == 0, result.stderr
        assert (
            field in result.stdout
            and "required" in result.stdout
            and "READS remote data" in result.stdout
        )
        assert "--login" in result.stdout and "--login-url" in result.stdout
    result = command(graphql, "sample_graphql", ["CreateItem", "--help"])
    assert "WRITES remote data" in result.stdout


def test_generated_openapi_cli_calls_typed_methods_and_guards_writes(
    generated, service, tmp_path
):
    rest = generated[0]
    url, counts = service
    inputs = tmp_path / "input.json"
    inputs.write_text('{"item_id": 7}')
    result = command(
        rest, "sample_rest", ["get_item", "--url", url, "--input", str(inputs)]
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"id": 7, "name": "Example"}
    assert counts == {"reads": 1, "writes": 0}
    inputs.write_text('{"body": {"name": "Created"}}')
    denied = command(
        rest, "sample_rest", ["create_item", "--url", url, "--input", str(inputs)]
    )
    assert denied.returncode != 0 and counts["writes"] == 0
    result = command(
        rest,
        "sample_rest",
        [
            "create_item",
            "--url",
            url,
            "--input",
            str(inputs),
            "--confirm-write",
            "create_item",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert counts["writes"] == 1


def test_generated_graphql_cli_calls_typed_methods_and_guards_writes(
    generated, service, tmp_path
):
    graphql = generated[1]
    url, counts = service
    inputs = tmp_path / "input.json"
    inputs.write_text('{"id": "7"}')
    result = command(
        graphql,
        "sample_graphql",
        ["GetItem", "--url", url + "/graphql", "--input", str(inputs)],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"item": {"id": "7", "name": "Example"}}
    assert counts == {"reads": 1, "writes": 0}
    inputs.write_text('{"name": "Created"}')
    denied = command(
        graphql,
        "sample_graphql",
        ["CreateItem", "--url", url + "/graphql", "--input", str(inputs)],
    )
    assert denied.returncode != 0 and counts["writes"] == 0
    result = command(
        graphql,
        "sample_graphql",
        [
            "CreateItem",
            "--url",
            url + "/graphql",
            "--input",
            str(inputs),
            "--confirm-write",
            "CreateItem",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert counts["writes"] == 1


def test_generation_rejects_existing_output_and_invalid_graphql(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_text("keep")
    with pytest.raises(BrowserError, match="already exists"):
        generate(output, openapi=FIXTURES / "openapi.json")
    assert sentinel.read_text() == "keep"
    operations = tmp_path / "invalid.graphql"
    operations.write_text("query Invalid { missing }")
    with pytest.raises(BrowserError, match="valid against the schema"):
        generate(
            tmp_path / "invalid",
            schema=FIXTURES / "schema.graphql",
            operations=operations,
        )
    assert not (tmp_path / "invalid").exists()


def test_generated_login_rejects_pipes_and_unconfirmed_writes_before_launch(
    generated, service, tmp_path
):
    url, counts = service
    inputs = tmp_path / "input.json"
    inputs.write_text('{"item_id": 7}')
    result = command(
        generated[0],
        "sample_rest",
        ["get_item", "--url", url, "--input", str(inputs), "--login"],
    )
    assert result.returncode != 0 and "interactive terminal" in result.stderr
    result = command(
        generated[0], "sample_rest", ["create_item", "--url", url, "--login"]
    )
    assert result.returncode != 0 and "requires --confirm-write" in result.stderr
    result = command(
        generated[0], "sample_rest", ["get_item", "--url", url, "--browser", "chrome"]
    )
    assert result.returncode != 0 and "Browser options require --login" in result.stderr
    assert counts == {"reads": 0, "writes": 0}


def test_generated_cli_rejects_oversized_json_before_request(
    generated, service, tmp_path
):
    url, counts = service
    inputs = tmp_path / "oversized.json"
    inputs.write_text(json.dumps({"item_id": 7, "extra": "x" * (1024 * 1024)}))
    result = command(
        generated[0],
        "sample_rest",
        ["get_item", "--url", url, "--input", str(inputs)],
    )
    assert result.returncode != 0
    assert "Input exceeds 1 MiB" in result.stderr
    assert counts == {"reads": 0, "writes": 0}


@pytest.mark.parametrize("header_value", ["x" * 65536, "\u20ac" * 24000])
def test_generated_cli_bounds_header_bytes_without_echoing_credentials(
    generated, service, tmp_path, header_value
):
    url, counts = service
    inputs = tmp_path / "input.json"
    inputs.write_text('{"item_id": 7}')
    credential_marker = "private-header-value"
    headers = json.dumps(
        {"Authorization": credential_marker + header_value}, ensure_ascii=False
    )
    result = command(
        generated[0],
        "sample_rest",
        ["get_item", "--url", url, "--input", str(inputs), "--headers-stdin"],
        stdin=headers,
    )
    assert result.returncode != 0
    assert "Headers exceed 64 KiB" in result.stderr
    assert credential_marker not in result.stdout + result.stderr
    assert counts == {"reads": 0, "writes": 0}
