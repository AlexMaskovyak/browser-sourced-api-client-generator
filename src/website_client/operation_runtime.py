"""Standalone HTTPX client runtime for captured operation documents."""

from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import MappingProxyType
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .operation_contracts import (
    MAX_DOCUMENT_BYTES,
    Operation,
    compile_operations,
    validate_variables,
)

UNSET = object()


class OperationError(RuntimeError):
    """Safe diagnostics without server messages, bodies or credential values."""

    def __init__(self, message, *, http_status=None):
        super().__init__(message)
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class BoundOperation:
    """Pair a compiled GraphQL document with its observed HTTP conventions."""

    operation: Operation
    method: str
    operation_name_in_url: bool

    def __post_init__(self):
        """Validate packaged metadata before any transport activity or mutation."""
        if (
            type(self.operation) is not Operation
            or type(self.method) is not str
            or self.method not in {"GET", "POST"}
            or type(self.operation_name_in_url) is not bool
        ):
            raise ValueError("Invalid operation request metadata.")
        if self.operation.write and self.method != "POST":
            raise ValueError("Mutation requests cannot use GET.")


@cache
def contracts(package):
    """Load and compile packaged documents once, exposing a read-only mapping."""
    metadata = json.loads(files(package).joinpath("_operations.json").read_text())
    result = {}
    for name, item in metadata.items():
        operations = compile_operations((item["document"],))
        if len(operations) != 1 or operations[0].name != name:
            raise ValueError("Operation metadata does not match its document.")
        result[name] = BoundOperation(
            operations[0], item["httpMethod"], item["operationNameInUrl"]
        )
    return MappingProxyType(result)


def validate_arguments(package, name, arguments):
    """Check declared variables without contacting the service or opening a browser."""
    return validate_variables(contracts(package)[name].operation, arguments)


class OperationClient:
    """Send captured operations with HTTPX, optionally using an injected transport.

    Args:
        url: Exact GraphQL endpoint. HTTPS is required except on localhost.
        headers: Temporary request headers. Callers supply authentication at runtime.
        http_client: An existing HTTPX client, optionally backed by browser login.
            The caller owns and closes this client. If omitted, this class creates
            and owns an HTTPX client with redirects disabled and a 30-second timeout.
        allow_writes: Explicit permission to execute mutation methods. Defaults false.

    Use as a context manager or call close(). This class owns no accounting state
    and never retries automatically. An injected HTTP client must also disable
    retries in its transport when uncertain writes must not be repeated.
    """

    def __init__(self, url: str, headers=None, http_client=None, *, allow_writes=False):
        if type(allow_writes) is not bool:
            raise ValueError("Input must use a boolean write permission.")
        parsed = urlsplit(url)
        local = parsed.scheme == "http" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (parsed.scheme != "https" and not local)
        ):
            raise ValueError("Use HTTPS without URL credentials or a fragment.")
        self.url = url
        self.headers = dict(headers or {})
        self.allow_writes = allow_writes
        self.http_client = http_client or httpx.Client(
            headers=self.headers, follow_redirects=False, timeout=30
        )
        self._owns_client = http_client is None
        self._operations = contracts(type(self).__module__.rpartition(".")[0])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        """Close an internally created connection; leave injected clients open."""
        if self._owns_client:
            self.http_client.close()

    def _execute(self, name: str, arguments: dict[str, Any]):
        """Validate one operation, send it once and reject partial GraphQL results."""
        bound = self._operations[name]
        operation = bound.operation
        variables = validate_variables(operation, arguments)
        # Validate before any transport or browser activity. Missing values have
        # already been omitted by generated methods; explicit None stays null.
        if operation.write and not self.allow_writes:
            raise ValueError(
                "This write requires allow_writes=True on the Python client."
            )
        payload = {
            "operationName": name,
            "query": operation.document,
            "variables": variables,
        }
        url = self.url
        if bound.operation_name_in_url:
            parts = urlsplit(url)
            params = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key != "operationName"
            ]
            params.append(("operationName", name))
            url = urlunsplit(parts._replace(query=urlencode(params)))
        options = {"headers": self.headers, "follow_redirects": False}
        if bound.method == "GET":
            options["params"] = {
                **payload,
                "variables": json.dumps(variables, allow_nan=False),
            }
        else:
            options["json"] = payload
        with self.http_client.stream(bound.method, url, **options) as response:
            if not 200 <= response.status_code < 300:
                raise OperationError(
                    "Endpoint returned a non-success status; no automatic retry occurred.",
                    http_status=response.status_code,
                )
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise RuntimeError("Endpoint response exceeds the 8 MiB limit.")
                chunks.append(chunk)
            try:
                result = json.loads(b"".join(chunks))
            except Exception:
                raise RuntimeError("Endpoint returned invalid JSON.") from None
        if (
            not isinstance(result, dict)
            or result.get("errors")
            or not isinstance(result.get("data"), dict)
        ):
            raise RuntimeError(
                "GraphQL response failed; partial results were not marked successful."
            )
        return result["data"]
