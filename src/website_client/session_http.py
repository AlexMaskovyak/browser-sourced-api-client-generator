"""Authenticate and send one typed endpoint request through a native browser."""

import base64
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .browser import BrowserError, BrowserSession, origin

BYTES_PER_MEBIBYTE = 1024 * 1024
# Bound the memory used by one endpoint call, independently in each direction.
MAX_REQUEST_BODY_BYTES = 8 * BYTES_PER_MEBIBYTE
MAX_RESPONSE_BODY_BYTES = 8 * BYTES_PER_MEBIBYTE

# Base64 represents each group of up to three bytes with four ASCII characters.
# Check its exact maximum before decoding, then enforce the decoded byte limit.
MAX_BASE64_RESPONSE_CHARACTERS = 4 * ((MAX_RESPONSE_BODY_BYTES + 2) // 3)

# Cached session headers have a separate, smaller budget than endpoint bodies.
MAX_SESSION_HEADER_VALUE_BYTES = 16 * 1024
MAX_SESSION_HEADER_NAME_CHARACTERS = 128
MAX_SESSION_HEADERS = 32
# This bounds the endpoint request; manual sign-in itself has no time limit.
REQUEST_TIMEOUT_SECONDS = 30
BROWSER_EVENT_POLL_SECONDS = 0.1

DEFAULT_HEADERS = {
    "authorization",
    "x-csrf-token",
    "x-xsrf-token",
    "csrf-token",
    "xsrf-token",
}
MANAGED_HEADERS = {
    "host",
    "cookie",
    "content-length",
    "connection",
    "accept-encoding",
    "user-agent",
    "origin",
    "referer",
    "transfer-encoding",
}
DECODED_RESPONSE_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
}


def canonical(url):
    """Normalize a browser URL for exact request and redirect comparisons."""
    parsed = urlsplit(str(url))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )


class BrowserLoginTransport(httpx.BaseTransport):
    """Own login and one endpoint request, including failure cleanup."""

    def __init__(
        self,
        endpoint,
        method,
        login_url=None,
        browser="chrome",
        browser_path=None,
        session_headers=(),
    ):
        self._origin = origin(endpoint)
        self._login_url = login_url or endpoint
        self._login_origin = origin(self._login_url)
        self._method = method.upper()
        self._browser_name = browser
        self._browser_path = browser_path
        self._allowed_headers = set(DEFAULT_HEADERS)

        for name in session_headers:
            self._validate_session_header_name(name)
            self._allowed_headers.add(name.lower())
        if len(self._allowed_headers) > MAX_SESSION_HEADERS:
            raise BrowserError("Too many selected session headers.")

        self._headers = {}
        self._session = None
        self._owns_session = True
        self._request_connection = None
        self._target = None
        self._used = False

    def __repr__(self):
        return "<BrowserLoginTransport temporary; credentials not exported>"

    @classmethod
    def for_session(cls, session, endpoint, method, headers=None):
        """Send one scoped request without taking ownership of an open session."""
        selected = {}
        for name, value in (headers or {}).items():
            if not isinstance(name, str):
                raise BrowserError("Choose a valid additional session-header name.")
            if name.lower() in MANAGED_HEADERS or name.lower().startswith("sec-"):
                continue
            selected[name] = value
        transport = cls(
            endpoint,
            method,
            login_url=session._url,
            session_headers=tuple(selected),
        )
        transport._session = session
        transport._owns_session = False
        transport._observe(
            "Network.requestWillBeSent",
            {
                "documentURL": session._url,
                "request": {"url": endpoint, "headers": selected},
            },
        )
        return transport

    @staticmethod
    def _validate_session_header_name(name):
        valid_name = (
            isinstance(name, str)
            and len(name) <= MAX_SESSION_HEADER_NAME_CHARACTERS
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", name)
        )
        if (
            not valid_name
            or name.lower() in MANAGED_HEADERS
            or name.lower().startswith("sec-")
        ):
            raise BrowserError("Choose a valid additional session-header name.")

    def _observe(self, method, params):
        """Keep selected API headers in memory; ignore bodies and other origins."""
        if method != "Network.requestWillBeSent":
            return

        try:
            request = params["request"]
            if origin(request["url"]) != self._origin:
                return
            if origin(params["documentURL"]) not in {
                self._login_origin,
                self._origin,
            }:
                return

            for name, value in request.get("headers", {}).items():
                if name.lower() not in self._allowed_headers:
                    continue
                if not isinstance(value, str):
                    continue
                if len(value.encode()) > MAX_SESSION_HEADER_VALUE_BYTES:
                    continue
                if "\r" in value or "\n" in value:
                    continue
                self._headers[name.lower()] = value
        except Exception:
            # Malformed events and unrelated sign-in pages are not auth inputs.
            return

    def __enter__(self):
        # The CLI and HTTPX both enter this transport. They share one login.
        if self._session is not None:
            return self

        try:
            self._session = BrowserSession(
                self._login_url,
                self._browser_name,
                self._browser_path,
                on_event=self._observe,
            )
            self._session.__enter__()
            self._session.confirm_login(
                prompt=(
                    "Log in in the browser, including verification codes. "
                    "Open the application page, then press Enter here to run "
                    "the endpoint. Ctrl+C cancels."
                )
            )
            self._session._connection.pump(timeout=BROWSER_EVENT_POLL_SECONDS)
            return self
        except BaseException:
            self.close()
            raise

    def _validate_request(self, request):
        allowed = (
            not self._used
            and self._session is not None
            and origin(str(request.url)) == self._origin
            and request.method == self._method
        )
        if not allowed:
            raise BrowserError(
                "This browser session permits one request to the selected "
                "API origin and method."
            )

        request_body_bytes = request.read()
        if len(request_body_bytes) > MAX_REQUEST_BODY_BYTES:
            raise BrowserError("The request exceeds the 8 MiB browser transport limit.")
        return request_body_bytes

    def _collect_paused_request(self, method, event):
        if method != "Fetch.requestPaused":
            return
        try:
            if canonical(event["request"]["url"]) != canonical(
                self._active_request.url
            ):
                self._request_connection.call(
                    "Fetch.failRequest",
                    {
                        "requestId": event["requestId"],
                        "errorReason": "BlockedByClient",
                    },
                )
                raise BrowserError("Browser request redirects are blocked.")
            if "responseStatusCode" in event:
                self._response = self._finish_response(self._active_request, event)
            else:
                if self._sent:
                    raise BrowserError(
                        "A second browser request was blocked; no automatic retry is allowed."
                    )
                self._sent = True
                self._continue_request(self._active_request, event, self._active_body)
        except BaseException as error:
            # Keep exceptions out of Playwright's event dispatcher. The calling
            # command receives them after navigation unwinds.
            self._failure = error
            try:
                self._request_connection.call(
                    "Fetch.failRequest",
                    {
                        "requestId": event["requestId"],
                        "errorReason": "Aborted",
                    },
                )
            except Exception:
                pass

    def _open_request_target(self):
        """Playwright owns the separate tab and its CDP connection."""
        self._target = self._session._context.new_page()
        self._request_connection = self._session.connection_for(self._target)
        self._request_connection.on_event = self._collect_paused_request
        self._request_connection.call(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "*", "requestStage": "Request"},
                    {"urlPattern": "*", "requestStage": "Response"},
                ]
            },
        )

    def _request_headers(self, request, event):
        # Preserve native cookies and browser identity. Typed methods supply
        # content headers, but cannot replace browser-managed session headers.
        headers = {
            name.lower(): value
            for name, value in event["request"].get("headers", {}).items()
            if name.lower() not in {"content-length", "content-type"}
        }
        headers.update(self._headers)

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            application = urlsplit(self._login_url)
            headers["origin"] = urlunsplit(
                (application.scheme, application.netloc, "", "", "")
            )
            headers["referer"] = urlunsplit(
                (
                    application.scheme,
                    application.netloc,
                    application.path or "/",
                    "",
                    "",
                )
            )

        for name, value in request.headers.items():
            if name.lower() in MANAGED_HEADERS:
                continue
            if name.lower().startswith("sec-"):
                continue
            headers[name] = value

        return [{"name": name, "value": value} for name, value in headers.items()]

    def _continue_request(self, request, event, request_body_bytes):
        parameters = {
            "requestId": event["requestId"],
            "method": request.method,
            "headers": self._request_headers(request, event),
            "interceptResponse": True,
        }
        if request_body_bytes or request.method not in {"GET", "HEAD"}:
            parameters["postData"] = base64.b64encode(request_body_bytes).decode()
        self._request_connection.call("Fetch.continueRequest", parameters)

    def _read_response_body(self, request, event):
        status_code = int(event["responseStatusCode"])
        no_body = (
            request.method == "HEAD"
            or status_code in {204, 304}
            or 300 <= status_code < 400
        )
        if no_body:
            return b""

        response = self._request_connection.call(
            "Fetch.getResponseBody",
            {"requestId": event["requestId"]},
        )
        raw_body = response.get("body", "")
        if response.get("base64Encoded"):
            if len(raw_body) > MAX_BASE64_RESPONSE_CHARACTERS:
                raise BrowserError("Encoded browser response exceeds its size limit.")
            response_body_bytes = base64.b64decode(raw_body, validate=True)
        else:
            response_body_bytes = raw_body.encode()

        if len(response_body_bytes) > MAX_RESPONSE_BODY_BYTES:
            raise BrowserError("Browser response exceeds the 8 MiB limit.")
        return response_body_bytes

    def _finish_response(self, request, event):
        response_body_bytes = self._read_response_body(request, event)

        # Read the API bytes without rendering the response as a webpage.
        self._request_connection.call(
            "Fetch.failRequest",
            {"requestId": event["requestId"], "errorReason": "Aborted"},
        )

        # Chrome already decoded the body. Removing these headers prevents
        # HTTPX from decompressing it again or using the original wire length.
        headers = [
            (item["name"], item["value"])
            for item in event.get("responseHeaders", [])
            if item["name"].lower() not in DECODED_RESPONSE_HEADERS
        ]
        return httpx.Response(
            int(event["responseStatusCode"]),
            headers=headers,
            content=response_body_bytes,
            request=request,
        )

    def _wait_for_response(self):
        deadline_seconds = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        while time.monotonic() < deadline_seconds:
            self._session._check()
            if self._failure is not None:
                raise self._failure
            if self._response is not None:
                return self._response
            self._request_connection.pump(timeout=BROWSER_EVENT_POLL_SECONDS)
        raise BrowserError(
            "Browser endpoint request timed out. Check the service before retrying."
        )

    def handle_request(self, request):
        request_body_bytes = self._validate_request(request)
        self._used = True
        self._session._check()

        self._active_request = request
        self._active_body = request_body_bytes
        self._response = self._failure = None
        self._sent = False
        try:
            self._open_request_target()
            try:
                self._target.goto(
                    canonical(request.url),
                    wait_until="commit",
                    timeout=REQUEST_TIMEOUT_SECONDS * 1000,
                )
            except Exception:
                # Aborting the intercepted response intentionally cancels page
                # navigation. A captured result or failure remains authoritative.
                if self._response is None and self._failure is None:
                    raise
            return self._wait_for_response()
        except (BrowserError, KeyboardInterrupt):
            raise
        except Exception:
            raise BrowserError(
                "Browser endpoint request failed. It may have succeeded; "
                "check the service before retrying."
            ) from None
        finally:
            self._close_request_target()

    def _close_request_target(self):
        if self._request_connection:
            try:
                self._request_connection.close()
            except Exception:
                # Browser closure can disconnect the socket before cleanup.
                pass
            self._request_connection = None

        if self._target:
            try:
                self._target.close()
            except Exception:
                pass
            self._target = None
        self._active_request = self._active_body = self._response = self._failure = None

    def close(self):
        try:
            self._close_request_target()
            if self._session and self._owns_session:
                if self._session._connection:
                    self._session._connection.on_event = None
                self._session.close()
        finally:
            self._session = None
            self._headers.clear()

    def __exit__(self, *_args):
        self.close()
