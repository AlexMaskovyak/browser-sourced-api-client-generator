"""Keep observed API client and role context private and isolated by origin."""

from types import SimpleNamespace

import pytest

from website_client.autonomous import _capture_profiles
from website_client.browser import origin
from website_client.runtime_profile import OriginCaptures, ProfileStore, service_origin
from website_client.self_discovery import DiscoveryObserver

WEBSITE = "https://app.example.test"
API = "https://api.example.test/graphql"
SESSION_CONTEXT = {
    "apollographql-client-name": "SYNTHETIC_DASHBOARD",
    "apollographql-client-version": "SYNTHETIC_VERSION",
    "x-role-id": "SYNTHETIC_ROLE",
}


def request_event(url, headers):
    return {
        "documentURL": WEBSITE,
        "type": "XHR",
        "request": {
            "url": url,
            "method": "POST",
            "headers": headers,
            "postData": '{"query":"query Status { status }"}',
        },
    }


def test_observed_session_context_survives_private_profile_capture(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WEBSITE_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)
    captures.observe("Network.requestWillBeSent", request_event(API, SESSION_CONTEXT))
    session = SimpleNamespace(
        _context=SimpleNamespace(storage_state=lambda: {"cookies": [], "origins": []})
    )

    profiles, _exclusions = _capture_profiles(session, captures)

    with ProfileStore(profiles[service_origin(API)], API).locked() as store:
        assert store.load()["headers"] == SESSION_CONTEXT
    with ProfileStore(profiles[service_origin(WEBSITE)], WEBSITE).locked() as store:
        assert store.load()["headers"] == {}


@pytest.mark.parametrize("source", [WEBSITE, "https://unrelated.example.test/graphql"])
def test_other_origin_cannot_supply_api_session_context(source):
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)

    captures.observe(
        "Network.requestWillBeSent", request_event(source, SESSION_CONTEXT)
    )

    assert captures.captures[service_origin(API)].headers == {}


def test_definition_probes_receive_observed_session_context():
    observer = DiscoveryObserver(WEBSITE)

    observer.event("Network.requestWillBeSent", request_event(API, SESSION_CONTEXT))

    assert observer.headers[origin(API)] == SESSION_CONTEXT
