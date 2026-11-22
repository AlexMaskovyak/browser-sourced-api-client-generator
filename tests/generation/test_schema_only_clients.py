"""Native schema-only Ariadne operations, with no authored operation documents."""

import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading

from graphql import build_schema, graphql_sync, get_introspection_query
import pytest

from website_client.generation import generate

SDL = """
enum Status { READY }
type Record { id: ID!, title: String!, state: Status!, detail(required: String!): String }
interface Abstract { id: ID! }
type Recursive { child: Recursive }
type Query { status: String!, record(id: ID!): Record!, abstract: Abstract, recursive: Recursive }
"""


@pytest.mark.parametrize("format", ["sdl", "introspection"])
def test_schema_only_native_builders_python_and_unattended_cli(
    tmp_path, monkeypatch, format
):
    schema = build_schema(SDL)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(value)
            result = graphql_sync(
                schema,
                value["query"],
                variable_values=value.get("variables"),
                root_value={
                    "status": lambda _: "ready",
                    "record": lambda _, id: {
                        "id": id,
                        "title": "Example",
                        "state": "READY",
                    },
                },
            )
            body = json.dumps(result.formatted).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/graphql"
    source = tmp_path / "schema"
    source.write_text(
        SDL
        if format == "sdl"
        else json.dumps(graphql_sync(schema, get_introspection_query()).formatted)
    )
    output = tmp_path / "client"
    try:
        report = generate(
            output, "schema-only", schema=source, runtime_config={"endpoint": endpoint}
        )
        assert report["generationMode"] == "graphql-schema-only"
        assert set(report["operations"]) == {"query_status", "query_record"}
        assert {item["reason"] for item in report["coverage"]["exclusions"]} == {
            "unsupported_selection",
            "unsupported_abstract_selection",
        }
        monkeypatch.syspath_prepend(str(output))
        module = importlib.import_module("schema_only")
        builders = importlib.import_module("schema_only.custom_queries")
        fields = importlib.import_module("schema_only.custom_fields")
        with module.client_context() as client:
            assert client.query(builders.Query.status(), operation_name="Status") == {
                "status": "ready"
            }
            assert client.query(
                builders.Query.record(id="7").fields(fields.RecordFields.id),
                operation_name="Record",
            ) == {"record": {"id": "7"}}
        inputs = tmp_path / "input.json"
        inputs.write_text('{"id":"7"}')
        environment = {
            key: value for key, value in os.environ.items() if key != "PYTHONPATH"
        }
        for operation, arguments, expected in [
            ("query_status", [], {"status": "ready"}),
            (
                "query_record",
                ["--input", str(inputs)],
                {"record": {"id": "7", "state": "READY", "title": "Example"}},
            ),
        ]:
            result = subprocess.run(
                [sys.executable, "-m", "schema_only._cli", operation, *arguments],
                cwd=output,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == expected
        assert not (output / "operations.graphql").exists()
        assert len(calls) == 4
    finally:
        server.shutdown()
        server.server_close()
        for name in list(sys.modules):
            if name == "schema_only" or name.startswith("schema_only."):
                del sys.modules[name]


def test_schema_prose_and_sensitive_fields_are_not_packaged(tmp_path):
    source = tmp_path / "schema.graphql"
    source.write_text(
        '"CANARY_DESCRIPTION" type Query { status: String!, CANARY_SECRET: String }'
    )
    output = tmp_path / "client"
    report = generate(output, "private-schema", schema=source)
    for path in output.rglob("*"):
        if path.is_file():
            assert b"CANARY" not in path.read_bytes()

    assert report["operations"] == ["query_status"]
    assert report["coverage"]["exclusions"][0]["reason"] == "sensitive_structure"


def test_native_reserved_aliases_collisions_and_privacy_interfaces(
    tmp_path, monkeypatch
):
    from website_client.graphql_admission import safe_schema

    schema, excluded = safe_schema(
        "interface CANARY_PRIVATE { id:ID } type Record implements CANARY_PRIVATE { id:ID } type Query { status:String, record:Record }"
    )

    assert "record" not in schema
    assert "CANARY" not in schema
    assert "status" in schema
    source = tmp_path / "reserved.graphql"
    source.write_text(
        "type Record { from:String, class:String, camelCase:String } type Query { class:String, record:Record, status(optional:String,required:String!):String, fooBar:String, foo_bar:String }"
    )
    output = tmp_path / "client"
    generate(output, "reserved-fields", schema=source)
    metadata = json.loads((output / "reserved_fields" / "_operations.json").read_text())
    assert metadata["query_class"]["field"] == "class_"
    assert metadata["query_record"]["selection"] == ["camel_case", "class_", "from_"]
    assert "query_fooBar" not in metadata
    assert "query_foo_bar" not in metadata
    monkeypatch.syspath_prepend(str(output))
    wrapper = importlib.import_module("reserved_fields._cli")
    for item in metadata.values():
        assert callable(wrapper.load_operation_function(item))
    result = subprocess.run(
        [sys.executable, "-m", "reserved_fields._cli", "--help"],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "query_status" in result.stdout
    assert "query_class" in result.stdout
