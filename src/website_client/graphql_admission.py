"""Safe schema-only root selection using Ariadne's native operation builders."""

from copy import copy
from typing import Any

from graphql import (
    build_schema,
    get_named_type,
    GraphQLEnumType,
    GraphQLObjectType,
    GraphQLScalarType,
    Undefined,
    parse,
    print_ast,
    is_non_null_type,
)
from graphql.language import DocumentNode, Node, Visitor, visit

from .admission import sensitive
from .browser import BrowserError
from .definitions import graphql_schema

MAX_LEAF_FIELDS = 20


class _RemoveDescriptions(Visitor):
    def enter(self, node: Node, *_: Any) -> Node | None:
        if hasattr(node, "description") and node.description is not None:
            node = copy(node)
            node.description = None
            return node
        return None


class _NamedTypes(Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()

    def enter_named_type(self, node: Node, *_: Any) -> None:
        self.names.add(node.name.value)


def _root_names(document: DocumentNode) -> set[str]:
    roots = {"Query", "Mutation", "Subscription"}
    for node in document.definitions:
        if node.kind == "schema_definition":
            roots.update(
                operation.type.name.value for operation in node.operation_types
            )
    return roots


def _redact_definitions(
    document: DocumentNode, roots: set[str]
) -> tuple[list[Node], list[dict], set[str]]:
    definitions, excluded, unsafe = [], [], set()
    for node in document.definitions:
        if getattr(node, "name", None) is not None and sensitive(node.name.value):
            unsafe.add(node.name.value)
            continue
        if hasattr(node, "fields"):
            fields = []
            for field in node.fields:
                if sensitive(print_ast(field)):
                    if node.kind == "input_object_type_definition":
                        unsafe.add(node.name.value)
                    if node.name.value in roots:
                        name = (
                            "[omitted]"
                            if sensitive(field.name.value)
                            else field.name.value
                        )
                        excluded.append(
                            {"operation": name, "reason": "sensitive_structure"}
                        )
                else:
                    fields.append(field)
            node = copy(node)
            node.fields = tuple(fields)
            if not fields:
                unsafe.add(node.name.value)
        elif sensitive(print_ast(node)):
            unsafe.add(node.name.value)
        definitions.append(node)
    return definitions, excluded, unsafe


def _closed_fields(
    node: Node, unsafe: set[str], roots: set[str], excluded: list[dict]
) -> Node | None:
    fields = []
    for field in node.fields:
        references = _NamedTypes()
        visit(field, references)
        if unsafe.intersection(references.names):
            if node.name.value in roots:
                excluded.append(
                    {"operation": field.name.value, "reason": "sensitive_structure"}
                )
        else:
            fields.append(field)
    if not fields or (
        node.kind == "input_object_type_definition" and len(fields) != len(node.fields)
    ):
        unsafe.add(node.name.value)
        return None
    result = copy(node)
    result.fields = tuple(fields)
    return result


def _schema_closure(
    definitions: list[Node], unsafe: set[str], roots: set[str], excluded: list[dict]
) -> list[Node]:
    # A type's interfaces are part of its wire contract. Removing an interface
    # declaration without its dependent types would invalidate independent roots.
    changed = True
    while changed:
        changed = False
        output = []
        for node in definitions:
            if getattr(node, "name", None) is not None and node.name.value in unsafe:
                continue
            if hasattr(node, "interfaces") and any(
                interface.name.value in unsafe for interface in node.interfaces
            ):
                unsafe.add(node.name.value)
                changed = True
                continue
            if hasattr(node, "fields"):
                node = _closed_fields(node, unsafe, roots, excluded)
                if node is None:
                    changed = True
                    continue
            output.append(node)
        definitions = output
    return definitions


def safe_schema(source: str) -> tuple[str, list[dict]]:
    """Strip prose and unsafe declarations while preserving unchanged wire names."""
    document = visit(parse(graphql_schema(source)), _RemoveDescriptions())
    roots = _root_names(document)
    definitions, excluded, unsafe = _redact_definitions(document, roots)
    definitions = _schema_closure(definitions, unsafe, roots, excluded)
    for node in definitions:
        if node.kind == "schema_definition":
            node.operation_types = tuple(
                op for op in node.operation_types if op.type.name.value not in unsafe
            )
    document.definitions = tuple(definitions)
    try:
        return graphql_schema(print_ast(document) + "\n"), excluded
    except BrowserError:
        raise BrowserError("unsupported: no safe complete GraphQL schema") from None


def _native_name(wire: str) -> str:
    from ariadne_codegen.utils import process_name

    return process_name(wire, convert_to_snake_case=True)


def _native_groups(fields: dict) -> dict[str, list[str]]:
    names = {}
    for wire in fields:
        names.setdefault(_native_name(wire), []).append(wire)
    return names


def _selection(result: Any) -> tuple[list[str], str | None]:
    if isinstance(result, (GraphQLScalarType, GraphQLEnumType)):
        return [], None
    if not isinstance(result, GraphQLObjectType):
        return [], "unsupported_abstract_selection"
    names = _native_groups(result.fields)
    selection = []
    for leaf, value in sorted(result.fields.items()):
        if len(names[_native_name(leaf)]) != 1:
            continue
        if not isinstance(
            get_named_type(value.type), (GraphQLScalarType, GraphQLEnumType)
        ):
            continue
        if any(
            is_non_null_type(arg.type) and arg.default_value is Undefined
            for arg in value.args.values()
        ):
            continue
        selection.append(leaf)
    if not selection:
        return [], "unsupported_selection"
    return selection[:MAX_LEAF_FIELDS], None


def _root_operations(
    kind: str, root: GraphQLObjectType, builder: str
) -> tuple[dict, list[dict]]:
    operations, exclusions = {}, []
    names = _native_groups(root.fields)
    for name, field in sorted(root.fields.items()):
        command = kind + "_" + name
        if len(names[_native_name(name)]) > 1:
            exclusions.append({"operation": command, "reason": "ambiguous_native_name"})
            continue
        selection, reason = _selection(get_named_type(field.type))
        if reason:
            exclusions.append({"operation": command, "reason": reason})
            continue
        operations[command] = {
            "kind": "graphql-custom",
            "builder": builder,
            "field": _native_name(name),
            "selection": [_native_name(leaf) for leaf in selection],
            "operationType": kind,
            "write": kind == "mutation",
            "description": "GraphQL " + kind + " using native schema builders",
        }
    return operations, exclusions


def schema_operations(source: str) -> tuple[dict[str, dict], list[dict]]:
    """Select native root builders and bounded scalar or enum object leaves."""
    schema = build_schema(source)
    operations, exclusions = {}, []
    for kind, root, builder in [
        ("query", schema.query_type, "Query"),
        ("mutation", schema.mutation_type, "Mutation"),
    ]:
        if root is not None:
            selected, rejected = _root_operations(kind, root, builder)
            operations.update(selected)
            exclusions.extend(rejected)
    if schema.subscription_type:
        exclusions.extend(
            {"operation": "subscription_" + name, "reason": "unsupported_subscription"}
            for name in sorted(schema.subscription_type.fields)
        )
    return operations, exclusions
