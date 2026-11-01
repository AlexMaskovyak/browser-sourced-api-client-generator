"""Generate callable Python methods and CLI commands from operation contracts."""

from dataclasses import asdict
import hashlib
import importlib.metadata
from pathlib import Path
import tempfile

from .browser import BrowserError
from .discovery_manifest.records import Kind, Manifest, Service
from .discovery_manifest.model import encoded
from .generation import (
    GeneratedProject,
    package_name_for,
    publish_tree,
    render_template,
    write_json,
    write_project_readme,
    write_runtime_files,
)
from .operation_contracts import compile_operations
from .operation_requests import RequestMethod, RequestStyle, request_styles


def generate_operation_client(
    manifest: Manifest, target: Service, output, name, lineage
):
    artifacts = {item.id: item for item in manifest.artifacts}
    documents = tuple(artifacts[ref] for ref in target.operation_artifacts)
    if any(item.kind is not Kind.GRAPHQL_OPERATIONS for item in documents):
        raise BrowserError("Operation client requires operation documents.")
    try:
        operations = compile_operations(tuple(item.content for item in documents))
    except ValueError as error:
        raise BrowserError(str(error)) from None
    package = package_name_for(name)
    styles = request_styles(manifest, target.endpoint)
    metadata, methods = {}, []
    for operation in operations:
        style = styles.get(operation.name, RequestStyle())
        if operation.write and style.method is RequestMethod.GET:
            raise BrowserError("A captured mutation cannot be generated as GET.")
        metadata[operation.name] = {
            "kind": "graphql-operation",
            "method": operation.method,
            "httpMethod": style.method.value,
            "operationNameInUrl": style.operation_name_in_url,
            "requestStyleObserved": style.observed,
            "write": operation.write,
            "document": operation.document,
            "variables": [asdict(item) for item in operation.variables],
            "description": "Captured GraphQL "
            + operation.kind
            + "; complete schema not required.",
        }
        parameters = [
            item.parameter + ": Any" + ("" if item.required else " = UNSET")
            for item in operation.variables
        ]
        signature = "self" + (", *, " + ", ".join(parameters) if parameters else "")
        arguments = (
            "{"
            + ", ".join(
                repr(item.parameter) + ": " + item.parameter
                for item in operation.variables
            )
            + "}"
        )
        methods.append(
            f"    def {operation.method}({signature}) -> dict[str, Any]:\n"
            f"        values = {arguments}\n"
            f"        return self._execute({operation.name!r}, {{key: value for key, value in values.items() if value is not UNSET}})\n"
        )
    with tempfile.TemporaryDirectory(prefix="website-client-operations-") as directory:
        project = Path(directory) / "project"
        module = project / package
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("from .client import Client\n")
        (module / "client.py").write_text(
            '"""Methods generated from captured operation documents."""\n\n'
            "from typing import Any\nfrom .operation_runtime import OperationClient, UNSET\n\n\n"
            "class Client(OperationClient):\n" + "\n".join(methods)
        )
        for filename in ("operation_contracts.py", "operation_runtime.py"):
            (module / filename).write_text(
                Path(__file__).with_name(filename).read_text()
            )
        (project / "pyproject.toml").write_text(
            render_template(
                "operations_pyproject.toml.tmpl", name=name, package=package
            )
        )
        generated = GeneratedProject(
            project, package, metadata, "browser-sourced-api-client-generator"
        )
        write_runtime_files(generated)
        sources = project / "operations"
        sources.mkdir()
        for operation in operations:
            (sources / (operation.method + ".graphql")).write_text(operation.document)
        (project / "README.md").write_text(
            "## Operation documents and optional schemas\n\n"
            "This client uses captured operation documents. It checks declared variable names, "
            "required values, nullability, list elements and standard GraphQL scalars. "
            "Custom input types remain JSON values; their fields and response shapes are not schema-validated. "
            "Responses are JSON dictionaries. Generation does not prove live compatibility.\n\n"
            "Python mutation methods require `Client(..., allow_writes=True)`. "
            "The CLI requires `--confirm-write OPERATION`. No automatic retries occur.\n"
        )
        write_project_readme(generated, name)
        report = {
            "generator": generated.generator_name,
            "generatorVersion": importlib.metadata.version(generated.generator_name),
            "generationMode": "operation-documents",
            "schemaValidated": False,
            "responseModels": "JSON dictionaries",
            "operations": [item.name for item in operations],
            "operationsSha256": hashlib.sha256(encoded(metadata).encode()).hexdigest(),
            "operationDocumentSha256": {
                item.name: hashlib.sha256(item.document.encode()).hexdigest()
                for item in operations
            },
            "liveVerified": False,
            "lineage": lineage,
            "limitations": [
                "Unknown custom input fields and response types require an optional schema or service validation."
            ],
        }
        write_json(project / "generation.json", report)
        publish_tree(project, output)
        return report
