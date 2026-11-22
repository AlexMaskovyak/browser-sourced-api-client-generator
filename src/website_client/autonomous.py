"""One deterministic URL or saved-input path through the existing pipeline owners."""

from dataclasses import dataclass, field, replace
from typing import Any, Callable
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit

import yaml

from .admission import admit_openapi, METHODS
from .browser import BrowserError, BrowserSession
from .discovery import import_har, new_output, Discovery
from .discovery_manifest import encoded, merge_manifests
from .discovery_manifest.records import (
    Artifact,
    Confidence,
    Kind,
    Manifest,
    Producer,
    Protocol,
    Service,
    Source,
)
from .generation import generate, package_name_for
from .normalization import normalize_manifest
from .privacy import sanitized_manifest, safe_workflow
from .runtime_profile import OriginCaptures, save_browser_profile, service_origin
from .workflow_binding import (
    InputRegistry,
    bind_sources,
    referenced_local_files,
    register_definition,
    workflow_file,
)


def _profile_id(url):
    return "origin_" + hashlib.sha256(service_origin(url).encode()).hexdigest()[:24]


def _report_artifact(
    name: str, exclusions: list[dict], metadata: dict | None = None
) -> Artifact:
    return Artifact.create(
        name,
        Kind.REPORT,
        encoded({**(metadata or {}), "exclusions": exclusions}),
        Source.REPORT,
    )


def _rest_entries(discovery: Discovery) -> list[dict]:
    """Select observed API traffic without turning navigation into API evidence."""
    entries = []
    for entry in discovery.entries:
        request = entry["request"]
        content = entry["response"].get("content", {})
        if (
            entry.get("_definitionProbe")
            or entry.get("_resourceType") == "Document"
            or not any(kind in content.get("mimeType", "") for kind in ("json", "yaml"))
        ):
            continue
        if any(
            request["url"].split("?", 1)[0] == endpoint.split("?", 1)[0]
            for endpoint in discovery.graphql_endpoints
        ):
            continue
        try:
            value = json.loads(content.get("text", ""))
        except ValueError:
            value = None
        if isinstance(value, dict) and any(
            name in value for name in ("openapi", "arazzo", "__schema")
        ):
            continue
        entries.append(entry)
    return entries


def _exclude_unknown_bodies(
    discovery: Discovery, executable: dict, exclusions: list[dict]
) -> None:
    for item in discovery.endpoints.values():
        if not (item.get("requestBodyUnmodeled") or item.get("privacyUnmodeled")):
            continue
        path, method = item["path"], item["method"].lower()
        reason = (
            "sensitive_structure"
            if item.get("privacyUnmodeled")
            else "unknown_request_body"
        )
        if path in executable["paths"] and method in executable["paths"][path]:
            del executable["paths"][path][method]
            exclusions.append(
                {"path": path, "method": method.upper(), "reason": reason}
            )
            if not any(key in METHODS for key in executable["paths"][path]):
                del executable["paths"][path]


def _infer_group(
    discovery: Discovery, group: list[dict], index: int
) -> tuple[list[Artifact], list[Service]]:
    from .har_inference import infer_har

    safe_har = json.dumps({"log": {"version": "1.2", "entries": group}})
    spec, diagnostics = infer_har(safe_har)
    _, executable, exclusions = admit_openapi(spec)
    _exclude_unknown_bodies(discovery, executable, exclusions)
    unknown_responses = sum(
        "text" not in entry["response"].get("content", {}) for entry in group
    )
    if unknown_responses:
        exclusions.append(
            {"reason": "response_body_unknown", "count": unknown_responses}
        )
    artifact = Artifact.create(
        f"har2sdk-openapi-{index:04d}.json",
        Kind.OPENAPI,
        encoded(executable),
        Source.INFERRED,
        producer=Producer("har2sdk", "0.6.15"),
    )
    services = []
    if executable["paths"]:
        for server in executable.get("servers", []):
            endpoint = server["url"]
            services.append(
                Service.create(
                    Protocol.OPENAPI,
                    endpoint,
                    artifact.id,
                    confidence=Confidence.INFERRED,
                    identifier="service-har-"
                    + hashlib.sha256(endpoint.encode()).hexdigest()[:16],
                )
            )
    report = _report_artifact(
        f"har2sdk-report-{index:04d}.json", exclusions, metadata=diagnostics
    )
    return [artifact, report], services


def _har_contract(discovery: Discovery) -> Manifest:
    """Feed safe REST traffic to har2sdk while retaining native definition owners."""
    from .bundle_capture import capture_manifest

    files = {
        name: content
        for name, content in discovery.files("har").items()
        if not name.startswith("openapi-inferred")
    }
    manifest = capture_manifest(discovery, files)
    groups: dict[str, list[dict]] = {}
    for entry in _rest_entries(discovery):
        groups.setdefault(service_origin(entry["request"]["url"]), []).append(entry)
    artifacts, services = list(manifest.artifacts), list(manifest.services)
    if discovery.omitted or discovery.capture_failures:
        artifacts.append(
            _report_artifact(
                "capture-loss.json",
                [
                    {
                        "reason": "privacy_or_capture_omission",
                        "count": discovery.omitted + discovery.capture_failures,
                    }
                ],
            )
        )
    for index, (_, group) in enumerate(sorted(groups.items())):
        try:
            inferred_artifacts, inferred_services = _infer_group(
                discovery, group, index
            )
        except BrowserError as error:
            if str(error).startswith("har_dependency_required") and services:
                artifacts.append(
                    _report_artifact(
                        f"har-dependency-{index:04d}.json",
                        [{"reason": "har_dependency_required"}],
                    )
                )
                continue
            raise
        artifacts.extend(inferred_artifacts)
        services.extend(inferred_services)
    return Manifest.create(tuple(artifacts), tuple(services))


def _captured_manifest(manifest: Manifest) -> Manifest:
    """Reuse converted evidence, replacing only the obsolete legacy HAR inference."""
    manifest = sanitized_manifest(manifest)
    har_contracts = []
    converted = any(
        item.kind is Kind.OPENAPI
        and any(producer.name == "har2sdk" for producer in item.provenance.producers)
        for item in manifest.artifacts
    )
    for item in manifest.artifacts:
        if item.kind is Kind.HAR and not converted:
            discovery = Discovery()
            for entry in json.loads(item.content)["log"]["entries"]:
                discovery.add(entry)
            har_contracts.append(sanitized_manifest(_har_contract(discovery)))
    if har_contracts:
        by_id = {item.id: item for item in manifest.artifacts}
        targets = tuple(
            target
            for target in manifest.services
            if not (
                target.confidence is Confidence.INFERRED
                and target.schema_artifact in by_id
                and by_id[target.schema_artifact].name.startswith("openapi-inferred")
            )
        )
        manifest = merge_manifests(
            (replace(manifest, services=targets), *har_contracts)
        )
    return manifest


def _definition_input(
    path: str | Path,
    text: str,
    value: Any,
    endpoint: str | None,
    operations: str | Path | None,
    bindings: InputRegistry | None = None,
) -> Manifest:
    from .pipeline import import_definition

    options = SimpleNamespace(
        openapi=None,
        graphql_schema=None,
        operations=operations,
        endpoint=endpoint,
        service_id=None,
        producer="external-definition",
        producer_version="unspecified",
    )
    if isinstance(value, dict) and "openapi" in value:
        options.openapi = path
    else:
        from .definitions import graphql_schema

        try:
            graphql_schema(text)
            options.graphql_schema = path
        except BrowserError:
            options.operations = path
    result = sanitized_manifest(import_definition(options))
    if bindings is not None and options.openapi:
        register_definition(result, path, bindings, endpoint)
    return result


def _saved_input(
    path: str | Path,
    *,
    endpoint: str | None = None,
    operations: str | Path | None = None,
    bindings: InputRegistry | None = None,
) -> Manifest:
    """Classify the supplied file only; never fetch a target or a referenced URL."""
    from .definitions import MAX_DOCUMENT
    from .discovery_manifest import read_text

    text = read_text(path, 32 * 1024 * 1024)
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        value = None
    if isinstance(value, dict) and value.get("format") == "website-client-discovery":
        return _captured_manifest(Manifest.from_dict(value))
    if isinstance(value, dict) and isinstance(value.get("log"), dict):
        return _har_contract(import_har(path))
    if len(text.encode()) > MAX_DOCUMENT:
        raise BrowserError("unsupported: definition exceeds 8 MiB")
    if isinstance(value, dict) and "arazzo" in value:
        value, location, references = workflow_file(value, path)
        item = Artifact.create(
            "workflow.json", Kind.ARAZZO, encoded(safe_workflow(value)), Source.EXTERNAL
        )
        if bindings is not None:
            bindings.register_workflow(item, location, references)
        return Manifest.create((item,), ())
    return _definition_input(path, text, value, endpoint, operations, bindings)


def _capture_profiles(
    session: BrowserSession, credentials: OriginCaptures
) -> tuple[dict[str, str], list[dict]]:
    """Seed exact-origin profiles only from observed ownership evidence."""
    from .runtime_profile import scoped_state

    profiles, exclusions = {}, []
    state = session._context.storage_state()
    for api_origin, capture in credentials.captures.items():
        profile = _profile_id(api_origin)
        owned = credentials.cookies_for(api_origin)
        suppressed = sum(
            (cookie.get("name"), cookie.get("value")) not in owned
            for cookie in scoped_state(state, api_origin)["cookies"]
        )
        if suppressed:
            exclusions.append(
                {"reason": "cookie_origin_isolation", "count": suppressed}
            )
        save_browser_profile(
            session._context,
            profile,
            api_origin,
            capture.headers,
            capture.allowed_headers,
            owned_cookies=owned,
        )
        profiles[api_origin] = profile
    return profiles, exclusions


def _initial_definitions(session: BrowserSession) -> list[str]:
    from .self_discovery import schema_url

    links = session._page.locator("a[href], link[href]")
    return [
        selected
        for index in range(min(links.count(), 200))
        if (
            selected := schema_url(
                session._url,
                links.nth(index).get_attribute("href"),
                advertised="service-desc"
                in (links.nth(index).get_attribute("rel") or "").split(),
            )
        )
    ]


def _explore_session(
    session: BrowserSession,
    observer: Any,
    credentials: OriginCaptures,
    allowed_origins: set[str],
    *,
    max_pages: int,
    max_actions: int,
    max_depth: int,
    duration_seconds: float,
) -> tuple[Discovery, dict]:
    from .self_discovery import SelfDiscovery
    from .exploration import SiteExplorer

    initial_definitions = _initial_definitions(session)
    explorer = SiteExplorer(
        session,
        max_pages,
        max_actions,
        max_depth,
        duration_seconds,
        strict_origin=True,
        api_origins=allowed_origins,
        credential_observer=credentials,
    )
    explorer.prepare()
    return SelfDiscovery(
        session,
        observer,
        explorer=explorer,
        autonomous=True,
        credential_observer=credentials,
        initial_definitions=initial_definitions,
        allowed_origins=allowed_origins,
    ).run()


def _browser_manifest(
    discovery: Discovery, profiles: dict[str, str], cookie_exclusions: list[dict]
) -> Manifest:
    manifest = _har_contract(discovery)
    if cookie_exclusions:
        report_artifact = Artifact.create(
            "cookie-scope-report.json",
            Kind.REPORT,
            encoded({"exclusions": cookie_exclusions}),
            Source.REPORT,
        )
        manifest = replace(manifest, artifacts=manifest.artifacts + (report_artifact,))
    association = Artifact.create(
        "runtime-associations.json",
        Kind.REPORT,
        encoded({"runtimeProfiles": profiles}),
        Source.REPORT,
    )
    manifest = replace(manifest, artifacts=manifest.artifacts + (association,))
    return sanitized_manifest(manifest)


def _browser_input(
    url,
    *,
    browser="chrome",
    browser_path=None,
    login_handler=None,
    headless=False,
    max_pages=20,
    max_actions=60,
    max_depth=3,
    duration_seconds=120,
):
    from .self_discovery import DiscoveryObserver
    from .browser import origin

    allowed_origins = {origin(url)}
    observer, credentials = DiscoveryObserver(url, allowed_origins), OriginCaptures(url)

    def observe(method, params):
        observer.event(method, params)
        credentials.observe(method, params)

    with BrowserSession(
        url,
        browser,
        browser_path,
        on_event=observe,
        block_service_workers=True,
        headless=headless,
    ) as session:
        if login_handler is None:
            session.confirm_login(
                prompt="Sign in, complete verification and return to the requested website. Press Enter to finish login; discovery and generation then run automatically."
            )
        else:
            login_handler(session)
        discovery, report = _explore_session(
            session,
            observer,
            credentials,
            allowed_origins,
            max_pages=max_pages,
            max_actions=max_actions,
            max_depth=max_depth,
            duration_seconds=duration_seconds,
        )
        profiles, cookie_exclusions = _capture_profiles(session, credentials)
    from .privacy import safe_ui_report

    manifest = _browser_manifest(discovery, profiles, cookie_exclusions)
    return manifest, safe_ui_report(report), profiles, set(profiles)


@dataclass(frozen=True)
class RunRequest:
    """Validated public generation options, with private locations hidden in repr."""

    source: str | Path = field(repr=False)
    output: Path = field(repr=False)
    name: str = "service-client"
    inputs: tuple[str | Path, ...] = field(default=(), repr=False)
    endpoint: str | None = field(default=None, repr=False)
    operations: str | Path | None = field(default=None, repr=False)
    browser: str = "chrome"
    browser_path: str | None = field(default=None, repr=False)
    login_handler: Callable | None = field(default=None, repr=False)
    headless: bool = False
    max_pages: int = 20
    max_actions: int = 60
    max_depth: int = 3
    duration_seconds: float = 120

    def __post_init__(self) -> None:
        from .runtime_profile import profile_directory

        from .exploration import validate_exploration

        package_name_for(self.name)
        validate_exploration(
            self.max_pages, self.max_actions, self.max_depth, self.duration_seconds
        )
        if len(self.inputs) > 19:
            raise BrowserError("invalid_input_count")
        if profile_directory().resolve().is_relative_to(self.output.resolve()):
            raise BrowserError("profile_inside_output")


@dataclass
class CollectedInputs:
    """In-memory evidence and origin associations before any output is persisted."""

    manifests: list[Manifest] = field(repr=False)
    profiles: dict[str, str] = field(default_factory=dict, repr=False)
    allowed_origins: set[str] | None = field(default=None, repr=False)
    discovery_report: dict[str, Any] | None = field(default=None, repr=False)
    bindings: InputRegistry = field(default_factory=InputRegistry, repr=False)


def _initial_paths(request: RunRequest) -> list[str | Path]:
    paths = list(request.inputs)
    if urlsplit(str(request.source)).scheme not in {"http", "https"}:
        paths.insert(0, request.source)
    for path in list(paths):
        for declared in referenced_local_files(path):
            if all(Path(existing).resolve() != declared for existing in paths):
                paths.append(declared)
    if len(paths) > 20:
        raise BrowserError("invalid_input_count")
    return paths


def _supplied_file(
    request: RunRequest, path: str | Path, registry: InputRegistry, index: int
) -> Manifest:
    explicit = [*request.inputs]
    if urlsplit(str(request.source)).scheme not in {"http", "https"}:
        explicit.append(request.source)
    initially_supplied = any(
        Path(item).resolve() == Path(path).resolve() for item in explicit
    )
    try:
        manifest = _saved_input(
            path,
            endpoint=request.endpoint,
            operations=request.operations if str(path) == str(request.source) else None,
            bindings=registry,
        )
        if not initially_supplied and not any(
            item.kind is Kind.OPENAPI for item in manifest.artifacts
        ):
            raise BrowserError("unresolved_source")
        return manifest
    except Exception:
        if initially_supplied:
            raise
        # An unreadable or invalid declared OpenAPI source excludes its dependent
        # workflow, while separately supplied valid services remain available.
        report = _report_artifact(
            f"workflow-source-loss-{index:04d}.json", [{"reason": "unresolved_source"}]
        )
        return Manifest.create((report,), ())


def _collect_inputs(request: RunRequest) -> CollectedInputs:
    collected = CollectedInputs([])
    if urlsplit(str(request.source)).scheme in {"http", "https"}:
        manifest, report, profiles, allowed = _browser_input(
            str(request.source),
            browser=request.browser,
            browser_path=request.browser_path,
            login_handler=request.login_handler,
            headless=request.headless,
            max_pages=request.max_pages,
            max_actions=request.max_actions,
            max_depth=request.max_depth,
            duration_seconds=request.duration_seconds,
        )
        collected.manifests.append(manifest)
        collected.discovery_report, collected.profiles, collected.allowed_origins = (
            report,
            profiles,
            allowed,
        )
    for index, path in enumerate(_initial_paths(request)):
        collected.manifests.append(
            _supplied_file(request, path, collected.bindings, index)
        )
    if (
        sum(len(encoded(item.to_dict()).encode()) for item in collected.manifests)
        > 64 * 1024 * 1024
    ):
        raise BrowserError("input_budget_exceeded")
    return collected


def _merge_inputs(manifests: list[Manifest]) -> tuple[Manifest, list[dict[str, str]]]:
    # Preserve distinct service namespaces, quarantining only conflicting owners.
    conflicting_artifacts: set[str] = set()
    try:
        manifest = merge_manifests(tuple(manifests))
        conflicts = []
    except BrowserError:
        artifacts, targets, conflicts, conflicting_artifacts = {}, {}, [], set()
        for item in manifests:
            for artifact in item.artifacts:
                prior = artifacts.get(artifact.id)
                if prior is not None and (prior.kind, prior.content) != (
                    artifact.kind,
                    artifact.content,
                ):
                    conflicting_artifacts.add(artifact.id)
                    variant = Artifact.create(
                        "conflict-" + artifact.sha256[:12] + "." + artifact.kind.value,
                        artifact.kind,
                        artifact.content,
                        artifact.provenance.source,
                        locations=artifact.provenance.locations,
                    )
                    artifacts[variant.id] = variant
                else:
                    artifacts[artifact.id] = artifact
            for target in item.services:
                prior = targets.get(target.id)
                if prior and (
                    prior.protocol,
                    prior.endpoint,
                    prior.schema_artifact,
                    prior.operation_artifacts,
                ) != (
                    target.protocol,
                    target.endpoint,
                    target.schema_artifact,
                    target.operation_artifacts,
                ):
                    conflicts.append(
                        {"service": target.id, "reason": "conflicting_contracts"}
                    )
                else:
                    targets[target.id] = target

        def affected(reference, seen=None):
            if reference in conflicting_artifacts:
                return True
            seen = set() if seen is None else seen
            if reference in seen or reference not in artifacts:
                return False
            return any(
                affected(parent, seen | {reference})
                for parent in artifacts[reference].provenance.derived_from
            )

        for target in targets.values():
            references = [
                target.schema_artifact,
                target.source_schema_artifact,
                *target.operation_artifacts,
            ]
            if any(
                affected(reference) for reference in references if reference is not None
            ):
                conflicts.append(
                    {"service": target.id, "reason": "conflicting_artifact_identity"}
                )
        for conflict in conflicts:
            targets.pop(conflict["service"], None)
        manifest = Manifest.create(tuple(artifacts.values()), tuple(targets.values()))
    if conflicting_artifacts:
        report = _report_artifact(
            "artifact-conflicts.json",
            [
                {"artifact": identifier, "reason": "conflicting_artifact_identity"}
                for identifier in sorted(conflicting_artifacts)
            ],
        )
        manifest = replace(manifest, artifacts=manifest.artifacts + (report,))
    return manifest, conflicts


def _runtime_profiles(manifest: Manifest, profiles: dict[str, str]) -> dict[str, str]:
    profiles = dict(profiles)
    for artifact in manifest.artifacts:
        if artifact.kind is Kind.REPORT:
            try:
                value = json.loads(artifact.content)
                if isinstance(value, dict):
                    for api_origin, profile_id in value.get(
                        "runtimeProfiles", {}
                    ).items():
                        if profile_id == _profile_id(api_origin):
                            profiles[service_origin(api_origin)] = profile_id
            except (ValueError, TypeError):
                pass
    return profiles


def _coverage_report(
    manifest: Manifest, conflicts: list[dict[str, str]], discovery_report: dict | None
) -> dict[str, Any]:
    coverage = {
        "coverage": "unsupported",
        "reason": "no_executable_operations",
        "services": [],
        "exclusions": conflicts,
        "completeSiteCoverage": False,
        "workflows": [],
        "discovery": discovery_report,
    }
    for item in manifest.artifacts:
        if item.kind is Kind.REPORT:
            try:
                value = json.loads(item.content)
                if isinstance(value, dict):
                    coverage["exclusions"].extend(value.get("exclusions", []))
                elif isinstance(value, list):
                    for record in value:
                        coverage["exclusions"].extend(record.get("exclusions", []))
            except (ValueError, TypeError):
                pass
    return coverage


def _service_lineage(target: Service) -> dict[str, Any]:
    return {
        "service": {"id": target.id, "endpoint": target.endpoint},
        "source": "traffic-har2sdk"
        if target.confidence is Confidence.INFERRED
        else (
            "schema-less-operations" if target.schema_artifact is None else "definition"
        ),
        "sourceArtifacts": [
            ref
            for ref in (
                target.source_schema_artifact or target.schema_artifact,
                *target.operation_artifacts,
            )
            if ref
        ],
    }


def _generate_target(
    normalized: Manifest,
    target: Service,
    artifacts: dict[str, Artifact],
    destination: Path,
    directory_name: str,
    lineage: dict,
    config: dict,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="website-client-auto-") as temporary:
        root = Path(temporary)
        schema = artifacts.get(target.schema_artifact)
        if target.protocol is Protocol.GRAPHQL and schema is None:
            from .operation_generation import generate_operation_client

            report = generate_operation_client(
                normalized,
                target,
                destination,
                directory_name,
                lineage,
                runtime_config=config,
            )
        else:
            if schema is None or schema.kind not in {Kind.OPENAPI, Kind.GRAPHQL_SCHEMA}:
                raise BrowserError("missing_executable_definition")
            path = root / "definition"
            path.write_text(schema.content)
            if target.protocol is Protocol.OPENAPI:
                report = generate(
                    destination,
                    directory_name,
                    openapi=path,
                    lineage=lineage,
                    runtime_config=config,
                )
            else:
                compiled = artifacts.get(target.compiled_operations_artifact)
                if target.operation_artifacts and compiled is None:
                    raise BrowserError("invalid_named_operations")
                operation_path = root / "operations.graphql" if compiled else None
                if compiled:
                    operation_path.write_text(compiled.content)
                report = generate(
                    destination,
                    directory_name,
                    schema=path,
                    operations=operation_path,
                    lineage=lineage,
                    runtime_config=config,
                )
    return report


def _generate_service(
    normalized: Manifest,
    target: Service,
    artifacts: dict[str, Artifact],
    output: Path,
    name: str,
    profiles: dict[str, str],
) -> tuple[dict, list]:
    directory_name = name + "-" + hashlib.sha256(target.id.encode()).hexdigest()[:12]
    config = {"endpoint": target.endpoint}
    if service_origin(target.endpoint) in profiles:
        config["profileId"] = profiles[service_origin(target.endpoint)]
    lineage = _service_lineage(target)
    destination = output / "services" / directory_name
    report = _generate_target(
        normalized, target, artifacts, destination, directory_name, lineage, config
    )
    entry = {
        "id": target.id,
        "package": directory_name,
        "directory": "services/" + directory_name,
        "operations": report["operations"],
        "provenance": lineage["source"],
    }
    return entry, report.get("coverage", {}).get("exclusions", [])


def _package_workflows(
    normalized: Manifest,
    output: Path,
    name: str,
    profiles: dict[str, str],
    coverage: dict,
) -> None:
    from .workflow_generation import collect_workflows, generate_workflow_client

    documents = [item for item in normalized.artifacts if item.kind is Kind.ARAZZO]
    if not documents:
        return
    directory = output / "workflows"
    directory.mkdir(mode=0o700)
    entries, exclusions = collect_workflows(normalized, profiles)
    coverage["exclusions"].extend(exclusions)
    for item in documents:
        path = directory / (item.sha256[:16] + ".json")
        path.write_text(item.content)
        path.chmod(0o600)
        selected = [
            entry.public_id for entry in entries if entry.artifact_id == item.id
        ]
        rejected = [
            record for record in exclusions if record.get("artifact") == item.id
        ]
        coverage["workflows"].append(
            {
                "file": "workflows/" + path.name,
                "artifact": item.id,
                "status": ("partial" if rejected else "ready")
                if selected
                else "excluded",
                "workflows": selected,
            }
        )
    if entries:
        package = name + "-workflows"
        report = generate_workflow_client(directory / package, package, entries)
        coverage["workflowPackage"] = {
            "package": package,
            "directory": "workflows/" + package,
            "workflows": report["workflows"],
        }


def _finish_coverage(coverage: dict, discovery_report: dict | None) -> None:
    if coverage["services"] or coverage.get("workflowPackage"):
        incomplete = bool(
            coverage["exclusions"]
            or discovery_report
            and (
                discovery_report.get("candidatesNotProbed")
                or discovery_report.get("exploration", {}).get("queuedPagesNotVisited")
                or discovery_report.get("exploration", {}).get("omitted")
                or discovery_report.get("exploration", {}).get("timeLimitReached")
                or discovery_report.get("exploration", {}).get("blockedRequests")
            )
        )
        coverage.update(
            coverage="partial" if incomplete else "complete",
            reason="processed_available_evidence",
        )


def _persist_evidence(normalized: Manifest, output: Path, coverage: dict) -> None:
    from .unsupported_output import write_unsupported_output

    # Only already-sanitized evidence is persisted. Raw HAR/source files were never copied.
    for filename, value in [
        ("manifest.json", normalized.to_dict()),
        ("coverage.json", coverage),
    ]:
        path = output / filename
        path.write_text(encoded(value))
        path.chmod(0o600)
    write_unsupported_output(output, coverage)


def run(
    source: str | Path,
    output: str | Path,
    *,
    name: str = "service-client",
    inputs: tuple = (),
    endpoint: str | None = None,
    operations: str | Path | None = None,
    browser: str = "chrome",
    browser_path: str | None = None,
    login_handler: Callable | None = None,
    headless: bool = False,
    max_pages: int = 20,
    max_actions: int = 60,
    max_depth: int = 3,
    duration_seconds: float = 120,
) -> dict[str, Any]:
    """Generate all ready services from a URL or saved evidence without executing writes.

    `login_handler` is an optional initial-login callback. After login, collection,
    validation, generation, and coverage reporting require no further decisions.
    """
    request = RunRequest(
        source=source,
        output=new_output(output),
        name=name,
        inputs=tuple(inputs),
        endpoint=endpoint,
        operations=operations,
        browser=browser,
        browser_path=browser_path,
        login_handler=login_handler,
        headless=headless,
        max_pages=max_pages,
        max_actions=max_actions,
        max_depth=max_depth,
        duration_seconds=duration_seconds,
    )
    # Claim a fresh destination before work begins so every accepted run can
    # leave guidance on failure, without replacing someone else's files.
    request.output.mkdir(mode=0o700)
    try:
        return _run_request(request)
    except (Exception, KeyboardInterrupt) as error:
        from .unsupported_output import write_failed_run

        write_failed_run(request.output, error)
        raise


def _run_request(request: RunRequest) -> dict[str, Any]:
    """Collect evidence and package usable services inside the owned output."""
    collected = _collect_inputs(request)
    manifest, conflicts = _merge_inputs(collected.manifests)
    manifest = bind_sources(manifest, collected.bindings)
    profiles = _runtime_profiles(manifest, collected.profiles)
    normalized = normalize_manifest(manifest)
    artifacts = {item.id: item for item in normalized.artifacts}
    coverage = _coverage_report(manifest, conflicts, collected.discovery_report)
    (request.output / "services").mkdir(mode=0o700)
    for target in sorted(normalized.services, key=lambda item: item.id):
        if (
            collected.allowed_origins is not None
            and service_origin(target.endpoint) not in collected.allowed_origins
        ):
            coverage["exclusions"].append(
                {"service": target.id, "reason": "undeclared_origin"}
            )
            continue
        try:
            entry, exclusions = _generate_service(
                normalized, target, artifacts, request.output, request.name, profiles
            )
            coverage["services"].append(entry)
            coverage["exclusions"].extend(exclusions)
        except BrowserError:
            coverage["exclusions"].append(
                {"service": target.id, "reason": "unsupported_contract"}
            )
    _package_workflows(normalized, request.output, request.name, profiles, coverage)
    _finish_coverage(coverage, collected.discovery_report)
    _persist_evidence(normalized, request.output, coverage)
    return coverage
