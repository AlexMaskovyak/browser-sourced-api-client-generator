"""Public signatures retain the upstream association between query names and values."""

import importlib
import json
import sys

from website_client.generation import generate_openapi_client


def test_two_query_parameters_keep_their_wire_names(tmp_path, monkeypatch):
    definition = {
        "openapi": "3.0.3",
        "info": {"title": "Records", "version": "1"},
        "paths": {
            "/record": {
                "get": {
                    "operationId": "get_record",
                    "parameters": [
                        {
                            "name": "z-last",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "a-first",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Record",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"subject": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    output = tmp_path / "client"
    generate_openapi_client(
        output,
        "query-record-client",
        json.dumps(definition),
        website="https://example.com",
    )
    monkeypatch.syspath_prepend(str(output))
    module = importlib.import_module("query_record_client")
    seen = {}

    def execute(self, name, arguments):
        seen.update(arguments)
        return name

    monkeypatch.setattr(module.Client, "_execute", execute)
    assert module.Client().get_record(a_first=7, z_last="second") == "get_record"
    upstream = importlib.import_module("query_record_client.api.default.get_record")
    request = upstream._get_kwargs(**seen)
    assert request["params"] == {"z-last": "second", "a-first": 7}
    inventory = json.loads((output / "query_record_client/_public.json").read_text())
    assert {
        parameter["wire"]: parameter["python"]
        for parameter in inventory["operations"]["get_record"]["parameters"]
    } == {"z-last": "z_last", "a-first": "a_first"}
    for name in list(sys.modules):
        if name.startswith("query_record_client"):
            sys.modules.pop(name)
