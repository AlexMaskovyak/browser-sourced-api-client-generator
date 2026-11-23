"""Publish a login-capable Python client independently of the browser login result."""

from importlib import metadata, resources
from pathlib import Path
from uuid import uuid4

from .browser import BrowserError, origin
from .discovery import publish


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


def generate_login_client(url: str, output: Path) -> None:
    """Export the shared browser implementation and a credential-free entry point."""
    website = _website_default(url)
    package = resources.files("website_client")
    templates = package.joinpath("templates")
    source = templates.joinpath("login_client.py.tmpl").read_text()
    distribution = metadata.distribution("browser-sourced-api-client-generator")
    license_text = next(
        entry.read_text()
        for entry in distribution.files or ()
        if entry.name == "LICENSE" and ".dist-info/" in str(entry)
    )
    publish(
        output,
        {
            "client.py": source.replace("DEFAULT_WEBSITE", repr(website)),
            "_browser.py": package.joinpath("browser.py").read_text(),
            "requirements.txt": "playwright>=1.58,<2\n",
            "README.md": templates.joinpath("login_readme.md.tmpl").read_text(),
            "LICENSE": license_text,
        },
    )
