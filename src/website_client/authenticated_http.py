"""Dispatch upstream-built bytes through an isolated Playwright request context."""

from contextlib import contextmanager, ExitStack
from collections.abc import Iterator, Mapping
from typing import Any
import httpx

from .runtime_profile import ProfileStore, ProfileError, service_origin

LIMIT = 8 * 1024 * 1024


class AuthenticationRequired(RuntimeError):
    def __init__(self):
        super().__init__("authentication_required")


class TransportFailure(RuntimeError):
    """A request failed after dispatch; remote effects remain unknown."""

    def __init__(self):
        super().__init__(
            "transport_failure: request may have succeeded; no automatic retry occurred"
        )


class AuthenticatedDispatch:
    def __init__(self, api, endpoint, headers=None):
        self.api = api
        self.origin = service_origin(endpoint)
        self.headers = dict(headers or {})

    def send(self, method, url, headers, body):
        if service_origin(url) != self.origin:
            raise ProfileError("undeclared_destination")
        if body is None:
            body = b""
        if isinstance(body, str):
            body = body.encode()
        if not isinstance(body, bytes):
            raise ProfileError("prepared_body_must_be_bytes")
        if len(body) > LIMIT:
            raise ProfileError("request_exceeds_8_mib")
        selected = {
            k: v
            for k, v in headers.items()
            if k.lower() not in {"cookie", "host", "content-length", "accept-encoding"}
        }
        # Profile credentials take precedence over a generated request's defaults.
        selected = {k: v for k, v in selected.items() if k.lower() not in self.headers}
        selected.update(self.headers)
        try:
            response = self.api.fetch(
                str(url),
                method=method,
                headers=selected,
                data=body,
                timeout=30000,
                max_redirects=0,
                max_retries=0,
                fail_on_status_code=False,
            )
        except Exception:
            # Playwright call logs can contain all sent cookies and credentials.
            # Neither the exception text nor its formatted chain may escape.
            raise TransportFailure() from None
        try:
            status = response.status
            if status in {401, 403}:
                raise AuthenticationRequired()
            if 300 <= status < 400:
                raise ProfileError("redirect_rejected")
            content = response.body()
            if len(content) > LIMIT:
                raise ProfileError("response_exceeds_8_mib")
            output_headers = [
                (h["name"], h["value"])
                for h in response.headers_array
                if h["name"].lower()
                not in {"content-encoding", "content-length", "transfer-encoding"}
            ]
            return status, output_headers, content
        except (AuthenticationRequired, ProfileError):
            raise
        except Exception:
            raise TransportFailure() from None
        finally:
            try:
                response.dispose()
            except Exception:
                raise TransportFailure() from None


class SDKTransport(httpx.BaseTransport):
    def __init__(self, dispatch):
        self.dispatch = dispatch

    def handle_request(self, request):
        # Bound stream consumption before allocating the complete request body.
        chunks, size = [], 0
        for chunk in request.stream:
            size += len(chunk)
            if size > LIMIT:
                raise ProfileError("request_exceeds_8_mib")
            chunks.append(chunk)
        status, headers, body = self.dispatch.send(
            request.method, str(request.url), dict(request.headers), b"".join(chunks)
        )
        return httpx.Response(status, headers=headers, content=body, request=request)


def _profile_associations(endpoints: Mapping[str, str | None]) -> dict[str, str | None]:
    associations = {}
    for endpoint, identifier in endpoints.items():
        selected = service_origin(endpoint)
        if selected in associations and associations[selected] != identifier:
            raise ProfileError("profile_origin_mismatch")
        associations[selected] = identifier
    identifiers = [item for item in associations.values() if item is not None]
    if len(identifiers) != len(set(identifiers)):
        raise ProfileError("profile_origin_mismatch")
    return associations


def _save_and_dispose(contexts: Mapping[str, Any], loaded: Mapping[str, tuple]) -> None:
    failures = False
    for selected, context in reversed(list(contexts.items())):
        try:
            if selected in loaded:
                store, profile = loaded[selected]
                store.save(
                    context.storage_state(),
                    profile["headers"],
                    profile.get("header_names", ()),
                )
        except Exception:
            failures = True
        finally:
            try:
                context.dispose()
            except Exception:
                failures = True
    if failures:
        # Driver diagnostics can include cookies. Keep cleanup failures bounded,
        # including when they occur while unwinding another exception.
        raise ProfileError("profile_save_failed") from None


@contextmanager
def profile_dispatches(
    endpoints: Mapping[str, str | None],
) -> Iterator[dict[str, AuthenticatedDispatch]]:
    """Hold sorted whole-job profile locks with one driver and isolated contexts.

    SDK jobs use one origin; workflow jobs may use several. Sharing this lifecycle
    keeps save-on-error and credential isolation identical without nested drivers.
    """
    from playwright.sync_api import sync_playwright

    associations = _profile_associations(endpoints)
    with ExitStack() as locks:
        loaded = {}
        # Every job takes locks in the same order, preventing opposing multi-origin
        # jobs from deadlocking. Locks cover load, all requests, save, and disposal.
        for selected, identifier in sorted(
            associations.items(), key=lambda item: (item[1] or "", item[0])
        ):
            if identifier is not None:
                store = locks.enter_context(ProfileStore(identifier, selected).locked())
                loaded[selected] = (store, store.load())
        with sync_playwright() as playwright:
            contexts = {}
            try:
                dispatches = {}
                for selected in sorted(associations):
                    profile = loaded[selected][1] if selected in loaded else None
                    options = (
                        {"storage_state": profile["storage_state"]} if profile else {}
                    )
                    context = playwright.request.new_context(
                        ignore_https_errors=False, **options
                    )
                    contexts[selected] = context
                    dispatches[selected] = AuthenticatedDispatch(
                        context, selected, profile["headers"] if profile else {}
                    )
                yield dispatches
            finally:
                _save_and_dispose(contexts, loaded)


@contextmanager
def profile_dispatch(
    identifier: str | None, endpoint: str
) -> Iterator[AuthenticatedDispatch]:
    """Open one SDK job using the shared whole-job lifecycle."""
    with profile_dispatches({endpoint: identifier}) as dispatches:
        yield dispatches[service_origin(endpoint)]


@contextmanager
def workflow_session(dispatch):
    """Adapt only already-prepared requests; disable environment proxies and netrc."""
    import requests
    from requests.adapters import BaseAdapter

    class PreparedAdapter(BaseAdapter):
        def send(self, request, **kwargs):
            status, headers, body = dispatch.send(
                request.method, request.url, dict(request.headers), request.body
            )
            response = requests.Response()
            response.status_code, response._content = status, body
            response.headers = requests.structures.CaseInsensitiveDict(headers)
            response.request, response.url = request, request.url
            return response

        def close(self):
            pass

    with requests.Session() as session:
        session.trust_env = False
        adapter = PreparedAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        yield session
