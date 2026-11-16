"""Public context factory for the native generated client and its saved defaults."""

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field, replace
from importlib import import_module
from importlib.resources import files
import json
from collections.abc import Iterator, Mapping
from types import ModuleType
from typing import Any

import httpx

from .authenticated_http import profile_dispatch, SDKTransport
from .runtime_profile import profile_identifier, service_origin


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated nonsecret service defaults; locations stay out of diagnostic repr."""

    kind: str
    endpoint: str | None = field(default=None, repr=False)
    profile_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {
            "openapi",
            "graphql",
            "graphql-custom",
            "graphql-operation",
        }:
            raise ValueError("invalid_runtime_configuration")
        if self.endpoint is not None:
            if not isinstance(self.endpoint, str):
                raise ValueError("invalid_runtime_configuration")
            service_origin(self.endpoint)
        if self.profile_id is not None:
            profile_identifier(self.profile_id)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeConfig":
        """Validate stored defaults without opening credentials or HTTP contexts."""
        if not isinstance(value, Mapping) or set(value) - {
            "kind",
            "endpoint",
            "profileId",
            "origin",
        }:
            raise ValueError("invalid_runtime_configuration")
        return cls(value.get("kind"), value.get("endpoint"), value.get("profileId"))

    def selected(self, url: str | None, profile_id: str | None) -> "RuntimeConfig":
        """Apply explicit caller overrides through the same validation boundary."""
        return replace(
            self,
            endpoint=url or self.endpoint,
            profile_id=profile_id or self.profile_id,
        )


def runtime_defaults() -> dict[str, Any]:
    """Read and validate only nonsecret configuration installed with the package."""
    value = json.loads(files(__package__).joinpath("_runtime.json").read_text())
    RuntimeConfig.from_mapping(value)
    return value


def _native_client(
    module: ModuleType, config: RuntimeConfig, http: httpx.Client, allow_writes: bool
) -> Any:
    if config.kind == "openapi":
        native = module.Client(base_url=config.endpoint, follow_redirects=False)
        native.set_httpx_client(http)
        return native
    options = {"url": config.endpoint, "http_client": http}
    if config.kind == "graphql-operation":
        options["allow_writes"] = allow_writes
    return module.Client(**options)


@contextmanager
def client_context(
    *, url: str | None = None, profile_id: str | None = None, allow_writes: bool = False
) -> Iterator[Any]:
    """Yield the unchanged upstream client using installed endpoint/profile defaults.

    The profile lock covers the entire context, including multiple operations.
    Missing or expired credentials terminate without reading standard input.
    """
    config = RuntimeConfig.from_mapping(runtime_defaults()).selected(url, profile_id)
    if not config.endpoint:
        raise ValueError("service_endpoint_required")
    module = import_module(__package__ + ".client")
    with (
        profile_dispatch(config.profile_id, config.endpoint) as dispatch,
        ExitStack() as resources,
    ):
        http = httpx.Client(
            base_url=config.endpoint,
            transport=SDKTransport(dispatch),
            follow_redirects=False,
            trust_env=False,
            timeout=30,
        )
        # The upstream Client enters the HTTP client itself. Register cleanup
        # without entering it twice, including if native construction fails.
        resources.callback(http.close)
        with _native_client(module, config, http, allow_writes) as native:
            yield native
