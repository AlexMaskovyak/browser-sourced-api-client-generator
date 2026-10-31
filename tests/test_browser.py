import io
import json
import signal
import subprocess
import sys
from unittest.mock import Mock

import pytest

from website_client.browser import BrowserSession, BrowserError, Connection, find_browser, origin
from website_client.cli import main


@pytest.mark.parametrize("url", ["http://example.test", "https://u:p@example.test", "https://example.test/#secret", "https://example.test:bad", "https://example.test/\nsecret", "https:///missing"])
def test_rejects_unsafe_url(url):
    with pytest.raises(BrowserError): origin(url)


def test_origin_normalized():
    assert origin("https://EXAMPLE.test/app") == origin("https://example.test:443/other")


def test_missing_browser_is_clear(monkeypatch):
    monkeypatch.setattr("website_client.browser.shutil.which", lambda _: None)
    with pytest.raises(BrowserError, match="Browser not found"): find_browser("chrome")
    with pytest.raises(BrowserError): find_browser("other")
    with pytest.raises(BrowserError): find_browser("chrome", "relative-browser")


@pytest.mark.parametrize("url", ["ws://evil.test:123/devtools/page/a", "wss://127.0.0.1:123/devtools/page/a", "ws://127.0.0.1:124/devtools/page/a", "ws://u:p@127.0.0.1:123/devtools/page/a", "ws://127.0.0.1:123/other"])
def test_control_connection_is_loopback_only(url):
    with pytest.raises(BrowserError): Connection(url, 123)


def test_protocol_tracks_response_ids_and_redacts_server_error(monkeypatch):
    sock = Mock()
    sock.recv.side_effect = [json.dumps({"method": "event"}), json.dumps({"id": 1, "result": {"ok": True}}), json.dumps({"id": 2, "error": {"message": "private-token"}})]
    monkeypatch.setattr("website_client.browser.websocket.create_connection", lambda *_a, **_k: sock)
    connection = Connection("ws://127.0.0.1:123/devtools/page/a", 123)
    assert connection.call("Page.getFrameTree") == {"ok": True}
    with pytest.raises(BrowserError) as error: connection.call("DOM.getDocument")
    assert "private-token" not in str(error.value)
    assert "127.0.0.1" not in repr(connection)


class Terminal(io.StringIO):
    def isatty(self): return True


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr("website_client.browser.find_browser", lambda *_a: "/browser")
    owned = BrowserSession("https://example.test/app")
    owned._process = Mock()
    owned._process.poll.return_value = None
    owned._connection = Mock()
    owned._connection.call.return_value = {"frameTree": {"frame": {"url": "https://example.test/app"}}}
    monkeypatch.setattr(owned, "_password_visible", lambda: False)
    selector = Mock()
    selector.__enter__ = Mock(return_value=selector)
    selector.__exit__ = Mock(return_value=False)
    selector.select.return_value = [(None, None)]
    monkeypatch.setattr("website_client.browser.selectors.DefaultSelector", lambda: selector)
    return owned


def test_confirmation_reports_uncertainty(session):
    result = session.confirm_login(Terminal("\n"), io.StringIO())
    assert result == {"sessionReady": True, "authentication": "user-confirmed", "apiVerified": False}
    assert "example.test" not in repr(session)


@pytest.mark.parametrize("state", ["password", "login", "sso", "closed", "cancelled", "eof", "pipe"])
def test_incomplete_or_cancelled_login_fails(session, monkeypatch, state):
    if state == "password": monkeypatch.setattr(session, "_password_visible", lambda: True)
    if state in {"login", "sso"}:
        url = "https://example.test/login" if state == "login" else "https://sso.test/app"
        session._connection.call.return_value = {"frameTree": {"frame": {"url": url}}}
    if state == "closed": session._process.poll.return_value = 0
    if state == "cancelled": session._cancelled = True
    incoming = io.StringIO("\n") if state == "pipe" else Terminal("" if state == "eof" else "\n")
    with pytest.raises((BrowserError, KeyboardInterrupt)):
        session.confirm_login(incoming, io.StringIO())


def test_cleanup_runs_after_connection_failure(session):
    connection, process = session._connection, session._process
    connection.call.side_effect = RuntimeError("private-value")
    session._profile = Mock()
    profile = session._profile
    session.close()
    connection.close.assert_called_once()
    process.wait.assert_called_once()
    profile.cleanup.assert_called_once()


def test_failed_launch_cleans_profile_and_signals(monkeypatch):
    monkeypatch.setattr("website_client.browser.find_browser", lambda *_a: "/browser")
    profile = Mock(name="profile")
    monkeypatch.setattr("website_client.browser.tempfile.TemporaryDirectory", lambda **_k: profile)
    monkeypatch.setattr("website_client.browser.subprocess.Popen", Mock(side_effect=RuntimeError("private-value")))
    previous = signal.getsignal(signal.SIGINT)
    with pytest.raises(BrowserError) as error:
        with BrowserSession("https://example.test/app"): pass
    assert "private-value" not in str(error.value)
    assert signal.getsignal(signal.SIGINT) == previous
    profile.cleanup.assert_called_once()


def test_cli_rejects_pipe_before_opening_browser(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr("website_client.cli.BrowserSession", Mock(side_effect=AssertionError("Must not launch")))
    assert main(["login", "--url", "https://example.test"]) == 1


def test_help_is_installed_and_offline():
    result = subprocess.check_output([sys.executable, "-m", "website_client.cli", "login", "--help"], text=True)
    assert "verification" in result
    assert "does not save" in result
