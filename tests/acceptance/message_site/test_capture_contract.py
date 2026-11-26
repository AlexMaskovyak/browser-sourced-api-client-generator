"""Follow-on capture check, authored independently after initial implementation."""

import json
from urllib.parse import parse_qs, urlsplit


def test_capture_pairs_the_natural_exchange_without_private_values(site, generated_application):
    capture_text = (generated_application.project_directory / "capture.har").read_text()
    capture = json.loads(capture_text)
    message_entries = [
        entry for entry in capture["log"]["entries"]
        if urlsplit(entry["request"]["url"]).path == "/message"
    ]
    assert len(message_entries) == 1, "Capture must retain the one natural message exchange."
    request = message_entries[0]["request"]
    response = message_entries[0]["response"]
    assert request["method"] == "GET"
    assert response["status"] == 200
    assert response["content"]["mimeType"] == "application/json"

    query_arguments = {argument["name"]: argument["value"] for argument in request["queryString"]}
    assert set(query_arguments) == {"message_id"}
    assert isinstance(query_arguments["message_id"], str)
    assert query_arguments["message_id"] != "m1"
    assert "m1" not in parse_qs(urlsplit(request["url"]).query).get("message_id", [])

    captured_body = json.loads(response["content"]["text"])
    assert set(captured_body) == {"id", "subject"}
    assert all(isinstance(value, str) for value in captured_body.values())
    assert captured_body["id"] != site.messages["m1"]["id"]
    assert captured_body["subject"] != site.messages["m1"]["subject"]

    for credential in site.secrets.values():
        assert credential not in capture_text, "Capture leaked a fixture credential."
    for message in site.messages.values():
        assert message["subject"] not in capture_text, "Capture leaked a message example."
