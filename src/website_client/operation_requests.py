"""Interpret captured request metadata without retaining query values or headers."""

from dataclasses import dataclass
from enum import StrEnum
import json

from .browser import BrowserError
from .discovery_manifest.model import observed_endpoint
from .discovery_manifest.records import Kind, Manifest


class RequestMethod(StrEnum):
    GET = "GET"
    POST = "POST"


@dataclass(frozen=True, slots=True)
class RequestStyle:
    method: RequestMethod = RequestMethod.POST
    operation_name_in_url: bool = False
    observed: bool = False

    def __post_init__(self):
        if (
            type(self.method) is not RequestMethod
            or type(self.operation_name_in_url) is not bool
            or type(self.observed) is not bool
        ):
            raise BrowserError("Invalid operation request style.")


def request_styles(manifest: Manifest, endpoint: str) -> dict[str, RequestStyle]:
    styles = {}
    for document in manifest.artifacts:
        if document.kind is not Kind.REPORT or document.name != "discovery.json":
            continue
        try:
            report = json.loads(document.content)
        except Exception:
            raise BrowserError("Captured request report is not valid JSON.") from None
        # This is a JSON-format adapter. Business logic consumes RequestStyle.
        if not isinstance(report, dict) or not isinstance(
            report.get("endpoints", []), list
        ):
            raise BrowserError("Captured request report has invalid endpoints.")
        for item in report.get("endpoints", []):
            if not isinstance(item, dict):
                raise BrowserError("Captured request metadata must be an object.")
            operation = item.get("graphql") or {}
            if not isinstance(operation, dict):
                raise BrowserError("Captured GraphQL metadata must be an object.")
            name = operation.get("name")
            if not isinstance(name, str) or item.get("method") not in {"GET", "POST"}:
                continue
            try:
                address = observed_endpoint(item["origin"] + item["path"])
            except (KeyError, TypeError, BrowserError):
                continue
            if address != endpoint:
                continue
            parameters = item.get("queryParameters", [])
            if not isinstance(parameters, list) or any(
                type(key) is not str for key in parameters
            ):
                raise BrowserError("Captured query parameter names must be strings.")
            candidate = RequestStyle(
                RequestMethod(item["method"]),
                "operationName" in parameters,
                True,
            )
            prior = styles.get(name)
            if prior is None or candidate.method is RequestMethod.POST:
                styles[name] = candidate
    return styles
