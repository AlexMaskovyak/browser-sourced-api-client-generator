"""Describe supported public reads using verified upstream argument names."""

import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from string import Template

SCALAR_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
EXAMPLES = {"string": "'value'", "integer": "1", "number": "1.0", "boolean": "True"}
TEMPLATES = Path(__file__).with_name("templates")


@dataclass(frozen=True)
class Parameter:
    wire: str
    python: str
    type: str
    required: bool


@dataclass(frozen=True)
class PublicOperation:
    name: str
    module: str
    parameters: tuple[Parameter, ...]
    annotation: str
    imports: tuple[str, ...]


def template(filename, **values):
    return Template((TEMPLATES / filename).read_text()).substitute(values)


def query_mapping(function):
    """Read literal wire query keys assigned by the upstream request builder."""
    mapping = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "params"
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                mapping[target.slice.value] = node.value.id
    return mapping


def describe_operation(project, operation, parameters):
    identifier = operation.get("operationId", "")
    if (
        identifier not in project.operations
        or not identifier.isidentifier()
        or identifier.startswith("_")
        or identifier in {"login", "close"}
    ):
        return None
    if any(
        parameter.get("in") != "query"
        or parameter.get("schema", {}).get("type") not in SCALAR_TYPES
        for parameter in parameters
    ):
        return None
    module = project.operations[identifier]["module"]
    path = (project.directory / Path(*module.split("."))).with_suffix(".py")
    tree = ast.parse(path.read_text())
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    mapping = query_mapping(functions["_get_kwargs"])
    function = functions["sync_detailed"]
    names = {argument.arg for argument in function.args.kwonlyargs} - {"client"}
    if (
        set(mapping) != {parameter["name"] for parameter in parameters}
        or set(mapping.values()) != names
    ):
        return None
    mapped = tuple(
        Parameter(
            parameter["name"],
            mapping[parameter["name"]],
            parameter["schema"]["type"],
            parameter.get("required", False),
        )
        for parameter in parameters
    )
    response_type = function.returns.slice
    imports = tuple(
        sorted(
            {node.id for node in ast.walk(response_type) if isinstance(node, ast.Name)}
        )
    )
    return PublicOperation(
        identifier, module, mapped, ast.unparse(response_type), imports
    )


def public_operations(project, spec):
    selected = []
    for path, item in sorted(spec.get("paths", {}).items()):
        operation = item.get("get", {})
        response = operation.get("responses", {}).get("200", {})
        schema = (
            response.get("content", {}).get("application/json", {}).get("schema", {})
        )
        if schema.get("type") != "object" and not schema.get("$ref"):
            continue
        described = describe_operation(
            project,
            operation,
            item.get("parameters", []) + operation.get("parameters", []),
        )
        if described:
            selected.append(described)
    return selected


def render_method(operation):
    ordered = sorted(operation.parameters, key=lambda parameter: not parameter.required)
    declarations = [
        parameter.python
        + ": "
        + SCALAR_TYPES[parameter.type]
        + ("" if parameter.required else " = _OMITTED")
        for parameter in ordered
    ]
    signature = ", *, " + ", ".join(declarations) if declarations else ""
    arguments = ", ".join(
        repr(parameter.python) + ": " + parameter.python
        for parameter in operation.parameters
    )
    return template(
        "public_method.py.tmpl",
        name=operation.name,
        signature=signature,
        annotation=operation.annotation,
        arguments=arguments,
        quoted_name=repr(operation.name),
    )


def write_public_sources(project, website, operations):
    target = project.directory / project.package_name
    for module in ("runtime_profile.py", "public_runtime.py", "public_cli.py"):
        (target / module).write_text(Path(__file__).with_name(module).read_text())
    inventory = {
        operation.name: {
            "module": operation.module,
            "parameters": [asdict(parameter) for parameter in operation.parameters],
        }
        for operation in operations
    }
    (target / "_public.json").write_text(
        json.dumps({"website": website, "operations": inventory}, indent=2) + "\n"
    )
    imports = "\n".join(
        "from " + operation.module + " import " + ", ".join(operation.imports)
        for operation in operations
        if operation.imports
    )
    (target / "_public.py").write_text(
        template(
            "public_client.py.tmpl",
            imports=imports,
            methods="\n".join(map(render_method, operations)),
        )
    )
    (target / "__init__.py").write_text(
        "from ._public import Client\nfrom .client import AuthenticatedClient\n\n"
        '__all__ = ["Client", "AuthenticatedClient"]\n'
    )


def write_public_readme(project, name, website, operations):
    python_examples = []
    cli_examples = []
    for operation in operations:
        arguments = ", ".join(
            parameter.python + "=" + EXAMPLES[parameter.type]
            for parameter in operation.parameters
        )
        python_examples.append(f"result = client.{operation.name}({arguments})")
        flags = " ".join(
            "--"
            + parameter.python.replace("_", "-")
            + " "
            + EXAMPLES[parameter.type].strip("'").lower()
            for parameter in operation.parameters
        )
        cli_examples.append(
            f"{name} {operation.name.replace('_', '-')} {flags}".rstrip()
        )
    readme = project.directory / "README.md"
    previous = readme.read_text()
    readme.write_text(
        template(
            "public_readme.md.tmpl",
            package=project.package_name,
            name=name,
            website=website,
            python_examples="\n".join(python_examples),
            cli_examples="\n".join(cli_examples),
        )
    )
    if set(project.operations) - {operation.name for operation in operations}:
        readme.write_text(
            readme.read_text() + "\n# Other upstream endpoint commands\n\n" + previous
        )
