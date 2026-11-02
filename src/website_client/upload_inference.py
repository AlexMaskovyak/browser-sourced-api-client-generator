"""Infer supported file-backed mutation contracts from source declarations."""

import re

def discover_workflows(modules, api_endpoint):
    """Recognize a bounded Active Storage sequence from loaded module declarations.

    Require one literal application endpoint, the protocol implementation, and
    one attachment property in the same module as a one-object-input mutation.
    Ambiguous protocols, fields and unresolved operation fragments do not generate
    workflows. This is declared source evidence, not a verified server schema.
    """
    from .upload_workflows import UploadWorkflow

    endpoints = {url for item in modules for url in item.get("uploadSignals", {}).get("metadataEndpoints", [])}
    implementation = [item["sourceSha256"] for item in modules if item.get("uploadSignals", {}).get("activeStorageImplementation")]
    if not api_endpoint or len(endpoints) != 1 or not implementation:
        return ()
    metadata = next(iter(endpoints))
    evidence = {item["sourceSha256"] for item in modules if metadata in item.get("uploadSignals", {}).get("metadataEndpoints", [])}
    evidence.update(implementation)
    workflows = {}
    conflicts = set()
    for item in modules:
        fields = item.get("uploadSignals", {}).get("attachmentFields", [])
        if len(fields) != 1:
            continue
        for operation in item.get("operations", []):
            variables = operation["variables"]
            if operation["kind"] != "mutation" or not operation["documentExported"] or len(variables) != 1:
                continue
            variable, kind = next(iter(variables.items()))
            if not re.fullmatch(r"[_A-Za-z][_0-9A-Za-z]*Input!?", kind):
                continue
            workflow = UploadWorkflow(operation["name"], api_endpoint, metadata, variable, fields[0],
                                      tuple(sorted(evidence | {item["sourceSha256"]})))
            key = workflow.operation
            if key in workflows and workflows[key].attachmentField != workflow.attachmentField:
                conflicts.add(key)
            workflows[key] = workflow
    return tuple(workflows[key] for key in sorted(workflows) if key not in conflicts)
