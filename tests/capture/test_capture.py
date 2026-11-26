import json
from types import SimpleNamespace
from unittest.mock import Mock

from website_client.capture import NetworkCapture


def test_controlled_exploration_defers_body_reads_until_after_event_callbacks():
    connection = Mock()
    connection.call.return_value = {"body": "{}"}
    capture = NetworkCapture(
        SimpleNamespace(_connection=connection, _origin=("example.test", 443)),
        defer_bodies=True,
    )
    capture.event(
        "Network.requestWillBeSent",
        {
            "requestId": "1",
            "documentURL": "https://example.test/app",
            "type": "XHR",
            "request": {"url": "https://example.test/items", "method": "GET"},
        },
    )
    capture.event(
        "Network.responseReceived",
        {"requestId": "1", "response": {"status": 200, "mimeType": "application/json"}},
    )
    capture.event("Network.loadingFinished", {"requestId": "1"})
    connection.call.assert_not_called()
    capture.drain()
    connection.call.assert_called_once_with(
        "Network.getResponseBody", {"requestId": "1"}
    )


def test_native_network_events_are_scoped_bounded_and_discard_headers():
    connection = Mock()
    connection.call.return_value = {
        "body": '{"id":"private-response"}',
        "base64Encoded": False,
    }
    session = SimpleNamespace(_connection=connection, _origin=("example.test", 443))
    capture = NetworkCapture(session)
    request = {
        "requestId": "1",
        "documentURL": "https://example.test/app",
        "type": "Fetch",
        "timestamp": 1,
        "request": {
            "url": "https://api.example.test/items",
            "method": "GET",
            "headers": {"Authorization": "private-token"},
        },
    }
    capture.event("Network.requestWillBeSent", request)
    assert "private-token" not in json.dumps(capture.pending)
    capture.event(
        "Network.responseReceived",
        {"requestId": "1", "response": {"status": 200, "mimeType": "application/json"}},
    )
    capture.event("Network.loadingFinished", {"requestId": "1", "timestamp": 1.1})
    connection.call.assert_called_once_with(
        "Network.getResponseBody", {"requestId": "1"}
    )
    # The next navigation can discard the browser's cached response before the
    # next terminal poll. The completion callback must already have read it.
    connection.call.side_effect = RuntimeError("response evicted by navigation")
    capture.drain()
    files = capture.discovery.files("browser")
    assert len(capture.discovery.entries) == 1
    assert "private-response" not in json.dumps(files)
    assert not capture.pending
    capture.event(
        "Network.requestWillBeSent",
        {**request, "requestId": "2", "documentURL": "https://sso.test/login"},
    )
    assert not capture.pending


def test_response_body_failures_preserve_endpoint_metadata_and_report_gap():
    connection = Mock()
    connection.call.side_effect = RuntimeError("private-token")
    capture = NetworkCapture(
        SimpleNamespace(_connection=connection, _origin=("example.test", 443))
    )
    capture.event(
        "Network.requestWillBeSent",
        {
            "requestId": "1",
            "documentURL": "https://example.test/app",
            "type": "XHR",
            "request": {"url": "https://example.test/items", "method": "GET"},
        },
    )
    capture.event(
        "Network.responseReceived",
        {"requestId": "1", "response": {"status": 200, "mimeType": "application/json"}},
    )
    capture.event("Network.loadingFinished", {"requestId": "1"})
    capture.drain()
    assert capture.body_failures == 1
    assert len(capture.discovery.endpoints) == 1
