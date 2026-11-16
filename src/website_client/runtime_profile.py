"""Private, origin-scoped login state shared by installed clients on this machine."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from filelock import FileLock
from platformdirs import user_state_path

AUTH_HEADERS = frozenset({'authorization', 'x-csrf-token', 'x-xsrf-token', 'csrf-token', 'xsrf-token'})


class ProfileError(ValueError):
    """A bounded diagnostic that never contains profile contents."""


def service_origin(url):
    try:
        p = urlsplit(str(url))
        local = p.scheme == 'http' and p.hostname in {'localhost', '127.0.0.1', '::1'}
        if (p.scheme != 'https' and not local) or not p.hostname or p.username or p.password or p.fragment or any(ord(c) <= 32 for c in str(url)):
            raise ValueError()
        host = p.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        port = p.port or (443 if p.scheme == 'https' else 80)
        return f'{p.scheme}://{host}:{port}'
    except (ValueError, TypeError):
        raise ProfileError('invalid_service_origin') from None


def profile_directory():
    return Path(os.environ.get('WEBSITE_CLIENT_PROFILE_DIR') or user_state_path('website-client') / 'profiles')


def _private(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_mode & 0o077 or (hasattr(os, 'getuid') and info.st_uid != os.getuid()):
        raise ProfileError('unsafe_profile_permissions')


def scoped_headers(headers, allowed=AUTH_HEADERS):
    result = {}
    for name, value in headers.items():
        if (not isinstance(name, str) or name.lower() not in allowed or not isinstance(value, str)
                or len(value.encode()) > 16384 or '\r' in value or '\n' in value):
            raise ProfileError('invalid_profile_headers')
        result[name.lower()] = value
    return result


def scoped_state(state, endpoint):
    selected = service_origin(endpoint)
    host = urlsplit(selected).hostname
    return {
        'cookies': [cookie for cookie in state.get('cookies', [])
                    if host == cookie.get('domain', '').lstrip('.') or host.endswith('.' + cookie.get('domain', '').lstrip('.'))],
        'origins': [item for item in state.get('origins', []) if service_origin(item['origin']) == selected],
    }


class ProfileStore:
    """Serialize the complete load, call/job, cookie-save cycle with a file lock."""

    def __init__(self, identifier, endpoint):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}', identifier):
            raise ProfileError('invalid_profile_identifier')
        self.identifier = identifier
        self.origin = service_origin(endpoint)
        self.root = profile_directory()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(self.root, directory=True)
        self.path = self.root / (identifier + '.json')
        self.lock_path = self.root / (identifier + '.lock')

    @contextmanager
    def locked(self):
        # Pre-create privately; reject links before FileLock can follow them.
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            _private(self.lock_path)
        else:
            os.close(fd)
        with FileLock(self.lock_path, timeout=30, mode=0o600):
            _private(self.lock_path)
            yield self

    def load(self):
        try:
            _private(self.path)
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ProfileError('invalid_profile')
            value = json.loads(self.path.read_text())
            if value['origin'] != self.origin or value['profile'] != self.identifier:
                raise ProfileError('profile_origin_mismatch')
            value['headers'] = scoped_headers(value['headers'])
            value['storage_state'] = scoped_state(value['storage_state'], self.origin)
            return value
        except FileNotFoundError:
            raise ProfileError('authentication_required') from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ProfileError('invalid_profile') from None

    def save(self, state, headers):
        if self.path.exists() or self.path.is_symlink():
            _private(self.path)
        value = {'version': 1, 'profile': self.identifier, 'origin': self.origin,
                 'headers': scoped_headers(headers), 'storage_state': scoped_state(state, self.origin)}
        descriptor, temporary = tempfile.mkstemp(prefix='.profile-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def save_browser_profile(context, identifier, endpoint, headers=None):
    """Save selected login state; call only after explicit initial login completes."""
    with ProfileStore(identifier, endpoint).locked() as store:
        store.save(context.storage_state(), headers or {})


class HeaderCapture:
    """Observe known authentication headers only at the configured API origin."""

    def __init__(self, endpoint):
        self.origin = service_origin(endpoint)
        self.headers = {}

    def observe(self, method, params):
        if method != 'Network.requestWillBeSent':
            return
        try:
            request = params['request']
            if service_origin(request['url']) == self.origin:
                selected = {k: v for k, v in request.get('headers', {}).items() if k.lower() in AUTH_HEADERS}
                self.headers.update(scoped_headers(selected))
        except (KeyError, TypeError, ProfileError):
            return
