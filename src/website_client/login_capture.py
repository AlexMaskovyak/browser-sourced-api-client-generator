"""Capture the page's natural JSON reads before the login navigation starts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

from .browser import BrowserError, origin
from .capture import NetworkCapture
from .definitions import openapi_document
from .discovery import Discovery, route, safe_query
from .har_inference import infer_har
from .operation_contracts import method_name


@dataclass(frozen=True)
class LoginCaptureResult:
    """One upstream-inferred definition and its sanitized supporting files."""

    definition: dict | None
    evidence: dict[str, str]


def _literal_read(url):
    """Bound this first slice to literal routes and unrepeated string arguments."""
    try:
        _, path, parameters = route(url)
        parsed = urlsplit(url)
        keys = [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
        return (
            not parameters
            and path == parsed.path
            and re.fullmatch(r"/[A-Za-z0-9._~/-]*", path)
            and len(keys) == len(set(keys))
            and set(keys) == set(safe_query(url))
        )
    except (BrowserError, ValueError):
        return False


class LoginCapture:
    """Retain sanitized successful same-origin JSON GET exchanges without replay."""

    def __init__(self, session):
        self.session = session
        self.capture = NetworkCapture(session)

    def event(self, method, params):
        # The listener is installed before BrowserSession enters and navigates.
        self.capture.connection = self.session._connection
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            try:
                eligible = (
                    request.get("method") == "GET"
                    and params.get("type") in {"Fetch", "XHR"}
                    and origin(request.get("url")) == self.session._origin
                    and _literal_read(request.get("url"))
                )
            except BrowserError:
                eligible = False
            if not eligible:
                # A redirect can reuse an ID. Never pair an allowed request with
                # a later cross-origin or otherwise excluded response body.
                previous = self.capture.pending.pop(params.get("requestId"), None)
                if previous:
                    self.capture.pending_bytes -= previous.get("_bytes", 0)
                return
        self.capture.event(method, params)

    def finish(self):
        """Infer contracts offline from completed, sanitized successful JSON reads."""
        self.capture.connection = self.session._connection
        self.capture.drain()
        eligible = Discovery()
        for entry in self.capture.discovery.entries:
            response = entry["response"]
            content = response["content"]
            if not 200 <= response["status"] < 300:
                continue
            if not (
                content["mimeType"] == "application/json"
                or content["mimeType"].endswith("+json")
            ):
                continue
            try:
                body = json.loads(content.get("text", ""))
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            eligible.add(entry)
        if not eligible.entries:
            return LoginCaptureResult(None, {})
        files = {"capture.har": eligible.har_text()}
        definition, diagnostics = infer_har(files["capture.har"])
        definition = _public_definition(definition)
        if not definition["paths"]:
            return LoginCaptureResult(None, {})
        files["inference.json"] = (
            json.dumps(diagnostics, indent=2, sort_keys=True) + "\n"
        )
        return LoginCaptureResult(definition, files)


def _public_definition(definition):
    """Give inferred GETs deterministic route names and preserve query wire names."""
    paths = {}
    names = {}
    for path, item in sorted(definition.get("paths", {}).items()):
        operation = item.get("get")
        if not isinstance(operation, dict) or "{" in path:
            continue
        base = method_name("get_" + re.sub(r"[^a-zA-Z0-9_]+", "_", path).strip("_"))
        name = base
        if name in names:
            name += "_" + hashlib.sha256(("GET " + path).encode()).hexdigest()[:8]
        names[name] = path
        operation["operationId"] = name
        for parameter in item.get("parameters", []) + operation.get("parameters", []):
            if parameter.get("in") == "query":
                parameter["schema"] = {"type": "string"}
                # Observation establishes a name, not whether omission is valid.
                parameter["required"] = False
        paths[path] = {**item, "get": operation}
    definition["paths"] = paths
    definition["x-discovery-inferred"] = True
    definition["x-discovery-limitations"] = (
        "Observed successful JSON GETs only; field presence and complete service coverage are unknown."
    )
    return openapi_document(definition)
