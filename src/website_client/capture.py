"""Observe native browser traffic after login without replaying requests."""

from datetime import datetime, timezone
import time

from .browser import BrowserError, origin
from .discovery import Discovery, MAX_BODY


class NetworkCapture:
    def __init__(self, session, max_entries=1000):
        self.session = session
        self.connection = session._connection
        self.discovery = Discovery(max_entries)
        self.pending = {}
        self.finished = []
        self.body_failures = 0
        self.pending_bytes = 0

    def event(self, method, params):
        identifier = params.get("requestId")
        if not isinstance(identifier, str): return
        if method == "Network.requestWillBeSent":
            try:
                if origin(params.get("documentURL")) != self.session._origin: return
            except BrowserError: return
            if params.get("type") not in {"Fetch", "XHR", "Document"}: return
            request = params.get("request", {})
            if identifier in self.pending:
                previous = self.pending.pop(identifier)
                self.pending_bytes -= previous.pop("_bytes", 0)
                redirect = params.get("redirectResponse", {})
                previous["response"] = {"status": redirect.get("status", 0), "content": {"mimeType": redirect.get("mimeType")}}
                self.discovery.add(previous)
            post = request.get("postData", "")
            size = len(post.encode()) if isinstance(post, str) else 0
            if (len(self.pending) >= 100 or size > MAX_BODY or self.pending_bytes + size > 8 * MAX_BODY
                    or len(self.discovery.entries) >= self.discovery.max_entries):
                self.discovery.omitted += 1
                return
            self.pending_bytes += size
            self.pending[identifier] = {"request": {"url": request.get("url"), "method": request.get("method"),
                "postData": {"text": post}}, "response": {}, "_started": params.get("timestamp", 0), "_bytes": size,
                "startedDateTime": datetime.fromtimestamp(params.get("wallTime", time.time()), timezone.utc).isoformat()}
        elif method == "Network.responseReceived" and identifier in self.pending:
            response = params.get("response", {})
            protocol = {"h2": "HTTP/2.0", "h3": "HTTP/3.0", "http/1.1": "HTTP/1.1", "http/1.0": "HTTP/1.0"}.get(response.get("protocol"), "")
            self.pending[identifier]["request"]["httpVersion"] = protocol
            self.pending[identifier]["response"] = {"status": response.get("status", 0),
                "httpVersion": protocol,
                "content": {"mimeType": response.get("mimeType"), "size": response.get("encodedDataLength", 0)}}
        elif method in {"Network.loadingFinished", "Network.loadingFailed"} and identifier in self.pending:
            self.finished.append((identifier, params.get("timestamp", 0), method == "Network.loadingFailed"))
            # Read completed bodies before another navigation can evict them.
            # Playwright delivers this callback while servicing the browser.
            self._consume_finished()

    def drain(self):
        self.connection.pump()
        self._consume_finished()

    def _consume_finished(self):
        completed, self.finished = self.finished, []
        for identifier, ended, failed in completed:
            entry = self.pending.pop(identifier, None)
            if entry is None: continue
            self.pending_bytes -= entry.pop("_bytes", 0)
            entry["time"] = max(0, (ended - entry.pop("_started", ended)) * 1000)
            content = entry.setdefault("response", {}).setdefault("content", {})
            content_type = content.get("mimeType", "") or ""
            if not failed and any(part in content_type for part in ("json", "yaml", "graphql")):
                try:
                    body = self.connection.call("Network.getResponseBody", {"requestId": identifier})
                    text = body.get("body", "")
                    if not isinstance(text, str) or len(text.encode()) > MAX_BODY: raise ValueError()
                    content["text"] = text
                    if body.get("base64Encoded"): content["encoding"] = "base64"
                except Exception: self.body_failures += 1
            if failed: self.discovery.capture_failures += 1
            self.discovery.add(entry)

    def run(self, input_stream=None, output_stream=None):
        self.connection.on_event = self.event
        try:
            self.connection.call("Network.enable", {"maxTotalBufferSize": 8 * MAX_BODY, "maxResourceBufferSize": MAX_BODY, "maxPostDataSize": MAX_BODY})
            self.session.wait_for_enter(
                "Capture started. Reload the page and browse the workflows to cover. "
                "Press Enter here to save the HAR and definitions. Ctrl+C cancels.",
                input_stream, output_stream, self.drain)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                self.drain()
                if not self.pending: break
            self.discovery.omitted += len(self.pending)
            self.discovery.capture_failures += self.body_failures
            return self.discovery
        finally:
            self.connection.on_event = None
            self.pending.clear()
            self.finished.clear()
            try: self.connection.call("Network.disable")
            except Exception: pass
