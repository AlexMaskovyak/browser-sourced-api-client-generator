"""Generate typed source with upstream tools and package a standalone client."""

import ast
from dataclasses import dataclass
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
from string import Template
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from graphql import build_schema, parse, print_ast, validate

from .browser import BrowserError
from .definitions import read_document
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
    "runtime_profile.py": "runtime_profile.py",
    "authenticated_http.py": "authenticated_http.py",
    "runtime.py": "client_runtime.py",
    "native_files.py": "native_files.py",
    "result_output.py": "result_output.py",
}


@dataclass(frozen=True)
class GeneratedProject:
    """A staged SDK and its command metadata before exclusive publication.

    Backends own generated request code; shared packaging adds browser transport,
    CLI entry points and reports after backend generation succeeds."""

    directory: Path
    package_name: str
    operations: dict
    generator_name: str
    coverage: dict | None = None


def write_json(path, value):
    """Write stable, indented JSON with a final newline for reviewable output."""
    path.write_text(json.dumps(value, indent=JSON_INDENT_SPACES) + "\n")


def render_template(filename, *, name, package):
    """Render a packaged project template with validated distribution and module names."""
    template = Template((TEMPLATE_DIRECTORY / filename).read_text())
    return template.substitute(name=name, package=package)


def package_name_for(name):
    """Validate a distribution name and derive its importable Python package name."""
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
    """Inspect generated OpenAPI endpoint modules to build the CLI operation inventory.

    Read the actual request verb from syntax instead of guessing read/write behavior
    from a filename. Endpoint modules remain the sole request builders."""
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
        '[tool.poetry.dependencies]\nplaywright = "==1.64.1"\nplatformdirs = ">=4.3,<5"\nfilelock = ">=3.20,<4"\n',
        1,
    )
    metadata_path.write_text(metadata)


def _openapi_backend(stage, name, package, spec, project):
    """Use the pinned public API so warnings are structured, never terminal text."""
    from openapi_python_client import generate as upstream_generate
    from openapi_python_client.config import Config, ConfigFile, MetaType

    stage.mkdir(parents=True, exist_ok=True)
    definition_path = stage / "definition.json"
    write_json(definition_path, spec)
    config = Config.from_sources(
        ConfigFile(
            project_name_override=name, package_name_override=package, post_hooks=[]
        ),
        MetaType.POETRY,
        definition_path,
        "utf-8",
        False,
        project,
    )
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        errors = upstream_generate(config=config)
    return [
        {
            "reason": "upstream_generation_warning",
            "type": type(item).__name__,
            "level": item.level.value,
        }
        for item in errors
    ]


def _generated_python_diagnostics(project: Path, package: str) -> list[dict[str, str]]:
    """Reject malformed upstream output without exposing source lines in diagnostics."""
    for path in (project / package).rglob("*.py"):
        try:
            ast.parse(path.read_text())
        except SyntaxError:
            return [
                {
                    "reason": "upstream_generation_warning",
                    "type": "SyntaxError",
                    "level": "warning",
                }
            ]
    return []


def generate_openapi_project(stage, name, package, source):
    """Keep independent usable methods when admission or the backend excludes others."""
    from .admission import METHODS, admit_openapi, prune_components

    safe, spec, exclusions = admit_openapi(source)
    project = stage / "project"
    if not spec["paths"]:
        raise BrowserError("unsupported: no executable OpenAPI operations")
    diagnostics = _openapi_backend(stage / "backend", name, package, spec, project)
    diagnostics.extend(_generated_python_diagnostics(project, package))
    if diagnostics:
        # A backend warning may drop part of a model while leaving a callable method.
        # Test each dependency closure through that same backend before packaging it.
        shutil.rmtree(project, ignore_errors=True)
        admitted = deepcopy(spec)
        admitted["paths"] = {}
        for index, (path, method, operation) in enumerate(
            (path, method, operation)
            for path, item in spec["paths"].items()
            for method, operation in item.items()
            if method in METHODS
        ):
            candidate = deepcopy(spec)
            candidate["paths"] = {
                path: {
                    key: deepcopy(value)
                    for key, value in spec["paths"][path].items()
                    if key not in METHODS
                }
            }
            candidate["paths"][path][method] = deepcopy(operation)
            prune_components(candidate)
            probe = stage / ("probe-" + str(index))
            warnings = _openapi_backend(
                probe, name, package, candidate, probe / "project"
            )
            warnings.extend(_generated_python_diagnostics(probe / "project", package))
            if warnings:
                exclusions.append(
                    {
                        "operation": operation.get("operationId"),
                        "method": method.upper(),
                        "path": path,
                        "reason": "upstream_generation_warning",
                        "diagnostics": warnings,
                    }
                )
            else:
                selected = admitted["paths"].setdefault(
                    path,
                    {
                        key: deepcopy(value)
                        for key, value in spec["paths"][path].items()
                        if key not in METHODS
                    },
                )
                selected[method] = deepcopy(operation)
        spec = admitted
        prune_components(spec)
        if not spec["paths"]:
            raise BrowserError("unsupported: no executable OpenAPI operations")
        final_warnings = _openapi_backend(stage / "final", name, package, spec, project)
        final_warnings.extend(_generated_python_diagnostics(project, package))
        if final_warnings:
            raise BrowserError("unsupported: conflicting upstream generation inputs")
    operations = operation_metadata(project, package)
    expected = sum(
        method in METHODS for item in spec["paths"].values() for method in item
    )
    if len(operations) != expected:
        raise BrowserError(
            "unsupported: upstream omitted operations without attributable diagnostics"
        )
    configure_openapi_package(project, name, package)
    write_json(project / "openapi.json", spec)
    write_json(project / "source-openapi.json", safe)
    return GeneratedProject(
        project,
        package,
        operations,
        "openapi-python-client",
        {"exclusions": exclusions, "diagnostics": diagnostics},
    )


def validated_graphql_operations(schema_sdl, operations_path):
    """Parse named queries/mutations and validate them against the supplied full schema.

    This is the schema-backed path. It rejects anonymous operations, subscriptions
    and invalid selections before invoking the external SDK generator."""
    if not operations_path:
        raise BrowserError(
            "GraphQL generation requires --operations as well as --graphql-schema."
        )
    from .privacy import safe_operations

    query_text = safe_operations(read_document(operations_path))
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
    """Map validated operations to Ariadne method names and explicit write flags."""
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
    """Write local schema, operation and Ariadne configuration files into staging."""
    schema_path = stage / "schema.graphql"
    operations_path = stage / "operations.graphql"
    configuration_path = stage / "config.toml"
    schema_path.write_text(schema_sdl)
    if document is not None:
        operations_path.write_text(print_ast(document))
    configuration = [
        "[tool.ariadne-codegen]",
        f"schema_path = {json.dumps(str(schema_path))}",
        *(
            [f"queries_path = {json.dumps(str(operations_path))}"]
            if document is not None
            else ["enable_custom_operations = true"]
        ),
        f"target_package_name = {json.dumps(package)}",
        f"target_package_path = {json.dumps(str(project))}",
        "async_client = false",
        "multipart_uploads = true",
        'include_comments = "none"',
    ]
    configuration_path.write_text("\n".join(configuration))
    return configuration_path


def generate_graphql_project(stage, name, package, source, operations_path):
    # Validate before invoking the backend. Captured operations alone cannot
    # supply a full schema, and unsupported subscriptions are not CLI methods.
    """Build a schema-backed synchronous GraphQL SDK through Ariadne in private staging.

    Unlike the operation-only backend, this path has a complete schema and can emit
    typed response models. No endpoint is called during generation."""
    from .graphql_admission import safe_schema, schema_operations

    schema_sdl, privacy_exclusions = safe_schema(source)
    operation_exclusions = []
    if operations_path:
        from .privacy import safe_operations

        text, operation_exclusions = safe_operations(
            read_document(operations_path), with_exclusions=True
        )
        admitted_operations = stage / "admitted-operations.graphql"
        admitted_operations.write_text(text)
        document, selected = validated_graphql_operations(
            schema_sdl, admitted_operations
        )
    else:
        document, selected = None, None
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

    if selected is not None:
        operations, exclusions = graphql_operation_metadata(selected), []
    else:
        operations, exclusions = schema_operations(schema_sdl)
    if not operations:
        raise BrowserError("unsupported: no executable GraphQL operations")
    (project / "pyproject.toml").write_text(
        render_template("graphql_pyproject.toml.tmpl", name=name, package=package)
    )
    (project / "schema.graphql").write_text(schema_sdl)
    if document is not None:
        (project / "operations.graphql").write_text(print_ast(document) + "\n")
    return GeneratedProject(
        project,
        package,
        operations,
        "ariadne-codegen",
        {"exclusions": privacy_exclusions + operation_exclusions + exclusions},
    )


def copy_runtime_sources(target: Path, sources: dict[str, str]) -> None:
    """Copy declared standalone runtime modules into an already staged package."""
    for output_name, source_name in sources.items():
        target.joinpath(output_name).write_text(
            Path(__file__).with_name(source_name).read_text()
        )


def write_runtime_files(project, runtime_config=None):
    """Copy only the standalone runtime, never the generator implementation."""
    target = project.directory / project.package_name
    copy_runtime_sources(target, RUNTIME_SOURCES)
    write_json(target / "_operations.json", project.operations)
    config = {"kind": next(iter(project.operations.values()))["kind"]}
    config.update(runtime_config or {})
    if config.get("endpoint"):
        from .runtime_profile import service_origin

        config["origin"] = service_origin(config["endpoint"])
        if urlsplit(config["endpoint"]).query:
            raise BrowserError("Runtime endpoints cannot contain query values.")
    if set(config) - {"kind", "endpoint", "origin", "profileId"}:
        raise BrowserError("Invalid non-secret runtime configuration.")
    if config.get("profileId") is not None and (
        not isinstance(config["profileId"], str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", config["profileId"])
    ):
        raise BrowserError("Invalid runtime profile identifier.")
    write_json(target / "_runtime.json", config)
    with (target / "__init__.py").open("a") as stream:
        stream.write("\nfrom .runtime import client_context\n")


def write_project_readme(project, name):
    """Prepend shared install and command instructions to the backend README."""
    readme = project.directory / "README.md"
    upstream_readme = readme.read_text() if readme.exists() else ""
    introduction = render_template(
        "client_readme.md.tmpl", name=name, package=project.package_name
    )
    readme.write_text(introduction + upstream_readme)


def write_generation_report(project, source, operations_source=None, lineage=None):
    """Record source hashes, backend version and validation scope without claiming live use."""
    report = {
        "generator": project.generator_name,
        "generatorVersion": importlib.metadata.version(project.generator_name),
        "definitionSha256": hashlib.sha256(source.encode()).hexdigest(),
        "operations": list(project.operations),
        "liveVerified": False,
    }
    if project.generator_name == "ariadne-codegen":
        report.update(
            generationMode=(
                "graphql-schema-only"
                if any(
                    item["kind"] == "graphql-custom"
                    for item in project.operations.values()
                )
                else "graphql-schema"
            ),
            schemaValidated=True,
        )
    else:
        report.update(generationMode="openapi", definitionValidated=True)
    if operations_source is not None:
        report["operationsSha256"] = hashlib.sha256(
            operations_source.encode()
        ).hexdigest()
        report["generatedOperationsSha256"] = hashlib.sha256(
            (project.directory / "operations.graphql").read_bytes()
        ).hexdigest()
    if project.coverage is not None:
        report["coverage"] = project.coverage
    if lineage is not None:
        report["lineage"] = lineage
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


def generate(
    output,
    name="service-client",
    openapi=None,
    schema=None,
    operations=None,
    lineage=None,
    runtime_config=None,
):
    """Generate a schema-backed client, then publish it into a new directory.

    Read local OpenAPI or GraphQL definitions, invoke the selected backend in private
    staging and add shared runtime files. Existing output is never overwritten.
    Operation-only generation is dispatched separately by pipeline.generate_bundle."""
    output = new_output(output)
    package = package_name_for(name)
    source = read_document(openapi or schema)
    operations_source = (
        read_document(operations) if not openapi and operations else None
    )

    # Each backend writes into private staging. Shared packaging and publication
    # occur only after upstream generation and metadata extraction succeed.
    with tempfile.TemporaryDirectory(prefix="website-client-generation-") as directory:
        stage = Path(directory)
        if operations_source is not None:
            operations = stage / "input-operations.graphql"
            operations.write_text(operations_source, encoding="utf-8")
        if openapi:
            project = generate_openapi_project(stage, name, package, source)
        else:
            project = generate_graphql_project(stage, name, package, source, operations)
        write_runtime_files(project, runtime_config)
        write_project_readme(project, name)
        from .project_metadata import write_distribution_support

        write_distribution_support(
            project.directory, project.package_name, name, (project.generator_name,)
        )
        report = write_generation_report(project, source, operations_source, lineage)
        publish_tree(project.directory, output)
        return report
