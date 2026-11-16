"""Dispatch upstream-built bytes through an isolated Playwright request context."""

from contextlib import contextmanager
import httpx

from .runtime_profile import ProfileStore, ProfileError, service_origin

LIMIT = 8 * 1024 * 1024


class AuthenticationRequired(RuntimeError):
    def __init__(self):
        super().__init__('authentication_required')


class AuthenticatedDispatch:
    def __init__(self, api, endpoint, headers=None):
        self.api = api
        self.origin = service_origin(endpoint)
        self.headers = dict(headers or {})

    def send(self, method, url, headers, body):
        if service_origin(url) != self.origin:
            raise ProfileError('undeclared_destination')
        if body is None:
            body = b''
        if isinstance(body, str):
            body = body.encode()
        if not isinstance(body, bytes):
            raise ProfileError('prepared_body_must_be_bytes')
        if len(body) > LIMIT:
            raise ProfileError('request_exceeds_8_mib')
        selected = {k: v for k, v in headers.items() if k.lower() not in {'cookie', 'host', 'content-length', 'accept-encoding'}}
        # Profile credentials take precedence over a generated request's defaults.
        selected = {k: v for k, v in selected.items() if k.lower() not in self.headers}
        selected.update(self.headers)
        response = self.api.fetch(str(url), method=method, headers=selected, data=body,
                                  timeout=30000, max_redirects=0, max_retries=0,
                                  fail_on_status_code=False)
        try:
            status = response.status
            if status in {401, 403}:
                raise AuthenticationRequired()
            if 300 <= status < 400:
                raise ProfileError('redirect_rejected')
            content = response.body()
            if len(content) > LIMIT:
                raise ProfileError('response_exceeds_8_mib')
            output_headers = [(h['name'], h['value']) for h in response.headers_array
                              if h['name'].lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}]
            return status, output_headers, content
        finally:
            response.dispose()


class SDKTransport(httpx.BaseTransport):
    def __init__(self, dispatch):
        self.dispatch = dispatch

    def handle_request(self, request):
        # Bound stream consumption before allocating the complete request body.
        chunks, size = [], 0
        for chunk in request.stream:
            size += len(chunk)
            if size > LIMIT:
                raise ProfileError('request_exceeds_8_mib')
            chunks.append(chunk)
        status, headers, body = self.dispatch.send(request.method, str(request.url), dict(request.headers), b''.join(chunks))
        return httpx.Response(status, headers=headers, content=body, request=request)


@contextmanager
def profile_dispatch(identifier, endpoint):
    """Load once, hold the lock for the entire job, save cookies even on failure."""
    from playwright.sync_api import sync_playwright
    if identifier is None:
        service_origin(endpoint)
        with sync_playwright() as playwright:
            api = playwright.request.new_context(ignore_https_errors=False)
            try:
                yield AuthenticatedDispatch(api, endpoint)
            finally:
                api.dispose()
        return
    with ProfileStore(identifier, endpoint).locked() as store:
        profile = store.load()
        with sync_playwright() as playwright:
            api = playwright.request.new_context(storage_state=profile['storage_state'],
                                                  ignore_https_errors=False)
            try:
                yield AuthenticatedDispatch(api, endpoint, profile['headers'])
            finally:
                try:
                    store.save(api.storage_state(), profile['headers'])
                finally:
                    api.dispose()


@contextmanager
def workflow_session(dispatch):
    """Adapt only already-prepared requests; disable environment proxies and netrc."""
    import requests
    from requests.adapters import BaseAdapter

    class PreparedAdapter(BaseAdapter):
        def send(self, request, **kwargs):
            status, headers, body = dispatch.send(request.method, request.url, dict(request.headers), request.body)
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
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        yield session
