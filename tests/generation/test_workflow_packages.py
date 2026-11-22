"""Run, build, install, and execute standalone workflows using ordinary test fixtures."""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from support.workflow import PAYLOAD, FIXTURES
from support.workflow import service as service

from website_client.autonomous import run, _profile_id
from website_client.discovery_manifest import write_manifest
from website_client.discovery_manifest.records import Artifact, Kind, Manifest, Source
from website_client.runtime_profile import ProfileStore, service_origin


from support.packaging import supplied_bundle, runtime_association, installed_target, installed_cli

def test_run_build_install_python_cli_rotation_and_local_output_failure(tmp_path, service):
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    association = runtime_association(tmp_path, url)
    output = tmp_path / 'output'
    coverage = run(workflow, output, name='neutral', inputs=[association])
    assert coverage['workflowPackage']['workflows'] == ['upload']
    assert not server.calls  # Supplying definitions never fetches the target.
    project = output / coverage['workflowPackage']['directory']
    for path in project.rglob('*'):
        if path.is_file():
            assert b'SYNTHETIC_TOKEN' not in path.read_bytes()
            assert b'"INITIAL"' not in path.read_bytes()
    target = installed_target(project, tmp_path)
    payload = tmp_path / 'payload.bin'
    payload.write_bytes(PAYLOAD)
    bootstrap = f'import sys,json,pathlib,importlib.metadata; sys.path.insert(0,{str(target)!r}); from neutral_workflows import execute_workflow; print(json.dumps(execute_workflow("upload",{{"file":pathlib.Path({str(payload)!r}).read_bytes()}}))); assert "website_client" not in sys.modules; assert importlib.metadata.version("arazzo-runner")=="0.9.6+websiteclient.1"'
    python = subprocess.run([sys.executable, '-I', '-c', bootstrap], cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40)
    assert python.returncode == 0, python.stderr
    assert json.loads(python.stdout)['outputs'] == {'complete': True}
    cli = installed_cli(target, 'neutral-workflows', ['upload', '--file', 'file=' + str(payload)], tmp_path)
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)['status'] == 'success'
    occupied = tmp_path / 'already-exists.json'
    occupied.write_text('existing')
    cli = installed_cli(target, 'neutral-workflows', ['upload', '--file', 'file=' + str(payload), '--output', str(occupied)], tmp_path)
    assert cli.returncode == 1, cli.stderr
    summary = json.loads(cli.stderr)
    assert summary['status'] == 'success' and summary['reason'] == 'workflow_completed'
    assert summary['effects'] == 'possible'
    assert summary['output']['reason'] == 'local_output_failure'
    assert summary['requests'] == summary['responses'] == 3
    assert occupied.read_text() == 'existing'
    assert server.commits == 3 and len(server.calls) == 9
    assert [call[2] for call in server.calls[:3]] == ['sid=INITIAL', 'sid=ROTATED', 'sid=ROTATED']
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{"credential":"CANARY_PRIVATE", broken')
    cli = installed_cli(target, 'neutral-workflows', ['upload', '--input', str(invalid)], tmp_path)
    assert cli.returncode == 2 and 'CANARY_PRIVATE' not in cli.stderr
    assert server.commits == 3
    cli = installed_cli(target, 'neutral-workflows', ['CANARY_PRIVATE'], tmp_path)
    assert cli.returncode == 2 and 'CANARY_PRIVATE' not in cli.stderr


def test_saved_manifest_regenerates_workflow_package_without_target_access(tmp_path, service):
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    first = tmp_path / 'first'
    report = run(workflow, first, name='offline', inputs=[runtime_association(tmp_path, url)])
    second = tmp_path / 'second'
    regenerated = run(first / 'manifest.json', second, name='offline')
    assert not server.calls
    assert regenerated['workflowPackage']['workflows'] == report['workflowPackage']['workflows']
    assert (second / regenerated['workflowPackage']['directory'] / 'offline_workflows' / '_workflows.json').read_text() == (first / report['workflowPackage']['directory'] / 'offline_workflows' / '_workflows.json').read_text()


def test_advertised_workflow_fetches_exact_source_and_regenerates_installed_cli_offline(tmp_path, service):
    url, server = service
    original = server.RequestHandlerClass
    server.website_calls = []
    spec = json.loads((FIXTURES / 'openapi.json').read_text())
    spec['servers'] = [{'url': url}]
    workflow = (FIXTURES / 'workflow.json').read_text()

    class Website(original):
        def web(self, body, media, cookie=None):
            raw = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            server.website_calls.append(self.path)
            if self.path == '/':
                return self.web('<link rel="service-desc" href="/workflows/main.json"><button role="tab" onclick="fetch(\'/session\',{headers:{Authorization:\'Bearer SYNTHETIC_TOKEN\'}})">Data</button>', 'text/html', 'sid=INITIAL; Path=/; HttpOnly')
            if self.path == '/workflows/main.json':
                return self.web(workflow, 'application/json')
            if self.path == '/workflows/openapi.json':
                return self.web(json.dumps(spec), 'application/json')
            if self.path == '/session':
                return self.web('neutral', 'text/plain')
            return super().do_GET()

    server.RequestHandlerClass = Website
    first = tmp_path / 'browser-output'
    report = run(url, first, name='browser-sequence', login_handler=lambda _: None, headless=True, max_pages=1, max_actions=2, duration_seconds=10)
    assert report['workflowPackage']['workflows'] == ['upload'], report
    assert server.commits == 0
    assert '/workflows/openapi.json' in server.website_calls
    calls = list(server.website_calls)
    second = tmp_path / 'offline-output'
    saved = run(first / 'manifest.json', second, name='browser-sequence')
    assert server.website_calls == calls
    target = installed_target(second / saved['workflowPackage']['directory'], tmp_path)
    payload = tmp_path / 'fixture.bin'
    payload.write_bytes(PAYLOAD)
    result = installed_cli(target, 'browser-sequence-workflows', ['upload', '--file', 'file=' + str(payload)], tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['outputs'] == {'complete': True}
    assert server.commits == 1


def test_private_required_structure_never_reappears_from_retained_source_report(tmp_path, service):
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    spec_path = tmp_path / 'openapi.json'
    value = json.loads(spec_path.read_text())
    value['paths']['/prepare']['post']['requestBody']['content']['application/json']['schema'] = {'type': 'object', 'properties': {'CANARY_PRIVATE': {'type': 'string'}}, 'required': ['CANARY_PRIVATE']}
    spec_path.write_text(json.dumps(value))
    first = tmp_path / 'first'
    report = run(workflow, first)
    assert not report.get('workflowPackage')
    assert any(item['reason'] == 'unresolved_operation' for item in report['exclusions'])
    assert report['services'] and not server.calls
    regenerated = run(first / 'manifest.json', tmp_path / 'second')
    assert not regenerated.get('workflowPackage') and not server.calls
    for path in first.rglob('*'):
        if path.is_file():
            assert b'CANARY_PRIVATE' not in path.read_bytes()


def test_duplicate_native_workflow_ids_get_distinct_deterministic_public_names(tmp_path, service):
    url, server = service
    sources = []
    for name in ['one', 'two']:
        directory = tmp_path / name
        directory.mkdir()
        sources.append(supplied_bundle(directory, url))
    first = run(sources[0], tmp_path / 'first', inputs=[sources[1]])
    second = run(sources[0], tmp_path / 'second', inputs=[sources[1]])
    public = first['workflowPackage']['workflows']
    assert len(public) == len(set(public)) == 2
    assert all(name.startswith('upload__') for name in public)
    assert public == second['workflowPackage']['workflows']
    assert not server.calls


def test_destructive_workflow_privacy_is_reported_and_survives_saved_regeneration(tmp_path, service):
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    value = json.loads(workflow.read_text())
    value['workflows'].append({'workflowId': 'CANARY_PRIVATE', 'steps': [{'stepId': 'send', 'operationId': 'prepare'}]})
    workflow.write_text(json.dumps(value))
    first = tmp_path / 'first'
    report = run(workflow, first)
    assert report['workflowPackage']['workflows'] == ['upload']
    assert report['coverage'] == 'partial'
    second = run(first / 'manifest.json', tmp_path / 'second')
    assert second['workflowPackage']['workflows'] == ['upload']
    assert any(item['reason'] == 'sensitive_structure' for item in second['exclusions'])
    assert not server.calls


def test_invalid_declared_source_excludes_workflow_but_keeps_independent_service(tmp_path, service):
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    original = (tmp_path / 'openapi.json').read_bytes()
    (tmp_path / 'openapi.json').write_text('{"credential":"CANARY_PRIVATE", broken')
    independent = tmp_path / 'independent.json'
    independent.write_bytes(original)
    output = tmp_path / 'output'
    report = run(workflow, output, inputs=[independent])
    assert report['services'] and not report.get('workflowPackage')
    assert report['coverage'] == 'partial'
    assert any(item['reason'] == 'unresolved_source' for item in report['exclusions'])
    assert not server.calls
    assert all(b'CANARY_PRIVATE' not in path.read_bytes() for path in output.rglob('*') if path.is_file())


def test_conflicting_source_artifact_cannot_change_workflow_destination(tmp_path, service):
    from dataclasses import replace
    from website_client.discovery_manifest import read_manifest
    from website_client.discovery_manifest.records import Protocol, Service
    from website_client.workflow_binding import report_bindings
    url, server = service
    workflow = supplied_bundle(tmp_path, url)
    first = tmp_path / 'first'
    run(workflow, first)
    manifest = read_manifest(first / 'manifest.json')
    source_id = next(iter(report_bindings(manifest).values()))['api'].artifact_id
    source = next(item for item in manifest.artifacts if item.id == source_id)
    value = json.loads(source.content)
    value['servers'] = [{'url': 'https://other.invalid'}]
    conflict = replace(Artifact.create('conflict.json', Kind.OPENAPI, json.dumps(value), Source.EXTERNAL), id=source_id)
    independent = Artifact.create('independent.json', Kind.OPENAPI, json.dumps(value), Source.EXTERNAL)
    second = tmp_path / 'other-manifest.json'
    write_manifest(Manifest.create((conflict, independent), (Service.create(Protocol.OPENAPI, 'https://other.invalid', conflict.id), Service.create(Protocol.OPENAPI, 'https://independent.invalid', independent.id))), second)
    report = run(first / 'manifest.json', tmp_path / 'output', inputs=[second])
    assert report['services'] and not report.get('workflowPackage')
    assert any(item['reason'] == 'ambiguous_source' for item in report['exclusions'])
    assert not server.calls


def test_two_fixed_origins_use_distinct_native_contexts_profiles_and_rotation(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    servers, associations = [], {}
    workflow = {'arazzo': '1.0.0', 'info': {'title': 'Two fixed origins', 'version': '1'}, 'sourceDescriptions': [], 'workflows': [{'workflowId': 'verify', 'steps': [], 'outputs': {}}]}
    for name in ['alpha', 'beta']:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                expected = self.server.label
                self.server.calls.append((self.headers.get('Cookie'), self.headers.get('Authorization')))
                assert self.headers.get('Cookie') == 'sid=' + expected
                assert self.headers.get('Authorization') == 'Bearer credential-' + expected
                raw = b'{"verified":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Set-Cookie', 'sid=' + expected + '-rotated; Path=/; HttpOnly')
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.label, server.calls = name, []
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}'
        identifier = _profile_id(url)
        associations[service_origin(url)] = identifier
        with ProfileStore(identifier, url).locked() as store:
            store.save({'cookies': [{'name': 'sid', 'value': name, 'domain': '127.0.0.1', 'path': '/', 'expires': -1, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'}], 'origins': []}, {'authorization': 'Bearer credential-' + name})
        spec = {'openapi': '3.0.3', 'info': {'title': 'Fixed destination', 'version': '1'}, 'servers': [{'url': url}], 'paths': {'/read': {'get': {'operationId': 'read_' + name, 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'type': 'object', 'required': ['verified'], 'properties': {'verified': {'type': 'boolean'}}}}}}}}}}}
        (tmp_path / (name + '.json')).write_text(json.dumps(spec))
        workflow['sourceDescriptions'].append({'name': name, 'type': 'openapi', 'url': name + '.json'})
        workflow['workflows'][0]['steps'].append({'stepId': name, 'operationId': 'read_' + name, 'outputs': {'verified': '$response.body#/verified'}, 'successCriteria': [{'condition': '$response.body.verified == true'}]})
        workflow['workflows'][0]['outputs'][name] = '$steps.' + name + '.verified'
    try:
        source = tmp_path / 'workflow.json'
        source.write_text(json.dumps(workflow))
        association = Artifact.create('runtime.json', Kind.REPORT, json.dumps({'runtimeProfiles': associations}), Source.REPORT)
        initial = tmp_path / 'profiles.json'
        write_manifest(Manifest.create((association,), ()), initial)
        output = tmp_path / 'output'
        report = run(source, output, name='two-origins', inputs=[initial])
        assert all(not server.calls for server in servers)
        target = installed_target(output / report['workflowPackage']['directory'], tmp_path)
        result = installed_cli(target, 'two-origins-workflows', ['verify'], tmp_path)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['outputs'] == {'alpha': True, 'beta': True}
        for server in servers:
            assert server.calls == [('sid=' + server.label, 'Bearer credential-' + server.label)]
            url = f'http://127.0.0.1:{server.server_port}'
            with ProfileStore(_profile_id(url), url).locked() as store:
                assert store.load()['storage_state']['cookies'][0]['value'] == server.label + '-rotated'
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_installed_cli_disconnect_reports_one_unknown_effect_without_retry(tmp_path, service):
    url, server = service
    source = supplied_bundle(tmp_path, url)
    output = tmp_path / 'output'
    report = run(source, output, name='disconnect', inputs=[runtime_association(tmp_path, url)])
    target = installed_target(output / report['workflowPackage']['directory'], tmp_path)
    payload = tmp_path / 'payload.bin'
    payload.write_bytes(PAYLOAD)
    server.mode = 'disconnect'
    result = installed_cli(target, 'disconnect-workflows', ['upload', '--file', 'file=' + str(payload)], tmp_path)
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary['reason'] == 'transport_failure' and summary['effects'] == 'unknown'
    assert summary['requests'] == 1 and summary['responses'] == 0
    assert server.commits == 1 and len(server.calls) == 1
    assert 'SYNTHETIC_TOKEN' not in result.stdout + result.stderr


def test_workflow_only_cli_generation_succeeds_if_sdk_backend_is_unavailable(tmp_path, service, monkeypatch, capsys):
    from website_client.browser import BrowserError
    from website_client.cli import main
    url, server = service
    source = supplied_bundle(tmp_path, url)
    def unavailable(*_args, **_kwargs):
        raise BrowserError('upstream generator unavailable')
    monkeypatch.setattr('website_client.autonomous._generate_service', unavailable)
    assert main(['run', str(source), '--output', str(tmp_path / 'output')]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert not summary['services'] and summary['workflowPackage']['workflows'] == ['upload']
    assert summary['coverage'] == 'partial' and not server.calls


def test_installed_cli_cancellation_preserves_unknown_write_effect(tmp_path, service):
    import signal
    import threading
    url, server = service
    original = server.RequestHandlerClass
    started, release = threading.Event(), threading.Event()
    class Hold(original):
        def do_POST(self):
            if self.path != '/prepare':
                return super().do_POST()
            body = self.rfile.read(int(self.headers['Content-Length']))
            assert json.loads(body) == {'filename': 'fixture.bin'}
            assert self.headers.get('Cookie') == 'sid=INITIAL'
            assert self.headers.get('Authorization') == 'Bearer SYNTHETIC_TOKEN'
            server.calls.append((self.command, self.path, self.headers.get('Cookie'), body))
            server.commits += 1
            started.set()
            release.wait(15)
            try:
                self.send({'ticket': 'neutral-ticket', 'ready': True})
            except (BrokenPipeError, ConnectionResetError):
                pass
    server.RequestHandlerClass = Hold
    source = supplied_bundle(tmp_path, url)
    output = tmp_path / 'output'
    report = run(source, output, name='cancel', inputs=[runtime_association(tmp_path, url)])
    target = installed_target(output / report['workflowPackage']['directory'], tmp_path)
    payload = tmp_path / 'payload.bin'
    payload.write_bytes(PAYLOAD)
    bootstrap = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    process = subprocess.Popen([sys.executable, '-I', '-c', bootstrap, str(target), str(target / 'bin' / 'cancel-workflows'), 'upload', '--file', 'file=' + str(payload)], cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert started.wait(10)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 130, stderr
        summary = json.loads(stdout)
        assert summary['status'] == 'cancelled' and summary['effects'] == 'unknown'
        assert summary['requests'] == 1 and summary['responses'] == 0
        assert server.commits == 1 and len(server.calls) == 1
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_closed_stdout_reports_completed_workflow_as_local_output_failure(tmp_path, service):
    import os
    url, server = service
    source = supplied_bundle(tmp_path, url)
    output = tmp_path / 'output'
    report = run(source, output, name='pipe', inputs=[runtime_association(tmp_path, url)])
    target = installed_target(output / report['workflowPackage']['directory'], tmp_path)
    payload = tmp_path / 'payload.bin'
    payload.write_bytes(PAYLOAD)
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        result = installed_cli(target, 'pipe-workflows', ['upload', '--file', 'file=' + str(payload)], tmp_path, stdout=write_end)
    finally:
        os.close(write_end)
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stderr)
    assert summary['status'] == 'success' and summary['reason'] == 'workflow_completed'
    assert summary['output']['reason'] == 'local_output_failure'
    assert summary['effects'] == 'possible'
    assert summary['requests'] == summary['responses'] == 3
    assert server.commits == 1 and len(server.calls) == 3
    assert 'BrokenPipeError' not in result.stderr and 'SYNTHETIC_TOKEN' not in result.stderr


def test_missing_cli_required_input_is_usage_error_before_any_request(tmp_path, service):
    url, server = service
    source = supplied_bundle(tmp_path, url)
    output = tmp_path / 'output'
    report = run(source, output, name='usage', inputs=[runtime_association(tmp_path, url)])
    target = installed_target(output / report['workflowPackage']['directory'], tmp_path)
    result = installed_cli(target, 'usage-workflows', ['upload'], tmp_path)
    assert result.returncode == 2, result.stderr
    summary = json.loads(result.stdout)
    assert summary['status'] == 'rejected' and summary['reason'] == 'missing_input'
    assert summary['requests'] == 0 and summary['effects'] == 'none'
    assert not server.calls
