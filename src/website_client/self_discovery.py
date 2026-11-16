"""Find service definitions with bounded reads in an authenticated browser."""

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


def schema_url(base_url, href, advertised=False):
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
        return candidate if advertised or SCHEMA_PATH.search(parsed.path) else None
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

    def __init__(self, url, allowed_origins=None):
        self.allowed_origins = allowed_origins
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
            selected = {}
            for name, value in request.get("headers", {}).items() if self.allowed_origins is None or target_origin in self.allowed_origins else ():
                if (
                    name.lower() in DEFAULT_HEADERS
                    and isinstance(value, str)
                    and len(value.encode()) <= MAX_SESSION_HEADER_VALUE_BYTES
                    and "\r" not in value
                    and "\n" not in value
                ):
                    selected[name.lower()] = value
            # Static/analytics requests without session headers consume no slot.
            # Exhausted credential storage must not suppress endpoint evidence.
            if selected and (
                target_origin in self.headers or len(self.headers) < MAX_AUTH_ORIGINS
            ):
                self.headers.setdefault(target_origin, {}).update(selected)
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
        explorer=None,
        autonomous=False,
        credential_observer=None,
        initial_definitions=(),
        allowed_origins=None,
    ):
        validate_settings(max_probes, observe_seconds, graphql_endpoints)
        self.session = session
        self.observer = observer
        self.capture = NetworkCapture(
            session, max_entries, defer_bodies=explorer is not None
        )
        self.max_probes = max_probes
        self.observe_seconds = observe_seconds
        self.explicit_endpoints = list(graphql_endpoints)
        self.attempts = []
        self.explorer = explorer
        self.autonomous = autonomous
        self.credential_observer = credential_observer
        self.reference_documents = {}
        self.initial_definitions = tuple(initial_definitions)
        self.allowed_origins = allowed_origins

    def _event(self, method, params):
        self.observer.event(method, params)
        if self.credential_observer is not None:
            self.credential_observer.observe(method, params)
        self.capture.event(method, params)
        if self.explorer is not None:
            self.explorer.event(method, params)

    def _candidates(self):
        candidates = [
            ("graphql", value)
            for value in self.explicit_endpoints + self.observer.graphql_endpoints
        ]
        links = self.session._page.locator("a[href], link[href]")
        for index in range(min(links.count(), MAX_LINKS)):
            value = schema_url(
                self.session._url, links.nth(index).get_attribute("href"),
                advertised="service-desc" in (links.nth(index).get_attribute("rel") or "").split(),
            )
            if value:
                candidates.append(("definition", value))
        candidates += [("definition", value) for value in self.observer.schema_links]
        candidates += [
            ("definition", urljoin(self.session._url, path))
            for path in COMMON_DEFINITION_PATHS
        ]
        return list(dict.fromkeys(candidates))

    def _bundle_references(self, value, location, document=None, active=None):
        """Resolve bounded same-origin definitions only; leave other refs for exclusion."""
        from copy import deepcopy
        from jsonpointer import resolve_pointer
        import yaml
        from .authenticated_http import AuthenticatedDispatch
        document = value if document is None else document
        active = set() if active is None else active
        if isinstance(value, dict):
            reference = value.get('$ref')
            if isinstance(reference, str) and (not reference.startswith('#/') or location != self._definition_location):
                address, fragment = __import__('urllib.parse', fromlist=['urldefrag']).urldefrag(urljoin(location, reference))
                key = address + '#' + fragment
                try:
                    if key in active or len(value) != 1 or origin(address) != self.session._origin or urlsplit(address).query or AUTH.search(urlsplit(address).path):
                        return deepcopy(value)
                    if address == location:
                        target_document = document
                    else:
                        if address not in self.reference_documents:
                            if len(self.reference_documents) >= 12:
                                return deepcopy(value)
                            headers = self.observer.headers.get(origin(address), {})
                            status, response_headers, body = AuthenticatedDispatch(self.session._context.request, address, headers).send('GET', address, {}, b'')
                            self.credential_observer.observe_response(address, [value for name, value in response_headers if name.lower() == 'set-cookie'])
                            if not 200 <= status < 300 or len(body) > MAX_BODY:
                                return deepcopy(value)
                            self.reference_documents[address] = yaml.safe_load(body)
                        target_document = self.reference_documents[address]
                    target = resolve_pointer(target_document, '/' + fragment.lstrip('/')) if fragment else target_document
                    return self._bundle_references(target, address, target_document, active | {key})
                except Exception:
                    return deepcopy(value)
            return {key: self._bundle_references(item, location, document, active) for key, item in value.items()}
        if isinstance(value, list):
            return [self._bundle_references(item, location, document, active) for item in value]
        return value

    def _save_definition(self, response, kind, endpoint=None):
        import yaml
        text = response.text
        if kind == "graphql":
            value = graphql_schema(text)
            definition_kind = "graphql-schema"
        else:
            raw = yaml.safe_load(text)
            if isinstance(raw, dict) and 'arazzo' in raw:
                from .privacy import safe_workflow
                value = json.dumps(safe_workflow(raw), indent=2) + "\n"
                definition_kind = 'arazzo'
            elif isinstance(raw, dict) and 'openapi' in raw:
                from .admission import admit_openapi
                self._definition_location = endpoint
                if self.autonomous:
                    raw = self._bundle_references(raw, endpoint)
                safe, executable, exclusions = admit_openapi(raw)
                if self.autonomous and self.allowed_origins is not None:
                    for server in executable.get('servers', []):
                        selected = urljoin(endpoint, server['url'])
                        selected_origin = origin(selected)
                        self.allowed_origins.add(selected_origin)
                        names = [scheme['name'] for scheme in executable.get('components', {}).get('securitySchemes', {}).values() if scheme.get('type') == 'apiKey' and scheme.get('in') == 'header']
                        self.credential_observer.declare(selected, names)
                value = json.dumps(safe, indent=2) + "\n"
                definition_kind = 'openapi'
            else:
                value = graphql_schema(text)
                definition_kind = 'graphql-schema'
        self.capture.discovery.record_definition(definition_kind, value, endpoint, __import__("hashlib").sha256(response.content).hexdigest())
        if kind == "graphql" and endpoint:
            self.capture.discovery.graphql_endpoints.add(endpoint)
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
            if self.autonomous and hasattr(self.credential_observer, "captures"):
                from .runtime_profile import service_origin
                capture = self.credential_observer.captures.get(service_origin(endpoint))
                if capture is not None:
                    headers = capture.headers
            if self.autonomous:
                from .authenticated_http import AuthenticatedDispatch, SDKTransport
                transport = SDKTransport(AuthenticatedDispatch(self.session._context.request, endpoint, headers))
            else:
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
            if self.autonomous and self.credential_observer is not None:
                self.credential_observer.observe_response(endpoint, response.headers.get_list("set-cookie"))
            attempt["status"] = response.status_code
            self.capture.discovery.add(
                {
                    "_definitionProbe": True,
                    "_skipDefinition": self.autonomous,
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
                    attempt["definitionKind"] = self._save_definition(
                        response, kind, endpoint
                    )
                    attempt["outcome"] = "found"
                except BrowserError:
                    attempt["outcome"] = (
                        "schema_unavailable"
                        if kind == "graphql"
                        else "invalid_definition"
                    )
        except Exception:
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
            for endpoint in self.initial_definitions[:self.max_probes]:
                self._probe("definition", endpoint)
            exploration = None
            if self.explorer is not None:
                exploration = self.explorer.run(self.capture, self.observer)
            else:
                self.session._page.reload(wait_until="domcontentloaded", timeout=30_000)
                deadline_seconds = time.monotonic() + self.observe_seconds
                while time.monotonic() < deadline_seconds:
                    self.session._check()
                    self.capture.drain()
            candidates = self._candidates()
            if self.autonomous:
                candidates += [("definition", location) for (kind, _), locations in self.capture.discovery.definition_sources.items() if kind == "openapi" for location in sorted(locations)]
                candidates = list(dict.fromkeys(candidates))
            if self.explorer is not None:
                self.explorer.freeze()
            attempted = {item["endpoint"] for item in self.attempts}
            for kind, endpoint in [(kind, endpoint) for kind, endpoint in candidates if endpoint not in attempted][: max(0, self.max_probes - len(self.attempts))]:
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
            if exploration is not None:
                report["exploration"] = exploration
            return result, report
        finally:
            if self.explorer is not None:
                self.explorer.close()
            self.session._connection.on_event = previous
            self.observer.discovering = False
            self.observer.clear()
