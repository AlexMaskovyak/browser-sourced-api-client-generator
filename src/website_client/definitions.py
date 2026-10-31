"""Validate real service definitions without resolving external references."""

import json
from pathlib import Path

import yaml
from graphql import build_client_schema, build_schema, print_schema, validate_schema
from openapi_spec_validator import validate as validate_openapi

from .browser import BrowserError

MAX_DOCUMENT = 8 * 1024 * 1024


def read_document(path):
    try:
        path = Path(path)
        if path.stat().st_size > MAX_DOCUMENT:
            raise ValueError()
        text = path.read_text(encoding="utf-8")
        return text
    except Exception:
        raise BrowserError("Definition could not be read or exceeds the 8 MiB limit.") from None


def no_external_refs(value, depth=0):
    if depth > 80:
        raise BrowserError("Definition exceeds the supported nesting limit.")
    if isinstance(value, dict):
        if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
            raise BrowserError("Bundle external references into the definition before importing it.")
        for item in value.values(): no_external_refs(item, depth + 1)
    elif isinstance(value, list):
        for item in value: no_external_refs(item, depth + 1)


def openapi_document(value):
    try:
        if isinstance(value, str): value = yaml.safe_load(value)
        if not isinstance(value, dict) or not str(value.get("openapi", "")).startswith(("3.0.", "3.1.")):
            raise BrowserError("Supply an OpenAPI 3.0 or 3.1 definition.")
        no_external_refs(value)
        validate_openapi(value)
        return value
    except BrowserError:
        raise
    except Exception:
        raise BrowserError("OpenAPI definition is invalid. Validate or bundle the source document first.") from None


def graphql_schema(value):
    try:
        if isinstance(value, str):
            if value.lstrip().startswith("{"):
                value = json.loads(value)
            else:
                schema = build_schema(value)
        if isinstance(value, dict):
            if value.get("errors"):
                raise ValueError()
            data = value.get("data", value)
            if not isinstance(data, dict) or not isinstance(data.get("__schema"), dict):
                raise ValueError()
            schema = build_client_schema(data)
        if validate_schema(schema): raise ValueError()
        return print_schema(schema) + "\n"
    except Exception:
        raise BrowserError("Supply a valid complete GraphQL schema or successful introspection response. Operations alone are not a schema.") from None
