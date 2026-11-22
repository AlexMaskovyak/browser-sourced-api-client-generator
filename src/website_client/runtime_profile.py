"""Private, origin-scoped login state shared by installed clients on this machine."""

from contextlib import contextmanager
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from filelock import FileLock
from platformdirs import user_state_path

AUTH_HEADERS = frozenset(
    {
        "authorization",
        "x-csrf-token",
        "x-xsrf-token",
        "csrf-token",
        "xsrf-token",
        "x-api-key",
        "api-key",
    }
)


class ProfileError(ValueError):
    """A bounded diagnostic that never contains profile contents."""


def service_origin(url: str) -> str:
    """Return an exact scheme/host/port origin or a bounded validation error."""
    try:
        p = urlsplit(str(url))
        local = p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            (p.scheme != "https" and not local)
            or not p.hostname
            or p.username
            or p.password
            or p.fragment
            or any(ord(c) <= 32 for c in str(url))
        ):
            raise ValueError()
        host = p.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = p.port or (443 if p.scheme == "https" else 80)
        return f"{p.scheme}://{host}:{port}"
    except (ValueError, TypeError):
        raise ProfileError("invalid_service_origin") from None


def profile_directory() -> Path:
    """Locate private machine state independently of generated package output."""
    return Path(
        os.environ.get("WEBSITE_CLIENT_PROFILE_DIR")
        or user_state_path("website-client") / "profiles"
    )


def _private(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or info.st_mode & 0o077
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
    ):
        raise ProfileError("unsafe_profile_permissions")


def header_names(names: Iterable[str]) -> set[str]:
    blocked = {
        "cookie",
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "origin",
        "referer",
    }
    result = set(AUTH_HEADERS)
    for name in names:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", name)
            or name.lower() in blocked
            or name.lower().startswith("sec-")
        ):
            raise ProfileError("invalid_profile_headers")
        result.add(name.lower())
    return result


def scoped_headers(
    headers: Mapping[str, str], allowed: Iterable[str] = AUTH_HEADERS
) -> dict[str, str]:
    result = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or name.lower() not in allowed
            or not isinstance(value, str)
            or len(value.encode()) > 16384
            or "\r" in value
            or "\n" in value
        ):
            raise ProfileError("invalid_profile_headers")
        result[name.lower()] = value
    return result


def scoped_state(state: dict[str, Any], endpoint: str) -> dict[str, Any]:
    selected = service_origin(endpoint)
    host = urlsplit(selected).hostname
    return {
        "cookies": [
            cookie
            for cookie in state.get("cookies", [])
            if host == cookie.get("domain", "").lstrip(".")
            or host.endswith("." + cookie.get("domain", "").lstrip("."))
        ],
        "origins": [
            item
            for item in state.get("origins", [])
            if service_origin(item["origin"]) == selected
        ],
    }


def profile_identifier(identifier: str) -> str:
    """Validate a nonsecret machine-local profile name without opening storage."""
    if not isinstance(identifier, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", identifier
    ):
        raise ProfileError("invalid_profile_identifier")
    return identifier


class ProfileStore:
    """Serialize the complete load, call/job, cookie-save cycle with a file lock."""

    def __init__(self, identifier: str, endpoint: str) -> None:
        self.identifier = profile_identifier(identifier)
        self.origin = service_origin(endpoint)
        self.root = profile_directory()
        module_directory = Path(__file__).resolve().parent
        package_directory = (
            module_directory.parent
            if (module_directory.parent / "pyproject.toml").is_file()
            else module_directory
        )
        if self.root.resolve().is_relative_to(package_directory):
            raise ProfileError("profile_inside_package")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(self.root, directory=True)
        self.path = self.root / (identifier + ".json")
        self.lock_path = self.root / (identifier + ".lock")

    @contextmanager
    def locked(self) -> Iterator["ProfileStore"]:
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

    def load(self) -> dict[str, Any]:
        try:
            _private(self.path)
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ProfileError("invalid_profile")
            value = json.loads(self.path.read_text())
            if value["origin"] != self.origin or value["profile"] != self.identifier:
                raise ProfileError("profile_origin_mismatch")
            value["header_names"] = sorted(header_names(value.get("header_names", [])))
            value["headers"] = scoped_headers(
                value["headers"], set(value["header_names"])
            )
            value["storage_state"] = scoped_state(value["storage_state"], self.origin)
            return value
        except FileNotFoundError:
            raise ProfileError("authentication_required") from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ProfileError("invalid_profile") from None

    def save(
        self,
        state: dict[str, Any],
        headers: Mapping[str, str],
        allowed_headers: Iterable[str] = (),
    ) -> None:
        if self.path.exists() or self.path.is_symlink():
            _private(self.path)
        value = {
            "version": 1,
            "profile": self.identifier,
            "origin": self.origin,
            "headers": scoped_headers(headers, header_names(allowed_headers)),
            "header_names": sorted(header_names(allowed_headers)),
            "storage_state": scoped_state(state, self.origin),
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".profile-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def save_browser_profile(
    context: Any,
    identifier: str,
    endpoint: str,
    headers: Mapping[str, str] | None = None,
    allowed_headers: Iterable[str] = (),
    owned_cookies: set[tuple[str, str]] | None = None,
) -> None:
    """Save selected login state; call only after explicit initial login completes."""
    with ProfileStore(identifier, endpoint).locked() as store:
        state = context.storage_state()
        if owned_cookies is not None:
            state["cookies"] = [
                cookie
                for cookie in state.get("cookies", [])
                if (cookie.get("name"), cookie.get("value")) in owned_cookies
            ]
        store.save(state, headers or {}, allowed_headers)


class HeaderCapture:
    """Observe known authentication headers only at the configured API origin."""

    def __init__(self, endpoint: str, allowed_headers: Iterable[str] = ()) -> None:
        self.origin = service_origin(endpoint)
        self.allowed_headers = header_names(allowed_headers)
        self.headers = {}

    def observe(self, method: str, params: Mapping[str, Any]) -> None:
        if method != "Network.requestWillBeSent":
            return
        try:
            request = params["request"]
            if service_origin(request["url"]) == self.origin:
                selected = {
                    k: v
                    for k, v in request.get("headers", {}).items()
                    if k.lower() in self.allowed_headers
                }
                self.headers.update(scoped_headers(selected, self.allowed_headers))
        except (KeyError, TypeError, ProfileError):
            return


class OriginCaptures:
    """Capture credentials only for admitted API origins and authentication headers."""

    def __init__(self, endpoint: str) -> None:
        self.captures = {}
        self.cookie_owners = {}
        self.cookie_issuers = {}
        self.declare(endpoint)

    def declare(self, endpoint: str, names: Iterable[str] = ()) -> None:
        origin = service_origin(endpoint)
        capture = self.captures.setdefault(origin, HeaderCapture(endpoint))
        capture.allowed_headers.update(header_names(names))

    def observe(self, method: str, params: Mapping[str, Any]) -> None:
        try:
            selected = self.captures.get(service_origin(params["request"]["url"]))
            if selected is not None:
                selected.observe(method, params)
                if method == "Network.requestWillBeSent":
                    self._cookies(
                        params["request"]["url"], params["request"].get("headers", {})
                    )
        except (KeyError, TypeError, ProfileError):
            return

    def _cookies(self, url: str, headers: Mapping[str, str]) -> None:
        selected = service_origin(url)
        if selected not in self.captures:
            return
        raw = next(
            (value for name, value in headers.items() if name.lower() == "cookie"), ""
        )
        cookies = SimpleCookie()
        try:
            cookies.load(raw)
        except Exception:
            return
        for name, item in cookies.items():
            self.cookie_owners.setdefault((name, item.value), set()).add(selected)

    def observe_request(self, request: Any) -> None:
        """Called only for allowed reads, before native request dispatch."""
        try:
            headers = (
                request.all_headers()
                if hasattr(request, "all_headers")
                else request.headers
            )
            self._cookies(request.url, headers)
        except Exception:
            return

    def observe_response(self, url: str, set_cookie_headers: Iterable[str]) -> None:
        """Native Set-Cookie evidence attributes renewal to its exact response origin."""
        try:
            selected = service_origin(url)
            if selected not in self.captures:
                return
            for raw in set_cookie_headers:
                cookies = SimpleCookie()
                cookies.load(raw)
                for name, item in cookies.items():
                    self.cookie_issuers.setdefault((name, item.value), set()).add(
                        selected
                    )
        except Exception:
            return

    def cookies_for(
        self,
        api_origin: str,
        *,
        browser_cookies: Iterable[Mapping[str, Any]] = (),
    ) -> set[tuple[str, str]]:
        """Retain exact-origin cookies and observed, native domain-cookie sharing."""
        identities = self.cookie_owners.keys() | self.cookie_issuers.keys()
        owned = {
            identity
            for identity in identities
            if self.cookie_issuers.get(
                identity, self.cookie_owners.get(identity, set())
            )
            == {api_origin}
        }
        return owned | self._observed_domain_cookies(api_origin, browser_cookies)

    def _observed_domain_cookies(
        self, api_origin: str, browser_cookies: Iterable[Mapping[str, Any]]
    ) -> set[tuple[str, str]]:
        """A cookie's domain is necessary but never sufficient API-use evidence."""
        if api_origin not in self.captures:
            return set()
        result = set()
        for cookie in browser_cookies:
            identity = (cookie.get("name"), cookie.get("value"))
            observed = self.cookie_owners.get(identity, set())
            if api_origin not in observed:
                continue
            evidence = observed | self.cookie_issuers.get(identity, set())
            if _domain_cookie_covers_hosts(cookie.get("domain"), evidence):
                result.add(identity)
        return result


def _domain_cookie_covers_hosts(domain: Any, origins: set[str]) -> bool:
    """Preserve browser domain scope without treating ports as cookie ownership."""
    if not isinstance(domain, str) or not domain.startswith("."):
        return False
    parsed = [urlsplit(origin) for origin in origins]
    hosts = {item.hostname for item in parsed}
    # Different ports on one host remain ambiguous. This exception is only for
    # a native domain cookie actually observed at separate admitted HTTPS hosts.
    if len(hosts) < 2 or len(hosts) != len(origins):
        return False
    if any(item.scheme != "https" or not item.hostname for item in parsed):
        return False
    suffix = domain[1:].lower()
    return bool(suffix) and all(
        host == suffix or host.endswith("." + suffix) for host in hosts
    )
