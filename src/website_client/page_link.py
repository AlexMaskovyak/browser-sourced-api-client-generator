"""Describe one ordinary page link from the authenticated dashboard."""

from collections.abc import Iterable
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .browser import origin
from .discovery import route
from .exploration_policy import clean_navigation


@dataclass(frozen=True)
class PageLink:
    """A literal page path suitable for an OpenAPI GET operation."""

    path: str

    def __post_init__(self):
        if not re.fullmatch(r"/[a-zA-Z0-9._~/\-]*", self.path) or "//" in self.path:
            raise ValueError("A page link needs a literal website path.")

    def openapi_paths(self) -> dict:
        return {
            self.path: {
                "get": {
                    "operationId": "read_linked_page",
                    "responses": {
                        "200": {
                            "description": "Linked page",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            }
        }


def first_page_link(website: str, dashboard: str, hrefs: Iterable[str]) -> PageLink | None:
    """Select a literal path without copying credentials, query values or identifiers."""
    allowed_origin = origin(website)
    if origin(dashboard.split("#", 1)[0]) != allowed_origin:
        return None
    dashboard_path = urlsplit(dashboard).path
    for href in hrefs:
        address = clean_navigation(dashboard, href, allowed_origin)
        if not address:
            continue
        parsed = urlsplit(address)
        if parsed.query or parsed.fragment or parsed.path == dashboard_path:
            continue
        _, path, parameters = route(address)
        if not parameters and path == parsed.path:
            try:
                return PageLink(path)
            except ValueError:
                continue
    return None
