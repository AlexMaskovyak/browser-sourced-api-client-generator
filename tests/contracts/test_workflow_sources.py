"""Source associations are explicit document references, never filename/value proximity."""

import json
from pathlib import Path

from website_client.autonomous import _saved_input
from website_client.discovery_manifest.records import Manifest
from website_client.workflow_binding import bind_sources, input_registry, report_bindings, referenced_local_files
from website_client.privacy import sanitized_manifest

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'workflow'


def test_relative_supplied_source_binds_exact_file_and_survives_snapshot(tmp_path):
    workflow, definition = tmp_path / 'workflow.json', tmp_path / 'openapi.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    definition.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    registry = input_registry()
    first = _saved_input(workflow, bindings=registry)
    second = _saved_input(definition, bindings=registry)
    combined = Manifest.create(first.artifacts + second.artifacts, second.services)
    bound = bind_sources(combined, registry)
    associations = report_bindings(bound)
    assert len(associations) == 1
    assert set(next(iter(associations.values()))) == {'api'}
    source_id = next(iter(associations.values()))['api'].artifact_id
    assert any(artifact.id == source_id for artifact in bound.artifacts)
    assert report_bindings(sanitized_manifest(bound)) == associations
    assert str(tmp_path) not in json.dumps(bound.to_dict())
    assert referenced_local_files(workflow) == [definition]


def test_similarly_named_or_nearby_file_cannot_satisfy_reference(tmp_path):
    workflow = tmp_path / 'workflow.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    different = tmp_path / 'other-openapi.json'
    different.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    registry = input_registry()
    first, second = _saved_input(workflow, bindings=registry), _saved_input(different, bindings=registry)
    bound = bind_sources(Manifest.create(first.artifacts + second.artifacts, second.services), registry)
    assert report_bindings(bound) == {}
    assert referenced_local_files(workflow) == []


def test_declared_file_cannot_escape_initial_bundle(tmp_path):
    outside = tmp_path / 'outside.json'
    outside.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    directory = tmp_path / 'bundle'
    directory.mkdir()
    workflow = directory / 'workflow.json'
    value = json.loads((FIXTURES / 'workflow.json').read_text())
    value['sourceDescriptions'][0]['url'] = '../outside.json'
    workflow.write_text(json.dumps(value))
    assert referenced_local_files(workflow) == []


def test_explicit_endpoint_and_canonical_symlink_reference_survive_offline_snapshot(tmp_path):
    workflow, definition = tmp_path / 'workflow.json', tmp_path / 'openapi.json'
    definition.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    (tmp_path / 'linked.json').symlink_to(definition)
    value = json.loads((FIXTURES / 'workflow.json').read_text())
    value['sourceDescriptions'][0]['url'] = 'linked.json'
    workflow.write_text(json.dumps(value))
    registry = input_registry()
    first = _saved_input(workflow, bindings=registry)
    second = _saved_input(definition, endpoint='https://selected.invalid', bindings=registry)
    combined = Manifest.create(first.artifacts + second.artifacts, second.services)
    saved = sanitized_manifest(bind_sources(combined, registry))
    target = next(iter(report_bindings(saved).values()))['api']
    assert target.endpoint == 'https://selected.invalid'
    assert referenced_local_files(workflow) == [definition]
    assert str(tmp_path) not in json.dumps(saved.to_dict())


def test_unknown_or_wrong_kind_report_targets_cannot_bind_sources(tmp_path):
    from website_client.discovery_manifest.records import Artifact, Kind, Source
    workflow = tmp_path / 'workflow.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    first = _saved_input(workflow)
    evidence = Artifact.create('unrelated.json', Kind.REPORT, '{}', Source.REPORT)
    for target in ['missing-artifact', evidence.id]:
        report = Artifact.create('bindings.json', Kind.REPORT, json.dumps({'workflowBindings': {first.artifacts[0].id: {'api': {'artifact': target}}}}), Source.REPORT)
        assert report_bindings(Manifest.create(first.artifacts + (evidence, report), ())) == {}


def test_registry_and_binding_representations_do_not_expose_private_values():
    from website_client.workflow_binding import InputRegistry, SourceBinding
    canary = 'CREDENTIAL_CANARY_PRIVATE'
    binding = SourceBinding(canary, 'https://fixture.invalid/' + canary)
    registry = InputRegistry(documents={canary: {binding}})
    assert canary not in repr(binding)
    assert canary not in repr(registry)


def test_conflicting_saved_associations_stay_excluded_even_with_initial_file_registry(tmp_path):
    from website_client.discovery_manifest.records import Artifact, Kind, Source
    workflow, definition = tmp_path / 'workflow.json', tmp_path / 'openapi.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    definition.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    registry = input_registry()
    first = _saved_input(workflow, bindings=registry)
    second = _saved_input(definition, bindings=registry)
    source_id = next(iter(next(iter(registry.documents.values())))).artifact_id
    reports = tuple(Artifact.create('binding-' + name + '.json', Kind.REPORT, json.dumps({'workflowBindings': {first.artifacts[0].id: {'api': {'artifact': source_id, 'endpoint': 'https://' + name + '.invalid'}}}}), Source.REPORT) for name in ['first', 'second'])
    combined = Manifest.create(first.artifacts + second.artifacts + reports, second.services)
    assert report_bindings(bind_sources(combined, registry)) == {}


def test_saved_report_cannot_override_contradictory_exact_initial_source(tmp_path):
    from website_client.autonomous import _merge_inputs
    from website_client.discovery_manifest.records import Artifact, Kind, Source
    from website_client.workflow_generation import collect_workflows
    workflow, definition, different = (tmp_path / name for name in ['workflow.json', 'openapi.json', 'different.json'])
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    definition.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    other = json.loads(definition.read_text())
    other['servers'] = [{'url': 'https://different.invalid'}]
    different.write_text(json.dumps(other))
    registry = input_registry()
    first = _saved_input(workflow, bindings=registry)
    exact = _saved_input(definition, bindings=registry)
    other = _saved_input(different, bindings=registry)
    wrong = next(iter(registry.documents[different.as_uri()])).artifact_id
    report = Artifact.create('prior.json', Kind.REPORT, json.dumps({'workflowBindings': {first.artifacts[0].id: {'api': {'artifact': wrong}}}}), Source.REPORT)
    combined, _ = _merge_inputs([first, exact, other, Manifest.create((report,), ())])
    bound = bind_sources(combined, registry)
    assert report_bindings(bound) == {}
    entries, exclusions = collect_workflows(bound, {})
    assert not entries and any(item['reason'] == 'ambiguous_source' for item in exclusions)
    saved = sanitized_manifest(bound)
    rebound = bind_sources(saved)
    assert report_bindings(rebound) == {}
    assert not collect_workflows(rebound, {})[0]


def test_plain_provenance_and_same_artifact_endpoint_override_are_compatible(tmp_path):
    from dataclasses import replace
    from website_client.discovery_manifest.records import Artifact, Kind, Source
    from website_client.workflow_binding import SourceBinding
    workflow, definition = tmp_path / 'workflow.json', tmp_path / 'openapi.json'
    workflow.write_bytes((FIXTURES / 'workflow.json').read_bytes())
    definition.write_bytes((FIXTURES / 'openapi.json').read_bytes())
    registry = input_registry()
    first = _saved_input(workflow, bindings=registry)
    second = _saved_input(definition, endpoint='https://selected.invalid', bindings=registry)
    identifier = next(iter(registry.documents[definition.as_uri()])).artifact_id
    # The same exact source location carries native provenance without a caller override.
    registry.documents[definition.as_uri()].add(SourceBinding(identifier))
    report = Artifact.create('prior.json', Kind.REPORT, json.dumps({'workflowBindings': {first.artifacts[0].id: {'api': {'artifact': identifier}}}}), Source.REPORT)
    combined = Manifest.create(first.artifacts + second.artifacts + (report,), second.services)
    bound = bind_sources(combined, registry)
    assert next(iter(report_bindings(bound).values()))['api'].endpoint == 'https://selected.invalid'
