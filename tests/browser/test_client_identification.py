"""Keep observed API client identity private and isolated from other origins."""

from types import SimpleNamespace

import pytest

from website_client.autonomous import _capture_profiles
from website_client.browser import origin
from website_client.runtime_profile import OriginCaptures, ProfileStore, service_origin
from website_client.self_discovery import DiscoveryObserver

WEBSITE = "https://app.example.test"
API = "https://api.example.test/graphql"
IDENTITY = {
    "apollographql-client-name": "SYNTHETIC_DASHBOARD",
    "apollographql-client-version": "SYNTHETIC_VERSION",
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


def test_observed_client_identity_survives_private_profile_capture(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WEBSITE_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)
    captures.observe("Network.requestWillBeSent", request_event(API, IDENTITY))
    session = SimpleNamespace(
        _context=SimpleNamespace(storage_state=lambda: {"cookies": [], "origins": []})
    )

    profiles, _exclusions = _capture_profiles(session, captures)

    with ProfileStore(profiles[service_origin(API)], API).locked() as store:
        assert store.load()["headers"] == IDENTITY
    with ProfileStore(profiles[service_origin(WEBSITE)], WEBSITE).locked() as store:
        assert store.load()["headers"] == {}


@pytest.mark.parametrize("source", [WEBSITE, "https://unrelated.example.test/graphql"])
def test_other_origin_cannot_supply_api_client_identity(source):
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)

    captures.observe("Network.requestWillBeSent", request_event(source, IDENTITY))

    assert captures.captures[service_origin(API)].headers == {}


def test_definition_probes_receive_observed_client_identity():
    observer = DiscoveryObserver(WEBSITE)

    observer.event("Network.requestWillBeSent", request_event(API, IDENTITY))

    assert observer.headers[origin(API)] == IDENTITY
