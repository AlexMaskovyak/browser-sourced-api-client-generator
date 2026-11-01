"""Classify navigation and requests without trusting page labels as permission."""

import json
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from graphql import get_operation_ast, parse

from .browser import BrowserError, origin
from .discovery import AUTH, MAX_BODY, route

WRITE_WORDS = re.compile(
    r"(?:^|[^a-z])(delete|remove|submit|save|create|approve|reject|pay|purchase|checkout|cancel|send|upload|signout|logout|unsubscribe|disconnect|revoke)(?:$|[^a-z])",
    re.I,
)
PAGINATION = re.compile(
    r"^(?:next|previous|prev|older|newer|load more|show more)(?:\s+(?:page|results|items))?$",
    re.I,
)
DOWNLOAD_PATH = re.compile(
    r"\.(?:pdf|zip|csv|xlsx?|docx?|png|jpe?g|gif|mp4|exe|dmg)(?:$|[?#])", re.I
)
OPEN_FORM = re.compile(r"^(?:add|new|edit|create|upload)(?:\s|$)", re.I)


def clean_navigation(base_url, href, allowed_origin):
    """Keep actual navigation addresses in memory, never send them to a model."""
    try:
        url = urljoin(base_url, href)
        parsed = urlsplit(url)
        without_fragment = urlunsplit(parsed._replace(fragment=""))
        if origin(without_fragment) != allowed_origin:
            return None
        if (
            AUTH.search(parsed.path)
            or WRITE_WORDS.search(unquote(parsed.path + "?" + parsed.query))
            or DOWNLOAD_PATH.search(parsed.path)
        ):
            return None
        if any(AUTH.search(key) for key in parse_qs(parsed.query)):
            return None
        if parsed.fragment and not parsed.fragment.startswith(("/", "!/")):
            return None
        if parsed.fragment and WRITE_WORDS.search(unquote(parsed.fragment)):
            return None
        return parsed.geturl()
    except (BrowserError, ValueError, TypeError):
        return None


def safe_address(url):
    """Use the same path anonymization as the HAR and omit query values."""
    try:
        parsed = urlsplit(url)
        base, path, _params = route(urlunsplit(parsed._replace(fragment="", query="")))
        result = base + path
        if parsed.query:
            result += "?[values omitted]"
        if parsed.fragment.startswith(("/", "!/")):
            _base, fragment, _params = route(base + "/" + parsed.fragment.lstrip("!/"))
            result += "#" + fragment
        return result
    except Exception:
        return "[address omitted]"


def label(value):
    """Retain useful control words, removing common personal identifiers."""
    text = re.sub(r"\s+", " ", value or "").strip()[:120]
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", text)
    return re.sub(r"\d+|[a-f0-9]{24,}", "[value]", text, flags=re.I)


def graphql_read(payload):
    """A parsed selected query is readable; unknown persisted operations are not."""
    if isinstance(payload, list):
        return (
            bool(payload)
            and len(payload) <= 20
            and all(graphql_read(item) for item in payload)
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
        return False
    try:
        document = parse(payload["query"], max_tokens=20_000)
        selected = get_operation_ast(document, payload.get("operationName"))
        return selected is not None and selected.operation.value == "query"
    except Exception:
        return False


def request_allowed(request, allowed_origin):
    """Return a reason for a blocked request, or None for an allowed read."""
    try:
        parsed = urlsplit(request.url)
        target = origin(urlunsplit(parsed._replace(fragment="")))
        if request.is_navigation_request() and target != allowed_origin:
            return "off_site_navigation"
        static_asset = getattr(request, "resource_type", "") in {
            "script",
            "stylesheet",
            "image",
            "font",
            "media",
        }
        if not static_asset and (
            AUTH.search(parsed.path) or WRITE_WORDS.search(unquote(parsed.path))
        ):
            return "authentication_or_action_path"
        if request.method in {"GET", "HEAD"}:
            fields = parse_qs(parsed.query)
            if "query" in fields and not graphql_read(
                {
                    "query": fields["query"][0],
                    "operationName": fields.get("operationName", [None])[0],
                }
            ):
                return "graphql_write_or_unknown_operation"
            if any(
                WRITE_WORDS.search(value)
                for key, values in fields.items()
                if key.lower() in {"action", "method", "command"}
                for value in values
            ):
                return "action_query"
            return None
        if request.method == "OPTIONS":
            return None
        if request.method != "POST":
            return "write_method"
        body = request.post_data or ""
        if len(body.encode()) > MAX_BODY:
            return "unmodeled_post"
        return None if graphql_read(json.loads(body)) else "write_or_unmodeled_post"
    except Exception:
        return "unmodeled_request"
