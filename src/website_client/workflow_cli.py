"""Noninteractive command entry copied into standalone workflow packages."""

import argparse
import json
import os
import stat
import sys
from typing import Any, NoReturn

from .authenticated_http import LIMIT
from .runtime import execute_workflow, workflow_index
from .result_output import write_result_text
from .workflow_admission import IDENTIFIER
from .workflow_runtime import ExecutionSummary


def _file_bytes(path: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError("invalid_file_input")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("invalid_file_input")
    return raw


def _inputs(options: argparse.Namespace) -> dict[str, Any]:
    value = json.loads(_file_bytes(options.input)) if options.input else {}
    if not isinstance(value, dict):
        raise ValueError("invalid_inputs")
    for declaration in options.file:
        name, separator, path = declaration.partition("=")
        if not separator or not IDENTIFIER.fullmatch(name) or name in value:
            raise ValueError("invalid_file_input")
        value[name] = _file_bytes(path)
    return value


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default error text echoes argument values. Keep usage
        # failures machine-readable without exposing an accidental credential.
        print(
            json.dumps(ExecutionSummary("[omitted]", reason="invalid_usage").to_dict()),
            file=sys.stderr,
        )
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    """Show installed workflow IDs locally; help sends no service requests."""
    parser = _ArgumentParser(
        description="Run a declared workflow. Steps may write remote data; no automatic retries occur."
    )
    parser.add_argument("workflow_id", choices=sorted(workflow_index()))
    parser.add_argument(
        "--input", help="JSON object file of ordinary workflow inputs, up to 8 MiB."
    )
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Read a regular file as a bounded binary input; repeat for distinct input names.",
    )
    parser.add_argument(
        "--output",
        help="Write the result to a new private JSON file instead of standard output.",
    )
    return parser


def _write_result(summary: dict[str, Any], output: str | None) -> None:
    text = json.dumps(summary, allow_nan=False) + "\n"
    write_result_text(text, output)


CALLER_INPUT_REASONS = frozenset(
    {
        "missing_input",
        "invalid_inputs",
        "unsupported_input_object",
        "input_depth_exceeded",
        "file_exceeds_8_mib",
        "invalid_binary_input",
        "invalid_request",
        "invalid_multipart_file",
        "unsupported_path_value",
        "missing_required_parameter",
        "missing_required_body",
        "malformed_request_json",
    }
)


def _exit_code(summary: dict[str, Any]) -> int:
    if summary["status"] == "cancelled":
        return 130
    if (
        summary["status"] == "rejected"
        and summary["requests"] == 0
        and summary["reason"] in CALLER_INPUT_REASONS
    ):
        return 2
    return 0 if summary["status"] == "success" else 1


def main(args: list[str] | None = None) -> int:
    """Run once, preserving remote outcomes even if local result output fails."""
    try:
        options = build_parser().parse_args(args)
        supplied = _inputs(options)
    except KeyboardInterrupt:
        print(
            json.dumps(
                ExecutionSummary(
                    "[omitted]", status="cancelled", reason="cancelled"
                ).to_dict()
            ),
            file=sys.stderr,
        )
        return 130
    except Exception:
        print(
            json.dumps(
                ExecutionSummary("[omitted]", reason="invalid_inputs").to_dict()
            ),
            file=sys.stderr,
        )
        return 2
    summary = execute_workflow(options.workflow_id, supplied)
    try:
        _write_result(summary, options.output)
    except (Exception, KeyboardInterrupt) as error:
        # Result output is a local stage after execution. Preserve the verified
        # workflow/transport/assertion outcome and effects rather than retrying.
        cancelled = isinstance(error, KeyboardInterrupt)
        summary["commandStatus"] = "cancelled" if cancelled else "failure"
        summary["output"] = {
            "status": "failure",
            "reason": "output_cancelled" if cancelled else "local_output_failure",
        }
        print(json.dumps(summary, allow_nan=False), file=sys.stderr, flush=True)
        return 130 if cancelled else 1
    return _exit_code(summary)


def entrypoint() -> None:
    """Exit the installed command using its explicit success/failure/usage status."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
