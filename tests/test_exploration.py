import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from website_client.browser import BrowserError
from website_client.cli import main
from website_client.contracts import extract_documents, FrontendContracts
from website_client.discovery import Discovery
from website_client.exploration import SiteExplorer, validate_exploration
from website_client.exploration_policy import (
    clean_navigation,
    graphql_read,
    request_allowed,
    safe_address,
)


def request(
    method="GET",
    url="https://example.test/items",
    payload=None,
    navigation=False,
    resource_type="fetch",
):
    return SimpleNamespace(
        url=url,
        method=method,
        post_data=json.dumps(payload) if payload is not None else None,
        resource_type=resource_type,
        is_navigation_request=lambda: navigation,
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://other.test/orders",
        "http://example.test/orders",
        "javascript:alert(1)",
        "/logout",
        "/items/delete",
        "/orders?action=cancel",
        "/orders?token=secret",
        "/statement.pdf",
        "#heading",
    ],
)
def test_navigation_excludes_actions_external_sites_and_downloads(href):
    assert (
        clean_navigation("https://example.test/app", href, ("example.test", 443))
        is None
    )


def test_spa_and_pagination_links_preserve_runtime_addresses():
    assert (
        clean_navigation(
            "https://example.test/app", "/#/orders?page=2", ("example.test", 443)
        )
        == "https://example.test/#/orders?page=2"
    )
    assert (
        clean_navigation("https://example.test/app", "?page=2", ("example.test", 443))
        == "https://example.test/app?page=2"
    )
    assert "customer@example" not in safe_address(
        "https://example.test/orders?search=customer@example.test"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "mutation Save { save }"},
        {
            "operationName": "Save",
            "extensions": {"persistedQuery": {"sha256Hash": "hash"}},
        },
        [{"query": "query Read { items }"}, {"query": "mutation Save { save }"}],
        {"query": "query A { items } query B { items }"},
        {"query": "broken"},
    ],
)
def test_only_selected_graphql_queries_are_readable(payload):
    assert graphql_read(payload) is False
    assert (
        request_allowed(request("POST", payload=payload), ("example.test", 443))
        is not None
    )


def test_selected_query_may_share_document_with_an_unselected_mutation():
    payload = {
        "query": "query Read { items } mutation Save { save }",
        "operationName": "Read",
    }
    assert graphql_read(payload)
    assert (
        request_allowed(request("POST", payload=payload), ("example.test", 443)) is None
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_unknown_writes_are_blocked(method):
    assert request_allowed(
        request(method, payload={"amount": 12}), ("example.test", 443)
    )


def test_get_graphql_mutation_and_off_site_redirect_are_blocked():
    assert request_allowed(
        request(url="https://example.test/graphql?query=mutation%20Save%20%7Bsave%7D"),
        ("example.test", 443),
    )
    assert (
        request_allowed(
            request(url="https://other.test/page", navigation=True),
            ("example.test", 443),
        )
        == "off_site_navigation"
    )
    assert (
        request_allowed(
            request(
                url="https://cdn.example.test/create-form.js", resource_type="script"
            ),
            ("example.test", 443),
        )
        is None
    )


def test_source_literals_include_writes_without_executing_source(tmp_path):
    source = 'const value = {body: "mutation Add($name: String!) { add(name: $name) { id } }"}; throw Error("must not execute");'
    documents = extract_documents(source)
    assert len(documents) == 1
    assert "mutation Add" in documents[0]
    assert (
        extract_documents('gql`query Dynamic { item(id: "${secret}") { id } }`') == []
    )
    assert extract_documents("graphql`query Read { items { id } }`")


def test_frontend_declarations_are_separate_from_observed_endpoints():
    connection = Mock()
    connection.call.return_value = {
        "body": 'const source={body: "mutation Save($name: String!) { save(name: $name) { id } }"};'
    }
    discovery = Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    frontend.event(
        "Network.responseReceived",
        {
            "requestId": "1",
            "type": "Script",
            "response": {"url": "https://example.test/create-form.js"},
        },
    )
    frontend.event("Network.loadingFinished", {"requestId": "1"})
    frontend.drain()
    item = frontend.modules[0]["operations"][0]
    assert item["kind"] == "mutation" and item["executed"] is False
    assert item["variables"] == {"name": "String!"}
    assert discovery.documents and not discovery.endpoints
    assert "mutation Save" in discovery.files("browser")["operations.graphql"]


def test_interception_records_write_shape_without_values():
    browser = SimpleNamespace(
        current_url=lambda: "https://example.test/app", _origin=("example.test", 443)
    )
    explorer = SiteExplorer(browser)
    intercepted = Mock()
    intercepted.request = request(
        "POST", payload={"amount": 1234, "customer": "private-name"}
    )
    explorer._guard(intercepted)
    intercepted.abort.assert_called_once()
    intercepted.continue_.assert_not_called()
    assert (
        explorer.blocked[0]["requestSchema"]["properties"]["amount"]["type"]
        == "integer"
    )
    assert "private-name" not in json.dumps(explorer.blocked)
    assert "1234" not in json.dumps(explorer.blocked)


def test_exploration_refuses_a_session_with_active_service_workers():
    browser = SimpleNamespace(
        current_url=lambda: "https://example.test/app", _block_service_workers=False
    )
    with pytest.raises(BrowserError, match="service workers"):
        SiteExplorer(browser).prepare()


@pytest.mark.parametrize(
    "settings", [(0, 60, 3, 120), (20, 0, 3, 120), (20, 60, -1, 120), (20, 60, 3, 0)]
)
def test_exploration_budgets_are_validated(settings):
    with pytest.raises(BrowserError):
        validate_exploration(*settings)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--har", "file.har", "--explore"],
        ["--url", "https://example.test", "--max-pages", "3"],
        ["--url", "https://example.test", "--explore", "--max-pages", "0"],
    ],
)
def test_exploration_cli_validation_precedes_login(monkeypatch, tmp_path, arguments):
    factory = Mock(side_effect=AssertionError("Must validate first"))
    monkeypatch.setattr("website_client.cli.BrowserSession", factory)
    assert main(["discover", "--output", str(tmp_path / "new"), *arguments]) == 1
    factory.assert_not_called()
