"""Render file-upload methods from portable declarations, never service constants."""

import json
from pathlib import Path

from .browser import BrowserError
from .discovery_manifest.records import Artifact, Kind, Source
from .upload_workflows import read_workflows, write_workflows


def workflows_for(manifest, api_endpoint):
    """Validate every upload document and reject conflicting upstream contracts."""
    selected = {}
    for artifact in manifest.artifacts:
        if artifact.name != "upload-workflows.json":
            continue
        try:
            workflows = read_workflows(artifact.content)
        except (ValueError, TypeError) as error:
            raise BrowserError("Invalid upload workflow document: " + str(error)) from None
        for workflow in workflows:
            if workflow.apiEndpoint != api_endpoint:
                continue
            if workflow.operation in selected and selected[workflow.operation] != workflow:
                raise BrowserError("Conflicting file-upload workflow definitions.")
            selected[workflow.operation] = workflow
    return selected


def install_file_methods(stage, package, metadata, manifest, endpoint):
    """Generate Python file helpers and CLI metadata for matching mutation methods."""
    workflows = workflows_for(manifest, endpoint)
    methods = []
    active = []
    for name, workflow in sorted(workflows.items()):
        if name not in metadata:
            continue
        operation = metadata[name]
        if operation["kind"] != "graphql-operation" or not operation["write"]:
            raise BrowserError("File-upload workflows require the operation-document mutation backend.")
        variables = operation["variables"]
        if workflow.inputVariable not in {item["parameter"] for item in variables}:
            raise BrowserError("Upload input variable is missing from the mutation.")
        if workflow.inputVariable in {"receipt_path", "metadata_client", "storage_client"}:
            raise BrowserError("Upload input variable conflicts with file-helper arguments.")
        method = operation["method"] + "_with_file"
        if method in {item["method"] for item in metadata.values()}:
            raise BrowserError("A file-upload method conflicts with an existing generated method.")
        operation["fileUpload"] = workflow.to_dict()
        methods.append(f'''
    def {method}(self, *, receipt_path, metadata_client=None, storage_client=None, **arguments):
        """Upload a file and call {name} once with its discovered attachment field.

        Requires allow_writes=True. arguments are the original operation variables.
        metadata_client authenticates the application upload endpoint. Storage must
        be credential-free. No stage retries; check the service after an uncertain
        failure before submitting again. See upload_runtime.submit_with_file.
        """
        from .upload_runtime import submit_with_file
        return submit_with_file(self, {name!r}, arguments, {workflow.to_dict()!r}, receipt_path,
                                metadata_client=metadata_client, storage_client=storage_client)
''')
        active.append(workflow)
    if active:
        path = stage / package / "client.py"
        path.write_text(path.read_text() + "\n".join(methods))
        for filename in ("upload_workflows.py", "upload_runtime.py"):
            (stage / package / filename).write_text(Path(__file__).with_name(filename).read_text())
        (stage / package / "_operations.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return tuple(active)


def workflow_artifact(workflows):
    """Retain only selected upload contracts in the portable rebuild input."""
    return Artifact.create("upload-workflows.json", Kind.REPORT, write_workflows(workflows), Source.EXTERNAL)


def assign_upload_operations(manifest):
    """Resolve declared upload operations for any upstream manifest producer.

    A workflow explicitly names its API and operation. Keep its operation document
    attached through normalization even when a producer supplied source declarations
    separately. Never choose between conflicting definitions or override a captured
    request already assigned to that API.
    """
    from dataclasses import replace
    from graphql import parse
    from .discovery_manifest.records import Service, Protocol, Confidence

    # Older captures and independent producers may retain source declarations
    # inside their exploration report. Normalize that evidence into the same
    # versioned contract instead of requiring a separate assembly step.
    if not any(item.name == "upload-workflows.json" for item in manifest.artifacts):
        endpoints = {item.endpoint for item in manifest.services if item.protocol is Protocol.GRAPHQL}
        if len(endpoints) == 1:
            from .upload_inference import discover_workflows
            inferred = {}
            parents = []
            for artifact in manifest.artifacts:
                if artifact.name not in {"self-discovery.json", "exploration.json"}:
                    continue
                try:
                    report = json.loads(artifact.content)
                    exploration = report.get("exploration", report)
                    found = discover_workflows(exploration.get("frontendModules", []), next(iter(endpoints)))
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise BrowserError("Invalid upload source declaration report.") from None
                for item in found:
                    if item.operation in inferred and inferred[item.operation] != item:
                        raise BrowserError("Conflicting inferred upload workflows.")
                    inferred[item.operation] = item
                if found:
                    parents.append(artifact.id)
            if inferred:
                contract = Artifact.create("upload-workflows.json", Kind.REPORT,
                    write_workflows(tuple(inferred[name] for name in sorted(inferred))),
                    Source.NORMALIZED, derived_from=tuple(parents))
                manifest = replace(manifest, artifacts=manifest.artifacts + (contract,))
    apis = {item.apiEndpoint for artifact in manifest.artifacts
            if artifact.name == "upload-workflows.json"
            for item in read_workflows(artifact.content)}
    targets = {item.id: item for item in manifest.services}
    indexed = {item.id: item for item in manifest.artifacts}
    for api in sorted(apis):
        workflows = workflows_for(manifest, api)
        matches = [item for item in targets.values() if item.endpoint == api and item.protocol is Protocol.GRAPHQL]
        if len(matches) > 1:
            raise BrowserError("Upload API selection is ambiguous.")
        target = matches[0] if matches else Service.create(Protocol.GRAPHQL, api, confidence=Confidence.DECLARED)
        assigned_names = set()
        for reference in target.operation_artifacts:
            assigned_names.update(node.name.value for node in parse(indexed[reference].content).definitions
                                  if node.kind == "operation_definition" and node.name)
        for name in workflows:
            if name in assigned_names:
                continue
            candidates = [item for item in manifest.artifacts if item.kind is Kind.GRAPHQL_OPERATIONS
                          and item.name != "operations.graphql"
                          and any(node.kind == "operation_definition" and node.name and node.name.value == name
                                  for node in parse(item.content).definitions)]
            if len({item.content for item in candidates}) != 1:
                raise BrowserError("Upload operation document is missing or conflicting: " + name)
            target = replace(target, operation_artifacts=target.operation_artifacts + (candidates[0].id,))
        targets[target.id] = target
    return replace(manifest, services=tuple(targets.values()))
