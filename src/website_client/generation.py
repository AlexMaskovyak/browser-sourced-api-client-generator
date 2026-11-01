"""Generate typed source with upstream tools and package a standalone client."""

import ast
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
from string import Template
import subprocess
import sys
import tempfile

from graphql import build_schema, parse, print_ast, validate

from .browser import BrowserError
from .definitions import graphql_schema, openapi_document, read_document
from .discovery import new_output

GENERATOR_TIMEOUT_SECONDS = 120
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
JSON_INDENT_SPACES = 2
READ_ONLY_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
TEMPLATE_DIRECTORY = Path(__file__).with_name("templates")
RUNTIME_SOURCES = {
    "_cli.py": "client_cli.py",
    "browser.py": "browser.py",
    "session_http.py": "session_http.py",
}


@dataclass(frozen=True)
class GeneratedProject:
    directory: Path
    package_name: str
    operations: dict
    generator_name: str


def write_json(path, value):
    path.write_text(json.dumps(value, indent=JSON_INDENT_SPACES) + "\n")


def render_template(filename, *, name, package):
    template = Template((TEMPLATE_DIRECTORY / filename).read_text())
    return template.substitute(name=name, package=package)


def package_name_for(name):
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
        raise BrowserError(
            "--name must use lowercase letters, numbers and single hyphens, "
            "starting with a letter."
        )
    package = name.replace("-", "_")
    if not package.isidentifier():
        raise BrowserError("Invalid generated Python package name.")
    return package


def run_generator(module, arguments, cwd):
    """Keep upstream diagnostics private; publish only a complete generation."""
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=cwd,
        capture_output=True,
        timeout=GENERATOR_TIMEOUT_SECONDS,
    )
    if result.returncode:
        raise BrowserError(
            "Upstream generation failed. Check the validated definition and "
            "supported upstream features; no client was published."
        )


def generated_http_method(tree):
    """Read the verb emitted by the upstream request builder, not its filename."""
    method = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "method"
                and isinstance(value, ast.Constant)
            ):
                method = value.value
    return method


def operation_metadata(project, package):
    operations = {}
    for path in sorted((project / package / "api").rglob("*.py")):
        tree = ast.parse(path.read_text())
        has_endpoint = any(
            isinstance(item, ast.FunctionDef) and item.name == "sync_detailed"
            for item in tree.body
        )
        if not has_endpoint:
            continue

        method = generated_http_method(tree)
        if method is None or path.stem in operations:
            raise BrowserError(
                "Generated operation names or methods are ambiguous. "
                "Review the OpenAPI operation identifiers."
            )
        method = method.upper()
        module = ".".join(path.relative_to(project).with_suffix("").parts)
        operations[path.stem] = {
            "kind": "openapi",
            "module": module,
            "httpMethod": method,
            "write": method not in READ_ONLY_HTTP_METHODS,
            "description": method + " endpoint",
        }

    if not operations:
        raise BrowserError("No callable OpenAPI operations were generated.")
    return operations


def configure_openapi_package(project, name, package):
    """Extend the upstream Poetry metadata without replacing its dependencies."""
    metadata_path = project / "pyproject.toml"
    metadata = metadata_path.read_text()
    metadata += f'\n[tool.poetry.scripts]\n{name} = "{package}._cli:entrypoint"\n'
    metadata = metadata.replace(
        "[tool.poetry.dependencies]\n",
        '[tool.poetry.dependencies]\nplaywright = ">=1.58,<2"\n',
        1,
    )
    metadata_path.write_text(metadata)


def generate_openapi_project(stage, name, package, source):
    spec = openapi_document(source)
    definition_path = stage / "openapi.json"
    configuration_path = stage / "config.json"
    project = stage / "project"
    write_json(definition_path, spec)
    write_json(
        configuration_path,
        {
            "project_name_override": name,
            "package_name_override": package,
            # The wrapper supplies its own runtime; no shell hooks are needed.
            "post_hooks": [],
        },
    )
    run_generator(
        "openapi_python_client",
        [
            "generate",
            "--path",
            str(definition_path),
            "--output-path",
            str(project),
            "--config",
            str(configuration_path),
            "--meta",
            "poetry",
            "--fail-on-warning",
        ],
        stage,
    )
    operations = operation_metadata(project, package)
    configure_openapi_package(project, name, package)
    write_json(project / "openapi.json", spec)
    return GeneratedProject(project, package, operations, "openapi-python-client")


def validated_graphql_operations(schema_sdl, operations_path):
    if not operations_path:
        raise BrowserError(
            "GraphQL generation requires --operations as well as --graphql-schema."
        )
    query_text = read_document(operations_path)
    try:
        document = parse(query_text)
        if validate(build_schema(schema_sdl), document):
            raise ValueError()

        selected = [
            item for item in document.definitions if item.kind == "operation_definition"
        ]
        unsupported = any(
            item.name is None or item.operation.value == "subscription"
            for item in selected
        )
        if not selected or unsupported:
            raise ValueError()
        return document, selected
    except Exception:
        raise BrowserError(
            "GraphQL operations must be named, valid against the schema, "
            "and queries or mutations."
        ) from None


def graphql_operation_metadata(selected):
    from ariadne_codegen.utils import str_to_snake_case

    operations = {
        item.name.value: {
            "kind": "graphql",
            "method": str_to_snake_case(item.name.value),
            "write": item.operation.value == "mutation",
            "description": "GraphQL " + item.operation.value,
        }
        for item in selected
    }
    method_names = {item["method"] for item in operations.values()}
    if len(method_names) != len(operations):
        raise BrowserError("GraphQL operation names collide as Python method names.")
    return operations


def write_graphql_configuration(stage, project, package, schema_sdl, document):
    schema_path = stage / "schema.graphql"
    operations_path = stage / "operations.graphql"
    configuration_path = stage / "config.toml"
    schema_path.write_text(schema_sdl)
    operations_path.write_text(print_ast(document))
    configuration = [
        "[tool.ariadne-codegen]",
        f"schema_path = {json.dumps(str(schema_path))}",
        f"queries_path = {json.dumps(str(operations_path))}",
        f"target_package_name = {json.dumps(package)}",
        f"target_package_path = {json.dumps(str(project))}",
        "async_client = false",
        "multipart_uploads = false",
        'include_comments = "none"',
    ]
    configuration_path.write_text("\n".join(configuration))
    return configuration_path


def generate_graphql_project(stage, name, package, source, operations_path):
    # Validate before invoking the backend. Captured operations alone cannot
    # supply a full schema, and unsupported subscriptions are not CLI methods.
    if not operations_path:
        raise BrowserError(
            "GraphQL generation requires --operations as well as --graphql-schema."
        )
    schema_sdl = graphql_schema(source)
    document, selected = validated_graphql_operations(schema_sdl, operations_path)
    project = stage / "project"
    project.mkdir()
    configuration_path = write_graphql_configuration(
        stage, project, package, schema_sdl, document
    )
    run_generator(
        "ariadne_codegen",
        ["--config", str(configuration_path)],
        stage,
    )

    operations = graphql_operation_metadata(selected)
    (project / "pyproject.toml").write_text(
        render_template("graphql_pyproject.toml.tmpl", name=name, package=package)
    )
    (project / "schema.graphql").write_text(schema_sdl)
    (project / "operations.graphql").write_text(print_ast(document) + "\n")
    return GeneratedProject(project, package, operations, "ariadne-codegen")


def write_runtime_files(project):
    """Copy only the standalone runtime, never the generator implementation."""
    target = project.directory / project.package_name
    for output_name, source_name in RUNTIME_SOURCES.items():
        target.joinpath(output_name).write_text(
            Path(__file__).with_name(source_name).read_text()
        )
    write_json(target / "_operations.json", project.operations)


def write_project_readme(project, name):
    readme = project.directory / "README.md"
    upstream_readme = readme.read_text() if readme.exists() else ""
    introduction = render_template(
        "client_readme.md.tmpl", name=name, package=project.package_name
    )
    readme.write_text(introduction + upstream_readme)


def write_generation_report(project, source):
    report = {
        "generator": project.generator_name,
        "generatorVersion": importlib.metadata.version(project.generator_name),
        "definitionSha256": hashlib.sha256(source.encode()).hexdigest(),
        "operations": list(project.operations),
        "liveVerified": False,
    }
    write_json(project.directory / "generation.json", report)
    return report


def publish_tree(source, output):
    """Create private output exclusively; preserve existing files on failure."""
    output = new_output(output)
    output.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    try:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError()
            target = output / path.relative_to(source)
            if path.is_dir():
                target.mkdir(mode=PRIVATE_DIRECTORY_MODE)
            else:
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(path.read_bytes())
    except Exception:
        raise BrowserError(
            "Client publication was incomplete. "
            "Inspect the new output directory before retrying."
        ) from None


def generate(output, name="service-client", openapi=None, schema=None, operations=None):
    output = new_output(output)
    package = package_name_for(name)
    source = read_document(openapi or schema)

    # Each backend writes into private staging. Shared packaging and publication
    # occur only after upstream generation and metadata extraction succeed.
    with tempfile.TemporaryDirectory(prefix="website-client-generation-") as directory:
        stage = Path(directory)
        if openapi:
            project = generate_openapi_project(stage, name, package, source)
        else:
            project = generate_graphql_project(stage, name, package, source, operations)
        write_runtime_files(project)
        write_project_readme(project, name)
        report = write_generation_report(project, source)
        publish_tree(project.directory, output)
        return report
