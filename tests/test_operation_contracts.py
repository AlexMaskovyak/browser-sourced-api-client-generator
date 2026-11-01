"""Validate captured operation contracts independently of a server schema."""

from pathlib import Path
import pytest
from website_client.operation_contracts import compile_operations, validate_variables

DOC = (Path(__file__).parent / "fixtures/captured-operations.graphql").read_text()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not graphql",
        "{ item { id } }",
        "subscription Events { event { id } }",
        "query Q { item(id: $unknown) { id } }",
        "query Q($id: ID!) { item { id } }",
        "query Q($id: ID!, $id: ID!) { item(id: $id) { id } }",
        "query Q { item { ...Absent } }",
        "query Q { item { ...A } } fragment A on Item { ...B } fragment B on Item { ...A }",
        "query Q { one } query Q { two }",
        "query One { one } query ONE { two }",
        "type Query { item: String }",
    ],
)
def test_invalid_or_ambiguous_operations_are_rejected(text):
    with pytest.raises(ValueError):
        compile_operations((text,))


def test_defaults_nullability_scalars_and_fragments_without_a_schema():
    operation = next(item for item in compile_operations((DOC,)) if item.name == "Find")
    assert validate_variables(operation, {"id": "7"}) == {"id": "7"}
    assert validate_variables(operation, {"id": 7, "label": None}) == {
        "id": 7,
        "label": None,
    }
    assert "fragment Fields" in operation.document
    assert "mutation Create" not in operation.document
    for data in [
        {"id": None},
        {"id": True},
        {"id": "7", "limit": None},
        {"id": "7", "limit": 2**40},
        {"id": "7", "extra": 1},
    ]:
        with pytest.raises(ValueError):
            validate_variables(operation, data)


def test_fragment_names_are_scoped_to_each_captured_request():
    sources = (
        "query One { item { ...F } } fragment F on Item { id }",
        "query Two { item { ...F } } fragment F on Item { name }",
    )
    operations = compile_operations(sources)
    assert "id" in operations[0].document and "name" not in operations[0].document
    assert "name" in operations[1].document


def test_reserved_python_names_get_safe_parameters():
    operation = compile_operations(
        (
            "query Close($class: ID!, $self: ID!, $__private: ID!) { a(a:$class,b:$self,c:$__private) }",
        )
    )[0]
    assert operation.method == "operation_close"
    values = {item.parameter: "7" for item in operation.variables}
    assert validate_variables(operation, values) == {
        "class": "7",
        "self": "7",
        "__private": "7",
    }
