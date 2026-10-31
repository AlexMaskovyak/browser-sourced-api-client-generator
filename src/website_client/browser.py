"""Own a native Chrome process and its temporary authentication session."""

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import websocket


class BrowserError(Exception):
    """A bounded message safe to show without browser diagnostics."""


def origin(value):
    try:
        if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        return parsed.hostname.lower(), parsed.port or 443
    except (ValueError, TypeError):
        raise BrowserError("Use an HTTPS URL without embedded credentials or a fragment.") from None


class Connection:
    """One bounded, synchronous Chrome DevTools Protocol connection."""

    def __init__(self, url, port, max_message_bytes=4 * 1024 * 1024):
        parsed = urlsplit(url)
        if (parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port != port
                or parsed.username or parsed.password or not parsed.path.startswith("/devtools/page/")):
            raise BrowserError("The temporary browser returned an invalid control connection.")
        self._socket = websocket.create_connection(url, timeout=3, suppress_origin=True, http_no_proxy=["127.0.0.1"], redirect_limit=0)
        self._next_id = 0
        self._responses = {}
        self._max_message_bytes = max_message_bytes
        self.on_event = None

    def send(self, method, params=None):
        self._next_id += 1
        self._socket.send(json.dumps({"id": self._next_id, "method": method, "params": params or {}}))
        return self._next_id

    def _accept(self, response):
        if "id" in response:
            if len(self._responses) >= 100: raise BrowserError("Too many pending browser control responses.")
            self._responses[response["id"]] = response
        else:
            self._event(response)

    def _event(self, response):
        if "method" in response and self.on_event:
            self.on_event(response["method"], response.get("params", {}))

    def pump(self, timeout=0.1, max_messages=100):
        deadline = time.monotonic() + timeout
        for _ in range(max_messages):
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            self._socket.settimeout(remaining)
            try: raw = self._socket.recv()
            except websocket.WebSocketTimeoutException: break
            if not raw or len(raw) > self._max_message_bytes:
                raise BrowserError("The browser control response was unavailable or too large.")
            self._accept(json.loads(raw))

    def __repr__(self):
        return "<BrowserConnection private>"

    def call(self, method, params=None):
        identifier = self.send(method, params)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if identifier in self._responses:
                response = self._responses.pop(identifier)
                if "error" in response: raise BrowserError("The browser could not complete a control request.")
                return response.get("result", {})
            self._socket.settimeout(min(3, max(0.01, deadline - time.monotonic())))
            raw = self._socket.recv()
            if not raw or len(raw) > self._max_message_bytes:
                raise BrowserError("The browser control response was unavailable or too large.")
            response = json.loads(raw)
            self._accept(response)
        raise BrowserError("The browser control request timed out.")

    def close(self):
        self._socket.close()


def find_browser(name, explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise BrowserError("The --browser-path must name an existing executable using its absolute path.")
    if name not in {"chrome", "chromium"}:
        raise BrowserError("Choose chrome or chromium.")
    candidates = (["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable"]
                  if name == "chrome" else ["/Applications/Chromium.app/Contents/MacOS/Chromium", "chromium", "chromium-browser"])
    if sys.platform == "win32" and name == "chrome":
        candidates = [str(Path(os.environ.get(root, "")) / "Google/Chrome/Application/chrome.exe")
                      for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise BrowserError("Browser not found. Install Chrome or Chromium, or supply --browser-path.")


class BrowserSession:
    """Keep an owned browser alive until the caller exits its context manager."""

    def __init__(self, url, browser="chrome", browser_path=None, on_event=None):
        self._origin = origin(url)
        self._url = url
        self._executable = find_browser(browser, browser_path)
        self._process = self._profile = self._connection = None
        self._signals = {}
        self._cancelled = False
        self._on_event = on_event
        self._port = None

    def __repr__(self):
        return "<BrowserSession temporary; credentials not exported>"

    def _cancel(self, *_args):
        self._cancelled = True

    def _check(self):
        if self._cancelled:
            raise KeyboardInterrupt()
        if self._process.poll() is not None:
            raise BrowserError("The login browser closed. Start a fresh command to retry.")

    def __enter__(self):
        try:
            if threading.current_thread() is threading.main_thread():
                for event in (signal.SIGINT, signal.SIGTERM):
                    self._signals[event] = signal.signal(event, self._cancel)
            self._profile = tempfile.TemporaryDirectory(prefix="website-client-browser-")
            self._process = subprocess.Popen([
                self._executable, f"--user-data-dir={self._profile.name}",
                "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                "--no-first-run", "--no-default-browser-check", "--disable-sync", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
            active = Path(self._profile.name) / "DevToolsActivePort"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self._check()
                try:
                    port = int(active.read_text().splitlines()[0])
                    if not 0 < port < 65536:
                        raise ValueError()
                    opener = build_opener(ProxyHandler({}))
                    with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                        targets = json.loads(response.read(1024 * 1024))
                    page = next(item for item in targets if item.get("type") == "page")
                    self._connection = Connection(page["webSocketDebuggerUrl"], port)
                    self._port = port
                    break
                except (OSError, ValueError, KeyError, StopIteration):
                    time.sleep(0.1)
            if self._connection is None:
                raise BrowserError("The browser did not open its control connection. Check the installation and retry.")
            self._connection.call("Browser.setDownloadBehavior", {"behavior": "deny"})
            if self._on_event:
                self._connection.on_event = self._on_event
                self._connection.call("Network.enable")
            result = self._connection.call("Page.navigate", {"url": self._url})
            if result.get("errorText"):
                raise BrowserError("Could not open the website. Check its address and connection.")
            return self
        except (KeyboardInterrupt, BrowserError):
            self.close()
            raise
        except Exception:
            self.close()
            raise BrowserError("Could not start the browser session. Check the browser installation and retry.") from None

    def current_url(self):
        self._check()
        return self._connection.call("Page.getFrameTree")["frameTree"]["frame"]["url"]

    def _password_visible(self):
        root = self._connection.call("DOM.getDocument")["root"]["nodeId"]
        nodes = self._connection.call("DOM.querySelectorAll", {"nodeId": root, "selector": 'input[type="password"]'})["nodeIds"]
        for node in nodes:
            # Hidden inputs have no box. This checks layout through the native
            # protocol rather than evaluating a script in the website.
            try:
                model = self._connection.call("DOM.getBoxModel", {"nodeId": node})["model"]
                if model["width"] > 0 and model["height"] > 0:
                    return True
            except BrowserError:
                continue
        return False

    def wait_for_enter(self, prompt, input_stream=None, output_stream=None, poll=None):
        incoming = sys.stdin if input_stream is None else input_stream
        outgoing = sys.stderr if output_stream is None else output_stream
        if not incoming.isatty():
            raise BrowserError("Login requires an interactive terminal, without piped input.")
        print(prompt, file=outgoing, flush=True)
        try:
            if sys.platform == "win32":
                import msvcrt
                while True:
                    self.current_url()
                    if poll: poll()
                    if msvcrt.kbhit():
                        key = msvcrt.getwch()
                        if key == "\x03": raise KeyboardInterrupt()
                        if key in {"\r", "\n"}: break
                    time.sleep(0.1)
            else:
                with selectors.DefaultSelector() as terminal:
                    terminal.register(incoming, selectors.EVENT_READ)
                    while True:
                        self.current_url()
                        if poll: poll()
                        if terminal.select(timeout=0.1):
                            if not incoming.readline():
                                raise BrowserError("Terminal input closed. Start a fresh command to retry.")
                            break
        except (BrowserError, KeyboardInterrupt):
            raise
        except Exception:
            raise BrowserError("The browser session ended unexpectedly. Start a fresh command to retry.") from None

    def confirm_login(self, input_stream=None, output_stream=None, prompt=None):
        self.wait_for_enter(prompt or "Log in in the browser, including verification codes. Return to the requested website, "
                            "then press Enter here to finish. Ctrl+C cancels.", input_stream, output_stream)
        try:
            current = self.current_url()
            path = urlsplit(current).path.rstrip("/").split("/")[-1].lower()
            if (origin(current) != self._origin or path in {"login", "signin", "sign-in", "sign_in", "mfa"}
                    or self._password_visible()):
                raise BrowserError("Login is not complete. Sign in and return to the requested website before confirming.")
        except (BrowserError, KeyboardInterrupt):
            raise
        except Exception:
            raise BrowserError("The browser session ended unexpectedly. Start a fresh command to retry.") from None
        return {"sessionReady": True, "authentication": "user-confirmed", "apiVerified": False}

    def close(self):
        try:
            if self._connection:
                try: self._connection.call("Browser.close")
                except Exception: pass
                try: self._connection.close()
                except Exception: pass
            if self._process:
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.terminate()
                    try: self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=3)
            if self._profile:
                self._profile.cleanup()
        finally:
            self._connection = self._process = self._profile = None
            for event, previous in self._signals.items():
                signal.signal(event, previous)
            self._signals.clear()

    def __exit__(self, *_args):
        self.close()
