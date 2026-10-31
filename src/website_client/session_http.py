"""Authenticate and send one typed endpoint request through a native browser."""

import base64
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .browser import BrowserError, BrowserSession, Connection, origin

MAX_BYTES = 8 * 1024 * 1024
DEFAULT_HEADERS = {"authorization", "x-csrf-token", "x-xsrf-token", "csrf-token", "xsrf-token"}
MANAGED_HEADERS = {"host", "cookie", "content-length", "connection", "accept-encoding", "user-agent", "origin", "referer", "transfer-encoding"}


def canonical(url):
    parsed = urlsplit(str(url))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class BrowserLoginTransport(httpx.BaseTransport):
    def __init__(self, endpoint, method, login_url=None, browser="chrome", browser_path=None, session_headers=()):
        self._origin = origin(endpoint)
        self._login_url = login_url or endpoint
        self._login_origin = origin(self._login_url)
        self._method = method.upper()
        self._browser_name, self._browser_path = browser, browser_path
        self._allowed_headers = set(DEFAULT_HEADERS)
        for name in session_headers:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", name) or name.lower() in MANAGED_HEADERS:
                raise BrowserError("Choose a valid additional session-header name.")
            self._allowed_headers.add(name.lower())
        self._headers = {}
        self._session = None
        self._request_connection = None
        self._target = None
        self._used = False

    def __repr__(self): return "<BrowserLoginTransport temporary; credentials not exported>"

    def _observe(self, method, params):
        if method != "Network.requestWillBeSent": return
        try:
            request = params["request"]
            if origin(request["url"]) != self._origin: return
            if origin(params["documentURL"]) not in {self._login_origin, self._origin}: return
            for name, value in request.get("headers", {}).items():
                if name.lower() in self._allowed_headers and isinstance(value, str) and len(value) <= 16384 and "\r" not in value and "\n" not in value:
                    self._headers[name.lower()] = value
        except Exception: return

    def __enter__(self):
        if self._session is not None: return self
        try:
            self._session = BrowserSession(self._login_url, self._browser_name, self._browser_path, on_event=self._observe)
            self._session.__enter__()
            self._session.confirm_login(prompt="Log in in the browser, including verification codes. Open the application page, "
                "then press Enter here to run the endpoint. Ctrl+C cancels.")
            self._session._connection.pump(timeout=0.1)
            return self
        except BaseException:
            self.close()
            raise

    def handle_request(self, request):
        if self._used or self._session is None or origin(str(request.url)) != self._origin or request.method != self._method:
            raise BrowserError("This browser session permits one request to the selected API origin and method.")
        body = request.read()
        if len(body) > MAX_BYTES: raise BrowserError("The request exceeds the 8 MiB browser transport limit.")
        self._used = True
        self._session._check()
        url = canonical(request.url)
        parent = self._session._connection
        paused = []
        try:
            self._target = parent.call("Target.createTarget", {"url": "about:blank", "background": True})["targetId"]
            connection = Connection(f"ws://127.0.0.1:{self._session._port}/devtools/page/{self._target}", self._session._port, max_message_bytes=16 * 1024 * 1024)
            self._request_connection = connection
            connection.on_event = lambda method, params: paused.append(params) if method == "Fetch.requestPaused" else None
            connection.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}, {"urlPattern": "*", "requestStage": "Response"}]})
            connection.send("Page.navigate", {"url": url})
            sent = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                self._session._check()
                connection.pump(timeout=0.1)
                ready, paused[:] = list(paused), []
                for event in ready:
                    identifier = event["requestId"]
                    if canonical(event["request"]["url"]) != url:
                        connection.call("Fetch.failRequest", {"requestId": identifier, "errorReason": "BlockedByClient"})
                        raise BrowserError("Browser request redirects are blocked.")
                    if "responseStatusCode" in event:
                        status = int(event["responseStatusCode"])
                        headers = [(item["name"], item["value"]) for item in event.get("responseHeaders", [])]
                        content = b""
                        if request.method != "HEAD" and status not in {204, 304} and not 300 <= status < 400:
                            response = connection.call("Fetch.getResponseBody", {"requestId": identifier})
                            raw = response.get("body", "")
                            if len(raw) > 12 * 1024 * 1024: raise BrowserError("Browser response exceeds its size limit.")
                            content = base64.b64decode(raw, validate=True) if response.get("base64Encoded") else raw.encode()
                            if len(content) > MAX_BYTES: raise BrowserError("Browser response exceeds the 8 MiB limit.")
                        connection.call("Fetch.failRequest", {"requestId": identifier, "errorReason": "Aborted"})
                        # Chrome supplies decoded bytes. Remove compression/length
                        # headers so HTTPX does not decompress a second time.
                        headers = [(key, value) for key, value in headers if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                        return httpx.Response(status, headers=headers, content=content, request=request)
                    if sent: raise BrowserError("A second browser request was blocked; no automatic retry is allowed.")
                    sent = True
                    headers = {key.lower(): value for key, value in event["request"].get("headers", {}).items() if key.lower() not in {"content-length", "content-type"}}
                    headers.update(self._headers)
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        application = urlsplit(self._login_url)
                        headers["origin"] = urlunsplit((application.scheme, application.netloc, "", "", ""))
                        headers["referer"] = urlunsplit((application.scheme, application.netloc, application.path or "/", "", ""))
                    for name, value in request.headers.items():
                        if name.lower() not in MANAGED_HEADERS and not name.lower().startswith("sec-"): headers[name] = value
                    parameters = {"requestId": identifier, "method": request.method,
                        "headers": [{"name": key, "value": value} for key, value in headers.items()], "interceptResponse": True}
                    if body or request.method not in {"GET", "HEAD"}: parameters["postData"] = base64.b64encode(body).decode()
                    connection.call("Fetch.continueRequest", parameters)
            raise BrowserError("Browser endpoint request timed out. Check the service before retrying.")
        except (BrowserError, KeyboardInterrupt): raise
        except Exception:
            raise BrowserError("Browser endpoint request failed. It may have succeeded; check the service before retrying.") from None
        finally:
            if self._request_connection:
                try: self._request_connection.close()
                except Exception: pass
                self._request_connection = None
            if self._target:
                try: parent.call("Target.closeTarget", {"targetId": self._target})
                except Exception: pass
                self._target = None

    def close(self):
        if self._session:
            if self._session._connection: self._session._connection.on_event = None
            self._session.close()
            self._session = None
        self._headers.clear()

    def __exit__(self, *_args): self.close()
