"""One deterministic URL or saved-input path through the existing pipeline owners."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit

import yaml

from .admission import admit_openapi, METHODS
from .browser import BrowserError, BrowserSession
from .discovery import import_har, new_output, publish, Discovery
from .discovery_manifest import encoded, read_manifest, merge_manifests
from .discovery_manifest.records import Artifact, Confidence, Kind, Manifest, Producer, Protocol, Service, Source
from .generation import generate, package_name_for
from .normalization import normalize_manifest
from .privacy import sanitized_manifest, safe_operations, safe_schema, safe_workflow
from .runtime_profile import OriginCaptures, save_browser_profile, service_origin


def _profile_id(url):
    return 'origin_' + hashlib.sha256(service_origin(url).encode()).hexdigest()[:24]


def _har_contract(discovery):
    """Feed only safe observed REST traffic to har2sdk, not the legacy inference."""
    from .har_inference import infer_har
    from .bundle_capture import capture_manifest
    files = discovery.files('har')
    # Native GraphQL compilation and downloaded definitions retain their owners.
    files = {name: content for name, content in files.items() if not name.startswith('openapi-inferred')}
    manifest = capture_manifest(discovery, files)
    entries = []
    for entry in discovery.entries:
        request = entry['request']
        content = entry['response'].get('content', {})
        if entry.get('_definitionProbe') or entry.get('_resourceType') == 'Document' or not any(kind in content.get('mimeType', '') for kind in ('json', 'yaml')):
            continue
        if any(request['url'].split('?', 1)[0] == endpoint.split('?', 1)[0] for endpoint in discovery.graphql_endpoints):
            continue
        content = entry['response'].get('content', {})
        try:
            value = json.loads(content.get('text', ''))
        except ValueError:
            value = None
        if isinstance(value, dict) and any(name in value for name in ('openapi', 'arazzo', '__schema')):
            continue
        entries.append(entry)
    groups = {}
    for entry in entries:
        groups.setdefault(service_origin(entry['request']['url']), []).append(entry)
    artifacts, services = list(manifest.artifacts), list(manifest.services)
    if discovery.omitted or discovery.capture_failures:
        artifacts.append(Artifact.create('capture-loss.json', Kind.REPORT, encoded({'exclusions': [{'reason': 'privacy_or_capture_omission', 'count': discovery.omitted + discovery.capture_failures}]}), Source.REPORT))
    for group_index, (api_origin, group) in enumerate(sorted(groups.items())):
        safe_har = json.dumps({'log': {'version': '1.2', 'entries': group}})
        try:
            spec, diagnostics = infer_har(safe_har)
        except BrowserError as error:
            if str(error).startswith('har_dependency_required') and services:
                artifacts.append(Artifact.create(f'har-dependency-{group_index:04d}.json', Kind.REPORT,
                    encoded({'exclusions': [{'reason': 'har_dependency_required'}]}), Source.REPORT))
                continue
            raise
        safe, executable, exclusions = admit_openapi(spec)
        unknown = {(item['path'], item['method'].lower()): ('sensitive_structure' if item.get('privacyUnmodeled') else 'unknown_request_body') for item in discovery.endpoints.values() if item.get('requestBodyUnmodeled') or item.get('privacyUnmodeled')}
        for (path, method), reason in unknown.items():
            if path in executable['paths'] and method in executable['paths'][path]:
                del executable['paths'][path][method]
                exclusions.append({'path': path, 'method': method.upper(), 'reason': reason})
                if not any(key in METHODS for key in executable['paths'][path]):
                    del executable['paths'][path]
        unknown_responses = sum('text' not in entry['response'].get('content', {}) for entry in group)
        if unknown_responses:
            exclusions.append({'reason': 'response_body_unknown', 'count': unknown_responses})
        artifact = Artifact.create(f'har2sdk-openapi-{group_index:04d}.json', Kind.OPENAPI, encoded(executable), Source.INFERRED, producer=Producer('har2sdk', '0.6.15'))
        artifacts.append(artifact)
        if executable['paths']:
            for server in executable.get('servers', []):
                endpoint = server['url']
                services.append(Service.create(Protocol.OPENAPI, endpoint, artifact.id, confidence=Confidence.INFERRED,
                                               identifier='service-har-' + hashlib.sha256(endpoint.encode()).hexdigest()[:16]))
        artifacts.append(Artifact.create(f'har2sdk-report-{group_index:04d}.json', Kind.REPORT, encoded({**diagnostics, 'exclusions': exclusions}), Source.REPORT))
    return Manifest.create(tuple(artifacts), tuple(services))


def _saved_input(path, *, endpoint=None, operations=None):
    """Classify the supplied file only; no target or referenced URL is fetched."""
    from .pipeline import import_definition
    from .definitions import MAX_DOCUMENT
    from .discovery_manifest import read_text
    text = read_text(path, 32 * 1024 * 1024)
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        value = None
    if isinstance(value, dict) and value.get('format') == 'website-client-discovery':
        manifest = sanitized_manifest(Manifest.from_dict(value))
        har_contracts = []
        converted = any(item.kind is Kind.OPENAPI and any(producer.name == "har2sdk" for producer in item.provenance.producers) for item in manifest.artifacts)
        for item in manifest.artifacts:
            if item.kind is Kind.HAR and not converted:
                discovery = Discovery()
                for entry in json.loads(item.content)['log']['entries']:
                    discovery.add(entry)
                har_contracts.append(sanitized_manifest(_har_contract(discovery)))
        if har_contracts:
            by_id = {item.id: item for item in manifest.artifacts}
            targets = tuple(target for target in manifest.services if not (
                target.confidence is Confidence.INFERRED and target.schema_artifact in by_id
                and by_id[target.schema_artifact].name.startswith('openapi-inferred')))
            manifest = merge_manifests((replace(manifest, services=targets), *har_contracts))
        return manifest
    if isinstance(value, dict) and isinstance(value.get('log'), dict):
        return _har_contract(import_har(path))
    if len(text.encode()) > MAX_DOCUMENT:
        raise BrowserError('unsupported: definition exceeds 8 MiB')
    if isinstance(value, dict) and 'arazzo' in value:
        item = Artifact.create('workflow.json', Kind.ARAZZO, encoded(safe_workflow(value)), Source.EXTERNAL)
        return Manifest.create((item,), ())
    options = SimpleNamespace(openapi=None, graphql_schema=None, operations=operations, endpoint=endpoint,
                              service_id=None, producer='external-definition', producer_version='unspecified')
    if isinstance(value, dict) and 'openapi' in value:
        options.openapi = path
    else:
        from .definitions import graphql_schema
        try:
            graphql_schema(text)
            options.graphql_schema = path
        except BrowserError:
            options.operations = path
    return sanitized_manifest(import_definition(options))


def _browser_input(url, *, browser='chrome', browser_path=None, login_handler=None, headless=False,
                   max_pages=20, max_actions=60, max_depth=3, duration_seconds=120):
    from .self_discovery import DiscoveryObserver, SelfDiscovery
    from .exploration import SiteExplorer
    from .browser import origin
    allowed_origins = {origin(url)}
    observer, credentials = DiscoveryObserver(url, allowed_origins), OriginCaptures(url)
    def observe(method, params):
        observer.event(method, params)
        credentials.observe(method, params)
    with BrowserSession(url, browser, browser_path, on_event=observe, block_service_workers=True, headless=headless) as session:
        if login_handler is None:
            session.confirm_login(prompt='Sign in, complete verification and return to the requested website. Press Enter to finish login; discovery and generation then run automatically.')
        else:
            login_handler(session)
        from .self_discovery import schema_url
        links = session._page.locator('a[href], link[href]')
        initial_definitions = [selected for index in range(min(links.count(), 200))
                               if (selected := schema_url(session._url, links.nth(index).get_attribute('href'),
                                   advertised='service-desc' in (links.nth(index).get_attribute('rel') or '').split()))]
        explorer = SiteExplorer(session, max_pages, max_actions, max_depth, duration_seconds, strict_origin=True, api_origins=allowed_origins, credential_observer=credentials)
        explorer.prepare()
        discovery, report = SelfDiscovery(session, observer, explorer=explorer, autonomous=True, credential_observer=credentials,
                                          initial_definitions=initial_definitions, allowed_origins=allowed_origins).run()
        profiles = {}
        cookie_exclusions = []
        from .runtime_profile import scoped_state
        state = session._context.storage_state()
        for api_origin, capture in credentials.captures.items():
            profile = _profile_id(api_origin)
            owned = credentials.cookies_for(api_origin)
            suppressed = sum((cookie.get("name"), cookie.get("value")) not in owned for cookie in scoped_state(state, api_origin)["cookies"])
            if suppressed:
                cookie_exclusions.append({"reason": "cookie_origin_isolation", "count": suppressed})
            save_browser_profile(session._context, profile, api_origin, capture.headers, capture.allowed_headers, owned_cookies=owned)
            profiles[api_origin] = profile
    manifest = _har_contract(discovery)
    if cookie_exclusions:
        report_artifact = Artifact.create("cookie-scope-report.json", Kind.REPORT, encoded({"exclusions": cookie_exclusions}), Source.REPORT)
        manifest = replace(manifest, artifacts=manifest.artifacts + (report_artifact,))
    association = Artifact.create("runtime-associations.json", Kind.REPORT, encoded({"runtimeProfiles": profiles}), Source.REPORT)
    manifest = replace(manifest, artifacts=manifest.artifacts + (association,))
    from .privacy import safe_ui_report
    return sanitized_manifest(manifest), safe_ui_report(report), profiles, set(profiles)


def run(source, output, *, name='service-client', inputs=(), endpoint=None, operations=None,
        browser='chrome', browser_path=None, login_handler=None, headless=False,
        max_pages=20, max_actions=60, max_depth=3, duration_seconds=120):
    """Finish all ready services automatically; never execute discovered writes.

    `login_handler` is an optional initial-login callback for programmatic callers.
    After it returns, traversal, admission, packaging and coverage need no decisions.
    """
    output = new_output(output)
    if len(inputs) > 19:
        raise BrowserError("invalid_input_count")
    package_name_for(name)
    from .runtime_profile import profile_directory
    if profile_directory().resolve().is_relative_to(output.resolve()):
        raise BrowserError("profile_inside_output")
    profiles, allowed, discovery_report = {}, None, None
    manifests = []
    if urlsplit(str(source)).scheme in {'http', 'https'}:
        manifest, discovery_report, profiles, allowed = _browser_input(str(source), browser=browser, browser_path=browser_path,
            login_handler=login_handler, headless=headless, max_pages=max_pages, max_actions=max_actions, max_depth=max_depth, duration_seconds=duration_seconds)
        manifests.append(manifest)
    else:
        manifests.append(_saved_input(source, endpoint=endpoint, operations=operations))
    for path in inputs:
        manifests.append(_saved_input(path, endpoint=endpoint))
    if sum(len(encoded(item.to_dict()).encode()) for item in manifests) > 64 * 1024 * 1024:
        raise BrowserError("input_budget_exceeded")
    # Preserve distinct service IDs/namespaces; block only actual conflicting IDs.
    try:
        manifest = merge_manifests(tuple(manifests))
        conflicts = []
    except BrowserError:
        artifacts, targets, conflicts, conflicting_artifacts = {}, {}, [], set()
        for item in manifests:
            for artifact in item.artifacts:
                prior = artifacts.get(artifact.id)
                if prior is not None and (prior.kind, prior.content) != (artifact.kind, artifact.content):
                    conflicting_artifacts.add(artifact.id)
                    variant = Artifact.create('conflict-' + artifact.sha256[:12] + '.' + artifact.kind.value,
                                              artifact.kind, artifact.content, artifact.provenance.source,
                                              locations=artifact.provenance.locations)
                    artifacts[variant.id] = variant
                else:
                    artifacts[artifact.id] = artifact
            for target in item.services:
                prior = targets.get(target.id)
                if prior and (prior.protocol, prior.endpoint, prior.schema_artifact, prior.operation_artifacts) != (target.protocol, target.endpoint, target.schema_artifact, target.operation_artifacts):
                    conflicts.append({'service': target.id, 'reason': 'conflicting_contracts'})
                else:
                    targets[target.id] = target
        def affected(reference, seen=None):
            if reference in conflicting_artifacts:
                return True
            seen = set() if seen is None else seen
            if reference in seen or reference not in artifacts:
                return False
            return any(affected(parent, seen | {reference}) for parent in artifacts[reference].provenance.derived_from)
        for target in targets.values():
            references = [target.schema_artifact, target.source_schema_artifact, *target.operation_artifacts]
            if any(affected(reference) for reference in references if reference is not None):
                conflicts.append({'service': target.id, 'reason': 'conflicting_artifact_identity'})
        for conflict in conflicts:
            targets.pop(conflict['service'], None)
        manifest = Manifest.create(tuple(artifacts.values()), tuple(targets.values()))
    for artifact in manifest.artifacts:
        if artifact.kind is Kind.REPORT:
            try:
                value = json.loads(artifact.content)
                if isinstance(value, dict):
                    for api_origin, profile_id in value.get('runtimeProfiles', {}).items():
                        if profile_id == _profile_id(api_origin):
                            profiles[service_origin(api_origin)] = profile_id
            except (ValueError, TypeError):
                pass
    normalized = normalize_manifest(manifest)
    artifacts = {item.id: item for item in normalized.artifacts}
    coverage = {'coverage': 'unsupported', 'reason': 'no_executable_operations', 'services': [], 'exclusions': conflicts,
                'completeSiteCoverage': False, 'workflows': [], 'discovery': discovery_report}
    for item in manifest.artifacts:
        if item.kind is Kind.REPORT:
            try:
                value = json.loads(item.content)
                if isinstance(value, dict):
                    coverage['exclusions'].extend(value.get('exclusions', []))
                elif isinstance(value, list):
                    for record in value:
                        coverage['exclusions'].extend(record.get('exclusions', []))
            except (ValueError, TypeError):
                pass
    output.mkdir(mode=0o700)
    (output / 'services').mkdir(mode=0o700)
    for target in sorted(normalized.services, key=lambda item: item.id):
        if allowed is not None and service_origin(target.endpoint) not in allowed:
            coverage['exclusions'].append({'service': target.id, 'reason': 'undeclared_origin'})
            continue
        directory_name = name + '-' + hashlib.sha256(target.id.encode()).hexdigest()[:12]
        config = {'endpoint': target.endpoint}
        if service_origin(target.endpoint) in profiles:
            config['profileId'] = profiles[service_origin(target.endpoint)]
        lineage = {'service': {'id': target.id, 'endpoint': target.endpoint},
                   'source': 'traffic-har2sdk' if target.confidence is Confidence.INFERRED else ('schema-less-operations' if target.schema_artifact is None else 'definition'),
                   'sourceArtifacts': [ref for ref in (target.source_schema_artifact or target.schema_artifact, *target.operation_artifacts) if ref]}
        destination = output / 'services' / directory_name
        try:
            with tempfile.TemporaryDirectory(prefix='website-client-auto-') as temporary:
                root = Path(temporary)
                schema = artifacts.get(target.schema_artifact)
                if target.protocol is Protocol.GRAPHQL and schema is None:
                    from .operation_generation import generate_operation_client
                    report = generate_operation_client(normalized, target, destination, directory_name, lineage, runtime_config=config)
                else:
                    if schema is None or schema.kind not in {Kind.OPENAPI, Kind.GRAPHQL_SCHEMA}:
                        raise BrowserError('missing_executable_definition')
                    path = root / 'definition'
                    path.write_text(schema.content)
                    if target.protocol is Protocol.OPENAPI:
                        report = generate(destination, directory_name, openapi=path, lineage=lineage, runtime_config=config)
                    else:
                        compiled = artifacts.get(target.compiled_operations_artifact)
                        if target.operation_artifacts and compiled is None:
                            raise BrowserError("invalid_named_operations")
                        operation_path = root / 'operations.graphql' if compiled else None
                        if compiled:
                            operation_path.write_text(compiled.content)
                        report = generate(destination, directory_name, schema=path, operations=operation_path, lineage=lineage, runtime_config=config)
            entry = {'id': target.id, 'package': directory_name, 'directory': 'services/' + directory_name,
                     'operations': report['operations'], 'provenance': lineage['source']}
            coverage['services'].append(entry)
            coverage['exclusions'].extend(report.get('coverage', {}).get('exclusions', []))
        except BrowserError:
            coverage['exclusions'].append({'service': target.id, 'reason': 'unsupported_contract'})
    for item in normalized.artifacts:
        if item.kind is Kind.ARAZZO:
            directory = output / 'workflows'
            directory.mkdir(mode=0o700, exist_ok=True)
            path = directory / (item.sha256[:16] + '.json')
            path.write_text(item.content)
            path.chmod(0o600)
            coverage['workflows'].append({'file': 'workflows/' + path.name, 'status': 'retained_for_runner'})
    if coverage['services']:
        incomplete = bool(coverage['exclusions'] or discovery_report and (
            discovery_report.get('candidatesNotProbed') or discovery_report.get('exploration', {}).get('queuedPagesNotVisited')
            or discovery_report.get('exploration', {}).get('omitted') or discovery_report.get('exploration', {}).get('timeLimitReached')
            or discovery_report.get('exploration', {}).get('blockedRequests')))
        coverage.update(coverage='partial' if incomplete else 'complete', reason='processed_available_evidence')
    # Only already-sanitized evidence is persisted. Raw HAR/source files were never copied.
    for filename, value in [('manifest.json', normalized.to_dict()), ('coverage.json', coverage)]:
        path = output / filename
        path.write_text(encoded(value))
        path.chmod(0o600)
    return coverage
