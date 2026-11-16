"""Admit only explicit linear Arazzo contracts before constructing the native runner."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping
from collections import Counter
import re
from urllib.parse import urlsplit

from jsonpointer import JsonPointer
from jsonschema import Draft202012Validator

from .capabilities import METHODS, CAPABILITIES, request_features, resolved
from .runtime_profile import service_origin
from .workflow_schema import WorkflowRejected, local_schema, validate_value

NAME = r"[A-Za-z][A-Za-z0-9_]*"
IDENTIFIER = re.compile(NAME + r"\Z")
INPUT = re.compile(r"\$inputs\.(" + NAME + r")\Z")
STEP_OUTPUT = re.compile(r"\$steps\.(" + NAME + r")\.(" + NAME + r")\Z")
POINTER_PREFIX = "$response.body#"
STATUS = re.compile(r"\$statusCode == ([1-5][0-9][0-9])\Z")
BOOLEAN = re.compile(r"\$response\.body\.(" + NAME + r") == (true|false)\Z")
MAX_STEPS = 100

PARAMETER_SCHEMA = {
    "type": "object",
    "required": ["name", "in", "value"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "in": {"enum": ["path", "query", "header"]},
        "value": {},
    },
}
STEP_SCHEMA = {
    "type": "object",
    "required": ["stepId", "operationId"],
    "additionalProperties": False,
    "properties": {
        "stepId": {"type": "string", "pattern": "^" + NAME + "$"},
        "operationId": {"type": "string"},
        "parameters": {"type": "array", "items": PARAMETER_SCHEMA},
        "requestBody": {
            "type": "object",
            "required": ["contentType"],
            "additionalProperties": False,
            "properties": {"contentType": {"type": "string"}, "payload": {}},
        },
        "outputs": {"type": "object", "additionalProperties": {"type": "string"}},
        "successCriteria": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["condition"],
                "additionalProperties": False,
                "properties": {"condition": {"type": "string"}},
            },
        },
    },
}
WORKFLOW_SCHEMA = {
    "type": "object",
    "required": ["workflowId", "steps"],
    "additionalProperties": False,
    "properties": {
        "workflowId": {"type": "string", "pattern": "^" + NAME + "$"},
        "inputs": {"type": "object"},
        "outputs": {"type": "object", "additionalProperties": {"type": "string"}},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_STEPS,
            "items": STEP_SCHEMA,
        },
    },
}
PROFILE_SCHEMA = {
    "type": "object",
    "required": ["arazzo", "info", "sourceDescriptions", "workflows"],
    "additionalProperties": False,
    "properties": {
        "arazzo": {"const": "1.0.0"},
        "info": {"type": "object"},
        "sourceDescriptions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "type", "url"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "pattern": "^" + NAME + "$"},
                    "type": {"const": "openapi"},
                    "url": {"type": "string"},
                },
            },
        },
        "workflows": {"type": "array", "items": WORKFLOW_SCHEMA},
    },
}


@dataclass(frozen=True)
class OperationBinding:
    """An operation's validated ownership within a bundled native OpenAPI document.

    Native documents remain dictionaries owned by upstream validators/builders.
    They are hidden from representations because examples can contain user data.
    """

    source: str = field(repr=False)
    document: dict[str, Any] = field(repr=False)
    path: str = field(repr=False)
    path_item: dict[str, Any] = field(repr=False)
    method: str
    operation: dict[str, Any] = field(repr=False)
    base: str = field(repr=False)

    def __post_init__(self) -> None:
        service_origin(self.base)
        if self.method.lower() not in METHODS or not self.path.startswith("/"):
            raise WorkflowRejected("unsupported_destination")

    def parameters(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Resolve inherited parameters, with operation-level overrides applied last."""
        native = self.path_item.get("parameters", []) + self.operation.get(
            "parameters", []
        )
        values = [resolved(self.document, item) for item in native]
        return {(value["in"], value["name"]): value for value in values}


def operation_index(
    sources: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, OperationBinding], set[str]]:
    """Index explicit operation IDs and retain ambiguous IDs for exclusion."""
    index, duplicates = {}, set()
    for source_name, document in sources.items():
        servers = document.get("servers", [])
        if not servers or not isinstance(servers[0], dict):
            raise WorkflowRejected("unsupported_destination")
        base = servers[0].get("url")
        service_origin(base)
        parsed = urlsplit(base)
        if (
            parsed.query
            or parsed.fragment
            or "{" in base
            or "}" in base
            or servers[0].get("variables")
        ):
            raise WorkflowRejected("unsupported_destination")
        for path, item in document.get("paths", {}).items():
            for method, operation in item.items():
                if method not in METHODS or not isinstance(operation, dict):
                    continue
                identifier = operation.get("operationId")
                if not isinstance(identifier, str):
                    continue
                if identifier in index:
                    duplicates.add(identifier)
                index[identifier] = OperationBinding(
                    source_name, document, path, item, method.upper(), operation, base
                )
    return index, duplicates


def check_reference(
    value: Any,
    inputs: set[str] | None,
    previous: Mapping[str, set[str]],
    *,
    response: bool = False,
) -> None:
    """Accept only whole native references whose declared owners already exist."""
    if isinstance(value, dict):
        for item in value.values():
            check_reference(item, inputs, previous, response=response)
    elif isinstance(value, list):
        for item in value:
            check_reference(item, inputs, previous, response=response)
    elif isinstance(value, str) and "$" in value:
        if match := INPUT.fullmatch(value):
            if inputs is not None and match[1] not in inputs:
                raise WorkflowRejected("unknown_input_reference")
        elif match := STEP_OUTPUT.fullmatch(value):
            if match[1] not in previous or match[2] not in previous[match[1]]:
                raise WorkflowRejected("unknown_step_output_reference")
        elif response and value.startswith(POINTER_PREFIX):
            try:
                pointer = JsonPointer(value[len(POINTER_PREFIX) :])
                if not pointer.parts or any(token == "" for token in pointer.parts):
                    raise ValueError()
            except Exception:
                raise WorkflowRejected("unsupported_empty_pointer_token") from None
        else:
            raise WorkflowRejected("unsupported_expression")


def _check_sources(document: dict, workflow: dict, sources: Mapping[str, dict]) -> None:
    candidate = {
        key: value for key, value in document.items() if key != "x-discovery-exclusions"
    }
    candidate["workflows"] = [workflow]
    if (
        next(Draft202012Validator(PROFILE_SCHEMA).iter_errors(candidate), None)
        is not None
    ):
        raise WorkflowRejected("unsupported_workflow_structure")
    names = [item["name"] for item in document["sourceDescriptions"]]
    if len(names) != len(set(names)) or set(names) != set(sources):
        raise WorkflowRejected("unresolved_source")


def _check_input_schema(workflow: dict) -> set[str] | None:
    schema = workflow.get("inputs", {})
    local_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise WorkflowRejected("invalid_input_schema") from None
    # An absent or open schema permits explicit runtime inputs. A closed schema
    # constrains names here; runtime validation still checks every supplied value.
    if schema.get("additionalProperties") is False:
        return set(schema.get("properties", {}))
    return None


def _check_step_outputs(step: dict, inputs: set[str] | None, previous: dict) -> None:
    for criterion in step.get("successCriteria", []):
        if not (
            STATUS.fullmatch(criterion["condition"])
            or BOOLEAN.fullmatch(criterion["condition"])
        ):
            raise WorkflowRejected("unsupported_criterion")
    for name, expression in step.get("outputs", {}).items():
        if not IDENTIFIER.fullmatch(name) or not expression.startswith(POINTER_PREFIX):
            raise WorkflowRejected("unsupported_output")
        check_reference(expression, inputs, previous, response=True)


def _check_body(step: dict, info: OperationBinding) -> None:
    body = step.get("requestBody")
    if not body:
        return
    definition = resolved(info.document, info.operation.get("requestBody", {}))
    media = body["contentType"]
    if media not in definition.get("content", {}):
        raise WorkflowRejected("unsupported_content_type")
    contract = definition["content"][media]
    schema = resolved(info.document, contract.get("schema", {}))
    if media == "multipart/form-data":
        if (
            not CAPABILITIES["workflow"]["flat_multipart"]
            or schema.get("type") != "object"
        ):
            raise WorkflowRejected("unsupported_multipart")
        fields = [
            resolved(info.document, item)
            for item in schema.get("properties", {}).values()
        ]
        if any(
            item.get("type") not in {"string", "integer", "number", "boolean"}
            for item in fields
        ):
            raise WorkflowRejected("unsupported_multipart")
        if any(
            set(item) - {"contentType"}
            for item in contract.get("encoding", {}).values()
        ):
            raise WorkflowRejected("unsupported_multipart")
    elif "json" not in media and (schema.get("type"), schema.get("format")) != (
        "string",
        "binary",
    ):
        raise WorkflowRejected("unsupported_content_type")
    if "payload" in body and body["payload"] is None:
        validate_value(
            None,
            schema,
            document=info.document,
            dialect=info.document["openapi"],
            reason="invalid_request",
        )


def _check_step(
    step: dict, info: OperationBinding, inputs: set[str] | None, previous: dict
) -> None:
    try:
        request_features(info.document, info.path_item, info.operation, workflow=True)
    except ValueError:
        raise WorkflowRejected("unsupported_serialization") from None
    if info.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise WorkflowRejected("unsupported_method")
    check_reference(step.get("parameters", []), inputs, previous)
    check_reference(step.get("requestBody", {}), inputs, previous)
    parameters = info.parameters()
    for parameter in step.get("parameters", []):
        if (parameter["in"], parameter["name"]) not in parameters:
            raise WorkflowRejected("unknown_parameter")
    _check_step_outputs(step, inputs, previous)
    # Validate only this operation's dependency closure. An unrelated remote
    # component must not remove otherwise independent workflows.
    selected = {
        key: info.operation[key]
        for key in ("requestBody", "responses", "parameters")
        if key in info.operation
    }
    local_schema(selected, document=info.document)
    local_schema(info.path_item.get("parameters", []), document=info.document)
    _check_body(step, info)


def check_workflow(
    document: dict[str, Any],
    workflow: dict[str, Any],
    sources: Mapping[str, dict[str, Any]],
) -> dict[str, OperationBinding]:
    """Validate a workflow completely before constructing any native runner."""
    _check_sources(document, workflow, sources)
    inputs = _check_input_schema(workflow)
    operations, duplicates = operation_index(sources)
    previous: dict[str, set[str]] = {}
    selected = {}
    for step in workflow["steps"]:
        identifier = step["stepId"]
        if identifier in previous:
            raise WorkflowRejected("duplicate_step_id")
        operation_id = step["operationId"]
        if operation_id in duplicates:
            raise WorkflowRejected("ambiguous_operation")
        if operation_id not in operations:
            raise WorkflowRejected("unresolved_operation")
        info = operations[operation_id]
        _check_step(step, info, inputs, previous)
        previous[identifier] = set(step.get("outputs", {}))
        selected[identifier] = info
    for name, expression in workflow.get("outputs", {}).items():
        if not IDENTIFIER.fullmatch(name) or not STEP_OUTPUT.fullmatch(expression):
            raise WorkflowRejected("unsupported_output")
        check_reference(expression, inputs, previous)
    return selected


def admit_workflows(
    document: dict[str, Any],
    sources: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Exclude unsupported workflows independently and keep the safe source separate."""
    if not isinstance(document, dict) or not isinstance(
        document.get("workflows"), list
    ):
        return None, [
            {"workflow": "[omitted]", "reason": "unsupported_workflow_structure"}
        ]
    accepted, exclusions = [], list(document.get("x-discovery-exclusions", []))
    counts = Counter(
        item.get("workflowId")
        for item in document["workflows"]
        if isinstance(item, dict) and isinstance(item.get("workflowId"), str)
    )
    for workflow in document["workflows"]:
        identifier = workflow.get("workflowId") if isinstance(workflow, dict) else None
        try:
            if isinstance(identifier, str) and counts[identifier] > 1:
                raise WorkflowRejected("duplicate_workflow_id")
            check_workflow(document, workflow, sources)
            accepted.append(deepcopy(workflow))
        except (
            WorkflowRejected,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as error:
            reason = (
                str(error)
                if isinstance(error, WorkflowRejected)
                else "unsupported_workflow_structure"
            )
            exclusions.append(
                {
                    "workflow": identifier
                    if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier)
                    else "[omitted]",
                    "reason": reason,
                }
            )
    result = {
        key: deepcopy(value)
        for key, value in document.items()
        if key != "x-discovery-exclusions"
    }
    result["workflows"] = accepted
    return result, exclusions
