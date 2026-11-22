"""Write useful local output when discovery yields no callable operations."""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class DiscoveryCounts:
    """Only counts, never target URLs, response bodies, or credentials, enter help."""

    pages: int
    blocked_requests: int
    unsuccessful_probes: int

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
