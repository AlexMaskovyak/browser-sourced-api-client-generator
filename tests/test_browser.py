import io
import signal
import subprocess
import sys
from unittest.mock import Mock

import pytest

from website_client.browser import (
    BrowserSession,
    BrowserError,
    Connection,
    find_browser,
    origin,
)
from website_client.cli import main


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "https://u:p@example.test",
        "https://example.test/#secret",
        "https://example.test:bad",
        "https://example.test/\nsecret",
        "https:///missing",
    ],
)
def test_rejects_unsafe_url(url):
    with pytest.raises(BrowserError):
        origin(url)


def test_origin_normalized():
    assert origin("https://EXAMPLE.test/app") == origin(
        "https://example.test:443/other"
    )


def test_missing_browser_is_clear(monkeypatch):
    monkeypatch.setattr("website_client.browser.shutil.which", lambda _: None)
    with pytest.raises(BrowserError, match="Browser not found"):
        find_browser("chrome")
    with pytest.raises(BrowserError):
        find_browser("other")
    with pytest.raises(BrowserError):
        find_browser("chrome", "relative-browser")


def test_playwright_control_errors_do_not_echo_private_diagnostics():
    page, protocol = Mock(), Mock()
    connection = Connection(page, protocol)
    protocol.send.return_value = {"ok": True}
    assert connection.call("Page.getFrameTree") == {"ok": True}
    protocol.send.side_effect = RuntimeError("private-token")
    with pytest.raises(BrowserError) as error:
        connection.call("DOM.getDocument")
    assert "private-token" not in str(error.value)
    assert "private-token" not in repr(connection)


def test_playwright_events_are_forwarded_and_detached():
    page, protocol = Mock(), Mock()
    connection = Connection(page, protocol)
    connection.on_event = Mock()
    listener = protocol.on.call_args_list[0].args[1]
    listener({"requestId": "1"})
    connection.on_event.assert_called_once_with(
        "Network.requestWillBeSent", {"requestId": "1"}
    )
    connection.pump(timeout=0.2)
    page.wait_for_timeout.assert_called_once_with(200)
    connection.close()
    protocol.detach.assert_called_once()
    assert connection.on_event is None


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr("website_client.browser.find_browser", lambda *_a: "/browser")
    owned = BrowserSession("https://example.test/app")
    owned._page = Mock()
    owned._page.is_closed.return_value = False
    owned._page.url = "https://example.test/app"
    owned._context = Mock()
    owned._playwright = Mock()
    owned._connection = Mock()
    owned._connection.call.return_value = {
        "frameTree": {"frame": {"url": "https://example.test/app"}}
    }
    monkeypatch.setattr(owned, "_password_visible", lambda: False)
    selector = Mock()
    selector.__enter__ = Mock(return_value=selector)
    selector.__exit__ = Mock(return_value=False)
    selector.select.return_value = [(None, None)]
    monkeypatch.setattr(
        "website_client.browser.selectors.DefaultSelector", lambda: selector
    )
    return owned


def test_confirmation_reports_uncertainty(session):
    result = session.confirm_login(Terminal("\n"), io.StringIO())
    assert result == {
        "sessionReady": True,
        "authentication": "user-confirmed",
        "apiVerified": False,
    }
    assert "example.test" not in repr(session)


@pytest.mark.parametrize(
    "state", ["password", "login", "sso", "closed", "cancelled", "eof", "pipe"]
)
def test_incomplete_or_cancelled_login_fails(session, monkeypatch, state):
    if state == "password":
        monkeypatch.setattr(session, "_password_visible", lambda: True)
    if state in {"login", "sso"}:
        url = (
            "https://example.test/login" if state == "login" else "https://sso.test/app"
        )
        session._page.url = url
    if state == "closed":
        session._page.is_closed.return_value = True
    if state == "cancelled":
        session._cancelled = True
    incoming = (
        io.StringIO("\n")
        if state == "pipe"
        else Terminal("" if state == "eof" else "\n")
    )
    with pytest.raises((BrowserError, KeyboardInterrupt)):
        session.confirm_login(incoming, io.StringIO())


def test_cleanup_runs_after_browser_failure(session):
    context, driver = session._context, session._playwright
    context.close.side_effect = RuntimeError("private-value")
    session._profile = Mock()
    profile = session._profile
    session.close()
    context.close.assert_called_once()
    driver.stop.assert_called_once()
    profile.cleanup.assert_called_once()


def test_failed_launch_cleans_profile_and_signals(monkeypatch):
    monkeypatch.setattr("website_client.browser.find_browser", lambda *_a: "/browser")
    profile = Mock(name="profile")
    monkeypatch.setattr(
        "website_client.browser.tempfile.TemporaryDirectory", lambda **_k: profile
    )
    monkeypatch.setattr(
        "website_client.browser.sync_playwright",
        Mock(side_effect=RuntimeError("private-value")),
    )
    previous = signal.getsignal(signal.SIGINT)
    with pytest.raises(BrowserError) as error:
        with BrowserSession("https://example.test/app"):
            pass
    assert "private-value" not in str(error.value)
    assert signal.getsignal(signal.SIGINT) == previous
    profile.cleanup.assert_called_once()


def test_cli_rejects_pipe_before_opening_browser(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr(
        "website_client.cli.BrowserSession",
        Mock(side_effect=AssertionError("Must not launch")),
    )
    assert main(["login", "--url", "https://example.test"]) == 1


def test_help_is_installed_and_offline():
    result = subprocess.check_output(
        [sys.executable, "-m", "website_client.cli", "login", "--help"], text=True
    )
    assert "verification" in result
    assert "does not save" in result
