"""Deterministic navigation, UI inspection and contract collection."""

from collections import deque
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import urljoin

from .browser import BrowserError, NAVIGATION_TIMEOUT_MILLISECONDS
from .contracts import inspect_forms, FrontendContracts
from .discovery import MAX_BODY, json_body, shape, operation
from .exploration_policy import (
    clean_navigation,
    label,
    PAGINATION,
    OPEN_FORM,
    request_allowed,
    safe_address,
    WRITE_WORDS,
)
from .self_discovery import schema_url

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_ACTIONS = 60
DEFAULT_MAX_DEPTH = 3
DEFAULT_EXPLORE_SECONDS = 120
MAX_QUEUE = 1000
MAX_REPORT_ITEMS = 1000
MAX_FORMS_TOTAL = 100
MAX_ELEMENTS = 200
SETTLE_SECONDS = 0.3
ACTION_TIMEOUT_MILLISECONDS = 3000


def validate_exploration(max_pages, max_actions, max_depth, duration_seconds):
    if (
        not 1 <= max_pages <= 100
        or not 1 <= max_actions <= 500
        or not 0 <= max_depth <= 10
        or not 1 <= duration_seconds <= 1800
    ):
        raise BrowserError(
            "Exploration requires 1 to 100 pages, 1 to 500 actions, depth 0 to 10 and 1 to 1800 seconds."
        )


class SiteExplorer:
    def __init__(
        self,
        session,
        max_pages=DEFAULT_MAX_PAGES,
        max_actions=DEFAULT_MAX_ACTIONS,
        max_depth=DEFAULT_MAX_DEPTH,
        duration_seconds=DEFAULT_EXPLORE_SECONDS,
    ):
        validate_exploration(max_pages, max_actions, max_depth, duration_seconds)
        self.session = session
        self.start_url = session.current_url()
        self.max_pages = max_pages
        self.max_actions = max_actions
        self.max_depth = max_depth
        self.duration_seconds = duration_seconds
        self.frontend = None
        self.queue = deque([(self.start_url, 0)])
        self.seen = {self.start_url}
        self.expanded = set()
        self.form_hashes = set()
        self.pages = []
        self.visited = set()
        self.actions = []
        self.forms = []
        self.blocked = []
        self.skipped = []
        self.omitted = 0
        self._guard_installed = False
        self._context_guard_installed = False
        self._control_phase = False
        self.current_depth = 0

    def _record(self, collection, item):
        if len(collection) < MAX_REPORT_ITEMS:
            collection.append(item)
        else:
            self.omitted += 1

    def _guard(self, route):
        reason = request_allowed(route.request, self.session._origin)
        if (
            reason is None
            and self._control_phase
            and route.request.is_navigation_request()
            and route.request.frame == self.session._page.main_frame
        ):
            if self.current_depth >= self.max_depth:
                reason = "depth_limit"
            elif len(self.pages) >= self.max_pages:
                reason = "page_limit"
        if reason:
            body = json_body(route.request.post_data or "")
            declaration = operation(body)
            self._record(
                self.blocked,
                {
                    "url": safe_address(route.request.url),
                    "method": route.request.method,
                    "reason": reason,
                    "sent": False,
                    "requestSchema": shape(body),
                    "graphql": {
                        key: declaration[key] for key in ("name", "kind", "variables")
                    }
                    if declaration
                    else None,
                    "source": "blocked_request",
                    "apiContractVerified": False,
                },
            )
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _websocket(self, socket):
        self._record(
            self.blocked,
            {
                "url": "[WebSocket address omitted]",
                "method": "WebSocket",
                "reason": "unmodeled_channel",
                "sent": False,
            },
        )
        # A routed socket is disconnected from the server unless
        # connect_to_server is called. Closing it inside the handshake callback
        # can wait on that callback itself; context shutdown removes the client.

    def prepare(self):
        if not self.session._block_service_workers:
            raise BrowserError(
                "Exploration needs a fresh session with service workers blocked."
            )
        context = self.session._context
        context.route("**/*", self._http_guard)
        self._context_guard_installed = True
        context.route_web_socket("**/*", self._websocket)
        # End connections opened before interception, then capture a fresh tab.
        old_page = self.session._page
        self.session._page = context.new_page()
        self.session._connection.close()
        self.session._connection = self.session.connection_for(self.session._page)
        old_page.close()
        context.on("page", self._popup)

    def _popup(self, page):
        self._record(self.skipped, {"kind": "popup", "reason": "popup_not_explored"})
        page.close()

    def _http_guard(self, route):
        # Playwright handles concurrent API, worker and popup traffic. Native
        # document interception also sees each redirect destination.
        try:
            document_in_tab = (
                route.request.is_navigation_request()
                and route.request.frame.page == self.session._page
            )
        except Exception:
            document_in_tab = False
        if document_in_tab:
            route.continue_()
        else:
            self._guard(route)

    def event(self, method, params):
        if method == "Fetch.requestPaused":
            self._intercept(params)
        if self.frontend is not None:
            self.frontend.event(method, params)

    def _intercept(self, event):
        """Chrome pauses each request, including each redirect destination."""
        raw = event["request"]
        request = SimpleNamespace(
            url=raw["url"],
            method=raw["method"],
            post_data=raw.get("postData"),
            resource_type=event.get("resourceType", "").lower(),
            is_navigation_request=lambda: event.get("resourceType") == "Document",
            frame=self.session._page.main_frame
            if event.get("frameId") == self.main_frame_id
            else None,
        )
        identifier = event["requestId"]
        connection = self.session._connection
        paused = SimpleNamespace(
            request=request,
            abort=lambda _reason: connection.call(
                "Fetch.failRequest",
                {"requestId": identifier, "errorReason": "BlockedByClient"},
            ),
            continue_=lambda: connection.call(
                "Fetch.continueRequest", {"requestId": identifier}
            ),
        )
        self._guard(paused)

    def _replace_tab(self, *, exploring):
        """Recover with a new tab instead of reusing a detached error target."""
        context = self.session._context
        context.remove_listener("page", self._popup)
        old_page, old_connection = self.session._page, self.session._connection
        callback = old_connection.on_event
        old_connection.on_event = None
        self._capture.discovery.omitted += len(self._capture.pending)
        self._capture.pending.clear()
        self._capture.finished.clear()
        self._capture.pending_bytes = 0
        self.frontend.omitted += len(self.frontend.pending)
        self.frontend.pending.clear()
        self.frontend.ready.clear()
        self.session._page = context.new_page()
        self.session._connection = self.session.connection_for(self.session._page)
        connection = self.session._connection
        connection.on_event = callback
        self._capture.connection = self.frontend.connection = connection
        try:
            old_connection.close()
        except Exception:
            pass
        try:
            old_page.close()
        except Exception:
            pass
        connection.call(
            "Network.enable",
            {
                "maxTotalBufferSize": 8 * MAX_BODY,
                "maxResourceBufferSize": MAX_BODY,
                "maxPostDataSize": MAX_BODY,
            },
        )
        self._guard_installed = False
        if exploring:
            context.on("page", self._popup)
            self._enable_interception()
        elif self._context_guard_installed:
            context.unroute("**/*", self._http_guard)
            self._context_guard_installed = False

    def _enable_interception(self):
        connection = self.session._connection
        self.main_frame_id = connection.call("Page.getFrameTree")["frameTree"]["frame"][
            "id"
        ]
        connection.call(
            "Fetch.enable",
            {
                "patterns": [
                    {
                        "urlPattern": "*",
                        "resourceType": "Document",
                        "requestStage": "Request",
                    }
                ]
            },
        )
        self._guard_installed = True

    def _settle(self, capture):
        deadline = min(self.deadline_seconds, time.monotonic() + SETTLE_SECONDS)
        while time.monotonic() < deadline:
            self.session._check()
            capture.drain()
            self.frontend.drain()

    def _inspect(self, observer, depth):
        page = self.session._page
        forms, truncated = inspect_forms(page)
        if truncated:
            self.omitted += 1
        for form in forms:
            digest = hashlib.sha256(repr(form).encode()).hexdigest()
            if digest not in self.form_hashes:
                self.form_hashes.add(digest)
                if len(self.forms) < MAX_FORMS_TOTAL:
                    self.forms.append({"page": safe_address(page.url), **form})
                else:
                    self.omitted += 1
        links = page.locator("a[href], link[href]")
        if links.count() > MAX_ELEMENTS:
            self.omitted += links.count() - MAX_ELEMENTS
        for index in range(min(links.count(), MAX_ELEMENTS)):
            element = links.nth(index)
            href = element.get_attribute("href")
            definition = schema_url(page.url, href)
            if (
                definition
                and definition not in observer.schema_links
                and len(observer.schema_links) < MAX_ELEMENTS
            ):
                observer.schema_links.append(definition)
            if not element.is_visible():
                continue
            target = clean_navigation(page.url, href, self.session._origin)
            if not target or definition:
                self._record(
                    self.skipped,
                    {
                        "page": safe_address(page.url),
                        "kind": "link",
                        "target": safe_address(urljoin(page.url, href or "")),
                        "source": "declared_link",
                        "reason": "definition_or_excluded_navigation",
                    },
                )
                continue
            if target in self.seen:
                continue
            if depth >= self.max_depth or len(self.queue) >= MAX_QUEUE:
                self.omitted += 1
                continue
            self.seen.add(target)
            self.queue.append((target, depth + 1))

    def _controls(self):
        page = self.session._page
        controls = page.locator(
            'summary, [role="tab"], button[aria-expanded], button[aria-haspopup], button, [role="button"]'
        )
        if controls.count() > MAX_ELEMENTS:
            self.omitted += controls.count() - MAX_ELEMENTS
        for index in range(min(controls.count(), MAX_ELEMENTS)):
            control = controls.nth(index)
            if not control.is_visible() or not control.is_enabled():
                continue
            # Even a menu-labelled button can be a submitter. Never click it
            # inside a form; the form inventory supplies its declaration.
            if (
                control.locator('xpath=ancestor::*[self::form or @role="form"]').count()
                or control.get_attribute("form") is not None
            ):
                continue
            text = control.inner_text(timeout=ACTION_TIMEOUT_MILLISECONDS)
            text = control.get_attribute("aria-label") or text
            opens_form = bool(OPEN_FORM.match(text.strip()))
            href = control.get_attribute("href")
            if href and not clean_navigation(page.url, href, self.session._origin):
                continue
            if (WRITE_WORDS.search(text) and not opens_form) or control.get_attribute(
                "type"
            ) in {
                "submit",
                "reset",
            }:
                self._record(
                    self.skipped,
                    {
                        "page": safe_address(page.url),
                        "kind": "control",
                        "label": label(text),
                        "reason": "write_or_submit_control",
                    },
                )
                continue
            role = control.get_attribute("role")
            expanded = control.get_attribute("aria-expanded")
            is_summary = control.locator("xpath=self::summary").count() > 0
            if is_summary and control.locator("xpath=parent::details[@open]").count():
                continue
            if expanded == "true" or control.get_attribute("aria-selected") == "true":
                continue
            if not (
                is_summary
                or role == "tab"
                or expanded == "false"
                or control.get_attribute("aria-haspopup") in {"true", "menu", "listbox"}
                or PAGINATION.fullmatch(text.strip())
                or opens_form
            ):
                continue
            yield (
                control,
                text,
                "form_dialog"
                if opens_form
                else "tab"
                if role == "tab"
                else "menu"
                if is_summary or expanded is not None
                else "pagination",
                index,
            )

    def _walk_controls(self, capture, observer, depth):
        while (
            len(self.actions) < self.max_actions
            and time.monotonic() < self.deadline_seconds
        ):
            self._inspect(observer, depth)
            selected = None
            # Successful request progress permits repeated pagination without
            # reading page text or input values. Blocked writes are not progress.
            state = sum(
                200 <= entry["response"]["status"] < 300
                for entry in capture.discovery.entries
            )
            for control, text, role, index in self._controls():
                key = (
                    self.session._page.url,
                    state if role == "pagination" else "",
                    role,
                    index,
                    text,
                )
                if key not in self.expanded:
                    selected = control, text, role, key
                    break
            if selected is None:
                return
            control, text, role, key = selected
            self.expanded.add(key)
            action = {
                "page": safe_address(self.session._page.url),
                "kind": role,
                "label": label(text),
                "outcome": "failed",
            }
            self.actions.append(action)
            try:
                before_url = self.session._page.url
                blocked_before = len(self.blocked)
                self._control_phase = True
                control.click(timeout=ACTION_TIMEOUT_MILLISECONDS)
                self._settle(capture)
                action["outcome"] = (
                    "request_blocked"
                    if len(self.blocked) > blocked_before
                    else "opened"
                )
                if self.session._page.url != before_url:
                    target = self.session._page.url
                    if target not in self.visited and len(self.pages) < self.max_pages:
                        self.visited.add(target)
                        self.pages.append(
                            {
                                "url": safe_address(target),
                                "depth": depth + 1,
                                "outcome": "visited_by_control",
                            }
                        )
                    self._inspect(observer, depth + 1)
                    return
            except Exception:
                action["outcome"] = "unavailable"
            finally:
                self._control_phase = False
        self._inspect(observer, depth)

    def run(self, capture, observer):
        self.deadline_seconds = time.monotonic() + self.duration_seconds
        self.frontend = FrontendContracts(
            self.session._connection, capture.discovery, self.session._origin
        )
        self._capture = capture
        self._enable_interception()
        while (
            self.queue
            and len(self.pages) < self.max_pages
            and time.monotonic() < self.deadline_seconds
        ):
            url, depth = self.queue.popleft()
            if url in self.visited:
                continue
            self.visited.add(url)
            self.current_depth = depth
            record = {"url": safe_address(url), "depth": depth, "outcome": "failed"}
            self.pages.append(record)
            try:
                remaining_ms = max(
                    1, int((self.deadline_seconds - time.monotonic()) * 1000)
                )
                self.session._page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=min(NAVIGATION_TIMEOUT_MILLISECONDS, remaining_ms),
                )
                self._settle(capture)
                record["outcome"] = "visited"
                self._walk_controls(capture, observer, depth)
            except BrowserError:
                raise
            except Exception as error:
                record["failureType"] = type(error).__name__
                record["outcome"] = (
                    "inspection_failed"
                    if record["outcome"] == "visited"
                    else "navigation_failed"
                )
                self._replace_tab(exploring=True)
            if len(self.actions) >= self.max_actions:
                break
        return {
            "algorithm": "breadth_first_navigation",
            "pages": self.pages,
            "actions": self.actions,
            "forms": self.forms,
            "frontendModules": self.frontend.modules,
            "blockedRequests": self.blocked,
            "skippedControls": self.skipped,
            "queuedPagesNotVisited": len(self.queue),
            "omitted": self.omitted
            + self.frontend.omitted
            + len(self.frontend.pending),
            "limits": {
                "pages": self.max_pages,
                "actions": self.max_actions,
                "depth": self.max_depth,
                "seconds": self.duration_seconds,
            },
            "timeLimitReached": time.monotonic() >= self.deadline_seconds,
            "completeServiceCoverage": False,
            "formsSubmitted": False,
            "limitations": [
                "Declared forms and frontend operations are unverified contracts.",
                "Persisted GraphQL and unknown POST requests are blocked.",
                "Read-only GET semantics depend on the server.",
                "Only supported navigation controls and loaded same-origin modules are inspected.",
            ],
        }

    def freeze(self):
        # Closing the explored tab ends scripts and pending navigations before
        # schema probes run in separate tabs of the same authenticated context.
        self._replace_tab(exploring=False)

    def close(self):
        try:
            if self._guard_installed:
                self.session._connection.call("Fetch.disable")
        except Exception:
            pass
        finally:
            if self._context_guard_installed:
                self.session._context.unroute("**/*", self._http_guard)
                self._context_guard_installed = False
            self.session._context.remove_listener("page", self._popup)
            self._guard_installed = False
