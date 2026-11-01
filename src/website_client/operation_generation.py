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


def operation_docstring(operation):
    """Describe only behavior known from the captured operation contract.

    A captured document provides names and declared types, not authoritative
    business descriptions. Do not invent meanings for custom input fields.
    """
    action = "Send the GraphQL mutation" if operation.write else "Run the GraphQL query"
    lines = [f"{action} {operation.name}.", ""]
    if operation.write:
        lines.extend(["Requires a client constructed with allow_writes=True.", ""])
    if operation.variables:
        lines.append("Args:")
        for variable in operation.variables:
            requirement = (
                "Required."
                if variable.required
                else "Defaults to UNSET, leaving the variable for the server to resolve."
            )
            lines.append(
                f"    {variable.parameter}: GraphQL ${variable.name}: {variable.type}. {requirement}"
            )
        lines.append("")
    lines.extend(
        [
            "Returns:",
            "    The response's data object as a JSON dictionary. Response fields and",
            "    custom input fields are not validated without a server schema.",
            "",
            "Raises:",
            "    ValueError: Declared variables are invalid or mutation permission is missing.",
            "    OperationError: The server returns a non-success HTTP status.",
            "    RuntimeError: The response is oversized, invalid JSON or contains GraphQL errors.",
            "    httpx.HTTPError: The HTTP transport fails. Browser login can raise BrowserError.",
            "",
            "No automatic retry occurs. Check the service before retrying an uncertain write.",
        ]
    )
    return '        """' + "\n        ".join(lines) + '\n        """\n'


def generate_operation_client(
    manifest: Manifest, target: Service, output, name, lineage
):
    """Publish a standalone SDK from one service's captured operation documents.

    Compile and validate every document before writing a temporary project.
    Publication refuses an existing output; authentication is never copied from
    the manifest into the SDK. Request conventions come from captured evidence.
    """
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
            + operation_docstring(operation)
            + f"        values = {arguments}\n"
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
            "class Client(OperationClient):\n"
            '    """Call captured operations using the shared authenticated HTTP client.\n\n'
            "    Use as a context manager. See OperationClient for connection ownership,\n"
            "    authentication inputs and write permission. Operation methods document\n"
            "    declared variables; they do not assert complete server API coverage.\n"
            '    """\n\n' + "\n".join(methods)
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
