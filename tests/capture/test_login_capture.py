"""The login listener must never pair a same-origin request with a redirected body."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from website_client.login_capture import LoginCapture


def listener():
    connection = Mock()
    connection.call.return_value = {
        "body": json.dumps({"id": "private-id", "subject": "private-subject"})
    }
    session = SimpleNamespace(_origin=("example.test", 443), _connection=connection)
    return LoginCapture(session), connection


def request(capture, url, method="GET", identifier="one"):
    capture.event(
        "Network.requestWillBeSent",
        {
            "requestId": identifier,
            "documentURL": "https://example.test/app",
            "type": "Fetch",
            "request": {"url": url, "method": method},
            "timestamp": 0,
            "wallTime": 1,
        },
    )


def complete(capture, status=200):
    capture.event(
        "Network.responseReceived",
        {
            "requestId": "one",
            "response": {
                "status": status,
                "mimeType": "application/json",
                "protocol": "h2",
            },
        },
    )
    capture.event("Network.loadingFinished", {"requestId": "one", "timestamp": 1})


def test_redirect_cannot_attach_cross_origin_response_to_allowed_request():
    capture, connection = listener()
    request(capture, "https://example.test/records?record_id=private-id")
    request(capture, "https://external.test/records")
    complete(capture)
    assert capture.capture.discovery.entries == []
    connection.call.assert_not_called()
    assert capture.capture.pending_bytes == 0


def test_capture_retains_query_names_and_types_without_values():
    capture, _ = listener()
    request(capture, "https://example.test/records?record_id=private-id")
    complete(capture)
    [entry] = capture.capture.discovery.entries
    assert "private-" not in json.dumps(entry)
    assert entry["request"]["queryString"] == [
        {"name": "record_id", "value": "[REDACTED]"}
    ]
    assert json.loads(entry["response"]["content"]["text"]) == {
        "id": "[REDACTED]",
        "subject": "[REDACTED]",
    }


def test_login_write_and_external_reads_are_never_retained():
    for url, method in (
        ("https://example.test/login", "POST"),
        ("https://external.test/records", "GET"),
    ):
        capture, connection = listener()
        request(capture, url, method)
        complete(capture)
        assert capture.capture.discovery.entries == []
        connection.call.assert_not_called()


def test_identifier_routes_and_repeated_query_arguments_are_excluded():
    unsupported = (
        "https://example.test/records/0123456789abcdef01234567",
        "https://example.test/records/1234",
        "https://example.test/records?record_id=one&record_id=two",
    )
    for url in unsupported:
        capture, connection = listener()
        request(capture, url)
        complete(capture)
        assert capture.capture.discovery.entries == []
        connection.call.assert_not_called()


def test_finish_exports_only_the_upstream_contract_and_sanitized_evidence(monkeypatch):
    capture, _ = listener()
    request(capture, "https://example.test/records?record_id=private-id")
    complete(capture)
    inferred = {
        "openapi": "3.1.0",
        "info": {"title": "Records", "version": "1"},
        "paths": {
            "/records": {"get": {"responses": {"200": {"description": "Observed"}}}}
        },
    }
    supplied = []

    def infer(sanitized_har):
        supplied.append(sanitized_har)
        return inferred, {"backend": "har2sdk", "version": "0.6.15"}

    monkeypatch.setattr("website_client.login_capture.infer_har", infer)
    result = capture.finish()
    assert result.definition["paths"]["/records"]["get"]["operationId"] == "get_records"
    assert set(result.evidence) == {"capture.har", "inference.json"}
    assert supplied == [result.evidence["capture.har"]]
    assert "private-" not in supplied[0]
