"""Non-fetching, dialect-correct schema validation at workflow wire boundaries."""

from copy import deepcopy
from jsonpointer import resolve_pointer, JsonPointerException
import json
from typing import Any, NoReturn

from jsonschema import Draft202012Validator
from openapi_schema_validator import OAS30Validator, OAS31Validator
from referencing import Registry
from referencing.exceptions import NoSuchResource


class WorkflowRejected(ValueError):
    """A fixed reason code safe to return without source values or validator details."""


def no_fetch(uri: str) -> NoReturn:
    """Prohibit external resolution even if a validator sees an unknown reference."""
    raise NoSuchResource(ref=uri)


REGISTRY = Registry(retrieve=no_fetch)


def local_schema(
    value: Any,
    depth: int = 0,
    context: str | None = None,
    document: dict[str, Any] | None = None,
    seen: set[str] | None = None,
) -> None:
    """Validate the selected local reference closure without fetching unused definitions."""
    seen = set() if seen is None else seen
    if depth > 80:
        raise WorkflowRejected("unsupported_schema_depth")
    if context in {"enum", "const", "default", "example", "examples"}:
        return
    maps = {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "schemas",
        "parameters",
        "responses",
        "headers",
        "paths",
        "requestBodies",
        "securitySchemes",
    }
    if isinstance(value, dict):
        if context in maps:
            for item in value.values():
                local_schema(item, depth + 1, document=document, seen=seen)
            return
        for key, item in value.items():
            if key in {
                "$id",
                "$dynamicRef",
                "$dynamicAnchor",
                "$recursiveRef",
                "$recursiveAnchor",
                "$vocabulary",
                "$anchor",
            }:
                raise WorkflowRejected("unsupported_schema_reference")
            if key == "$ref":
                if not isinstance(item, str) or not item.startswith("#/"):
                    raise WorkflowRejected("external_reference")
                if document is not None and item not in seen:
                    seen.add(item)
                    try:
                        target = resolve_pointer(document, item[1:])
                    except JsonPointerException:
                        raise WorkflowRejected("unresolved_reference") from None
                    local_schema(target, depth + 1, document=document, seen=seen)
            local_schema(item, depth + 1, key, document, seen)
    elif isinstance(value, list):
        for item in value:
            local_schema(item, depth + 1, document=document, seen=seen)


def validate_value(
    value: Any,
    schema: Any,
    *,
    document: dict[str, Any] | None = None,
    dialect: str = "2020-12",
    reason: str = "invalid_inputs",
) -> None:
    """Resolve only bundled local pointers through standard schema validators."""
    try:
        local_schema(schema, document=document)
        if document is not None:
            root = deepcopy(document)
            root["x-website-client-validation"] = schema
            root["$ref"] = "#/x-website-client-validation"
        else:
            root = schema
        validator = (
            OAS30Validator
            if dialect.startswith("3.0.")
            else OAS31Validator
            if dialect.startswith("3.1.")
            else Draft202012Validator
        )
        if (
            next(validator(root, registry=REGISTRY).iter_errors(value), None)
            is not None
        ):
            raise WorkflowRejected(reason)
    except WorkflowRejected:
        raise
    except Exception:
        raise WorkflowRejected(reason) from None


def strict_json(raw: bytes | str, reason: str = "malformed_json") -> Any:
    """Decode actual JSON, excluding nonstandard NaN and infinity constants."""

    def invalid_constant(_):
        raise ValueError()

    try:
        return json.loads(raw, parse_constant=invalid_constant)
    except Exception:
        raise WorkflowRejected(reason) from None
