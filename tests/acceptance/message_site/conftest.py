"""Provide a disposable site and generator output to each acceptance stage."""

from pathlib import Path
import ssl

import httpx
import pytest

from .support import BROWSER_PATH, GeneratedApplication, run_browser_sign_in


@pytest.fixture
def site(tmp_path, monkeypatch):
    if not Path("/work/docker/test-chromium.sh").is_file():
        pytest.skip("Requires the installed-wheel Chromium test container")
    monkeypatch.syspath_prepend("/work/docker")
    from message_site import MessageSite
    from test_login import assert_container_environment

    assert_container_environment()
    monkeypatch.setenv("HOME", str(tmp_path))
    message_site = MessageSite(tmp_path)
    try:
        yield message_site
    finally:
        message_site.close()



@pytest.fixture
def http_client(site, tmp_path):
    trust = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    with httpx.Client(base_url=site.url.rsplit("/", 1)[0], verify=trust) as client:
        yield client



@pytest.fixture
def signed_in_client(site, http_client):
    """Fixture controls authenticate separately from all browser acceptance work."""
    password_response = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password_response.status_code == 303
    assert password_response.headers["location"] == "/mfa"
    mfa_response = http_client.post("/mfa", data={"code": site.secrets["code"]})
    assert mfa_response.status_code == 303
    assert mfa_response.headers["location"] == "/app"
    return http_client



@pytest.fixture
def generated_application(site, tmp_path):
    project = tmp_path / "message-client"
    profile = tmp_path / "private-runtime-profile"
    arguments = [
        "login", "--url", site.url, "--output", str(project), "--profile", str(profile),
    ]
    generated = run_browser_sign_in(
        site, arguments, environment={"WEBSITE_CLIENT_BROWSER_PATH": BROWSER_PATH},
    )
    assert generated["clientGenerated"] is True
    return GeneratedApplication(project, profile)
