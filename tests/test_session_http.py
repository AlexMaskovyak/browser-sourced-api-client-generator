import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from website_client.browser import BrowserError, Connection
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
    monkeypatch.setattr(
        "website_client.session_http.Connection", lambda *_args, **_kwargs: connection
    )
    transport = BrowserLoginTransport("https://example.test/items", "POST")
    transport._session = SimpleNamespace(
        _connection=parent, _port=1234, _check=lambda: None
    )
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
    assert parent.call.call_args_list[-1].args[0] == "Target.closeTarget"
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


def test_protocol_retains_responses_to_other_outstanding_commands(monkeypatch):
    socket = Mock()
    socket.recv.side_effect = [
        json.dumps({"id": 1, "result": {"frameId": "frame"}}),
        json.dumps({"id": 2, "result": {}}),
    ]
    monkeypatch.setattr(
        "website_client.browser.websocket.create_connection",
        lambda *_args, **_kwargs: socket,
    )
    connection = Connection("ws://127.0.0.1:123/devtools/page/target", 123)
    connection.send("Page.navigate", {"url": "https://example.test"})
    assert connection.call("Fetch.continueRequest", {"requestId": "r1"}) == {}
    assert connection._responses[1] == {"id": 1, "result": {"frameId": "frame"}}


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
