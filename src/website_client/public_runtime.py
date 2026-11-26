"""Call the upstream SDK through an origin-scoped, browser-free authenticated transport."""

from contextlib import ExitStack
import importlib
from importlib.resources import files
import json

import httpx
from playwright.sync_api import sync_playwright

from .runtime_profile import ProfileStore, service_origin


class OperationError(RuntimeError):
    """A bounded failure with no credentials or response body."""

    def __init__(self, code, status=None):
        super().__init__(code + (f" (HTTP {status})" if status is not None else ""))
        self.http_status = status


class ProfileTransport(httpx.BaseTransport):
    """Let Playwright manage the cookie jar without starting a browser."""

    def __init__(self, context, endpoint):
        self.context = context
        self.origin = service_origin(endpoint)

    def handle_request(self, request):
        if request.method != "GET" or service_origin(str(request.url)) != self.origin:
            raise OperationError("request_outside_supported_scope")
        response = self.context.get(str(request.url), max_redirects=0, timeout=30000)
        try:
            length = response.headers.get("content-length")
            if length and int(length) > 8 * 1024 * 1024:
                raise OperationError("response_too_large")
            content = response.body()
            if len(content) > 8 * 1024 * 1024:
                raise OperationError("response_too_large")
            return httpx.Response(
                response.status,
                headers=response.headers,
                content=content,
                request=request,
            )
        finally:
            response.dispose()


class PublicClient:
    """Use this package's recorded service and a private machine login profile."""

    def __init__(self, *, profile=None):
        self._profile = profile

    def _execute(self, name, arguments):
        metadata = json.loads(files(__package__).joinpath("_public.json").read_text())
        endpoint = metadata["website"]
        operation = metadata["operations"][name]
        module = importlib.import_module(operation["module"])
        upstream = importlib.import_module(__package__ + ".client").Client
        store = ProfileStore(endpoint, self._profile)
        with store.locked(), ExitStack() as stack:
            state = store.load()
            playwright = stack.enter_context(sync_playwright())
            context = playwright.request.new_context(
                storage_state=state, ignore_https_errors=False
            )
            stack.callback(context.dispose)
            http = stack.enter_context(
                httpx.Client(
                    base_url=endpoint,
                    transport=ProfileTransport(context, endpoint),
                    follow_redirects=False,
                )
            )
            client = upstream(base_url=endpoint, follow_redirects=False)
            client.set_httpx_client(http)
            result = module.sync_detailed(client=client, **arguments)
            status = int(result.status_code)
            if status in {401, 403} or 300 <= status < 400:
                raise OperationError("authentication_required", status)
            if status == 404:
                raise OperationError("not_found", status)
            if not 200 <= status < 300:
                raise OperationError("http_error", status)
            if result.parsed is None:
                raise OperationError("unsupported_response", status)
            store.save(context.storage_state())
            return result.parsed
