"""Write useful local output when discovery yields no callable operations."""

from dataclasses import dataclass
from collections import Counter
import json
from pathlib import Path
from string import Template
from typing import Any, Mapping

from .browser import BrowserError

FAILURE_MESSAGES = {
    "no_executable_operations": "No callable API operations were found.",
    "invalid_or_unsupported_input": "Generation stopped because the input or browser session could not be processed.",
    "har_dependency_required": "Generation stopped because HAR inference dependencies are unavailable.",
    "generation_failed": "Generation did not finish. Partial project files have not been verified.",
    "cancelled": "Generation was cancelled. Partial project files have not been verified.",
}

EXCLUSION_GUIDANCE = {
    "har_inference_failed": "REST traffic conversion did not yield a usable contract. Independent GraphQL operations or published definitions can still produce clients; this is not a Node/npm prerequisite failure.",
    "undeclared_origin": "A discovered service was excluded because its API origin was not admitted. A separate GraphQL host can cause this; use a generator revision that admits observed read requests, then collect fresh evidence.",
    "har_dependency_required": "HAR conversion could not find a usable Node 20+ and npm. Run `node --version` and `npm --version` in the same terminal used to launch the generator. This recorded prerequisite failure does not establish which executable was missing at the time.",
    "cookie_origin_isolation": "Cookies with unproven or ambiguous origin ownership were omitted from private profiles. This may affect later authentication; it is not proof that the service has no operations.",
    "privacy_or_capture_omission": "Some traffic was unavailable or omitted during capture/privacy filtering. This count alone does not identify the reason for a missing client.",
}
PROBE_GUIDANCE = {
    "unauthorized": "A definition or introspection request was rejected by authentication or access policy.",
    "redirect_blocked": "A definition or introspection request redirected; discovery did not follow it.",
    "transport_failed": "The request transport failed before a usable response was returned.",
    "request_failed": "A probe failed without a recorded cause. The saved report cannot distinguish authentication, redirects, transport or another failure.",
    "not_found": "The probed definition path returned HTTP 404.",
    "schema_unavailable": "The endpoint did not return a usable GraphQL schema. Named observed operations may still support a client.",
}


@dataclass(frozen=True)
class DiscoveryCounts:
    """Only counts, never target URLs, response bodies, or credentials, enter help."""

    pages: int
    blocked_requests: int
    unsuccessful_probes: int
    blocked_graphql_queries: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in vars(self).values()):
            raise ValueError("Discovery counts must be nonnegative integers.")

    @classmethod
    def from_report(cls, report: Mapping[str, Any]) -> "DiscoveryCounts":
        discovery = report.get("discovery") or {}
        exploration = discovery.get("exploration") or {}
        return cls(
            pages=len(exploration.get("pages", [])),
            blocked_requests=len(exploration.get("blockedRequests", [])),
            unsuccessful_probes=sum(
                probe.get("outcome") != "found" for probe in discovery.get("probes", [])
            ),
            blocked_graphql_queries=sum(
                (item.get("graphql") or {}).get("kind") == "query"
                for item in exploration.get("blockedRequests", [])
            ),
        )


def write_unsupported_output(output: Path, coverage: Mapping[str, Any]) -> None:
    """Keep zero-service coverage truthful while leaving a runnable local stub."""
    if coverage.get("coverage") != "unsupported":
        return
    counts = DiscoveryCounts.from_report(coverage)
    reason = coverage.get("reason", "generation_failed")
    if reason not in FAILURE_MESSAGES:
        reason = "generation_failed"
    message = FAILURE_MESSAGES[reason]
    templates = Path(__file__).parent / "templates"
    readme = Template(
        (templates / "unsupported_readme.md.tmpl").read_text()
    ).substitute(
        message=message,
        reason=reason,
        pages=counts.pages,
        blocked_requests=counts.blocked_requests,
        unsuccessful_probes=counts.unsuccessful_probes,
        blocked_graphql_queries=counts.blocked_graphql_queries,
        details=_recorded_details(coverage),
    )
    client = Template(
        (templates / "unsupported_client.py.tmpl").read_text()
    ).substitute(
        reason=repr(reason),
        message=repr(message),
        exit_code=130 if reason == "cancelled" else 1,
    )
    for name, content in (
        ("README.md", readme),
        ("client.py", client),
    ):
        path = output / name
        path.write_text(content)
        path.chmod(0o600)


def _recorded_details(coverage: Mapping[str, Any]) -> str:
    """Explain only recognized evidence codes; untrusted report text stays out."""
    codes = {item.get("reason") for item in coverage.get("exclusions", [])}
    discovery = coverage.get("discovery") or {}
    probes = Counter(item.get("outcome") for item in discovery.get("probes", []))
    lines = [
        f"- **{code}**: {text}"
        for code, text in EXCLUSION_GUIDANCE.items()
        if code in codes
    ]
    lines.extend(
        f"- **{code}** ({probes[code]} probes): {text}"
        for code, text in PROBE_GUIDANCE.items()
        if probes[code]
    )
    return (
        "\n".join(lines) or "No more specific exclusion or probe reason was recorded."
    )


def write_failed_run(output: Path, error: BaseException) -> None:
    """Keep original exception behavior and persist only fixed, public reasons."""
    reason = "generation_failed"
    if isinstance(error, KeyboardInterrupt):
        reason = "cancelled"
    elif isinstance(error, BrowserError):
        reason = (
            "har_dependency_required"
            if str(error).startswith("har_dependency_required")
            else "invalid_or_unsupported_input"
        )
    coverage = {
        "coverage": "unsupported",
        "reason": reason,
        "services": [],
        "workflows": [],
        "completeSiteCoverage": False,
        "exclusions": [],
        "discovery": None,
    }
    path = output / "coverage.json"
    path.write_text(json.dumps(coverage, indent=2) + "\n")
    path.chmod(0o600)
    write_unsupported_output(output, coverage)
