"""Exercise both login programs against the protected HTTPS container fixture."""

from pathlib import Path
import os
import sys

import pytest


@pytest.mark.skipif(
    not Path("/work/docker/test-chromium.sh").is_file(),
    reason="Requires the repository's installed-wheel Chromium test container",
)
def test_generated_client_opens_its_own_login_browser(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend("/work/docker")
    from fixture_site import Site
    from test_login import assert_container_environment

    assert_container_environment()
    site = Site(tmp_path)
    unfinished = tmp_path / "unfinished-client"
    output = tmp_path / "confirmed-client"
    browser = [
        "--browser",
        "chromium",
        "--browser-path",
        "/work/docker/test-chromium.sh",
    ]
    generate = ["login", "--url", site.url, "--output", str(output), *browser]
    try:
        attempt = ["login", "--url", site.url, "--output", str(unfinished), *browser]
        sign_in(site, attempt, finish=False)
        assert (unfinished / "README.md").is_file()
        assert site.counts == {"password": 0, "mfa": 0, "app": 0}

        assert '"clientGenerated": true' in sign_in(site, generate)
        assert site.counts == {"password": 1, "mfa": 1, "app": 1}
        assert (output / "README.md").is_file()

        # Retry using the file emitted by the unfinished attempt. Enter the new browser's credentials
        # again and require a second authenticated dashboard visit on the server.
        generated = [str(unfinished / "client.py"), "login", *browser]
        console = sign_in(site, generated, executable=sys.executable)
        assert '"authentication": "user-confirmed"' in console
        assert site.counts == {"password": 2, "mfa": 2, "app": 2}
    finally:
        site.close()


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
