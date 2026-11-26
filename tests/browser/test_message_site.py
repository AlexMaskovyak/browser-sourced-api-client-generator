"""Keep natural browser evidence separate from fixture control requests."""

import json
import os
from pathlib import Path
import ssl

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    not Path("/work/docker/test-chromium.sh").is_file(),
    reason="Requires the installed-wheel Chromium test container",
)
BROWSER = ["--browser", "chromium", "--browser-path", "/work/docker/test-chromium.sh"]


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend("/work/docker")
    from message_site import MessageSite
    from test_login import assert_container_environment

    assert_container_environment()
    monkeypatch.setenv("HOME", str(tmp_path))
    local = MessageSite(tmp_path)
    try:
        yield local
    finally:
        local.close()


@pytest.fixture
def http_client(site, tmp_path):
    trust = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    with httpx.Client(base_url=site.url.rsplit("/", 1)[0], verify=trust) as client:
        yield client


@pytest.fixture
def signed_in_client(site, http_client):
    """Authenticate via both real forms without fabricating session cookies."""
    password = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password.status_code == 303 and password.headers["location"] == "/mfa"
    verified = http_client.post("/mfa", data={"code": site.secrets["code"]})
    assert verified.status_code == 303 and verified.headers["location"] == "/app"
    return http_client


def observe_natural_message(command, site):
    """Only observe navigation, its own fetch and rendering; inject no requests."""
    from message_site import DASHBOARD_HTML
    from terminal_command import eventually
    from test_login import complete_login

    page = command.connection.page
    origin = site.url.rsplit("/", 1)[0]
    expected = dict(site.messages["m1"])
    with page.expect_response(site.url) as shell, page.expect_response(
        origin + "/message?message_id=m1"
    ) as message:
        complete_login(command, site)
    assert shell.value.headers["content-type"] == "text/html"
    assert shell.value.text() == DASHBOARD_HTML
    assert expected["subject"] not in DASHBOARD_HTML
    response = message.value
    assert response.request.method == "GET"
    assert response.status == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected
    eventually(lambda: page.locator("#subject").inner_text() == expected["subject"])
    return {
        "method": "GET", "path": "/message?message_id=m1",
        "authenticated": True, "status": 200, "body": expected,
    }


def run_browser_sign_in(site, args, *, executable=None):
    from terminal_command import Command, eventually
    from test_login import assert_no_credentials

    before = site.counts.copy()
    initial_reads = len(site.message_requests)
    command = Command(site, args=args, executable=executable)
    try:
        eventually(command.ready)
        command.connect()
        natural = observe_natural_message(command, site)
        os.write(command.terminal, b"\n")
        assert command.wait() == 0, command.output
        command.cleanup_verified()
        assert_no_credentials(command.output, site)
        assert site.counts == {step: count + 1 for step, count in before.items()}
        assert site.message_requests[initial_reads:] == [natural]
        return json.loads(command.output.splitlines()[-1])
    finally:
        command.stop()


def test_generator_and_installed_client_complete_sign_in(site, tmp_path):
    from test_login import install_generated_wheel

    project = tmp_path / "message-client"
    generated = run_browser_sign_in(site, [
        "login", "--url", site.url, "--output", str(project), *BROWSER,
    ])
    assert generated["clientGenerated"] is True

    environment = tmp_path / "installed"
    install_generated_wheel(project, environment)
    # Isolated Python imports the installed wheel even beside the generated source.
    signed_in = run_browser_sign_in(site, [
        "-I", str(project / "client.py"), "login", "--url", site.url, *BROWSER,
    ], executable=str(environment / "bin/python"))
    assert signed_in == {"authentication": "user-confirmed", "sessionClosed": True}
    print("PASS generator and installed client each completed password and MFA, "
          "opened the protected dashboard and received message m1.", flush=True)

    report = json.loads((project / "generation.json").read_text())
    print("Generated operations: " + json.dumps(report["operations"]), flush=True)
    if os.environ.get("REQUIRE_MESSAGE_GET") == "1":
        spec = json.loads((project / "openapi.json").read_text())
        assert "get" in spec["paths"].get("/message", {}), (
            "Browser and backend verified GET /message, but the generated "
            "OpenAPI has no GET /message operation."
        )


def test_password_alone_and_wrong_mfa_cannot_read_messages(site, http_client):
    password = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password.status_code == 303
    assert password.headers["location"] == "/mfa"
    assert http_client.get("/message?message_id=m1").status_code == 401

    rejected = http_client.post("/mfa", data={"code": "incorrect"})
    assert rejected.status_code == 401
    assert http_client.get("/message?message_id=m1").status_code == 401
    assert site.counts == {"password": 1, "mfa": 0, "app": 0}


def test_fixture_rejects_unauthenticated_message_read(site, http_client):
    denied = http_client.get("/message?message_id=m1")
    assert denied.status_code == 401
    assert site.message_requests[-1]["authenticated"] is False


def test_fixture_controls_and_schema_absence(site, signed_in_client):
    client = signed_in_client
    first = dict(site.messages["m1"])
    site.messages["m2"]["subject"] = "Updated second fixture message"
    assert client.get("/message?message_id=m2").json() == site.messages["m2"]
    assert client.get("/message?message_id=m1").json() == first
    assert client.get("/message?message_id=unknown").status_code == 404
    for path in ("/openapi.json", "/swagger.json", "/api-docs", "/graphql", "/unknown"):
        missing = client.get(path)
        assert missing.status_code == 404
        assert missing.json() == {"error": "Not found"}
    assert client.post("/graphql", json={"query": "{ __typename }"}).status_code == 404
    assert client.post("/items", json={"name": "Unused"}).status_code == 404
    site.sessions_active = False
    assert client.get("/message?message_id=m2").status_code == 401
    print("PASS separate fixture controls: changed m2, unchanged m1, unknown ID, "
          "expired session, absent GET schemas and POST APIs.", flush=True)
