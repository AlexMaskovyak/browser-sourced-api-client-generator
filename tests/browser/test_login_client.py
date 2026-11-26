"""Exercise generated login and page reads against the protected HTTPS fixture."""

import json
from pathlib import Path
import ssl
import os
import sys

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    not Path("/work/docker/test-chromium.sh").is_file(),
    reason="Requires the repository's installed-wheel Chromium test container",
)

BROWSER = ["--browser", "chromium", "--browser-path", "/work/docker/test-chromium.sh"]


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend("/work/docker")
    from fixture_site import Site
    from test_login import assert_container_environment

    assert_container_environment()
    monkeypatch.setenv("HOME", str(tmp_path))
    fixture = Site(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def test_generated_client_opens_its_own_login_browser(tmp_path, site):
    unfinished = tmp_path / "unfinished-client"
    output = tmp_path / "confirmed-client"
    attempt = ["login", "--url", site.url, "--output", str(unfinished), *BROWSER]
    sign_in(site, attempt, finish=False)
    assert (unfinished / "README.md").is_file()
    assert site.counts == {"password": 0, "mfa": 0, "app": 0}

    generate = ["login", "--url", site.url, "--output", str(output), *BROWSER]
    assert '"clientGenerated": true' in sign_in(site, generate)
    assert site.counts == {"password": 1, "mfa": 1, "app": 1}
    assert (output / "README.md").is_file()

    generated = [str(unfinished / "client.py"), "login", *BROWSER]
    console = sign_in(site, generated, executable=sys.executable)
    assert '"authentication": "user-confirmed"' in console
    assert site.counts == {"password": 2, "mfa": 2, "app": 2}


def login_and_read_paths(site, output):
    console = sign_in(site, ["login", "--url", site.url, "--output", str(output), *BROWSER])
    result = json.loads(console.splitlines()[-1])
    paths = json.loads((output / "openapi.json").read_text())["paths"]
    assert result["linkedPath"] == next(iter(paths), None)
    return paths


def test_installed_client_reads_the_discovered_protected_page(tmp_path, site):
    from test_login import install_generated_wheel

    site.dashboard_hrefs = ["team/guide"]
    origin = site.url.rsplit("/", 1)[0]
    trust = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    assert httpx.get(origin + "/team/guide", verify=trust).status_code == 401

    project = tmp_path / "one-link"
    paths = login_and_read_paths(site, project)
    assert list(paths) == ["/team/guide"]
    assert list(paths["/team/guide"]) == ["get"]
    assert site.guide_reads == 0

    installed = tmp_path / "installed"
    install_generated_wheel(project, installed)
    # Change the page after generation. Only a fresh authenticated GET can return it.
    site.guide_html = "<main>Updated team guide</main>"
    response = tmp_path / "page.json"
    sign_in(
        site,
        ["read_linked_page", "--url", origin, "--login", *BROWSER, "--output", str(response)],
        executable=str(installed / "bin/login-client"),
    )
    assert json.loads(response.read_text()) == site.guide_html
    assert site.guide_reads == 1
    assert site.counts["mfa"] == 2


def test_dashboard_without_links_generates_a_working_login_client(tmp_path, site):
    project = tmp_path / "no-links"
    assert login_and_read_paths(site, project) == {}
    report = json.loads((project / "generation.json").read_text())
    assert report["operations"] == []
    assert (project / "README.md").is_file()

    sign_in(site, [str(project / "client.py"), "login", *BROWSER], executable=sys.executable)
    assert site.counts == {"password": 2, "mfa": 2, "app": 2}
    assert site.guide_reads == 0


def test_only_record_link_generates_login_client_without_endpoints(tmp_path, site):
    site.dashboard_hrefs = ["/records/507f1f77bcf86cd799439011"]
    project = tmp_path / "record-only"

    paths = login_and_read_paths(site, project)

    assert paths == {}
    report = json.loads((project / "generation.json").read_text())
    assert report["operations"] == []
    assert "zero endpoints" in (project / "README.md").read_text()


def test_record_link_before_help_generates_only_help_endpoint(tmp_path, site):
    site.dashboard_hrefs = ["/records/507f1f77bcf86cd799439011", "/help"]
    project = tmp_path / "record-then-help"

    paths = login_and_read_paths(site, project)

    assert list(paths) == ["/help"]
    assert list(paths["/help"]) == ["get"]


def sign_in(site, args, *, executable=None, finish=True):
    from terminal_command import Command, eventually
    from test_login import complete_login

    command = Command(site, args=args, executable=executable)
    try:
        eventually(command.ready)
        command.connect()
        eventually(lambda: command.node('input[name="username"]'))
        if finish:
            complete_login(command, site)
        os.write(command.terminal, b"\n")
        assert command.wait() == (0 if finish else 1), command.output
        command.cleanup_verified()
        return command.output
    finally:
        command.stop()
