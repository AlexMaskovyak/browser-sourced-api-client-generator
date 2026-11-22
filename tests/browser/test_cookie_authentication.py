"""Capture domain cookies only for admitted API origins that actually sent them."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from website_client.autonomous import _capture_profiles
from website_client.runtime_profile import OriginCaptures, ProfileStore, service_origin

WEBSITE = "https://app.example.test"
API = "https://api.example.test"
SESSION_COOKIE = {
    "name": "session",
    "value": "SYNTHETIC_SESSION",
    "domain": ".example.test",
    "path": "/",
    "secure": True,
    "httpOnly": True,
    "sameSite": "Lax",
    "expires": -1,
}


def observe_cookie(captures: OriginCaptures, endpoint: str) -> None:
    request = SimpleNamespace(
        url=endpoint,
        all_headers=lambda: {"Cookie": "session=SYNTHETIC_SESSION"},
    )
    captures.observe_request(request)


def test_programmatic_capture_preserves_observed_domain_cookie_in_api_profile(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WEBSITE_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)
    observe_cookie(captures, WEBSITE)
    observe_cookie(captures, API)
    state = {"cookies": [SESSION_COOKIE], "origins": []}
    session = SimpleNamespace(
        _context=SimpleNamespace(storage_state=lambda: deepcopy(state))
    )

    profiles, _exclusions = _capture_profiles(session, captures)

    api_origin = service_origin(API)
    with ProfileStore(profiles[api_origin], API).locked() as store:
        saved_profile = store.load()
    assert saved_profile["storage_state"]["cookies"] == [SESSION_COOKIE], (
        "The API sent the domain cookie during capture, so its private profile must retain it"
    )


def test_cookie_sent_to_both_admitted_hosts_survives_for_each_host():
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)
    observe_cookie(captures, WEBSITE)
    observe_cookie(captures, API)

    for endpoint in (WEBSITE, API):
        actual_cookies = captures.cookies_for(
            service_origin(endpoint), browser_cookies=[SESSION_COOKIE]
        )

        assert actual_cookies == {("session", "SYNTHETIC_SESSION")}, endpoint


def test_website_issued_domain_cookie_still_requires_observed_api_use():
    captures = OriginCaptures(WEBSITE)
    captures.declare(API)
    captures.observe_response(
        WEBSITE, ["session=SYNTHETIC_SESSION; Domain=.example.test; Path=/; Secure"]
    )

    assert (
        captures.cookies_for(service_origin(API), browser_cookies=[SESSION_COOKIE])
        == set()
    )

    observe_cookie(captures, API)

    assert captures.cookies_for(
        service_origin(API), browser_cookies=[SESSION_COOKIE]
    ) == {("session", "SYNTHETIC_SESSION")}


@pytest.mark.parametrize(
    "api_endpoint,cookie_domain,declare_api,observe_api",
    [
        pytest.param(API, ".example.test", True, False, id="jar-without-observed-use"),
        pytest.param(API, ".example.test", False, True, id="undeclared-api"),
        pytest.param(API, "app.example.test", True, True, id="host-only-cookie"),
        pytest.param(API, ".other.test", True, True, id="foreign-cookie-domain"),
        pytest.param(
            "https://app.example.test:8443",
            ".example.test",
            True,
            True,
            id="same-host-other-port",
        ),
    ],
)
def test_cookie_domain_does_not_replace_origin_evidence(
    api_endpoint, cookie_domain, declare_api, observe_api
):
    captures = OriginCaptures(WEBSITE)
    if declare_api:
        captures.declare(api_endpoint)
    observe_cookie(captures, WEBSITE)
    if observe_api:
        observe_cookie(captures, api_endpoint)
    cookie = {**SESSION_COOKIE, "domain": cookie_domain}

    actual_cookies = captures.cookies_for(
        service_origin(api_endpoint), browser_cookies=[cookie]
    )

    assert actual_cookies == set(), "Cookie domain alone must not grant an API session"
