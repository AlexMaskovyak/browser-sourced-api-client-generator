import base64
import copy
import json
import os

import pytest
from graphql import build_schema, get_introspection_query, graphql_sync

from website_client.browser import BrowserError
from website_client.cli import main
from website_client.definitions import graphql_schema, openapi_document
from website_client.discovery import Discovery, import_har, new_output, publish


SECRET = "private-value-never-persist"


def entry(url="https://example.test/api/items/123?search=" + SECRET, payload=None, response=None):
    request = {"url": url, "method": "GET" if payload is None else "POST", "headers": [{"name": "Authorization", "value": SECRET}],
               "cookies": [{"name": "session", "value": SECRET}]}
    if payload is not None: request["postData"] = {"text": json.dumps(payload)}
    return {"request": request, "time": 12, "response": {"status": 200,
        "headers": [{"name": "Set-Cookie", "value": SECRET}], "content": {"mimeType": "application/json",
        "text": json.dumps(response if response is not None else {"id": SECRET, "price": 23.81, "active": True})}}}


def test_har_redaction_and_inferred_openapi():
    discovery = Discovery()
    discovery.add(entry())
    discovery.add(entry("https://example.test/api/items/456?search=" + SECRET))
    files = discovery.files("har")
    assert SECRET not in json.dumps(files)
    assert "Authorization" not in json.dumps(files)
    assert len(discovery.endpoints) == 1
    spec = json.loads(files["openapi-inferred-0001.json"])
    openapi_document(spec)
    operation = spec["paths"]["/api/items/{id_3}"]["get"]
    assert operation["x-discovery-inferred"] is True
    assert operation["parameters"][0]["required"] is True
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["price"] == {"type": "number"}
    assert "required" not in schema
    har = json.loads(files["capture.har"])
    assert len(har["log"]["entries"]) == 2
    assert har["log"]["entries"][0]["request"]["headers"] == []
    assert "{" not in har["log"]["entries"][0]["request"]["url"]
    reimported = Discovery()
    for captured in har["log"]["entries"]: reimported.add(captured)
    assert next(iter(reimported.endpoints.values()))["path"] == "/api/items/{id_3}"


def test_distinct_graphql_operations_and_comments_not_collapsed():
    discovery = Discovery()
    for name in ("Items", "People"):
        discovery.add(entry("https://graphql.example.test/graphql?operationName=" + name, {
            "operationName": name, "query": f"# {SECRET}\nquery {name}($first: Int) {{ items(first: $first) {{ id }} }}",
            "variables": {"first": 10, "private": SECRET}}))
    files = discovery.files("har")
    assert len(discovery.endpoints) == 2
    assert len([name for name in files if name.startswith("operations-")]) == 2
    assert "query Items" in files["operations.graphql"] and "query People" in files["operations.graphql"]
    assert not any(name.startswith("openapi-inferred") for name in files)
    assert SECRET not in json.dumps(files)
    assert {item["graphql"]["name"] for item in discovery.endpoints.values()} == {"Items", "People"}


@pytest.mark.parametrize("literal", ['"' + SECRET + '"', "123", "PRIVATE_ENUM", "true"])
def test_graphql_literal_values_are_not_exported(literal):
    discovery = Discovery()
    discovery.add(entry("https://example.test/graphql", {"operationName": "Items", "query": f"query Items {{ items(value: {literal}) {{ id }} }}"}))
    files = discovery.files("har")
    assert not any(name.startswith("operations-") for name in files)
    assert SECRET not in json.dumps(files)
    assert next(iter(discovery.endpoints.values()))["graphql"]["document"] is None


def test_real_introspection_becomes_full_schema():
    schema = build_schema("type Query { items: [Item!]! } type Item { id: ID! }")
    introspection = graphql_sync(schema, get_introspection_query()).formatted
    discovery = Discovery()
    discovery.add(entry("https://example.test/graphql", {"operationName": "IntrospectionQuery", "query": get_introspection_query()}, introspection))
    files = discovery.files("har")
    actual = next(text for name, text in files.items() if name.startswith("graphql-schema-downloaded"))
    assert "type Query" in actual and "type Item" in actual
    assert graphql_schema(actual) == actual


def test_actual_openapi_stays_separate_from_inference():
    spec = {"openapi": "3.1.0", "info": {"title": "Example", "version": "1"}, "paths": {"/items": {"get": {"operationId": "list_items", "responses": {"200": {"description": "OK"}}}}}}
    discovery = Discovery()
    discovery.add(entry("https://example.test/openapi.json", response=spec))
    files = discovery.files("har")
    actual = json.loads(next(text for name, text in files.items() if name.startswith("openapi-downloaded")))
    assert actual == spec
    assert "openapi-inferred-0001.json" in files


def test_no_external_reference_resolution():
    spec = {"openapi": "3.1.0", "info": {"title": "Example", "version": "1"}, "paths": {}, "components": {"schemas": {"A": {"$ref": "https://example.test/private"}}}}
    with pytest.raises(BrowserError, match="Bundle external"): openapi_document(spec)
    with pytest.raises(BrowserError, match="Operations alone"): graphql_schema("query Items { items { id } }")


def test_auth_routes_are_omitted_and_bounds_are_reported():
    discovery = Discovery(1)
    discovery.add(entry("https://example.test/oauth/token"))
    discovery.add(entry())
    discovery.add(entry())
    report = json.loads(discovery.files("har")["discovery.json"])
    assert report["omitted"] == 2 and report["bounded"] is True
    assert report["completeServiceCoverage"] is False
    assert report["liveVerified"] is False


def test_offline_har_import_and_exclusive_private_output(tmp_path):
    source = tmp_path / "source.har"
    document = {"log": {"version": "1.2", "entries": [entry()]}}
    source.write_text(json.dumps(document))
    original = source.read_bytes()
    output = tmp_path / "result"
    assert main(["discover", "--har", str(source), "--output", str(output)]) == 0
    assert source.read_bytes() == original
    assert SECRET not in "".join(item.read_text() for item in output.iterdir())
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o700
        assert all(item.stat().st_mode & 0o777 == 0o600 for item in output.iterdir())
    with pytest.raises(BrowserError, match="already exists"): publish(output, {"capture.har": "replace"})


def test_base64_json_response_and_malformed_har(tmp_path):
    captured = entry()
    content = captured["response"]["content"]
    content["text"] = base64.b64encode(content["text"].encode()).decode()
    content["encoding"] = "base64"
    discovery = Discovery()
    discovery.add(captured)
    assert SECRET not in json.dumps(discovery.files("har"))
    source = tmp_path / "invalid.har"
    source.write_text(SECRET)
    with pytest.raises(BrowserError) as error: import_har(source)
    assert SECRET not in str(error.value)


def test_offline_har_never_opens_browser(tmp_path, monkeypatch):
    monkeypatch.setattr("website_client.cli.BrowserSession", lambda *_a, **_k: pytest.fail("Offline import opened a browser"))
    source = tmp_path / "source.har"
    source.write_text(json.dumps({"log": {"entries": [entry()]}}))
    assert main(["discover", "--har", str(source), "--output", str(tmp_path / "out")]) == 0


def test_unknown_graphql_is_not_folded_into_rest():
    discovery = Discovery()
    discovery.add(entry("https://example.test/graphql", {"query": "{ items { id } }"}))
    assert not any(name.startswith("openapi-inferred") for name in discovery.files("har"))
    assert next(iter(discovery.endpoints.values()))["graphqlUnmodeled"] is True


def test_graphql_definition_conflicts_are_explicit():
    discovery = Discovery()
    for field in ("id", "name"):
        discovery.add(entry("https://example.test/graphql", {"operationName": "Items", "query": f"query Items {{ items {{ {field} }} }}"}))
    files = discovery.files("har")
    assert "operations.graphql" not in files
    assert json.loads(files["discovery.json"])["graphqlDefinitionConflicts"] == ["Items"]


def test_actual_sdl_response_and_non_json_request_coverage():
    discovery = Discovery()
    captured = entry("https://example.test/schema.graphql")
    captured["response"]["content"] = {"mimeType": "application/graphql", "text": "type Query { hello: String! }"}
    discovery.add(captured)
    files = discovery.files("har")
    assert any(name.startswith("graphql-schema-downloaded") for name in files)
    captured = entry("https://example.test/custom")
    captured["request"].update(method="POST", postData={"text": "private-unmodeled-form"})
    discovery.add(captured)
    files = discovery.files("har")
    assert "private-unmodeled-form" not in json.dumps(files)
    assert any(item.get("requestBodyUnmodeled") for item in discovery.endpoints.values())


def test_listing_contract_exports_boolean_defaults_and_bounded_pagination():
    discovery = Discovery()
    query = 'query Expenses($includeVendors: Boolean = false, $team: Boolean!) { member { expenses(team: $team) { vendors(first: 100) { nodes { id name } } } } }'
    discovery.add(entry('https://example.test/graphql', {'operationName':'Expenses','query':query,'variables':{'team':True}}))
    document = next(iter(discovery.endpoints.values()))['graphql']['document']
    assert document is not None
    assert 'Boolean = false' in document and 'first: 100' in document
    assert 'query Expenses' in discovery.files('browser')['operations.graphql']


@pytest.mark.parametrize('fragment',['vendor(id: 123)', 'vendors(first: 999999999)', 'vendors(first: -123)', 'vendors(name: "private-person")'])
def test_data_literals_and_unbounded_controls_remain_omitted(fragment):
    discovery=Discovery()
    discovery.add(entry('https://example.test/graphql',{'operationName':'Expenses','query':'query Expenses { '+fragment+' { id } }'}))
    metadata=next(iter(discovery.endpoints.values()))['graphql']
    assert metadata['document'] is None
    assert metadata['documentOmissionReason']=='opaque_data_literal'
