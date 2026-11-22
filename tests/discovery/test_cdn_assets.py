"""CDN assets render the app without admitting the CDN as an API origin."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from support.cdn_website import fixture_server

from website_client.autonomous import run
from website_client.browser import origin
from website_client.exploration_policy import request_allowed


@pytest.mark.parametrize(
    "resource_type", ["script", "stylesheet", "image", "font", "media"]
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_strict_discovery_allows_page_asset_reads(resource_type, method):
    request = browser_request(method=method, resource_type=resource_type)
    assert request_allowed(request, origin("https://app.example.test"), True) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_type": "fetch"},
        {"resource_type": "xhr"},
        {"resource_type": "document", "navigation": True},
        {"resource_type": "script", "navigation": True},
        {"resource_type": "other"},
        {"method": "POST"},
        {"method": "PUT"},
        {"method": "DELETE"},
        {"method": "OPTIONS"},
        {"url": "https://cdn.example.test/app.js?action=delete"},
        {"url": "https://cdn.example.test/app.js?query=mutation%20Save%20%7Bsave%7D"},
        {"url": "http://cdn.example.test/app.js"},
        {"url": "https://user:secret@cdn.example.test/app.js"},
    ],
)
def test_page_assets_do_not_exempt_api_calls_writes_or_unsafe_urls(overrides):
    request = browser_request(**overrides)
    assert request_allowed(request, origin("https://app.example.test"), True)


def browser_request(
    *,
    method="GET",
    resource_type="script",
    navigation=False,
    url="https://cdn.example.test/create-form.js",
):
    return SimpleNamespace(
        url=url,
        method=method,
        resource_type=resource_type,
        post_data=None,
        is_navigation_request=lambda: navigation,
    )


@pytest.mark.parametrize(
    "separate_api",
    [
        pytest.param(False, id="same-origin-api"),
        pytest.param(True, id="separate-api-no-schema"),
    ],
)
def test_cdn_driven_app_generates_callable_client_without_admitting_cdn(
    tmp_path, monkeypatch, separate_api
):
    monkeypatch.setenv("WEBSITE_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    with fixture_server() as cdn, fixture_server() as app, fixture_server() as api:
        app.cdn = cdn.url
        api.url = api.url.replace("127.0.0.1", "localhost")
        api.website = app.url
        api.introspection = False
        cdn.api = api.url if separate_api else ""

        def initial_login(session):
            # This substitutes only for initial login and dashboard selection.
            if separate_api:
                session._context.add_cookies(
                    [
                        {
                            "name": "app_sid",
                            "value": "APP_COOKIE_CANARY",
                            "url": app.url,
                        },
                    ]
                )
            session._page.goto(app.url + "/dashboard")
            session._page.get_by_role("tab", name="Records").wait_for()
            app.calls.clear()
            cdn.calls.clear()

        output = tmp_path / "generated"
        report = run(
            app.url + "/login",
            output,
            login_handler=initial_login,
            headless=True,
            max_pages=1,
            max_actions=2,
            duration_seconds=10,
        )

        # Before the fix the fresh guarded tab cannot load the script, so this
        # assertion fails with unsupported/no_executable_operations.
        assert report["services"], report
        service = next(
            item for item in report["services"] if "Status" in item["operations"]
        )
        exploration = report["discovery"]["exploration"]
        assert exploration["actions"], (
            "The CDN script must render an actionable Records tab"
        )
        assert {"/entry.js", "/create-app.js"} <= {path for _, path in cdn.calls}
        assert all(path in {"/entry.js", "/create-app.js"} for _, path in cdn.calls)
        assert ("POST", "/write") not in app.calls
        blocked = exploration["blockedRequests"]
        assert any(item["reason"] == "undeclared_origin" for item in blocked)
        assert any(item["method"] == "POST" for item in blocked)

        # A CDN response does not declare an API or create a credential profile.
        profiles = list((tmp_path / "profiles").glob("*.json"))
        assert len(profiles) == (2 if separate_api else 1)
        assert all(cdn.url not in path.read_text() for path in profiles)
        assert_generated_read(output, service)
        target = api if separate_api else app
        assert sum(path == "/graphql" for _, path in target.calls) >= 3
        assert target.mutations == 0, "Discovery dispatched a GraphQL mutation"
        if separate_api:
            assert any(
                probe["kind"] == "graphql" and probe["outcome"] == "unauthorized"
                for probe in report["discovery"]["probes"]
            )
            assert service["provenance"] == "schema-less-operations"
            assert all("APP_COOKIE_CANARY" not in cookie for cookie in api.cookies)
            assert api.cookies and all(not cookie for cookie in api.cookies)
        for path in output.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                assert b"CDN_TEST_BEARER" not in path.read_bytes()
                assert b"COOKIE_CANARY" not in path.read_bytes()
                assert b"SYNTHETIC_DASHBOARD" not in path.read_bytes()
                assert b"SYNTHETIC_VERSION" not in path.read_bytes()


def assert_generated_read(output, service):
    project = output / service["directory"]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            service["package"].replace("-", "_") + "._cli",
            "Status",
        ],
        cwd=project,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "ready"}
