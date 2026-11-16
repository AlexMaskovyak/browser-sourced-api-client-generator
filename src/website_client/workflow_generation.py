"""Admit explicit workflow evidence and publish its standalone native runtime."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from .authenticated_http import LIMIT
from .discovery_manifest.records import Artifact, Kind, Manifest
from .discovery_manifest.model import sha256
from .generation import (
    copy_runtime_sources,
    package_name_for,
    publish_tree,
    render_template,
    write_json,
)
from .runtime_profile import service_origin
from .workflow_admission import admit_workflows, check_workflow
from .workflow_binding import (
    SourceBinding,
    binding_conflicts,
    openapi_source,
    report_bindings,
)
from .workflow_schema import WorkflowRejected
from .workflow_inputs import (
    workflow_input_inventory,
    inventory_markdown,
    inventory_example,
)

RUNTIME_SOURCES = {
    name: name
    for name in (
        "authenticated_http.py",
        "runtime_profile.py",
        "capabilities.py",
        "workflow_admission.py",
        "workflow_schema.py",
        "workflow_runtime.py",
    )
} | {
    "runtime.py": "workflow_api.py",
    "_cli.py": "workflow_cli.py",
    "result_output.py": "result_output.py",
}
DEPENDENCIES = Path(__file__).with_name("native_dependencies")
COMPANION = "arazzo_runner-0.9.6+websiteclient.1-py3-none-any.whl"


@dataclass(frozen=True)
class WorkflowEntry:
    """An admitted integration plan; native source documents remain upstream-owned."""

    public_id: str
    native_id: str
    artifact_id: str
    document: dict[str, Any] = field(repr=False)
    sources: dict[str, dict[str, Any]] = field(repr=False)
    source_ids: dict[str, str] = field(repr=False)
    profiles: dict[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        selected = [
            item
            for item in self.document["workflows"]
            if item["workflowId"] == self.native_id
        ]
        if len(selected) != 1:
            raise WorkflowRejected("ambiguous_workflow")
        check_workflow(self.document, selected[0], self.sources)


def _blocked_artifacts(manifest: Manifest) -> set[str]:
    blocked = set()
    for artifact in manifest.artifacts:
        if artifact.kind is not Kind.REPORT:
            continue
        try:
            value = json.loads(artifact.content)
            for exclusion in value.get("exclusions", []):
                if exclusion.get(
                    "reason"
                ) == "conflicting_artifact_identity" and isinstance(
                    exclusion.get("artifact"), str
                ):
                    blocked.add(exclusion["artifact"])
        except (ValueError, TypeError, AttributeError):
            continue
    changed = True
    while changed:
        changed = False
        for artifact in manifest.artifacts:
            if artifact.id not in blocked and blocked.intersection(
                artifact.provenance.derived_from
            ):
                blocked.add(artifact.id)
                changed = True
    return blocked


def _source_document(artifact: Artifact, binding: SourceBinding) -> dict:
    document = openapi_source(artifact)
    if document is None or len(artifact.content.encode()) > LIMIT:
        raise WorkflowRejected("invalid_source_binding")
    document = deepcopy(document)
    if binding.endpoint is not None:
        document["servers"] = [{"url": binding.endpoint}]
    else:
        for server in document.get("servers", []):
            if not urlsplit(server.get("url", "")).scheme:
                if len(artifact.provenance.locations) != 1:
                    raise WorkflowRejected("unresolved_source_destination")
                server["url"] = urljoin(artifact.provenance.locations[0], server["url"])
    return document


def _source_documents(
    document: dict,
    bindings: Mapping[str, SourceBinding],
    artifacts: Mapping[str, Artifact],
    blocked: set[str],
) -> dict[str, dict]:
    sources = {}
    for source in document.get("sourceDescriptions", []):
        name = source["name"]
        binding = bindings.get(name)
        if binding is None or binding.artifact_id not in artifacts:
            raise WorkflowRejected("unresolved_source")
        if binding.artifact_id in blocked:
            raise WorkflowRejected("ambiguous_source")
        sources[name] = _source_document(artifacts[binding.artifact_id], binding)
    return sources


def _document_entries(
    artifact: Artifact,
    bindings: Mapping[str, SourceBinding],
    artifacts: Mapping[str, Artifact],
    blocked: set[str],
    profiles: Mapping[str, str],
) -> tuple[list[WorkflowEntry], list[dict]]:
    document = json.loads(artifact.content)
    if artifact.id in blocked:
        raise WorkflowRejected("ambiguous_source")
    sources = _source_documents(document, bindings, artifacts, blocked)
    admitted, exclusions = admit_workflows(document, sources)
    entries = []
    if admitted is not None:
        for workflow in admitted["workflows"]:
            operations = check_workflow(admitted, workflow, sources)
            origins = {service_origin(item.base) for item in operations.values()}
            selected_profiles = {
                origin: profiles[origin]
                for origin in sorted(origins)
                if origin in profiles
            }
            entries.append(
                WorkflowEntry(
                    workflow["workflowId"],
                    workflow["workflowId"],
                    artifact.id,
                    admitted,
                    sources,
                    {name: target.artifact_id for name, target in bindings.items()},
                    selected_profiles,
                )
            )
    return entries, [{"artifact": artifact.id, **record} for record in exclusions]


def collect_workflows(
    manifest: Manifest, profiles: Mapping[str, str]
) -> tuple[list[WorkflowEntry], list[dict]]:
    """Admit each workflow document independently and assign collision-safe public IDs."""
    artifacts = {item.id: item for item in manifest.artifacts}
    bindings, blocked = report_bindings(manifest), _blocked_artifacts(manifest)
    blocked.update(document for document, _ in binding_conflicts(manifest))
    entries, exclusions = [], []
    for artifact in manifest.artifacts:
        if artifact.kind is not Kind.ARAZZO:
            continue
        try:
            selected, rejected = _document_entries(
                artifact, bindings.get(artifact.id, {}), artifacts, blocked, profiles
            )
            entries.extend(selected)
            exclusions.extend(rejected)
        except (
            WorkflowRejected,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as error:
            reason = (
                str(error)
                if isinstance(error, WorkflowRejected)
                else "invalid_workflow_structure"
            )
            exclusions.append({"artifact": artifact.id, "reason": reason})
    counts = Counter(item.public_id for item in entries)
    entries = [
        replace(item, public_id=item.native_id + "__" + sha256(item.artifact_id)[:12])
        if counts[item.public_id] > 1
        else item
        for item in entries
    ]
    counts = Counter(item.public_id for item in entries)
    unique = []
    for item in entries:
        if counts[item.public_id] == 1:
            unique.append(item)
        else:
            exclusions.append(
                {
                    "artifact": item.artifact_id,
                    "workflow": item.native_id,
                    "reason": "ambiguous_workflow_namespace",
                }
            )
    return unique, exclusions


def _write_definition(module: Path, label: str, document: dict) -> str:
    content = json.dumps(document, sort_keys=True, indent=2) + "\n"
    name = "definitions/" + label + "-" + sha256(content) + ".json"
    (module / name).write_text(content)
    return name


def _write_entries(module: Path, entries: list[WorkflowEntry]) -> dict:
    (module / "definitions").mkdir()
    metadata = {}
    for item in entries:
        metadata[item.public_id] = {
            "nativeId": item.native_id,
            "document": _write_definition(module, "workflow", item.document),
            "sources": {
                name: _write_definition(module, "source", document)
                for name, document in item.sources.items()
            },
            "profiles": item.profiles,
            "provenance": {
                "workflowArtifact": item.artifact_id,
                "sourceArtifacts": item.source_ids,
            },
        }
    write_json(module / "_workflows.json", metadata)
    return metadata


def _write_companion(project: Path) -> None:
    # The wheel is a sibling installation artifact, not an unavailable file URL
    # hidden inside a product wheel that pip has not yet installed.
    shutil.copyfile(DEPENDENCIES / COMPANION, project / COMPANION)
    provenance = project / "native_dependencies"
    provenance.mkdir()
    for path in DEPENDENCIES.iterdir():
        if path.is_file() and path.suffix != ".whl":
            shutil.copyfile(path, provenance / path.name)


def _write_readme(
    project: Path, name: str, package: str, entries: list[WorkflowEntry]
) -> None:
    inventory = workflow_input_inventory(entries)
    table = inventory_markdown(inventory)
    example, explanation = inventory_example(inventory, name, entries[0].public_id)
    text = f"""# Run the packaged workflows

These declared workflows may write remote data using the bundled destinations and
steps. No automatic retries occur, including after a lost response.

## Install

Use Python 3.11 or later. From this directory, install the companion native dependency
and this standalone project together. This changes only the selected Python environment:

```sh
python -m pip install ./{COMPANION} .
```

The native companion fixes explicit JSON null serialization. Its patch, source hash,
license notices and rebuild recipe are in `native_dependencies`. Product wheel metadata
requires its exact version and contains no workstation file URL. When distributing a
built product wheel, copy the companion beside it and supply both wheel paths to pip.

## Run

`{name} --help` lists the packaged workflow IDs without service requests. The public
Python function is `{package}.execute_workflow(workflow_id, inputs=None)`. It uses the
fixed bundled destinations and private machine profiles automatically. An absent
profile association uses an anonymous request context. Missing/expired associated
credentials stop without prompting. Run the generator again from the original website
sign-in URL with a new output directory to renew that machine's stable origin profiles.

The following inventory comes from declared input schemas and explicit whole input
references. Unknown types are not inferred. A referenced input is required before any
request. The bundled schema still owns validation; declared defaults are not persisted.

{table}

For ordinary inputs, create a JSON object file and pass `--input inputs.json`. A binary
input marked in the table accepts `--file NAME=PATH`, reading a regular file up to 8 MiB.
Only an explicit binary request mapping whose input subschema accepts bytes is marked; the complete input schema still validates every call.

{explanation}

```sh
{example}
```

A workflow that needs no inputs can omit both flags. Python accepts bounded bytes
for binary inputs; arbitrary Python file objects are rejected. `--output NEW_FILE`
writes a new private JSON result. A result-output failure preserves the workflow's
remote outcome and effects and exits 1. Status codes are 0 success, 1 workflow/output
failure, 2 usage/input error, and 130 cancellation.

Credentials are never included here. Within the same provisioned machine or runtime
environment, mount its protected profile directory separately from this package.
Another machine needs its own initial browser login. Runtime execution needs neither
Node, a browser executable, nor the generator; the installed Playwright request driver
handles HTTP requests.

## Offline package checks

Optional developer checks require pytest 8 or 9 in this Python environment:

```sh
python -m pip install 'pytest>=8,<10'
python -m pytest -q tests
```

These checks validate installed source associations, the native dependency version,
file-object rejection and component notices. They send no service requests and do not
establish live compatibility. Verify intended operations against your service separately.
Installed notice files are in `{package}/_notices`; the source project includes `NOTICE.md`.
"""
    (project / "README.md").write_text(text)


def generate_workflow_client(
    output: Path, name: str, entries: list[WorkflowEntry]
) -> dict[str, Any]:
    """Stage and exclusively publish one standalone package of admitted workflows."""
    if not entries:
        raise WorkflowRejected("no_executable_workflows")
    package = package_name_for(name)
    with tempfile.TemporaryDirectory(prefix="website-client-workflows-") as directory:
        project = Path(directory) / "project"
        module = project / package
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("from .runtime import execute_workflow\n")
        copy_runtime_sources(module, RUNTIME_SOURCES)
        metadata = _write_entries(module, entries)
        write_json(module / "_inputs.json", workflow_input_inventory(entries))
        (project / "pyproject.toml").write_text(
            render_template("workflows_pyproject.toml.tmpl", name=name, package=package)
        )
        _write_companion(project)
        _write_readme(project, name, package, entries)
        from .project_metadata import write_distribution_support

        write_distribution_support(
            project, package, name, ("arazzo-runner",), workflow=True
        )
        report = {
            "generator": "browser-sourced-api-client-generator",
            "generationMode": "native-arazzo-workflows",
            "workflows": sorted(metadata),
            "liveVerified": False,
            "nativeDependency": "arazzo-runner==0.9.6+websiteclient.1",
            "lineage": {name: item["provenance"] for name, item in metadata.items()},
        }
        write_json(project / "generation.json", report)
        publish_tree(project, output)
    return report
