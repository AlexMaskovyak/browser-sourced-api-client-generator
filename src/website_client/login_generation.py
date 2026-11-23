"""Publish a login-capable Python client independently of the browser login result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from .browser import BrowserError, origin
from .generation import generate_openapi_client

if TYPE_CHECKING:
    from .page_link import PageLink


def default_login_output() -> Path:
    """Give each invocation its own directory without replacing prior clients."""
    parent = Path.home() / "website-clients"
    parent.mkdir(mode=0o700, exist_ok=True)
    return parent / f"login-{uuid4().hex}"


def _website_default(url: str) -> str | None:
    try:
        host, port = origin(url)
    except BrowserError:
        # Keep a retry client even for a malformed URL. Never embed that input.
        return None
    hostname = f"[{host}]" if ":" in host else host
    return f"https://{hostname}" + (f":{port}" if port != 443 else "")


def generate_login_client(url: str, output: Path, *, link: PageLink | None = None) -> dict:
    """Package browser login and the discovered page, if any, in one SDK."""
    definition = {
        "openapi": "3.0.3",
        "info": {"title": "Login client", "version": "1.0.0"},
        "paths": link.openapi_paths() if link else {},
    }
    return generate_openapi_client(
        output, "login-client", json.dumps(definition), website=_website_default(url)
    )
