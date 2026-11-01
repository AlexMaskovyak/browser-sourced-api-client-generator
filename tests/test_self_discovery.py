import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from graphql import parse
import httpx
import pytest

from website_client.browser import BrowserError
from website_client.cli import main
from website_client.self_discovery import (
    DiscoveryObserver,
    SelfDiscovery,
    schema_url,
    validate_settings,
)
from website_client.session_http import BrowserLoginTransport


@pytest.mark.parametrize(
    "href",
    [
        "https://other.test/openapi.json",
        "/openapi.json?token=secret",
        "/logout",
        "/login/openapi.json",
        "http://example.test/openapi.json",
        "/orders/7",
        "https://user:secret@example.test/openapi.json",
    ],
)
def test_unrelated_or_credential_bearing_links_are_not_probed(href):
    assert schema_url("https://example.test/app", href) is None


def test_same_origin_schema_links_are_resolved():
    assert (
        schema_url("https://example.test/docs/", "openapi.yaml")
        == "https://example.test/docs/openapi.yaml"
    )


def test_observer_keeps_headers_scoped_and_discovers_only_after_login():
    observer = DiscoveryObserver("https://example.test/app")
    event = {
        "documentURL": "https://example.test/app",
        "request": {
            "url": "https://api.example.test/graphql?query=query%20Probe%20%7B__typename%7D&private=do-not-store",
            "method": "GET",
            "headers": {"Authorization": "private-token", "Cookie": "private-cookie"},
        },
    }
    observer.event("Network.requestWillBeSent", event)
    assert observer.graphql_endpoints == []
    observer.discovering = True
    observer.event(
        "Network.requestWillBeSent", {**event, "documentURL": "https://sso.test"}
    )
    assert observer.graphql_endpoints == []
    observer.event("Network.requestWillBeSent", event)
    assert observer.graphql_endpoints == ["https://api.example.test/graphql"]
    assert observer.headers == {
        ("api.example.test", 443): {"authorization": "private-token"}
    }
    assert "do-not-store" not in repr(vars(observer))
    observer.clear()
    assert observer.headers == {}


def session():
    page = Mock()
    page.locator.return_value.count.return_value = 0
    return SimpleNamespace(
        _url="https://example.test/app",
        _origin=("example.test", 443),
        _page=page,
        _connection=Mock(),
        _check=lambda: None,
    )


def test_shared_transport_closes_only_its_own_request_resources():
    browser = session()
    browser.close = Mock()
    transport = BrowserLoginTransport.for_session(
        browser,
        "https://example.test/openapi.json",
        "GET",
        {"Authorization": "private-token", "Cookie": "forbidden"},
    )
    assert transport._headers == {"authorization": "private-token"}
    transport.close()
    browser.close.assert_not_called()
    assert transport._session is None
    assert transport._headers == {}


@pytest.mark.parametrize(
    "status,outcome,body",
    [
        (403, "unauthorized", {}),
        (302, "redirect_blocked", {}),
        (404, "not_found", {}),
        (200, "schema_unavailable", {"errors": [{"message": "private service error"}]}),
    ],
)
def test_unsuccessful_introspection_is_reported_without_inventing_schema(
    monkeypatch, status, outcome, body
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        BrowserLoginTransport,
        "for_session",
        lambda *_args: httpx.MockTransport(respond),
    )
    engine = SelfDiscovery(session(), DiscoveryObserver("https://example.test/app"))
    engine._probe("graphql", "https://example.test/graphql")
    assert engine.attempts[0]["outcome"] == outcome
    assert not engine.capture.discovery.definitions
    assert "private service error" not in json.dumps(engine.attempts)
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert all(
        item.operation.value == "query"
        for item in parse(payload["query"]).definitions
        if item.kind == "operation_definition"
    )
    assert "__schema" in payload["query"]


def test_valid_download_uses_existing_openapi_validator(monkeypatch):
    document = json.loads((Path(__file__).parent / "fixtures/openapi.json").read_text())
    monkeypatch.setattr(
        BrowserLoginTransport,
        "for_session",
        lambda *_args: httpx.MockTransport(
            lambda request: httpx.Response(200, json=document)
        ),
    )
    engine = SelfDiscovery(session(), DiscoveryObserver("https://example.test/app"))
    engine._probe("definition", "https://example.test/openapi.json")
    assert engine.attempts[0]["outcome"] == "found"
    assert any(
        kind == "openapi" for kind, _digest in engine.capture.discovery.definitions
    )


def test_probe_limit_and_observer_cleanup(monkeypatch):
    browser = session()
    observer = DiscoveryObserver(browser._url)
    observer.headers[origin_key := ("example.test", 443)] = {"authorization": "private"}
    engine = SelfDiscovery(browser, observer, max_probes=2, observe_seconds=1)
    monkeypatch.setattr(
        engine,
        "_probe",
        lambda kind, endpoint: engine.attempts.append(
            {"kind": kind, "endpoint": endpoint}
        ),
    )
    clock = iter([0, 2])
    monkeypatch.setattr(
        "website_client.self_discovery.time.monotonic", lambda: next(clock)
    )
    _result, report = engine.run()
    assert len(report["probes"]) == 2
    assert report["candidatesNotProbed"] > 0
    assert report["completeServiceCoverage"] is False
    assert origin_key not in observer.headers
    assert observer.discovering is False
    browser._page.reload.assert_called_once()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--har", "input.har", "--auto"],
        ["--url", "https://example.test", "--max-probes", "2"],
        ["--url", "https://example.test", "--auto", "--max-probes", "0"],
        [
            "--url",
            "https://example.test",
            "--auto",
            "--graphql-endpoint",
            "https://example.test/graphql?token=secret",
        ],
    ],
)
def test_invalid_cli_combinations_fail_before_browser_launch(
    monkeypatch, tmp_path, arguments
):
    factory = Mock(side_effect=AssertionError("Must validate before login"))
    monkeypatch.setattr("website_client.cli.BrowserSession", factory)
    assert main(["discover", "--output", str(tmp_path / "new"), *arguments]) == 1
    factory.assert_not_called()


def test_authentication_paths_cannot_be_explicit_introspection_targets():
    with pytest.raises(BrowserError):
        validate_settings(12, 5, ["https://example.test/token"])
