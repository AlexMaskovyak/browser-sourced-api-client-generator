"""The single public workflow entry copied into standalone workflow packages."""

from importlib.resources import files
import json
from pathlib import PurePosixPath
from typing import Any

from .authenticated_http import LIMIT
from .workflow_runtime import ExecutionSummary, execute_document
from .workflow_schema import WorkflowRejected


def _resource_json(name: str) -> Any:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != "definitions"
    ):
        raise WorkflowRejected("invalid_workflow_package")
    raw = files(__package__).joinpath(name).read_bytes()
    if len(raw) > LIMIT:
        raise WorkflowRejected("invalid_workflow_package")
    return json.loads(raw)


def workflow_index() -> dict[str, dict]:
    """Read installed metadata without consulting the network or private profile state."""
    raw = files(__package__).joinpath("_workflows.json").read_bytes()
    if len(raw) > LIMIT:
        raise WorkflowRejected("invalid_workflow_package")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise WorkflowRejected("invalid_workflow_package")
    return value


def execute_workflow(
    workflow_id: str, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one packaged workflow noninteractively through its fixed source definitions.

    Native Arazzo owns scheduling, expressions, and serialization. Private profile
    locks cover the full job. A failed or uncertain write is never retried.
    """
    try:
        index = workflow_index()
        if not isinstance(workflow_id, str) or workflow_id not in index:
            return ExecutionSummary("[omitted]").to_dict()
        entry = index[workflow_id]
        document = _resource_json(entry["document"])
        sources = {
            name: _resource_json(resource)
            for name, resource in entry["sources"].items()
        }
        summary = execute_document(
            document, sources, entry["nativeId"], inputs, entry["profiles"]
        )
        summary["workflow"] = workflow_id
        return summary
    except KeyboardInterrupt:
        return ExecutionSummary(
            "[omitted]", status="cancelled", reason="cancelled"
        ).to_dict()
    except Exception:
        # Resource parsers and native source values must never appear in diagnostics.
        return ExecutionSummary(
            "[omitted]", reason="invalid_workflow_package"
        ).to_dict()
