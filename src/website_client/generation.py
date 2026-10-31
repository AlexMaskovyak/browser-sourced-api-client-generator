"""Use upstream schema generators, then package their methods with a CLI."""

import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from graphql import build_schema, parse, print_ast, validate

from .browser import BrowserError
from .definitions import graphql_schema, openapi_document, read_document
from .discovery import new_output


def run_generator(module, arguments, cwd):
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=cwd,
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise BrowserError(
            "Upstream generation failed. Check the validated definition and supported upstream features; no client was published."
        )


def operation_metadata(project, package):
    operations = {}
    for path in sorted((project / package / "api").rglob("*.py")):
        tree = ast.parse(path.read_text())
        if not any(
            isinstance(item, ast.FunctionDef) and item.name == "sync_detailed"
            for item in tree.body
        ):
            continue
        method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "method"
                        and isinstance(value, ast.Constant)
                    ):
                        method = value.value
        if method is None or path.stem in operations:
            raise BrowserError(
                "Generated operation names or methods are ambiguous. Review the OpenAPI operation identifiers."
            )
        module = ".".join(path.relative_to(project).with_suffix("").parts)
        operations[path.stem] = {
            "kind": "openapi",
            "module": module,
            "httpMethod": method.upper(),
            "write": method.upper() not in {"GET", "HEAD", "OPTIONS"},
            "description": method.upper() + " endpoint",
        }
    if not operations:
        raise BrowserError("No callable OpenAPI operations were generated.")
    return operations


def publish_tree(source, output):
    output = new_output(output)
    output.mkdir(mode=0o700)
    try:
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError()
            target = output / path.relative_to(source)
            if path.is_dir():
                target.mkdir(mode=0o700)
            else:
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(path.read_bytes())
    except Exception:
        raise BrowserError(
            "Client publication was incomplete. Inspect the new output directory before retrying."
        ) from None


def generate(output, name="service-client", openapi=None, schema=None, operations=None):
    output = new_output(output)
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
        raise BrowserError(
            "--name must use lowercase letters, numbers and single hyphens, starting with a letter."
        )
    package = name.replace("-", "_")
    if not package.isidentifier():
        raise BrowserError("Invalid generated Python package name.")
    source = read_document(openapi or schema)
    with tempfile.TemporaryDirectory(prefix="website-client-generation-") as directory:
        stage = Path(directory)
        project = stage / "project"
        if openapi:
            spec = openapi_document(source)
            definition = stage / "openapi.json"
            definition.write_text(json.dumps(spec))
            config = stage / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "project_name_override": name,
                        "package_name_override": package,
                        "post_hooks": [],
                    }
                )
            )
            run_generator(
                "openapi_python_client",
                [
                    "generate",
                    "--path",
                    str(definition),
                    "--output-path",
                    str(project),
                    "--config",
                    str(config),
                    "--meta",
                    "poetry",
                    "--fail-on-warning",
                ],
                stage,
            )
            metadata = operation_metadata(project, package)
            with (project / "pyproject.toml").open("a") as stream:
                stream.write(
                    f'\n[tool.poetry.scripts]\n{name} = "{package}._cli:entrypoint"\n'
                )
            metadata_path = project / "pyproject.toml"
            metadata_path.write_text(
                metadata_path.read_text().replace(
                    "[tool.poetry.dependencies]\n",
                    '[tool.poetry.dependencies]\nwebsocket-client = ">=1.9,<2"\n',
                    1,
                )
            )
            backend = "openapi-python-client"
            (project / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n")
        else:
            if not operations:
                raise BrowserError(
                    "GraphQL generation requires --operations as well as --graphql-schema."
                )
            sdl = graphql_schema(source)
            query_text = read_document(operations)
            try:
                document = parse(query_text)
                if validate(build_schema(sdl), document):
                    raise ValueError()
                selected = [
                    item
                    for item in document.definitions
                    if item.kind == "operation_definition"
                ]
                if not selected or any(
                    item.name is None or item.operation.value == "subscription"
                    for item in selected
                ):
                    raise ValueError()
            except Exception:
                raise BrowserError(
                    "GraphQL operations must be named, valid against the schema, and queries or mutations."
                ) from None
            project.mkdir()
            schema_path, queries_path = (
                stage / "schema.graphql",
                stage / "operations.graphql",
            )
            schema_path.write_text(sdl)
            queries_path.write_text(print_ast(document))
            config = stage / "config.toml"
            config.write_text(
                "[tool.ariadne-codegen]\n"
                + "\n".join(
                    [
                        f"schema_path = {json.dumps(str(schema_path))}",
                        f"queries_path = {json.dumps(str(queries_path))}",
                        f"target_package_name = {json.dumps(package)}",
                        f"target_package_path = {json.dumps(str(project))}",
                        "async_client = false",
                        "multipart_uploads = false",
                        'include_comments = "none"',
                    ]
                )
            )
            run_generator("ariadne_codegen", ["--config", str(config)], stage)
            from ariadne_codegen.utils import str_to_snake_case

            metadata = {
                item.name.value: {
                    "kind": "graphql",
                    "method": str_to_snake_case(item.name.value),
                    "write": item.operation.value == "mutation",
                    "description": "GraphQL " + item.operation.value,
                }
                for item in selected
            }
            if len({item["method"] for item in metadata.values()}) != len(metadata):
                raise BrowserError(
                    "GraphQL operation names collide as Python method names."
                )
            (project / "pyproject.toml").write_text(f'''[build-system]
requires = ["poetry-core>=2.2,<3"]
build-backend = "poetry.core.masonry.api"

[project]
name = "{name}"
version = "0.1.0"
description = "Generated GraphQL service client"
requires-python = ">=3.11,<4"
dependencies = ["httpx>=0.28,<0.29", "pydantic>=2.10,<3", "websocket-client>=1.9,<2"]

[project.scripts]
{name} = "{package}._cli:entrypoint"

[tool.poetry]
packages = [{{ include = "{package}" }}]
''')
            (project / "schema.graphql").write_text(sdl)
            (project / "operations.graphql").write_text(print_ast(document) + "\n")
            backend = "ariadne-codegen"
        target = project / package
        target.joinpath("_cli.py").write_text(
            Path(__file__).with_name("client_cli.py").read_text()
        )
        for module in ("browser.py", "session_http.py"):
            target.joinpath(module).write_text(
                Path(__file__).with_name(module).read_text()
            )
        target.joinpath("_operations.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        readme = project / "README.md"
        previous = readme.read_text() if readme.exists() else ""
        readme.write_text(
            f"# {name}\n\nInstall with `poetry install`, then run `poetry run {name} --help`.\n"
            f"Use `poetry run {name} OPERATION --help` for input fields and READ/WRITE behavior.\n"
            "Commands call the generated typed methods. JSON inputs use the Python argument names shown in help.\n"
            "Pass the service URL with --url. Add --login to open Chrome, sign in, then press Enter to call the endpoint through that browser session.\n"
            "If the application and API use different addresses, set --login-url to the application sign-in address. Chromium uses --browser chromium; custom locations use --browser-path.\n"
            "The temporary browser closes after the operation. Fresh commands require a fresh sign-in. Ctrl+C cancels. Browser session state is not saved or printed.\n"
            "A browser must be installed and --login requires an interactive terminal. Operation help lists --session-header for additional observed session-header names.\n"
            "Alternatively, runtime headers can be supplied as a JSON object through --headers-stdin. Do not combine the two authentication methods.\n"
            "Writes require --confirm-write followed by the exact operation name and are never retried automatically.\n"
            "Use --output NEW_FILE for private JSON output. A failed request may have succeeded; reconcile the service before retrying.\n"
            "Custom scalar mappings and complex input conversions may require the Python API. Multi-step service workflows require separate extensions.\n\n"
            + previous
        )
        report = {
            "generator": backend,
            "generatorVersion": importlib.metadata.version(backend),
            "definitionSha256": hashlib.sha256(source.encode()).hexdigest(),
            "operations": list(metadata),
            "liveVerified": False,
        }
        (project / "generation.json").write_text(json.dumps(report, indent=2) + "\n")
        publish_tree(project, output)
        return report
