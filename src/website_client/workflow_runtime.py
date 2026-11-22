"""Run admitted linear Arazzo jobs through native builders and scoped HTTP contexts."""

from contextlib import contextmanager
import email.parser
import email.policy
import io
import json
import logging
import math
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from jsonpointer import JsonPointer, JsonPointerException

from .authenticated_http import (
    AuthenticationRequired,
    TransportFailure,
    workflow_session,
    profile_dispatches,
    LIMIT,
)
from .capabilities import resolved
from .runtime_profile import ProfileError, service_origin
from .workflow_admission import (
    OperationBinding,
    check_workflow,
    INPUT,
    POINTER_PREFIX,
    BOOLEAN,
    STATUS,
)
from .workflow_schema import WorkflowRejected, strict_json, validate_value


@contextmanager
def job_dispatches(endpoints: Mapping[str, str | None]):
    """Use the same locked profile lifecycle as generated SDK clients."""
    with profile_dispatches(endpoints) as dispatches:
        yield dispatches


class _Discard(io.TextIOBase):
    def write(self, text):
        return len(text)


_QUIET_LOCK = logging.getLogger("website-client.workflow.quiet").__dict__.setdefault(
    "credential_output_lock", threading.RLock()
)


@contextmanager
def quiet_runner():
    """Serialize native suppression across copied runtimes and restore global state."""
    from contextlib import redirect_stderr, redirect_stdout

    with _QUIET_LOCK:
        saved = []
        try:
            for name in ("arazzo-runner", "arazzo_runner"):
                logger = logging.getLogger(name)
                saved.append((logger, logger.handlers, logger.propagate))
                logger.handlers, logger.propagate = [logging.NullHandler()], False
            with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
                yield
        finally:
            for logger, handlers, propagate in saved:
                logger.handlers, logger.propagate = handlers, propagate


def bounded_values(value: Any, depth: int = 0) -> None:
    """Reject arbitrary objects and bound binary inputs before native coercion."""
    if depth > 40:
        raise WorkflowRejected("input_depth_exceeded")
    if isinstance(value, bytes):
        if len(value) > LIMIT:
            raise WorkflowRejected("file_exceeds_8_mib")
    elif type(value) is dict:
        if any(type(key) is not str for key in value):
            raise WorkflowRejected("unsupported_input_object")
        for item in value.values():
            bounded_values(item, depth + 1)
    elif type(value) is list:
        for item in value:
            bounded_values(item, depth + 1)
    elif value is None or type(value) in {str, int, bool}:
        return
    elif type(value) is float and math.isfinite(value):
        return
    else:
        raise WorkflowRejected("unsupported_input_object")


def referenced_inputs(value: Any) -> set[str]:
    """List admitted whole input references without evaluating their values."""
    if isinstance(value, dict):
        return (
            set().union(*(referenced_inputs(item) for item in value.values()))
            if value
            else set()
        )
    if isinstance(value, list):
        return (
            set().union(*(referenced_inputs(item) for item in value))
            if value
            else set()
        )
    match = INPUT.fullmatch(value) if isinstance(value, str) else None
    return {match[1]} if match else set()


def _scalar(value, schema):
    kind = schema.get("type")
    if kind in {"integer", "number", "boolean"}:
        return strict_json(value, "invalid_request")
    return value


class WorkflowGuard:
    """Validate native prepared bytes and producing responses before the next step."""

    def __init__(
        self,
        workflow: dict,
        operations: dict[str, OperationBinding],
        dispatches: Mapping[str, Any],
    ) -> None:
        self.workflow, self.operations, self.dispatches = (
            workflow,
            operations,
            dispatches,
        )
        self.active = None
        self.requests = self.responses = self.assertions = 0
        self.step_requests = 0
        self.failure = None
        self.writes = False
        self.completed = []
        self.runner = None
        self.sources = None
        self.expected_payload = None

    def start(self, execution_id: str, workflow_id: str, step_id: str) -> None:
        """Prepare checks at the native callback boundary; dispatch owns failure propagation."""
        self.active = next(
            step for step in self.workflow["steps"] if step["stepId"] == step_id
        )
        self.step_requests = 0
        try:
            self._before_coercion(self.runner.execution_states[execution_id])
        except Exception as error:
            # Native callbacks swallow exceptions. Retain the failure so the adapter
            # blocks dispatch even if the builder subsequently stringifies a value.
            self.failure = (
                error
                if isinstance(error, WorkflowRejected)
                else WorkflowRejected("invalid_request")
            )

    def _before_coercion(self, state: Any) -> None:
        info = self.operations[self.active["stepId"]]
        self._native_parameters(state, info)
        step_body = self.active.get("requestBody")
        if step_body is None or "payload" not in step_body:
            self.expected_payload = None
            return
        payload = self._native_payload(step_body, state)
        bounded_values(payload)
        self.expected_payload = deepcopy(payload)
        body_definition = resolved(info.document, info.operation.get("requestBody", {}))
        media = step_body["contentType"]
        contract = body_definition.get("content", {}).get(media, {})
        schema = resolved(info.document, contract.get("schema", {}))
        if media == "multipart/form-data":
            self._multipart_values(payload, schema, info)
        elif "json" in media:
            self._validate(payload, schema, info, "invalid_request")
            try:
                json.dumps(payload, allow_nan=False)
            except (TypeError, ValueError):
                raise WorkflowRejected("invalid_request") from None
        elif not isinstance(payload, bytes):
            raise WorkflowRejected("invalid_binary_input")

    def _native_parameters(self, state: Any, info: OperationBinding) -> None:
        from arazzo_runner.executor.parameter_processor import ParameterProcessor

        parameter_values = ParameterProcessor(self.sources).prepare_parameters(
            self.active, state
        )
        for (location, name), definition in self._parameters(info).items():
            if name in parameter_values.get(location, {}):
                value = parameter_values[location][name]
                if location == "path" and (
                    not re.fullmatch(r"[A-Za-z0-9._~-]+", str(value))
                    or str(value) in {".", ".."}
                ):
                    raise WorkflowRejected("unsupported_path_value")
                bounded_values(value)
                self._validate(
                    value, definition.get("schema", {}), info, "invalid_request"
                )

    def _native_payload(self, step_body: dict, state: Any) -> Any:
        # Reuse upstream expression resolution before native builders coerce scalar
        # multipart values to text. This guard never evaluates expressions itself.
        from arazzo_runner.evaluator import ExpressionEvaluator

        payload = step_body["payload"]
        if isinstance(payload, dict):
            payload = ExpressionEvaluator.process_object_expressions(
                payload, state, self.sources
            )
        elif isinstance(payload, list):
            payload = ExpressionEvaluator.process_array_expressions(
                payload, state, self.sources
            )
        elif isinstance(payload, str) and payload.startswith("$"):
            payload = ExpressionEvaluator.evaluate_expression(
                payload, state, self.sources
            )
        return payload

    def _multipart_values(
        self, payload: Any, schema: dict, info: OperationBinding
    ) -> None:
        if not isinstance(payload, dict):
            raise WorkflowRejected("unsupported_multipart")
        values = {}
        for name, value in payload.items():
            field = resolved(info.document, schema.get("properties", {}).get(name, {}))
            if field.get("format") == "binary":
                content = value.get("content") if isinstance(value, dict) else value
                if not isinstance(content, bytes):
                    raise WorkflowRejected("invalid_multipart_file")
                if isinstance(value, dict) and (
                    set(value) - {"content", "file_name", "contentType"}
                    or not isinstance(value.get("file_name"), str)
                    or not isinstance(
                        value.get("contentType", "application/octet-stream"), str
                    )
                ):
                    raise WorkflowRejected("invalid_multipart_file")
                values[name] = ""
            else:
                if value is not None and type(value) not in {str, int, float, bool}:
                    raise WorkflowRejected("invalid_request")
                self._validate(value, field, info, "invalid_request")
                values[name] = value
        self._validate(values, schema, info, "invalid_request")

    def _parameters(self, info):
        return info.parameters()

    def _validate(self, value, schema, info, reason):
        validate_value(
            value,
            schema,
            document=info.document,
            dialect=info.document["openapi"],
            reason=reason,
        )

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | str | None,
    ) -> None:
        """Check native serialized bytes immediately before the single dispatch."""
        if self.active is None or self.step_requests:
            raise WorkflowRejected("unexpected_request")
        info = self.operations[self.active["stepId"]]
        if method.upper() != info.method or service_origin(url) != service_origin(
            info.base
        ):
            raise WorkflowRejected("undeclared_destination")
        path_values = self._path_values(info, url)
        header_values = self._request_parameters(info, url, headers, path_values)
        self._request_body(info, header_values, body)
        self.step_requests += 1
        self.requests += 1
        self.writes = self.writes or method.upper() not in {"GET", "HEAD", "OPTIONS"}

    def _path_values(self, info: OperationBinding, url: str) -> dict[str, str]:
        actual, template = urlsplit(url), urlsplit(info.base + info.path)
        pattern, names = "", []
        cursor = 0
        for match in re.finditer(r"\{([^{}]+)\}", template.path):
            pattern += re.escape(template.path[cursor : match.start()]) + r"([^/]+)"
            names.append(match[1])
            cursor = match.end()
        pattern += re.escape(template.path[cursor:])
        matched = re.fullmatch(pattern, actual.path)
        if matched is None:
            raise WorkflowRejected("unexpected_path")
        path_values = dict(zip(names, matched.groups()))
        for value in path_values.values():
            if not re.fullmatch(r"[A-Za-z0-9._~-]+", value) or value in {".", ".."}:
                raise WorkflowRejected("unsupported_path_value")
        return path_values

    def _request_parameters(
        self,
        info: OperationBinding,
        url: str,
        headers: Mapping[str, str],
        path_values: dict[str, str],
    ) -> dict[str, str]:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        header_values = {name.lower(): value for name, value in headers.items()}
        for (location, name), parameter in self._parameters(info).items():
            schema = resolved(info.document, parameter.get("schema", {}))
            value = (
                path_values.get(name)
                if location == "path"
                else query.get(name)
                if location == "query"
                else header_values.get(name.lower())
            )
            if value is None:
                if parameter.get("required"):
                    raise WorkflowRejected("missing_required_parameter")
                continue
            if location == "query":
                if schema.get("type") == "array":
                    value = [
                        _scalar(item, resolved(info.document, schema.get("items", {})))
                        for item in value
                    ]
                elif len(value) != 1:
                    raise WorkflowRejected("invalid_request")
                else:
                    value = _scalar(value[0], schema)
            else:
                value = _scalar(value, schema)
            self._validate(value, schema, info, "invalid_request")
        return header_values

    def _request_body(
        self,
        info: OperationBinding,
        header_values: Mapping[str, str],
        body: bytes | str | None,
    ) -> None:
        raw = body.encode() if isinstance(body, str) else body or b""
        if not isinstance(raw, bytes) or len(raw) > LIMIT:
            raise WorkflowRejected("request_exceeds_8_mib")
        definition = resolved(info.document, info.operation.get("requestBody", {}))
        if definition:
            if not raw and definition.get("required"):
                raise WorkflowRejected("missing_required_body")
            if raw:
                media = (
                    header_values.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                contract = definition.get("content", {}).get(media)
                if contract is None:
                    raise WorkflowRejected("unsupported_content_type")
                schema = resolved(info.document, contract.get("schema", {}))
                if "json" in media:
                    value = strict_json(raw, "malformed_request_json")
                    self._validate(value, schema, info, "invalid_request")
                    if json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(
                        self.expected_payload, sort_keys=True, allow_nan=False
                    ):
                        raise WorkflowRejected("unsupported_native_mapping")
                elif media == "multipart/form-data":
                    self._multipart(
                        raw, header_values["content-type"], schema, contract, info
                    )
                elif schema.get("type") != "string" or schema.get("format") != "binary":
                    raise WorkflowRejected("unsupported_content_type")
        elif raw:
            raise WorkflowRejected("unexpected_request_body")

    def _multipart(self, raw, content_type, schema, media, info):
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            ("Content-Type: " + content_type + "\r\n\r\n").encode() + raw
        )
        if not message.is_multipart():
            raise WorkflowRejected("invalid_multipart")
        values = {}
        properties = schema.get("properties", {})
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not isinstance(name, str) or name in values or part.is_multipart():
                raise WorkflowRejected("unsupported_multipart")
            content = part.get_payload(decode=True)
            field = resolved(info.document, properties.get(name, {}))
            if field.get("format") == "binary":
                if not part.get_filename() or content is None or len(content) > LIMIT:
                    raise WorkflowRejected("invalid_multipart_file")
                expected = media.get("encoding", {}).get(name, {}).get("contentType")
                if expected and part.get_content_type() != expected:
                    raise WorkflowRejected("invalid_multipart_media")
                # Binary's native wire representation is bytes, not a JSON string.
                # Requiredness and the rest of the object are still checked below.
                expected_value = self.expected_payload.get(name)
                expected_content = (
                    expected_value.get("content")
                    if isinstance(expected_value, dict)
                    else expected_value
                )
                if content != expected_content:
                    raise WorkflowRejected("invalid_multipart_file")
                if isinstance(expected_value, dict) and (
                    part.get_filename() != expected_value["file_name"]
                    or part.get_content_type()
                    != expected_value.get("contentType", "application/octet-stream")
                ):
                    raise WorkflowRejected("invalid_multipart_file")
                values[name] = ""
            else:
                if part.get_filename() is not None:
                    raise WorkflowRejected("unsupported_multipart")
                try:
                    values[name] = _scalar(content.decode("utf-8"), field)
                except (UnicodeDecodeError, AttributeError):
                    raise WorkflowRejected("invalid_multipart") from None
        self._validate(values, schema, info, "invalid_request")

    def response(self, status: int, headers: list[tuple[str, str]], raw: bytes) -> None:
        """Validate producing outputs and assertions before dependent steps advance."""
        self.responses += 1
        info = self.operations[self.active["stepId"]]
        definitions = info.operation.get("responses", {})
        response = resolved(
            info.document,
            definitions.get(
                str(status),
                definitions.get(str(status)[0] + "XX", definitions.get("default", {})),
            ),
        )
        media_types = response.get("content", {})
        actual_media = next(
            (
                value.split(";", 1)[0].strip().lower()
                for name, value in headers
                if name.lower() == "content-type"
            ),
            "",
        )
        contract = media_types.get(actual_media)
        needs_json = (
            bool(self.active.get("outputs"))
            or any(
                BOOLEAN.fullmatch(criterion["condition"])
                for criterion in self.active.get("successCriteria", [])
            )
            or bool(contract and "json" in actual_media)
        )
        body = strict_json(raw, "malformed_response_json") if needs_json else None
        if media_types and contract is None:
            raise WorkflowRejected("unexpected_response_type")
        if contract and "json" in actual_media:
            self._validate(body, contract.get("schema", {}), info, "invalid_response")
        for expression in self.active.get("outputs", {}).values():
            try:
                value = JsonPointer(expression[len(POINTER_PREFIX) :]).resolve(body)
            except JsonPointerException:
                raise WorkflowRejected("missing_required_output") from None
            if value is None:
                raise WorkflowRejected("null_required_output")
        for criterion in self.active.get("successCriteria", []):
            condition = criterion["condition"]
            if match := STATUS.fullmatch(condition):
                passed = status == int(match[1])
            else:
                match = BOOLEAN.fullmatch(condition)
                if not isinstance(body, dict) or type(body.get(match[1])) is not bool:
                    raise WorkflowRejected("criterion_requires_boolean")
                passed = body[match[1]] is (match[2] == "true")
            self.assertions += 1
            if not passed:
                raise WorkflowRejected("assertion_failed")
        if not self.active.get("successCriteria") and not 200 <= status < 300:
            raise WorkflowRejected("http_status_failure")
        self.completed.append(self.active["stepId"])

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | str | None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        """Dispatch once after validation, retaining any failure for native callbacks."""
        try:
            if self.failure is not None:
                raise self.failure
            self.request(method, url, headers, body)
            selected = service_origin(url)
            if selected not in self.dispatches:
                raise WorkflowRejected("undeclared_destination")
            result = self.dispatches[selected].send(method, url, headers, body)
            self.response(*result)
            return result
        except Exception as error:
            self.failure = error
            raise


def _no_external_credentials():
    from arazzo_runner.auth.credentials.fetch import FetchStrategy
    from arazzo_runner.auth.credentials.provider import CredentialProvider

    class ProfilesOnly(FetchStrategy):
        def fetch(self, requests, options=None):
            return []

        def fetch_one(self, request, options=None):
            return []

        def set_auth_requirements(self, requirements):
            pass

    return CredentialProvider(strategy=ProfilesOnly())


@dataclass(frozen=True)
class WorkflowPlan:
    """Validated integration inputs; native Arazzo/OpenAPI models retain ownership."""

    workflow: dict[str, Any] = field(repr=False)
    operations: dict[str, OperationBinding] = field(repr=False)
    inputs: dict[str, Any] = field(repr=False)
    endpoints: dict[str, str | None] = field(repr=False)

    @classmethod
    def prepare(
        cls,
        document: dict,
        sources: Mapping[str, dict],
        workflow_id: str,
        inputs: dict | None,
        profiles: Mapping[str, str] | None,
    ) -> "WorkflowPlan":
        workflows = [
            item
            for item in document.get("workflows", [])
            if item.get("workflowId") == workflow_id
        ]
        if len(workflows) != 1:
            raise WorkflowRejected("unknown_workflow")
        workflow = workflows[0]
        operations = check_workflow(document, workflow, sources)
        supplied = {} if inputs is None else inputs
        if type(supplied) is not dict:
            raise WorkflowRejected("invalid_inputs")
        bounded_values(supplied)
        validate_value(supplied, workflow.get("inputs", {}))
        if referenced_inputs(workflow) - supplied.keys():
            raise WorkflowRejected("missing_input")
        endpoints = {
            info.base: (profiles or {}).get(service_origin(info.base))
            for info in operations.values()
        }
        return cls(workflow, operations, supplied, endpoints)


@dataclass
class ExecutionSummary:
    """Bounded public outcome; private native events and exceptions never escape."""

    workflow: str = field(repr=False)
    status: str = "rejected"
    reason: str = "unknown_workflow"
    requests: int = 0
    responses: int = 0
    assertions: int = 0
    effects: str = "none"
    outputs: dict[str, Any] = field(default_factory=dict, repr=False)

    def fail(self, reason: str, guard: WorkflowGuard | None) -> None:
        self.status = "failure" if guard and guard.requests else "rejected"
        self.reason = reason

    def finish(self, guard: WorkflowGuard | None) -> dict[str, Any]:
        if guard is not None:
            self.requests, self.responses, self.assertions = (
                guard.requests,
                guard.responses,
                guard.assertions,
            )
            if guard.writes:
                self.effects = (
                    "unknown" if guard.requests > guard.responses else "possible"
                )
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Preserve the public mapping API without serializing private job state."""
        return {
            name: getattr(self, name)
            for name in (
                "status",
                "reason",
                "workflow",
                "requests",
                "responses",
                "assertions",
                "effects",
                "outputs",
            )
        }


def _drive_native(
    runner: Any, plan: WorkflowPlan, workflow_id: str, guard: WorkflowGuard
) -> dict[str, Any]:
    """Advance only the native scheduler and stop on every failed native state."""
    runner.register_callback("step_start", guard.start)
    execution = runner.start_workflow(workflow_id, plan.inputs)
    for _ in range(len(plan.workflow["steps"]) + 1):
        event = runner.execute_next_step(execution)
        state = runner.execution_states[execution]
        failed = any(
            getattr(value, "value", value) == "failure"
            for value in state.status.values()
        )
        status = str(event["status"])
        if guard.failure is not None:
            raise guard.failure
        if failed or status in {
            "step_error",
            "error",
            "retry",
            "goto_step",
            "goto_workflow",
        }:
            raise WorkflowRejected("upstream_workflow_failure")
        if status == "workflow_complete":
            if len(guard.completed) != len(plan.workflow["steps"]):
                raise WorkflowRejected("incomplete_workflow")
            return event.get("outputs", {})
    raise WorkflowRejected("incomplete_workflow")


def execute_document(
    document: dict[str, Any],
    sources: Mapping[str, dict[str, Any]],
    workflow_id: str,
    inputs: dict[str, Any] | None = None,
    profiles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one admitted workflow and return a safe, machine-readable summary.

    Upstream owns scheduling, expressions, and serialization. This boundary owns
    validation, private profiles, exact-byte dispatch, and failure reporting.
    """
    from arazzo_runner import ArazzoRunner

    guard = None
    summary = ExecutionSummary(workflow_id)
    try:
        plan = WorkflowPlan.prepare(document, sources, workflow_id, inputs, profiles)
        with job_dispatches(plan.endpoints) as dispatches, quiet_runner():
            guard = WorkflowGuard(plan.workflow, plan.operations, dispatches)
            with workflow_session(guard) as session:
                runner = ArazzoRunner(
                    arazzo_doc=document,
                    source_descriptions=sources,
                    http_client=session,
                    auth_provider=_no_external_credentials(),
                    blob_store=None,
                )
                guard.runner, guard.sources = runner, sources
                summary.outputs = _drive_native(runner, plan, workflow_id, guard)
                summary.status, summary.reason = "success", "workflow_completed"
    except KeyboardInterrupt:
        summary.status, summary.reason = "cancelled", "cancelled"
    except AuthenticationRequired:
        summary.status, summary.reason = "failure", "authentication_required"
    except TransportFailure:
        summary.status, summary.reason, summary.effects = (
            "failure",
            "transport_failure",
            "unknown",
        )
    except (WorkflowRejected, ProfileError) as error:
        summary.fail(str(error), guard)
    except Exception:
        summary.fail("workflow_runtime_failure", guard)
    return summary.finish(guard)
