"""Convert command JSON into native SDK inputs, owning file handles per command."""

import ast
from datetime import date, datetime
from dataclasses import dataclass, field
from contextlib import ExitStack
from typing import Any
from enum import Enum
import inspect
import importlib
import os
from pathlib import Path
import stat
import textwrap
import types
import typing

LIMIT = 8 * 1024 * 1024


def _json_value(value):
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if type(value) is list:
        return [_json_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("Input contains an unsupported object")


@dataclass(frozen=True)
class FileDescriptor:
    """Validated command input; the command's ExitStack owns the opened stream."""

    path: Path = field(repr=False)
    filename: str = field(repr=False)
    content_type: str = field(repr=False)

    def __post_init__(self) -> None:
        metadata = (self.filename, self.content_type)
        if not isinstance(self.path, Path) or not all(
            isinstance(item, str) and item and "\r" not in item and "\n" not in item
            for item in metadata
        ):
            raise ValueError("Input file metadata is invalid")

    @classmethod
    def parse(cls, value: Any) -> "FileDescriptor":
        if (
            not isinstance(value, dict)
            or set(value) - {"path", "filename", "content_type"}
            or "path" not in value
        ):
            raise ValueError("Input file requires path and a command-scoped lifetime")
        if not isinstance(value["path"], str):
            raise ValueError("Input file path must be a string")
        path = Path(value["path"])
        return cls(
            path,
            value.get("filename", path.name),
            value.get("content_type", "application/octet-stream"),
        )


def _file(value: Any, annotation: type, stack: ExitStack | None) -> Any:
    if stack is None:
        raise ValueError("Input file requires path and a command-scoped lifetime")
    descriptor = FileDescriptor.parse(value)
    path, filename, content_type = (
        descriptor.path,
        descriptor.filename,
        descriptor.content_type,
    )
    # Validate the opened descriptor, rather than trusting a prior path stat.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    stream = stack.enter_context(os.fdopen(descriptor, "rb"))
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        raise ValueError("Input file exceeds 8 MiB or is not a regular file")
    if annotation.__name__ == "File":
        return annotation(payload=stream, file_name=filename, mime_type=content_type)
    return annotation(filename=filename, content=stream, content_type=content_type)


def _attrs_aliases(annotation, names):
    """Read wire names from the pinned upstream serializer, never guess casing."""
    aliases = {}
    tree = ast.parse(textwrap.dedent(inspect.getsource(annotation.to_dict)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Name)
                    and value.id in names
                ):
                    aliases[key.value] = value.id
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in names
        ):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "field_dict"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    aliases[target.slice.value] = node.value.id
    if set(aliases.values()) != names:
        raise ValueError("Unsupported native model serializer")
    return aliases


def _native_file_type(annotation):
    """Identify the pinned upstream runtime class, excluding same-named models."""
    if not inspect.isclass(annotation):
        return False
    module_name = annotation.__module__
    expected_module = {"File": "types", "Upload": "base_model"}.get(annotation.__name__)
    if expected_module is None or module_name.rsplit(".", 1)[-1] != expected_module:
        return False
    module = importlib.import_module(module_name)
    return getattr(module, annotation.__name__, None) is annotation


def convert(value: Any, annotation: Any, stack: ExitStack | None = None) -> Any:
    """Use upstream model constructors and serializers with exact typed leaves."""
    if annotation in {typing.Any, inspect.Parameter.empty}:
        return _json_value(value)
    base = typing.get_origin(annotation)
    if base is typing.Annotated:
        return convert(value, typing.get_args(annotation)[0], stack)
    if base in {typing.Union, types.UnionType}:
        for member in typing.get_args(annotation):
            try:
                return convert(value, member, stack)
            except (TypeError, ValueError, KeyError):
                pass
        raise ValueError("Input does not match its declared type")
    if annotation is type(None):
        if value is not None:
            raise ValueError("Input must be null")
        return None
    if base is list:
        if not isinstance(value, list):
            raise ValueError("Input must be a list")
        return [convert(item, typing.get_args(annotation)[0], stack) for item in value]
    if base is dict:
        if not isinstance(value, dict):
            raise ValueError("Input must be an object")
        args = typing.get_args(annotation)
        return {
            convert(k, args[0], stack): convert(v, args[1], stack)
            for k, v in value.items()
        }
    if base is typing.Literal:
        if not any(
            type(value) is type(item) and value == item
            for item in typing.get_args(annotation)
        ):
            raise ValueError("Input is not a declared literal")
        return value
    if annotation in {date, datetime}:
        if not isinstance(value, str):
            raise ValueError("Input must be a date string")
        return annotation.fromisoformat(value)
    if annotation in {str, int, float, bool}:
        if type(value) is not annotation and not (
            annotation is float and type(value) is int
        ):
            raise ValueError("Input has an invalid scalar type")
        return value
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return annotation(value)
    if _native_file_type(annotation):
        return _file(value, annotation, stack)
    if hasattr(annotation, "model_fields"):
        return _pydantic_model(value, annotation, stack)
    if hasattr(annotation, "__attrs_attrs__") and hasattr(annotation, "from_dict"):
        return _attrs_model(value, annotation, stack)
    raise ValueError("Unsupported input type; use the generated Python API")


def _pydantic_model(value: Any, annotation: type, stack: ExitStack | None) -> Any:
    """Keep Ariadne aliases and omission semantics in its native constructor."""
    if not isinstance(value, dict):
        raise ValueError("Input model must be an object")
    fields = annotation.model_fields
    aliases = {field.alias or name: name for name, field in fields.items()}
    aliases.update({name: name for name in fields})
    if set(value) - aliases.keys():
        raise ValueError("Input has unknown model fields")
    converted = {}
    for key, item in value.items():
        name = aliases[key]
        if name in converted:
            raise ValueError("Input contains duplicate model aliases")
        converted[name] = convert(item, fields[name].annotation, stack)
    return annotation.model_validate(converted)


def _attrs_model(value: Any, annotation: type, stack: ExitStack | None) -> Any:
    """Keep OpenAPI aliases, defaults, and additional properties in native models."""
    if not isinstance(value, dict):
        raise ValueError("Input model must be an object")
    fields = {field.name: field for field in annotation.__attrs_attrs__ if field.init}
    aliases = _attrs_aliases(annotation, set(fields))
    hints = typing.get_type_hints(annotation)
    extra = {key: item for key, item in value.items() if key not in aliases}
    if extra and "additional_properties" not in hints:
        raise ValueError("Input has unknown model fields")
    arguments = {
        aliases[key]: convert(item, hints[aliases[key]], stack)
        for key, item in value.items()
        if key in aliases
    }
    model = annotation(**arguments)
    if extra:
        model.additional_properties = convert(
            extra, hints["additional_properties"], stack
        )
    return model
