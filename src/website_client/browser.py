"""Own a temporary login browser through Playwright's Python API."""

import os
from pathlib import Path
import selectors
import shutil
import signal
import sys
import tempfile
import threading
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

BROWSER_START_TIMEOUT_MILLISECONDS = 20_000
NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
EVENT_POLL_SECONDS = 0.1


class BrowserError(Exception):
    """A bounded message safe to show without browser diagnostics."""


def origin(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 8192
            or any(ord(c) <= 32 for c in value)
        ):
            raise ValueError()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError()
        return parsed.hostname.lower(), parsed.port or 443
    except (ValueError, TypeError):
        raise BrowserError(
            "Use an HTTPS URL without embedded credentials or a fragment."
        ) from None


class Connection:
    """Adapt Playwright CDP sessions to network capture and request policy.

    Playwright owns protocol framing, response matching and event dispatch.
    This adapter only selects events and bounds user-facing error messages.
    """

    def __init__(self, page, session):
        self.page = page
        self.session = session
        self.on_event = None
        for method in (
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
            "Fetch.requestPaused",
        ):
            session.on(method, lambda params, name=method: self._event(name, params))

    def _event(self, method, params):
        if self.on_event:
            self.on_event(method, params)

    def call(self, method, params=None):
        try:
            return self.session.send(method, params or {})
        except KeyboardInterrupt:
            raise
        except Exception:
            raise BrowserError(
                "The browser could not complete a control request."
            ) from None

    def pump(self, timeout=EVENT_POLL_SECONDS):
        # Waiting through Playwright also dispatches queued browser events.
        self.page.wait_for_timeout(timeout * 1000)

    def close(self):
        self.on_event = None
        self.session.detach()

    def __repr__(self):
        return "<BrowserConnection private>"


def find_browser(name, explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise BrowserError(
            "The --browser-path must name an existing executable using its absolute path."
        )
    if name not in {"chrome", "chromium"}:
        raise BrowserError("Choose chrome or chromium.")
    candidates = (
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "google-chrome",
            "google-chrome-stable",
        ]
        if name == "chrome"
        else [
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "chromium",
            "chromium-browser",
        ]
    )
    if sys.platform == "win32" and name == "chrome":
        candidates = [
            str(Path(os.environ.get(root, "")) / "Google/Chrome/Application/chrome.exe")
            for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        ]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise BrowserError(
        "Browser not found. Install Chrome or Chromium, or supply --browser-path."
    )


class BrowserSession:
    """Keep an owned browser alive until the caller exits its context manager."""

    def __init__(
        self,
        url,
        browser="chrome",
        browser_path=None,
        on_event=None,
        block_service_workers=False,
    ):
        self._origin = origin(url)
        self._url = url
        self._executable = find_browser(browser, browser_path)
        self._profile = self._connection = self._playwright = None
        self._context = self._page = None
        self._signals = {}
        self._cancelled = False
        self._on_event = on_event
        self._block_service_workers = block_service_workers

    def __repr__(self):
        return "<BrowserSession temporary; credentials not exported>"

    def _cancel(self, *_args):
        self._cancelled = True

    def _check(self):
        if self._cancelled:
            raise KeyboardInterrupt()
        if self._page is None or self._page.is_closed():
            raise BrowserError(
                "The login browser closed. Start a fresh command to retry."
            )

    def __enter__(self):
        try:
            if threading.current_thread() is threading.main_thread():
                for event in (signal.SIGINT, signal.SIGTERM):
                    self._signals[event] = signal.signal(event, self._cancel)
            self._profile = tempfile.TemporaryDirectory(
                prefix="website-client-browser-"
            )
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                self._profile.name,
                executable_path=self._executable,
                headless=False,
                chromium_sandbox=True,
                accept_downloads=False,
                service_workers="block" if self._block_service_workers else "allow",
                timeout=BROWSER_START_TIMEOUT_MILLISECONDS,
                handle_sigint=False,
                handle_sigterm=False,
                handle_sighup=False,
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
            self._connection = self.connection_for(self._page)
            self._connection.call("Browser.setDownloadBehavior", {"behavior": "deny"})
            if self._on_event:
                self._connection.on_event = self._on_event
                self._connection.call("Network.enable")
            self._page.goto(
                self._url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MILLISECONDS,
            )
            return self
        except (KeyboardInterrupt, BrowserError):
            self.close()
            raise
        except Exception:
            self.close()
            raise BrowserError(
                "Could not start the browser session. Check the browser installation and retry."
            ) from None

    def connection_for(self, page):
        return Connection(page, self._context.new_cdp_session(page))

    def current_url(self):
        self._check()
        # Enter can arrive between event dispatches. Read Chrome's current frame
        # rather than treating Playwright's cached URL as the login result.
        return self._connection.call("Page.getFrameTree")["frameTree"]["frame"]["url"]

    def _password_visible(self):
        return self._page.locator('input[type="password"]:visible').count() > 0

    def wait_for_enter(self, prompt, input_stream=None, output_stream=None, poll=None):
        incoming = sys.stdin if input_stream is None else input_stream
        outgoing = sys.stderr if output_stream is None else output_stream
        if not incoming.isatty():
            raise BrowserError(
                "Login requires an interactive terminal, without piped input."
            )
        print(prompt, file=outgoing, flush=True)
        try:
            if sys.platform == "win32":
                import msvcrt

                while True:
                    self.current_url()
                    if poll:
                        poll()
                    else:
                        self._connection.pump()
                    if msvcrt.kbhit():
                        key = msvcrt.getwch()
                        if key == "\x03":
                            raise KeyboardInterrupt()
                        if key in {"\r", "\n"}:
                            break
            else:
                with selectors.DefaultSelector() as terminal:
                    terminal.register(incoming, selectors.EVENT_READ)
                    while True:
                        self.current_url()
                        if poll:
                            poll()
                        else:
                            self._connection.pump()
                        if terminal.select(timeout=0.1):
                            if not incoming.readline():
                                raise BrowserError(
                                    "Terminal input closed. Start a fresh command to retry."
                                )
                            break
        except (BrowserError, KeyboardInterrupt):
            raise
        except Exception:
            raise BrowserError(
                "The browser session ended unexpectedly. Start a fresh command to retry."
            ) from None

    def confirm_login(self, input_stream=None, output_stream=None, prompt=None):
        self.wait_for_enter(
            prompt
            or "Log in in the browser, including verification codes. Return to the requested website, "
            "then press Enter here to finish. Ctrl+C cancels.",
            input_stream,
            output_stream,
        )
        try:
            current = self.current_url()
            path = urlsplit(current).path.rstrip("/").split("/")[-1].lower()
            if (
                origin(current) != self._origin
                or path in {"login", "signin", "sign-in", "sign_in", "mfa"}
                or self._password_visible()
            ):
                raise BrowserError(
                    "Login is not complete. Sign in and return to the requested website before confirming."
                )
        except (BrowserError, KeyboardInterrupt):
            raise
        except Exception:
            raise BrowserError(
                "The browser session ended unexpectedly. Start a fresh command to retry."
            ) from None
        return {
            "sessionReady": True,
            "authentication": "user-confirmed",
            "apiVerified": False,
        }

    def close(self):
        # A disconnected browser must not prevent stopping the driver, deleting
        # its temporary profile or restoring the caller's signal handlers.
        try:
            for resource, action in (
                (self._context, "close"),
                (self._playwright, "stop"),
                (self._profile, "cleanup"),
            ):
                if resource is not None:
                    try:
                        getattr(resource, action)()
                    except Exception:
                        pass
        finally:
            self._connection = self._context = self._page = self._playwright = (
                self._profile
            ) = None
            for event, previous in self._signals.items():
                signal.signal(event, previous)
            self._signals.clear()

    def __exit__(self, *_args):
        self.close()
