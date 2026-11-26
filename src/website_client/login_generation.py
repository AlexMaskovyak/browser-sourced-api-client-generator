"""Publish a login-capable Python client independently of the browser login result."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
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


def generate_login_client(
    url: str,
    output: Path,
    *,
    link: PageLink | None = None,
    definition: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    """Package browser login and the discovered page, if any, in one SDK."""
    definition = (
        json.loads(json.dumps(definition))
        if definition is not None
        else {
            "openapi": "3.0.3",
            "info": {"title": "Login client", "version": "1.0.0"},
            "paths": link.openapi_paths() if link else {},
        }
    )
    if link is not None:
        for path, methods in link.openapi_paths().items():
            definition["paths"].setdefault(path, methods)
        if (
            definition["paths"][link.path]["get"].get("operationId")
            != "read_linked_page"
        ):
            link = None
    report = generate_openapi_client(
        output,
        "login-client",
        json.dumps(definition),
        website=_website_default(url),
        linked_page=link.path if link else None,
    )
    for name, text in (evidence or {}).items():
        if Path(name).name != name:
            raise BrowserError("Invalid capture evidence filename.")
        path = output / name
        if path.exists():
            raise BrowserError("Capture evidence would replace a generated file.")
        path.write_text(text)
        path.chmod(0o600)
    return report


def project_snapshot(directory: Path) -> dict:
    """Identify the published tree so an upgrade cannot discard local edits."""
    snapshot = {}
    for path in (directory, *directory.rglob("*")):
        info = path.lstat()
        snapshot[path.relative_to(directory)] = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
        )
    return snapshot


def upgrade_login_client(
    url: str,
    output: Path,
    link: PageLink | None,
    initial: dict,
    *,
    definition: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    """Build the linked client in full before replacing this run's initial package."""
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".linked-client-"
    ) as stage:
        linked = Path(stage) / "client"
        report = generate_login_client(
            url, linked, link=link, definition=definition, evidence=evidence
        )
        if project_snapshot(output) != initial:
            raise BrowserError(
                "The login client changed during sign-in. Retry in a new directory."
            )

        # Keep the original outside staging so even a failed restoration leaves it recoverable.
        previous = output.with_name(f".login-backup-{uuid4().hex}")
        output.rename(previous)
        try:
            linked.rename(output)
        except BaseException:
            if not output.exists():
                previous.rename(output)
            raise
        shutil.rmtree(previous)
        return report
