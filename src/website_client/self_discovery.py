"""Find service definitions with bounded reads in an authenticated browser."""

import hashlib
import json
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from graphql import get_introspection_query, parse
import httpx

from .browser import BrowserError, origin
from .capture import NetworkCapture
from .definitions import graphql_schema, openapi_document
from .discovery import AUTH, AUTH_OPERATION, MAX_BODY, route
from .session_http import (
    BrowserLoginTransport,
    DEFAULT_HEADERS,
    MAX_SESSION_HEADER_VALUE_BYTES,
)

DEFAULT_MAX_PROBES = 12
DEFAULT_OBSERVE_SECONDS = 5
MAX_LINKS = 200
MAX_CANDIDATES = 50
MAX_AUTH_ORIGINS = 10
COMMON_DEFINITION_PATHS = (
    "/openapi.json",
    "/openapi.yaml",
    "/api/openapi.json",
    "/v3/api-docs",
    "/.well-known/openapi.json",
    "/swagger.json",
)
SCHEMA_PATH = re.compile(
    r"/(?:openapi(?:\.json|\.ya?ml)?|swagger\.json|schema\.(?:graphql|gql)|api-docs)$",
    re.I,
)


def validate_settings(max_probes, observe_seconds, graphql_endpoints):
    if not 1 <= max_probes <= 30 or not 1 <= observe_seconds <= 30:
        raise BrowserError("Use 1 to 30 probes and 1 to 30 observation seconds.")
    if len(graphql_endpoints) > 30:
        raise BrowserError("Supply at most 30 explicit GraphQL endpoints.")
    for endpoint in graphql_endpoints:
        origin(endpoint)
        if urlsplit(endpoint).query or AUTH.search(urlsplit(endpoint).path):
            raise BrowserError(
                "GraphQL endpoints must not contain query values or authentication paths."
            )


def schema_url(base_url, href):
    """Accept only same-origin definition links without query credentials."""
    try:
        candidate = urljoin(base_url, href)
        parsed = urlsplit(candidate)
        if (
            origin(candidate) != origin(base_url)
            or parsed.query
            or AUTH.search(parsed.path)
        ):
            return None
        return candidate if SCHEMA_PATH.search(parsed.path) else None
    except (BrowserError, ValueError, TypeError):
        return None


def graphql_request(request):
    """Recognize a GraphQL document without retaining variable or literal values."""
    try:
        if request.get("method") == "GET":
            payload = {
                key: values[0]
                for key, values in parse_qs(urlsplit(request["url"]).query).items()
            }
        else:
            text = request.get("postData", "")
            if not isinstance(text, str) or len(text.encode()) > MAX_BODY:
                return False
            payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
            return False
        if AUTH_OPERATION.search(str(payload.get("operationName", ""))):
            return False
        if len(payload["query"].encode()) > MAX_BODY:
            return False
        document = parse(payload["query"], max_tokens=20_000)
        return any(item.kind == "operation_definition" for item in document.definitions)
    except Exception:
        return False


class DiscoveryObserver:
    """Retain selected session headers by origin, never cookie or payload values."""

    def __init__(self, url):
        self.url = url
        self.origin = origin(url)
        self.headers = {}
        self.graphql_endpoints = []
        self.schema_links = []
        self.discovering = False

    def event(self, method, params):
        if method != "Network.requestWillBeSent":
            return
        try:
            if origin(params["documentURL"]) != self.origin:
                return
            request = params["request"]
            target_origin = origin(request["url"])
            if (
                target_origin not in self.headers
                and len(self.headers) >= MAX_AUTH_ORIGINS
            ):
                return
            selected = self.headers.setdefault(target_origin, {})
            for name, value in request.get("headers", {}).items():
                if (
                    name.lower() in DEFAULT_HEADERS
                    and isinstance(value, str)
                    and len(value.encode()) <= MAX_SESSION_HEADER_VALUE_BYTES
                    and "\r" not in value
                    and "\n" not in value
                ):
                    selected[name.lower()] = value
            if not self.discovering:
                return
            parsed = urlsplit(request["url"])
            if AUTH.search(parsed.path):
                return
            # Query strings can contain variables or tokens. They are not
            # forwarded to schema probes or persisted in the probe report.
            endpoint = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
            )
            if (
                graphql_request(request)
                and endpoint not in self.graphql_endpoints
                and len(self.graphql_endpoints) < MAX_CANDIDATES
            ):
                self.graphql_endpoints.append(endpoint)
            linked = schema_url(self.url, request["url"])
            if (
                linked
                and linked not in self.schema_links
                and len(self.schema_links) < MAX_CANDIDATES
            ):
                self.schema_links.append(linked)
        except (KeyError, BrowserError, TypeError, ValueError):
            return

    def clear(self):
        self.headers.clear()


class SelfDiscovery:
    """Observe a reload, then fetch definitions without replaying business calls."""

    def __init__(
        self,
        session,
        observer,
        max_entries=1000,
        max_probes=DEFAULT_MAX_PROBES,
        observe_seconds=DEFAULT_OBSERVE_SECONDS,
        graphql_endpoints=(),
    ):
        validate_settings(max_probes, observe_seconds, graphql_endpoints)
        self.session = session
        self.observer = observer
        self.capture = NetworkCapture(session, max_entries)
        self.max_probes = max_probes
        self.observe_seconds = observe_seconds
        self.explicit_endpoints = list(graphql_endpoints)
        self.attempts = []

    def _event(self, method, params):
        self.observer.event(method, params)
        self.capture.event(method, params)

    def _candidates(self):
        candidates = [
            ("graphql", value)
            for value in self.explicit_endpoints + self.observer.graphql_endpoints
        ]
        links = self.session._page.locator("a[href], link[href]")
        for index in range(min(links.count(), MAX_LINKS)):
            value = schema_url(
                self.session._url, links.nth(index).get_attribute("href")
            )
            if value:
                candidates.append(("definition", value))
        candidates += [("definition", value) for value in self.observer.schema_links]
        candidates += [
            ("definition", urljoin(self.session._url, path))
            for path in COMMON_DEFINITION_PATHS
        ]
        return list(dict.fromkeys(candidates))

    def _save_definition(self, response, kind):
        text = response.text
        if kind == "graphql":
            value = graphql_schema(text)
            definition_kind = "graphql-schema"
        else:
            try:
                value = json.dumps(openapi_document(text), indent=2) + "\n"
                definition_kind = "openapi"
            except BrowserError:
                value = graphql_schema(text)
                definition_kind = "graphql-schema"
        digest = hashlib.sha256(value.encode()).hexdigest()
        self.capture.discovery.definitions[(definition_kind, digest)] = value
        return definition_kind

    def _probe(self, kind, endpoint):
        base, path, _parameters = route(endpoint)
        attempt = {"endpoint": base + path, "kind": kind, "outcome": "request_failed"}
        self.attempts.append(attempt)
        method = "POST" if kind == "graphql" else "GET"
        payload = (
            {"operationName": "IntrospectionQuery", "query": get_introspection_query()}
            if kind == "graphql"
            else None
        )
        try:
            headers = self.observer.headers.get(origin(endpoint), {})
            transport = BrowserLoginTransport.for_session(
                self.session, endpoint, method, headers
            )
            with httpx.Client(transport=transport, follow_redirects=False) as client:
                request = (
                    client.build_request(method, endpoint, json=payload)
                    if payload
                    else client.build_request(method, endpoint)
                )
                response = client.send(request)
            attempt["status"] = response.status_code
            self.capture.discovery.add(
                {
                    "request": {
                        "url": endpoint,
                        "method": method,
                        "postData": {"text": json.dumps(payload) if payload else ""},
                    },
                    "response": {
                        "status": response.status_code,
                        "content": {
                            "mimeType": response.headers.get("content-type"),
                            "text": response.text
                            if len(response.content) <= MAX_BODY
                            else "",
                        },
                    },
                }
            )
            if 300 <= response.status_code < 400:
                attempt["outcome"] = "redirect_blocked"
            elif response.status_code in {401, 403}:
                attempt["outcome"] = "unauthorized"
            elif response.status_code == 404:
                attempt["outcome"] = "not_found"
            elif not 200 <= response.status_code < 300:
                attempt["outcome"] = "http_error"
            else:
                try:
                    attempt["definitionKind"] = self._save_definition(response, kind)
                    attempt["outcome"] = "found"
                except BrowserError:
                    attempt["outcome"] = (
                        "schema_unavailable"
                        if kind == "graphql"
                        else "invalid_definition"
                    )
        except BrowserError:
            # No server diagnostics, response bodies or authentication values
            # belong in this report. A failed probe is never retried.
            pass

    def run(self):
        connection = self.session._connection
        previous = connection.on_event
        self.observer.discovering = True
        connection.on_event = self._event
        try:
            connection.call(
                "Network.enable",
                {
                    "maxTotalBufferSize": 8 * MAX_BODY,
                    "maxResourceBufferSize": MAX_BODY,
                    "maxPostDataSize": MAX_BODY,
                },
            )
            self.session._page.reload(wait_until="domcontentloaded", timeout=30_000)
            deadline_seconds = time.monotonic() + self.observe_seconds
            while time.monotonic() < deadline_seconds:
                self.session._check()
                self.capture.drain()
            candidates = self._candidates()
            for kind, endpoint in candidates[: self.max_probes]:
                self.session._check()
                self._probe(kind, endpoint)
            self.capture.drain()
            result = self.capture.discovery
            result.omitted += len(self.capture.pending)
            result.capture_failures += self.capture.body_failures
            report = {
                "probes": self.attempts,
                "maxProbes": self.max_probes,
                "observeSeconds": self.observe_seconds,
                "candidatesNotProbed": max(0, len(candidates) - len(self.attempts)),
                "linksTruncated": self.session._page.locator(
                    "a[href], link[href]"
                ).count()
                > MAX_LINKS,
                "observationLimitReached": len(self.observer.headers)
                >= MAX_AUTH_ORIGINS
                or len(self.observer.graphql_endpoints) >= MAX_CANDIDATES
                or len(self.observer.schema_links) >= MAX_CANDIDATES,
                "completeServiceCoverage": False,
                "businessOperationsReplayed": False,
            }
            return result, report
        finally:
            connection.on_event = previous
            self.observer.discovering = False
            self.observer.clear()
