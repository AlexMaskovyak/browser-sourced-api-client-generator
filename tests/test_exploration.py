import json
import time
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
    connection.call.assert_not_called()
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


def test_websocket_guard_never_connects_to_the_server_or_waits_for_close():
    browser = SimpleNamespace(current_url=lambda: "https://example.test/app")
    explorer = SiteExplorer(browser)
    socket = Mock()
    explorer._websocket(socket)
    socket.connect_to_server.assert_not_called()
    socket.close.assert_not_called()
    assert explorer.blocked[0]["sent"] is False


@pytest.mark.parametrize("status,expected_clicks", [(0, 1), (200, 3)])
def test_pagination_uses_request_progress_without_reading_page_contents(
    monkeypatch, status, expected_clicks
):
    page = Mock()
    page.url = "https://example.test/app"
    browser = SimpleNamespace(current_url=lambda: page.url, _page=page)
    explorer = SiteExplorer(browser, max_actions=3)
    explorer.deadline_seconds = time.monotonic() + 10
    capture = SimpleNamespace(discovery=SimpleNamespace(entries=[]))
    button = Mock()
    button.click.side_effect = lambda **_kwargs: capture.discovery.entries.append(
        {"response": {"status": status}}
    )
    monkeypatch.setattr(
        explorer, "_controls", lambda: iter([(button, "Next", "pagination", 0)])
    )
    monkeypatch.setattr(explorer, "_inspect", Mock())
    monkeypatch.setattr(explorer, "_settle", Mock())
    explorer._walk_controls(capture, Mock(), 0)
    assert button.click.call_count == expected_clicks
    page.content.assert_not_called()


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
        ["--url", "https://example.test", "--explore", "--observe-seconds", "3"],
    ],
)
def test_exploration_cli_validation_precedes_login(monkeypatch, tmp_path, arguments):
    factory = Mock(side_effect=AssertionError("Must validate first"))
    monkeypatch.setattr("website_client.cli.BrowserSession", factory)
    assert main(["discover", "--output", str(tmp_path / "new"), *arguments]) == 1
    factory.assert_not_called()


def test_frontend_inspects_scripts_loaded_from_asset_origins_for_owned_page():
    connection = Mock()
    connection.call.return_value = {
        "body": 'const document={query: "query Expenses($first:Int) { expenses(first:$first) { id status } }"};'
    }
    discovery = Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    frontend.event(
        "Network.requestWillBeSent",
        {
            "requestId": "asset",
            "type": "Script",
            "documentURL": "https://example.test/expenses",
            "request": {"url": "https://assets.example.net/expenses.js"},
        },
    )
    frontend.event(
        "Network.responseReceived",
        {
            "requestId": "asset",
            "type": "Script",
            "response": {"url": "https://assets.example.net/expenses.js"},
        },
    )
    frontend.event("Network.loadingFinished", {"requestId": "asset"})
    frontend.drain()
    assert frontend.modules[0]["sourceInspected"] is True
    assert frontend.modules[0]["operations"][0]["name"] == "Expenses"
    assert discovery.documents


def test_unowned_cross_origin_scripts_are_not_inspected():
    connection = Mock()
    frontend = FrontendContracts(connection, Discovery(), ("example.test", 443))
    frontend.event(
        "Network.requestWillBeSent",
        {
            "requestId": "other",
            "type": "Script",
            "documentURL": "https://other.test/app",
            "request": {"url": "https://assets.example.net/other.js"},
        },
    )
    frontend.event(
        "Network.responseReceived",
        {
            "requestId": "other",
            "type": "Script",
            "response": {"url": "https://assets.example.net/other.js"},
        },
    )
    frontend.event("Network.loadingFinished", {"requestId": "other"})
    frontend.drain()
    connection.call.assert_not_called()
    assert not frontend.modules


def test_document_inline_operation_declarations_are_inspected_without_exporting_values():
    connection = Mock()
    connection.call.return_value = {
        "body": '<script>window.secret="do-not-export";const doc={query:"query PageExpenses { expenses { id status } }"};</script>'
    }
    discovery = Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    frontend.event(
        "Network.responseReceived",
        {
            "requestId": "doc",
            "type": "Document",
            "response": {"url": "https://example.test/expenses"},
        },
    )
    frontend.event("Network.loadingFinished", {"requestId": "doc"})
    frontend.drain()
    assert frontend.modules[0]["sourceType"] == "Document"
    assert "PageExpenses" in discovery.files("browser")["operations.graphql"]
    assert "do-not-export" not in str(discovery.files("browser"))


def test_prepare_retains_the_authenticated_tab_and_session_storage():
    page = Mock()
    connection = Mock()
    browser = SimpleNamespace(
        current_url=lambda: "https://example.test/expenses",
        _origin=("example.test", 443),
        _page=page,
        _connection=connection,
        _context=Mock(),
        _block_service_workers=True,
        connection_for=Mock(),
    )
    explorer = SiteExplorer(browser)
    explorer.prepare()
    assert browser._page is page
    page.goto.assert_called_once_with("about:blank", wait_until="commit")
    page.close.assert_not_called()
    browser._context.new_page.assert_not_called()


def test_page_creation_control_precedes_global_add_menu(monkeypatch):
    """A global Add control must not consume the page's creation action budget."""
    page = SimpleNamespace(url="https://example.test/expenses/team")
    session = SimpleNamespace(current_url=lambda: page.url, _page=page)
    explorer = SiteExplorer(session, max_actions=1)
    explorer.deadline_seconds = time.monotonic() + 10
    global_add, expense_add = Mock(), Mock()
    for control in (global_add, expense_add):
        control.locator.return_value.count.return_value = 0
    monkeypatch.setattr(explorer, "_controls", lambda: iter([
        (global_add, "Add shortcuts", "form_dialog", 0),
        (expense_add, "Add expense", "form_dialog", 1),
    ]))
    monkeypatch.setattr(explorer, "_inspect", Mock())
    monkeypatch.setattr(explorer, "_settle", Mock())
    explorer._walk_controls(SimpleNamespace(discovery=SimpleNamespace(entries=[])), Mock(), 0,
                            forms_only=True)
    expense_add.click.assert_called_once()
    global_add.click.assert_not_called()


def test_source_budget_retains_feature_contract_after_shared_chunks():
    """More than sixty shared chunks must not hide the feature declaration."""
    connection = Mock()
    connection.call.return_value = {"body": "export const utility = 1;"}
    discovery = Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    for index in range(70):
        frontend.event("Network.responseReceived", {
            "requestId": str(index), "type": "Script",
            "response": {"url": f"https://example.test/shared-{index}.js"},
        })
        frontend.event("Network.loadingFinished", {"requestId": str(index)})
    frontend.drain()
    connection.call.return_value = {"body": 'const operation={query:"mutation CreateRecord($input: RecordInput!) { createRecord(input:$input) { id } }"};'}
    frontend.event("Network.responseReceived", {
        "requestId": "feature", "type": "Script",
        "response": {"url": "https://example.test/create.js"},
    })
    frontend.event("Network.loadingFinished", {"requestId": "feature"})
    frontend.drain()
    assert frontend.modules[-1]["operations"][0]["name"] == "CreateRecord"
    assert frontend.modules[-1]["operations"][0]["executed"] is False


def test_fragments_from_later_modules_complete_writes_without_execution():
    """A separately bundled response fragment must be included in the write document."""
    connection, discovery = Mock(), Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    def load(identifier, source):
        connection.call.return_value = {"body": source}
        frontend.event("Network.responseReceived", {
            "requestId": identifier, "type": "Script",
            "response": {"url": f"https://example.test/{identifier}.js"},
        })
        frontend.event("Network.loadingFinished", {"requestId": identifier})
        frontend.drain()
    load("create", 'const query="mutation Create($input: Input!) { create(input:$input) { ...Receipt } }";')
    assert not discovery.documents
    assert frontend.modules[0]["operations"][0]["unresolvedFragments"] == ["Receipt"]
    load("fragment", 'const fields="fragment Receipt on Receipt { id ...Amount }"; const amount="fragment Amount on Receipt { amount }";')
    assert len(discovery.documents) == 1
    text = next(iter(discovery.documents.values()))
    assert "fragment Receipt" in text and "fragment Amount" in text
    assert frontend.modules[0]["operations"][0]["documentExported"]
    load("conflict", 'const conflicting="fragment Receipt on Receipt { privateField }";')
    assert not discovery.documents
    assert frontend.modules[0]["operations"][0]["unresolvedFragments"] == ["Receipt"]


def test_cyclic_source_fragments_cannot_be_exported():
    connection, discovery = Mock(), Discovery()
    frontend = FrontendContracts(connection, discovery, ("example.test", 443))
    connection.call.return_value = {"body": 'const query="mutation Save { save { ...A } } fragment A on Item { ...B } fragment B on Item { ...A }";'}
    frontend.event("Network.responseReceived", {"requestId": "cycle", "type": "Script",
                   "response": {"url": "https://example.test/cycle.js"}})
    frontend.event("Network.loadingFinished", {"requestId": "cycle"})
    frontend.drain()
    assert not discovery.documents
    assert frontend.modules[0]["operations"][0]["unresolvedFragments"]


def test_first_page_cannot_spend_the_whole_graph_walk_on_pagination(monkeypatch):
    page = Mock()
    page.url = "https://example.test/records"
    browser = SimpleNamespace(current_url=lambda: page.url, _page=page)
    explorer = SiteExplorer(browser, max_pages=20, max_actions=60)
    explorer.deadline_seconds = time.monotonic() + 10
    capture = SimpleNamespace(discovery=SimpleNamespace(entries=[]))
    next_page = Mock()
    next_page.click.side_effect = lambda **_: capture.discovery.entries.append({"response": {"status": 200}})
    monkeypatch.setattr(explorer, "_controls", lambda: iter([(next_page, "Next", "pagination", 0)]))
    monkeypatch.setattr(explorer, "_inspect", Mock())
    monkeypatch.setattr(explorer, "_settle", Mock())
    explorer._walk_controls(capture, Mock(), 0)
    assert next_page.click.call_count == 3
    assert len(explorer.actions) < explorer.max_actions
