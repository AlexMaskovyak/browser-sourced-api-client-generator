"""Preserve explicit workflow source references across safe, offline manifests."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urljoin, urlsplit

import yaml

from .admission import sensitive
from .browser import BrowserError
from .discovery_manifest.model import encoded, endpoint_url, sha256
from .discovery_manifest.records import Artifact, Kind, Manifest, Source
from .runtime_profile import service_origin
from .workflow_admission import IDENTIFIER

MAX_SOURCE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SourceBinding:
    """A native OpenAPI artifact and an optional explicitly supplied endpoint."""

    artifact_id: str = field(repr=False)
    endpoint: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise ValueError("invalid_source_binding")
        if self.endpoint is not None:
            service_origin(self.endpoint)
            endpoint_url(self.endpoint)

    @classmethod
    def from_dict(cls, value: Any) -> "SourceBinding":
        # String values keep earlier saved evidence readable; new reports use the
        # object form so an explicit endpoint survives regeneration as well.
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, dict) or set(value) - {"artifact", "endpoint"}:
            raise ValueError("invalid_source_binding")
        return cls(value.get("artifact"), value.get("endpoint"))

    def to_dict(self) -> dict[str, str]:
        value = {"artifact": self.artifact_id}
        if self.endpoint is not None:
            value["endpoint"] = self.endpoint
        return value


@dataclass
class InputRegistry:
    """Exact initial file facts kept in memory, never private paths in reports."""

    documents: dict[str, set[SourceBinding]] = field(default_factory=dict, repr=False)
    workflow_locations: dict[str, str] = field(default_factory=dict, repr=False)
    references: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.documents, dict) or any(
            not isinstance(address, str)
            or not isinstance(targets, set)
            or any(not isinstance(target, SourceBinding) for target in targets)
            for address, targets in self.documents.items()
        ):
            raise ValueError("invalid_input_registry")
        if not isinstance(self.workflow_locations, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.workflow_locations.items()
        ):
            raise ValueError("invalid_input_registry")
        if not isinstance(self.references, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, dict)
            or any(
                not isinstance(name, str) or not isinstance(address, str)
                for name, address in value.items()
            )
            for key, value in self.references.items()
        ):
            raise ValueError("invalid_input_registry")

    def register_workflow(
        self, artifact: Artifact, location: str, references: dict[str, str]
    ) -> None:
        if artifact.kind is not Kind.ARAZZO:
            raise ValueError("invalid_workflow_artifact")
        self.workflow_locations[artifact.id] = location
        self.references[artifact.id] = references


def input_registry() -> InputRegistry:
    """Create an empty initial-input registry without touching the filesystem."""
    return InputRegistry()


def _address(location: str, reference: str) -> str:
    address = urljoin(location, reference)
    parsed = urlsplit(address)
    if (
        parsed.scheme == "file"
        and not parsed.netloc
        and not parsed.query
        and not parsed.fragment
    ):
        return Path(unquote(parsed.path)).resolve().as_uri()
    return address


def workflow_file(value: dict, path: str | Path) -> tuple[dict, str, dict[str, str]]:
    """Remember exact local references while rebasing private filenames before filtering."""
    value = deepcopy(value)
    location = Path(path).resolve().as_uri()
    references = {}
    for source in value.get("sourceDescriptions", []):
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("name"), str)
            or not isinstance(source.get("url"), str)
        ):
            continue
        address = _address(location, source["url"])
        references[source["name"]] = address
        if urlsplit(address).scheme == "file":
            source["url"] = "pending-source-" + sha256(address)[:16] + ".json"
    return value, location, references


def referenced_local_files(path: str | Path) -> list[Path]:
    """Read only declared source files contained in the initially supplied bundle.

    Separately supplied files may live elsewhere and are matched exactly by the
    registry. This function does not grant workflow references new filesystem access.
    """
    try:
        path = Path(path).resolve()
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return []
        document = yaml.safe_load(path.read_text())
        if not isinstance(document, dict) or "arazzo" not in document:
            return []
        result = []
        for source in document.get("sourceDescriptions", []):
            if (
                not isinstance(source, dict)
                or source.get("type") != "openapi"
                or not isinstance(source.get("url"), str)
            ):
                continue
            parsed = urlsplit(_address(path.as_uri(), source["url"]))
            if (
                parsed.scheme != "file"
                or parsed.netloc
                or parsed.query
                or parsed.fragment
            ):
                continue
            selected = Path(unquote(parsed.path)).resolve()
            if selected.is_relative_to(path.parent) and selected.is_file():
                result.append(selected)
        return result
    except Exception:
        return []


def openapi_source(artifact: Artifact) -> dict | None:
    """Recognize an executable OpenAPI artifact admitted by the shared policy."""
    if artifact.kind is not Kind.OPENAPI:
        return None
    try:
        value = yaml.safe_load(artifact.content)
        return value if isinstance(value, dict) and "openapi" in value else None
    except Exception:
        return None


def register_definition(
    manifest: Manifest,
    path: str | Path,
    registry: InputRegistry,
    endpoint: str | None = None,
) -> None:
    # Source reports retain evidence after destructive filtering. Runtime binding
    # must use admitted OpenAPI artifacts so excluded essential structure cannot
    # reappear as a weakened executable request schema.
    selected = [item.id for item in manifest.artifacts if item.kind is Kind.OPENAPI]
    if len(selected) == 1:
        address = Path(path).resolve().as_uri()
        registry.documents.setdefault(address, set()).add(
            SourceBinding(selected[0], endpoint)
        )


def clean_binding_report(value: Any) -> dict[str, dict[str, dict[str, str]]]:
    """Filter source associations structurally; membership is checked against a manifest."""
    result = {}
    if not isinstance(value, dict):
        return result
    for document, sources in value.items():
        if (
            not isinstance(document, str)
            or (sensitive(document) and not _hashed_id(document))
            or not isinstance(sources, dict)
        ):
            continue
        selected = {}
        for name, target in sources.items():
            try:
                if (
                    not isinstance(name, str)
                    or not IDENTIFIER.fullmatch(name)
                    or sensitive(name)
                ):
                    continue
                binding = SourceBinding.from_dict(target)
                if sensitive(binding.artifact_id) and not _hashed_id(
                    binding.artifact_id
                ):
                    continue
                if binding.endpoint is not None and sensitive(binding.endpoint):
                    continue
                selected[name] = binding.to_dict()
            except (ValueError, TypeError, BrowserError):
                continue
        if selected:
            result[document] = selected
    return result


def _hashed_id(value: str) -> bool:
    from .privacy import ARTIFACT_ID

    return bool(ARTIFACT_ID.fullmatch(value))


def _report_candidates(manifest: Manifest) -> dict[tuple[str, str], set[SourceBinding]]:
    """Validate known workflow/source identities and quarantine conflicting reports."""
    artifacts = {item.id: item for item in manifest.artifacts}
    candidates = {}
    for artifact in manifest.artifacts:
        if artifact.kind is not Kind.REPORT:
            continue
        try:
            value = json.loads(artifact.content)
            records = clean_binding_report(value.get("workflowBindings", {}))
        except (ValueError, TypeError, AttributeError):
            continue
        for document, sources in records.items():
            if document not in artifacts or artifacts[document].kind is not Kind.ARAZZO:
                continue
            declared = {
                item.get("name")
                for item in json.loads(artifacts[document].content).get(
                    "sourceDescriptions", []
                )
                if isinstance(item, dict)
            }
            for name, target in sources.items():
                binding = SourceBinding.from_dict(target)
                if (
                    name not in declared
                    or binding.artifact_id not in artifacts
                    or openapi_source(artifacts[binding.artifact_id]) is None
                ):
                    continue
                candidates.setdefault((document, name), set()).add(binding)
    return candidates


def binding_conflicts(manifest: Manifest) -> set[tuple[str, str]]:
    """Return source names with contradictory validated saved associations."""
    return {
        key for key, targets in _report_candidates(manifest).items() if len(targets) > 1
    }


def report_bindings(manifest: Manifest) -> dict[str, dict[str, SourceBinding]]:
    """Read only known source identities with a single unambiguous association."""
    bindings = {}
    for (document, name), candidates in _report_candidates(manifest).items():
        if len(candidates) == 1:
            bindings.setdefault(document, {})[name] = next(iter(candidates))
    return bindings


def _available_sources(
    manifest: Manifest, registry: InputRegistry
) -> dict[str, set[SourceBinding]]:
    available = {
        address: set(targets) for address, targets in registry.documents.items()
    }
    for artifact in manifest.artifacts:
        if artifact.kind is Kind.OPENAPI:
            for location in artifact.provenance.locations:
                available.setdefault(location, set()).add(SourceBinding(artifact.id))
    return available


def _resolve_sources(
    artifact: Artifact,
    registry: InputRegistry,
    available: Mapping[str, set[SourceBinding]],
    previous: dict[str, SourceBinding],
    blocked: set[str],
) -> tuple[dict, dict[str, SourceBinding]]:
    document = json.loads(artifact.content)
    selected = dict(previous)
    locations = (
        [registry.workflow_locations[artifact.id]]
        if artifact.id in registry.workflow_locations
        else list(artifact.provenance.locations)
    )
    for source in document.get("sourceDescriptions", []):
        name = source.get("name")
        if not isinstance(name, str) or sensitive(name) or name in blocked:
            continue
        expected = registry.references.get(artifact.id, {}).get(name)
        addresses = (
            [expected]
            if expected
            else [_address(location, source["url"]) for location in locations]
        )
        matches = (
            set().union(*(available.get(address, set()) for address in addresses))
            if addresses
            else set()
        )
        if name not in selected and len(matches) == 1:
            selected[name] = next(iter(matches))
        if name in selected:
            source["url"] = "sources/" + selected[name].artifact_id + ".json"
    return document, selected


def bind_sources(manifest: Manifest, registry: InputRegistry | None = None) -> Manifest:
    """Persist exact supplied/advertised associations, never proximity or filename guesses."""
    registry = registry or InputRegistry()
    available = _available_sources(manifest, registry)
    bindings = report_bindings(manifest)
    conflicts = binding_conflicts(manifest)
    artifacts = list(manifest.artifacts)
    for index, artifact in enumerate(artifacts):
        if artifact.kind is not Kind.ARAZZO:
            continue
        document, selected = _resolve_sources(
            artifact,
            registry,
            available,
            bindings.get(artifact.id, {}),
            {name for document, name in conflicts if document == artifact.id},
        )
        if selected:
            bindings[artifact.id] = selected
            text = encoded(document)
            artifacts[index] = replace(artifact, content=text, sha256=sha256(text))
    if bindings:
        portable = {
            document: {name: target.to_dict() for name, target in sources.items()}
            for document, sources in bindings.items()
        }
        artifacts.append(
            Artifact.create(
                "workflow-bindings.json",
                Kind.REPORT,
                encoded({"workflowBindings": portable}),
                Source.REPORT,
            )
        )
    return replace(
        manifest,
        artifacts=tuple({artifact.id: artifact for artifact in artifacts}.values()),
    )
