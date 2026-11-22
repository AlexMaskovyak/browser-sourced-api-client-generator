"""Describe declared workflow inputs without inventing types or source connections."""

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, Sequence

from .capabilities import resolved
from .workflow_admission import INPUT, check_workflow
from .workflow_runtime import referenced_inputs
from .workflow_schema import WorkflowRejected, validate_value

if TYPE_CHECKING:
    from .workflow_generation import WorkflowEntry


@dataclass(frozen=True)
class InputDescription:
    """Portable user-facing facts about an already admitted workflow input."""

    name: str
    required: bool
    declared_type: str
    binary: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not isinstance(self.declared_type, str)
            or type(self.required) is not bool
            or type(self.binary) is not bool
        ):
            raise ValueError("invalid_input_description")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "type": self.declared_type,
            "binary": self.binary,
        }


def _binary_references(workflow: dict, operations: dict) -> set[str]:
    names = set()
    for step in workflow["steps"]:
        body = step.get("requestBody", {})
        payload, media = body.get("payload"), body.get("contentType", "")
        info = operations[step["stepId"]]
        definition = resolved(info.document, info.operation.get("requestBody", {}))
        schema = resolved(
            info.document,
            definition.get("content", {}).get(media, {}).get("schema", {}),
        )
        if schema.get("format") == "binary" and isinstance(payload, str):
            match = INPUT.fullmatch(payload)
            if match:
                names.add(match[1])
        if media != "multipart/form-data" or not isinstance(payload, dict):
            continue
        for name, value in payload.items():
            field = resolved(info.document, schema.get("properties", {}).get(name, {}))
            reference = value.get("content") if isinstance(value, dict) else value
            if field.get("format") == "binary" and isinstance(reference, str):
                match = INPUT.fullmatch(reference)
                if match:
                    names.add(match[1])
    return names


def _declared_type(schema: Any) -> str:
    if type(schema) is bool:
        return "unknown (no declared type)" if schema else "no allowed values"
    kind = schema.get("type")
    if isinstance(kind, list) and all(isinstance(item, str) for item in kind):
        return " or ".join(kind)
    if not isinstance(kind, str):
        return "unknown (no declared type)"
    format_name = schema.get("format")
    return kind + ("; format " + format_name if isinstance(format_name, str) else "")


def _accepts_bytes(schema: Any) -> bool:
    try:
        validate_value(b"", schema)
        return True
    except WorkflowRejected:
        return False


def workflow_input_inventory(
    entries: Sequence["WorkflowEntry"],
) -> dict[str, list[dict[str, Any]]]:
    """Report only explicit input schemas and whole input references in native steps."""
    inventory = {}
    for entry in entries:
        workflow = next(
            item
            for item in entry.document["workflows"]
            if item["workflowId"] == entry.native_id
        )
        operations = check_workflow(entry.document, workflow, entry.sources)
        binary_names = _binary_references(workflow, operations)
        schema = workflow.get("inputs", {})
        properties = schema.get("properties", {})
        references = referenced_inputs(workflow)
        required = set(schema.get("required", [])) | references
        descriptions = []
        for name in sorted(set(properties) | references | required):
            try:
                field = resolved(schema, properties.get(name, {}))
                known = True
            except ValueError:
                field, known = {}, False
            descriptions.append(
                InputDescription(
                    name,
                    name in required,
                    _declared_type(field),
                    known and name in binary_names and _accepts_bytes(field),
                ).to_dict()
            )
        inventory[entry.public_id] = descriptions
    return inventory


def inventory_markdown(inventory: dict[str, list[dict]]) -> str:
    """Render the same input facts shown by installed command help."""
    rows = [
        "| Workflow | Input | Required | Declared type | Binary file input |",
        "| --- | --- | --- | --- | --- |",
    ]
    for workflow, inputs in sorted(inventory.items()):
        if not inputs:
            rows.append(f"| `{workflow}` | none | no | none | no |")
        for item in inputs:
            name = item["name"].replace("|", "\\|")
            kind = item["type"].replace("|", "\\|")
            binary = f"`--file {name}=PATH`" if item["binary"] else "no"
            rows.append(
                f"| `{workflow}` | `{name}` | {'yes' if item['required'] else 'no'} | {kind} | {binary} |"
            )
    return "\n".join(rows) + "\n"


def inventory_example(
    inventory: dict[str, list[dict]], distribution: str, first: str
) -> tuple[str, str]:
    """Show real names and all required flags without inventing input values."""
    selected = next(
        (
            workflow
            for workflow, inputs in inventory.items()
            if any(item["binary"] for item in inputs)
        ),
        first,
    )
    inputs = inventory[selected]
    ordinary = any(item["required"] and not item["binary"] for item in inputs)
    binary = [item["name"] for item in inputs if item["required"] and item["binary"]]
    command = distribution + " " + selected
    notes = []
    if ordinary:
        command += " --input inputs.json"
        notes.append(
            "Create `inputs.json` as a JSON object containing the required ordinary input names in the table, with values matching their declared schemas."
        )
    for name in binary:
        command += " --file " + name + "=payload.bin"
    if binary:
        notes.append(
            "Supply your own existing `payload.bin` file for each binary input shown; use distinct file paths when the workflow needs different files."
        )
    if not notes:
        notes.append("This packaged workflow needs no required inputs.")
    return command, " ".join(notes)
