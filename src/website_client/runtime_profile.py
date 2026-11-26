"""Keep explicitly created login state private and scoped to one service origin."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import urlsplit

from filelock import FileLock
from platformdirs import user_state_path


class ProfileError(ValueError):
    """A bounded profile diagnostic safe to display."""


def service_origin(url):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(ord(c) <= 32 for c in url)
        ):
            raise ValueError()
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        return f"https://{host}:{parsed.port or 443}"
    except (ValueError, TypeError):
        raise ProfileError("invalid_service_origin") from None


def profile_directory(endpoint, directory=None):
    selected = directory or os.environ.get("WEBSITE_CLIENT_PROFILE")
    if selected:
        return Path(selected).expanduser().absolute()
    digest = hashlib.sha256(service_origin(endpoint).encode()).hexdigest()
    return user_state_path("website-client") / "profiles" / digest


def private_path(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or info.st_mode & 0o077
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
    ):
        raise ProfileError("unsafe_profile_permissions")


class ProfileStore:
    """Lock the load, request and atomic credential renewal as one operation."""

    def __init__(self, endpoint, directory=None):
        self.origin = service_origin(endpoint)
        self.root = profile_directory(endpoint, directory)
        module_directory = Path(__file__).resolve().parent
        package_root = (
            module_directory.parent
            if (module_directory.parent / "pyproject.toml").is_file()
            else module_directory
        )
        if self.root.resolve().is_relative_to(package_root):
            raise ProfileError("profile_inside_package")
        self.path = self.root / "state.json"

    @contextmanager
    def locked(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_path(self.root, True)
        lock = self.root / "state.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            private_path(lock)
        else:
            os.close(descriptor)
        with FileLock(lock, timeout=30, mode=0o600):
            private_path(lock)
            yield self

    def load(self):
        try:
            private_path(self.path)
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ProfileError("invalid_profile")
            value = json.loads(self.path.read_text())
            if value["origin"] != self.origin:
                raise ProfileError("profile_origin_mismatch")
            return value["storage_state"]
        except FileNotFoundError:
            raise ProfileError("authentication_required") from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ProfileError("invalid_profile") from None

    def save(self, state):
        if self.path.exists() or self.path.is_symlink():
            self.load()
        host = urlsplit(self.origin).hostname
        state = {
            "cookies": [
                cookie
                for cookie in state.get("cookies", [])
                if cookie.get("domain", "").lstrip(".") == host
            ],
            "origins": [
                item
                for item in state.get("origins", [])
                if service_origin(item["origin"]) == self.origin
            ],
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(
                    {"version": 1, "origin": self.origin, "storage_state": state},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def save_browser_profile(context, directory, endpoint):
    """Save only a fresh, explicitly confirmed login session for this origin."""
    with ProfileStore(endpoint, directory).locked() as store:
        store.save(context.storage_state())
