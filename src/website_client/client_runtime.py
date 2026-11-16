"""Public context factory for the native generated client and its saved defaults."""

from contextlib import contextmanager, ExitStack
from importlib import import_module
from importlib.resources import files
import json

import httpx

from .authenticated_http import profile_dispatch, SDKTransport


def runtime_defaults():
    """Return only non-secret service configuration installed with the package."""
    return json.loads(files(__package__).joinpath('_runtime.json').read_text())


@contextmanager
def client_context(*, url=None, profile_id=None, allow_writes=False):
    """Yield the unchanged upstream Client using the package's endpoint/profile.

    The profile lock covers this entire context, including multiple operations.
    Missing or expired credentials terminate without reading standard input.
    """
    defaults = runtime_defaults()
    endpoint = url or defaults.get('endpoint')
    profile = profile_id or defaults.get('profileId')
    if not endpoint:
        raise ValueError('service_endpoint_required')
    module = import_module(__package__ + '.client')
    with profile_dispatch(profile, endpoint) as dispatch:
        with ExitStack() as stack:
            http = httpx.Client(base_url=endpoint, transport=SDKTransport(dispatch),
                                follow_redirects=False, trust_env=False, timeout=30)
            stack.callback(http.close)
            if defaults['kind'] == 'openapi':
                native = module.Client(base_url=endpoint, follow_redirects=False)
                native.set_httpx_client(http)
            else:
                kwargs = {'url': endpoint, 'http_client': http}
                if defaults['kind'] == 'graphql-operation':
                    kwargs['allow_writes'] = allow_writes
                native = module.Client(**kwargs)
            with native:
                yield native
