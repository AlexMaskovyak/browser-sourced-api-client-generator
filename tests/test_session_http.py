import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from website_client.browser import BrowserError
from website_client.session_http import BrowserLoginTransport


def test_observed_session_headers_are_origin_scoped_and_transient():
    transport = BrowserLoginTransport(
        "https://api.example.test/graphql",
        "POST",
        "https://app.example.test",
        session_headers=["X-App-Context"],
    )
    event = {
        "documentURL": "https://app.example.test/home",
        "request": {
            "url": "https://api.example.test/graphql",
            "headers": {
                "Authorization": "private-bearer",
                "Cookie": "private-cookie",
                "X-App-Context": "private-context",
                "X-Unselected": "ignore",
            },
        },
    }
    transport._observe("Network.requestWillBeSent", event)
    assert transport._headers == {
        "authorization": "private-bearer",
        "x-app-context": "private-context",
    }
    transport._observe(
        "Network.requestWillBeSent",
        {**event, "documentURL": "https://sso.example.test"},
    )
    assert transport._headers["authorization"] == "private-bearer"
    assert "private" not in repr(transport)
    transport.close()
    assert not transport._headers


@pytest.mark.parametrize("name", ["Cookie", "Host", "Content-Length", "Bad\nName"])
def test_caller_cannot_select_browser_managed_session_headers(name):
    with pytest.raises(BrowserError):
        BrowserLoginTransport("https://example.test", "GET", session_headers=[name])


def prepare(monkeypatch, status=200):
    parent = Mock()
    parent.call.return_value = {"targetId": "target"}
    connection = Mock()
    connection.call.side_effect = lambda method, params=None: (
        {"body": base64.b64encode(b'{"id":7}').decode(), "base64Encoded": True}
        if method == "Fetch.getResponseBody"
        else {}
    )
    events = [
        {
            "requestId": "r1",
            "request": {
                "url": "https://example.test/items",
                "headers": {"User-Agent": "Native Chrome", "Cookie": "private-cookie"},
            },
        },
        {
            "requestId": "r1",
            "request": {"url": "https://example.test/items"},
            "responseStatusCode": status,
            "responseHeaders": [
                {"name": "Content-Type", "value": "application/json"},
                {"name": "Content-Encoding", "value": "gzip"},
            ],
        },
    ]
    connection.pump.side_effect = lambda **_kwargs: connection.on_event(
        "Fetch.requestPaused", events.pop(0)
    )
    target = Mock()

    def navigate(*_args, **_kwargs):
        while events:
            connection.pump()

    target.goto.side_effect = navigate
    context = Mock()
    context.new_page.return_value = target
    transport = BrowserLoginTransport("https://example.test/items", "POST")
    transport._session = SimpleNamespace(
        _connection=parent,
        _context=context,
        connection_for=lambda _target: connection,
        _check=lambda: None,
    )
    parent.target = target
    transport._headers["authorization"] = "private-bearer"
    return transport, connection, parent


def test_typed_request_uses_browser_interception_and_aborts_response_rendering(
    monkeypatch,
):
    transport, connection, parent = prepare(monkeypatch)
    request = httpx.Request(
        "POST", "https://example.test/items", json={"name": "Created"}
    )
    response = transport.handle_request(request)
    assert response.json() == {"id": 7}
    calls = connection.call.call_args_list
    continued = next(
        call.args[1] for call in calls if call.args[0] == "Fetch.continueRequest"
    )
    assert continued["method"] == "POST"
    assert json.loads(base64.b64decode(continued["postData"])) == {"name": "Created"}
    headers = {item["name"]: item["value"] for item in continued["headers"]}
    assert (
        headers["user-agent"] == "Native Chrome"
        and headers["authorization"] == "private-bearer"
    )
    assert headers["origin"] == "https://example.test"
    assert "content-encoding" not in response.headers
    assert any(
        call.args[0] == "Fetch.failRequest" and call.args[1]["errorReason"] == "Aborted"
        for call in calls
    )
    parent.target.close.assert_called_once()
    with pytest.raises(BrowserError, match="one request"):
        transport.handle_request(request)


def test_redirect_response_is_returned_without_following(monkeypatch):
    transport, connection, _parent = prepare(monkeypatch, status=302)
    response = transport.handle_request(
        httpx.Request("POST", "https://example.test/items", json={})
    )
    assert response.status_code == 302
    assert not any(
        call.args[0] == "Fetch.getResponseBody"
        for call in connection.call.call_args_list
    )
    assert (
        sum(
            call.args[0] == "Fetch.continueRequest"
            for call in connection.call.call_args_list
        )
        == 1
    )


def test_other_origin_or_method_is_rejected_before_native_request(monkeypatch):
    transport, _connection, parent = prepare(monkeypatch)
    for request in (
        httpx.Request("POST", "https://other.test/items"),
        httpx.Request("DELETE", "https://example.test/items"),
    ):
        with pytest.raises(BrowserError):
            transport.handle_request(request)
    parent.call.assert_not_called()


def test_httpx_entering_the_transport_again_reuses_the_owned_session(monkeypatch):
    browser = MagicMock()
    factory = Mock(return_value=browser)
    monkeypatch.setattr("website_client.session_http.BrowserSession", factory)
    transport = BrowserLoginTransport("https://example.test", "GET")
    with transport:
        with httpx.Client(transport=transport):
            assert transport.__enter__() is transport
    factory.assert_called_once()
    browser.confirm_login.assert_called_once()
    browser.close.assert_called_once()


def test_encoded_response_is_rejected_before_base64_decoding(monkeypatch):
    transport, _connection, _parent = prepare(monkeypatch)
    monkeypatch.setattr("website_client.session_http.MAX_BASE64_RESPONSE_CHARACTERS", 4)
    decoder = Mock(side_effect=AssertionError("Must reject before decoding"))
    monkeypatch.setattr("website_client.session_http.base64.b64decode", decoder)
    with pytest.raises(BrowserError, match="Encoded browser response"):
        transport.handle_request(
            httpx.Request("POST", "https://example.test/items", json={})
        )
    decoder.assert_not_called()


def test_decoded_response_has_its_own_byte_limit(monkeypatch):
    transport, _connection, _parent = prepare(monkeypatch)
    # The fixture response is eight bytes. Its base64 text fits the encoded
    # budget, but the decoded payload must still obey the separate byte limit.
    monkeypatch.setattr("website_client.session_http.MAX_RESPONSE_BODY_BYTES", 7)
    with pytest.raises(BrowserError, match="8 MiB limit"):
        transport.handle_request(
            httpx.Request("POST", "https://example.test/items", json={})
        )


def test_interception_failure_is_returned_after_navigation_and_cleans_up(monkeypatch):
    transport, connection, parent = prepare(monkeypatch)
    connection.call.side_effect = RuntimeError("private-protocol-error")
    with pytest.raises(BrowserError) as error:
        transport.handle_request(httpx.Request("POST", "https://example.test/items"))
    assert "private-protocol-error" not in str(error.value)
    parent.target.close.assert_called_once()
    assert transport._active_request is None
    assert transport._active_body is None


def test_aborted_navigation_keeps_intercepted_api_result(monkeypatch):
    transport, _connection, parent = prepare(monkeypatch)
    navigate = parent.target.goto.side_effect

    def aborted_after_response(*args, **kwargs):
        navigate(*args, **kwargs)
        raise RuntimeError("navigation aborted after response interception")

    parent.target.goto.side_effect = aborted_after_response
    response = transport.handle_request(
        httpx.Request("POST", "https://example.test/items")
    )
    assert response.json() == {"id": 7}
    parent.target.close.assert_called_once()


def test_explicit_shared_session_headers_are_retained_without_taking_ownership():
    session = SimpleNamespace(_url="https://app.example.test", close=Mock())
    transport = BrowserLoginTransport.for_session(
        session,
        "https://api.example.test/graphql",
        "POST",
        {"X-App-Context": "private-context"},
    )
    assert transport._headers == {"x-app-context": "private-context"}
    transport.close()
    session.close.assert_not_called()


def test_shared_session_ignores_managed_headers_and_rejects_excess_headers():
    session = SimpleNamespace(_url="https://app.example.test")
    transport = BrowserLoginTransport.for_session(
        session,
        "https://api.example.test/graphql",
        "POST",
        {"Cookie": "private", "Sec-Fetch-Site": "same-site"},
    )
    assert transport._headers == {}
    with pytest.raises(BrowserError):
        BrowserLoginTransport.for_session(
            session,
            "https://api.example.test/graphql",
            "POST",
            {f"X-Header-{number}": "private" for number in range(40)},
        )


def test_selected_protocol_identification_headers_follow_the_scoped_request(
    monkeypatch,
):
    transport, connection, _parent = prepare(monkeypatch)
    transport._allowed_headers.update(
        {"apollographql-client-name", "apollographql-client-version"}
    )
    transport._observe(
        "Network.requestWillBeSent",
        {
            "documentURL": "https://example.test/home",
            "request": {
                "url": "https://example.test/graphql",
                "headers": {
                    "apollographql-client-name": "example-ui",
                    "apollographql-client-version": "example-version",
                },
            },
        },
    )
    transport.handle_request(httpx.Request("POST", "https://example.test/items"))
    continued = next(
        call.args[1]
        for call in connection.call.call_args_list
        if call.args[0] == "Fetch.continueRequest"
    )
    headers = {item["name"]: item["value"] for item in continued["headers"]}
    assert headers["apollographql-client-name"] == "example-ui"
    assert headers["apollographql-client-version"] == "example-version"
