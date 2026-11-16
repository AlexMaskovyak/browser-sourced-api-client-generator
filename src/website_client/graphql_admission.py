"""Safe schema-only root selection using Ariadne's native operation builders."""

from copy import copy
from graphql import (
    build_schema, get_named_type, GraphQLEnumType, GraphQLObjectType, GraphQLScalarType,
    Undefined, parse, print_ast, is_non_null_type,
)
from graphql.language import Visitor, visit

from .admission import sensitive
from .browser import BrowserError
from .definitions import graphql_schema

MAX_LEAF_FIELDS = 20


class _RemoveDescriptions(Visitor):
    def enter(self, node, *_):
        if hasattr(node, 'description') and node.description is not None:
            node = copy(node)
            node.description = None
            return node


def safe_schema(source):
    """Strip prose and omit unsafe declarations, preserving unchanged wire names."""
    document = visit(parse(graphql_schema(source)), _RemoveDescriptions())
    definitions, excluded, unsafe_types = [], [], set()
    roots = {'Query', 'Mutation', 'Subscription'}
    for node in document.definitions:
        if node.kind == 'schema_definition':
            roots.update(operation.type.name.value for operation in node.operation_types)
    for node in document.definitions:
        if getattr(node, 'name', None) is not None and sensitive(node.name.value):
            unsafe_types.add(node.name.value)
            continue
        if hasattr(node, 'fields'):
            fields = []
            for field in node.fields:
                if sensitive(print_ast(field)):
                    if node.kind == 'input_object_type_definition':
                        unsafe_types.add(node.name.value)
                    if node.name.value in roots:
                        excluded.append({'operation': '[omitted]' if sensitive(field.name.value) else field.name.value, 'reason': 'sensitive_structure'})
                else:
                    fields.append(field)
            node = copy(node)
            node.fields = tuple(fields)
            if not fields:
                unsafe_types.add(node.name.value)
        elif sensitive(print_ast(node)):
            unsafe_types.add(node.name.value)
        definitions.append(node)
    # Remove references to unsafe types until the remaining schema is closed.
    changed = True
    while changed:
        changed = False
        output = []
        for node in definitions:
            if getattr(node, 'name', None) is not None and node.name.value in unsafe_types:
                continue
            if hasattr(node, 'fields'):
                fields = []
                for field in node.fields:
                    names = []
                    class Names(Visitor):
                        def enter_named_type(self, item, *_):
                            names.append(item.name.value)
                    visit(field, Names())
                    if unsafe_types.intersection(names):
                        if node.name.value in roots:
                            excluded.append({'operation': field.name.value, 'reason': 'sensitive_structure'})
                        continue
                    fields.append(field)
                if not fields or (node.kind == 'input_object_type_definition' and len(fields) != len(node.fields)):
                    unsafe_types.add(node.name.value)
                    changed = True
                    continue
                node = copy(node)
                node.fields = tuple(fields)
            output.append(node)
        definitions = output
    # Remove explicit root declarations whose type has disappeared.
    for node in definitions:
        if node.kind == 'schema_definition':
            node.operation_types = tuple(op for op in node.operation_types if op.type.name.value not in unsafe_types)
    document.definitions = tuple(definitions)
    text = print_ast(document) + '\n'
    try:
        return graphql_schema(text), excluded
    except BrowserError:
        raise BrowserError('unsupported: no safe complete GraphQL schema') from None


def schema_operations(source):
    """Select native root builders and bounded scalar/enum object leaves."""
    from ariadne_codegen.utils import str_to_snake_case
    schema = build_schema(source)
    operations, exclusions = {}, []
    for kind, root, builder in [('query', schema.query_type, 'Query'), ('mutation', schema.mutation_type, 'Mutation')]:
        if root is None:
            continue
        for name, field in sorted(root.fields.items()):
            result = get_named_type(field.type)
            selection = []
            reason = None
            if isinstance(result, GraphQLObjectType):
                selection = [leaf for leaf, value in sorted(result.fields.items())
                             if isinstance(get_named_type(value.type), (GraphQLScalarType, GraphQLEnumType))
                             and not any(is_non_null_type(arg.type) and arg.default_value is Undefined for arg in value.args.values())]
                if not selection:
                    reason = 'unsupported_selection'
                else:
                    selection = selection[:MAX_LEAF_FIELDS]
            elif not isinstance(result, (GraphQLScalarType, GraphQLEnumType)):
                reason = 'unsupported_abstract_selection'
            command = kind + '_' + name
            if reason:
                exclusions.append({'operation': command, 'reason': reason})
                continue
            operations[command] = {'kind': 'graphql-custom', 'builder': builder,
                                   'field': str_to_snake_case(name), 'selection': [str_to_snake_case(leaf) for leaf in selection],
                                   'operationType': kind, 'write': kind == 'mutation',
                                   'description': 'GraphQL ' + kind + ' using native schema builders'}
    if schema.subscription_type:
        exclusions.extend({'operation': 'subscription_' + name, 'reason': 'unsupported_subscription'} for name in sorted(schema.subscription_type.fields))
    return operations, exclusions
