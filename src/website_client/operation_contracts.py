"""Compile named GraphQL operations without inventing a server schema.

This module is also copied into operation-document clients. It uses graphql-core
for parsing and scalar checks; unknown input types remain JSON values.
"""

from dataclasses import dataclass
import json
import keyword
import re
from typing import Any

from graphql import (
    GraphQLBoolean, GraphQLFloat, GraphQLID, GraphQLInt, GraphQLString,
    parse, parse_type, print_ast, value_from_ast_untyped,
)
from graphql.language.ast import Node

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_TOKENS = 20_000
_RESERVED = {"self", "client", "UNSET"}
_SCALARS = {
    "Boolean": GraphQLBoolean, "Float": GraphQLFloat, "ID": GraphQLID,
    "Int": GraphQLInt, "String": GraphQLString,
}


@dataclass(frozen=True, slots=True)
class Variable:
    name: str
    parameter: str
    type: str
    required: bool


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    method: str
    kind: str
    document: str
    variables: tuple[Variable, ...]

    @property
    def write(self):
        return self.kind == "mutation"


def nodes(value):
    if isinstance(value, Node):
        yield value
        for key in value.keys:
            if key not in {"loc", "variable_definitions"}:
                yield from nodes(getattr(value, key, None))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from nodes(item)


def method_name(name):
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    if keyword.iskeyword(value) or value.startswith("_") or value in {
        "close", "execute", "validate", "http_client", "headers", "url", "operations", "allow_writes",
    }:
        value = "operation_" + value.lstrip("_")
    return value


def compile_operations(sources: tuple[str, ...]) -> tuple[Operation, ...]:
    """Resolve fragments per source, deduplicate equivalent operations, fail closed."""
    if not sources or sum(len(text.encode()) for text in sources) > MAX_DOCUMENT_BYTES:
        raise ValueError("Operation documents are missing or exceed the 8 MiB limit.")
    operations = {}
    for source in sources:
        try:
            document = parse(source, max_tokens=MAX_DOCUMENT_TOKENS)
        except Exception:
            raise ValueError("Invalid GraphQL operation document.") from None
        fragments, selected = {}, []
        for definition in document.definitions:
            if definition.kind not in {"operation_definition", "fragment_definition"} or definition.name is None:
                raise ValueError("Only named GraphQL operations and fragments are supported.")
            name = definition.name.value
            if definition.kind == "fragment_definition":
                if name in fragments and print_ast(fragments[name]) != print_ast(definition):
                    raise ValueError("Conflicting GraphQL fragment definitions.")
                fragments[name] = definition
            else:
                if definition.operation.value not in {"query", "mutation"}:
                    raise ValueError("Subscriptions are not supported by this client backend.")
                selected.append(definition)
        for definition in selected:
            reached, active = set(), set()

            def visit(name):
                if name in active:
                    raise ValueError("GraphQL fragment cycle.")
                if name in reached:
                    return
                if name not in fragments:
                    raise ValueError("Undefined GraphQL fragment.")
                active.add(name)
                for item in nodes(fragments[name]):
                    if item.kind == "fragment_spread":
                        visit(item.name.value)
                active.remove(name)
                reached.add(name)

            for item in nodes(definition):
                if item.kind == "fragment_spread":
                    visit(item.name.value)
            parts = [definition] + [fragments[name] for name in sorted(reached)]
            used = {item.name.value for part in parts for item in nodes(part) if item.kind == "variable"}
            variables, names, parameters = [], set(), set()
            for variable in definition.variable_definitions:
                name = variable.variable.name.value
                if name in names:
                    raise ValueError("Duplicate GraphQL variable definition.")
                names.add(name)
                parameter = name
                while keyword.iskeyword(parameter) or parameter.startswith("__") or parameter in _RESERVED or parameter in parameters:
                    parameter = "argument_" + parameter
                parameters.add(parameter)
                if variable.default_value is not None:
                    validate_value(value_from_ast_untyped(variable.default_value), variable.type)
                variables.append(Variable(
                    name, parameter, print_ast(variable.type),
                    variable.type.kind == "non_null_type" and variable.default_value is None,
                ))
            if used - names:
                raise ValueError("Undefined GraphQL variable.")
            if names - used:
                raise ValueError("Unused GraphQL variable.")
            text = "\n\n".join(print_ast(part) for part in parts) + "\n"
            operation = Operation(
                definition.name.value, method_name(definition.name.value),
                definition.operation.value, text, tuple(variables),
            )
            prior = operations.get(operation.name)
            if prior is not None and prior != operation:
                raise ValueError("Conflicting GraphQL operation definitions.")
            operations[operation.name] = operation
    if not operations:
        raise ValueError("No named GraphQL operations were supplied.")
    result = tuple(operations[name] for name in sorted(operations))
    if len({item.method for item in result}) != len(result):
        raise ValueError("GraphQL operation names collide as Python method names.")
    return result


def validate_value(value: Any, type_node):
    if type_node.kind == "non_null_type":
        if value is None:
            raise ValueError("Input must satisfy its non-null GraphQL type.")
        return validate_value(value, type_node.type)
    if value is None:
        return
    if type_node.kind == "list_type":
        # GraphQL also permits singleton coercion; use the same semantics.
        for item in value if isinstance(value, list) else [value]:
            validate_value(item, type_node.type)
        return
    scalar = _SCALARS.get(type_node.name.value)
    if scalar is not None:
        try:
            if type_node.name.value in {"Int", "Float", "ID"} and type(value) is bool:
                raise ValueError()
            scalar.parse_value(value)
        except Exception:
            raise ValueError("Input must match its declared GraphQL scalar type.") from None


def validate_variables(operation: Operation, arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {item.parameter for item in operation.variables}
    if arguments.keys() - allowed:
        raise ValueError("Input has unknown fields; check operation help.")
    values = {}
    for variable in operation.variables:
        if variable.parameter not in arguments:
            if variable.required:
                raise ValueError("A required input is missing: " + variable.parameter)
            continue
        value = arguments[variable.parameter]
        validate_value(value, parse_type(variable.type))
        values[variable.name] = value
    try:
        encoded = json.dumps(values, allow_nan=False)
        if len(encoded.encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Input must contain bounded JSON values.") from None
    return values
