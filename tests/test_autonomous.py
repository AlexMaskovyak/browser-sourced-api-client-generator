"""Ordinary URL and offline routes finish without post-login decisions."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from graphql import build_schema, graphql_sync, get_introspection_query
import pytest
import yaml

from website_client.autonomous import run
from website_client.cli import main
from website_client.discovery_manifest import write_manifest
from website_client.discovery_manifest.records import Artifact, Kind, Manifest, Protocol, Service, Source
from website_client.pipeline import import_definition


def spec(endpoint='https://neutral.invalid'):
    return {'openapi': '3.0.3', 'info': {'title': 'Contract', 'version': '1'}, 'servers': [{'url': endpoint}],
            'paths': {'/read': {'get': {'operationId': 'read', 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'integer'}}}}}}}}}}}


def test_saved_openapi_json_yaml_and_converted_definitions_need_no_node(tmp_path, monkeypatch):
    monkeypatch.setattr('website_client.har_inference.backend_module', lambda: pytest.fail('definition route invoked Node'))
    for index, suffix in enumerate(('json', 'yaml')):
        source = tmp_path / ('input.' + suffix)
        value = spec()
        source.write_text(json.dumps(value) if suffix == 'json' else yaml.safe_dump(value))
        coverage = run(source, tmp_path / ('output-' + str(index)))
        assert coverage['coverage'] == 'complete'
        assert len(coverage['services']) == 1 and coverage['services'][0]['operations'] == ['read']
        assert coverage['services'][0]['provenance'] == 'definition'


def test_saved_sdl_introspection_named_and_schema_less_operations_routes(tmp_path):
    schema = 'type Query { status: String! }'
    operations = tmp_path / 'operations.graphql'
    operations.write_text('query Status { status }')
    for index, (content, operations_path) in enumerate([
        (schema, None), (schema, operations),
        (json.dumps(graphql_sync(build_schema(schema), get_introspection_query()).formatted), None),
        (operations.read_text(), None),
        (json.dumps(graphql_sync(build_schema(schema), get_introspection_query()).formatted), operations),
    ]):
        source = tmp_path / ('input-' + str(index))
        source.write_text(content)
        coverage = run(source, tmp_path / ('output-' + str(index)), endpoint='https://neutral.invalid/graphql', operations=operations_path)
        assert len(coverage['services']) == 1
        assert coverage['services'][0]['operations'] == (['Status'] if index in {1, 3, 4} else ['query_status'])
        assert coverage['services'][0]['provenance'] == ('schema-less-operations' if index == 3 else 'definition')


def test_saved_and_merged_manifests_package_all_services_and_are_deterministic(tmp_path):
    sources = []
    for index in range(2):
        item = Artifact.create('definition.json', Kind.OPENAPI, json.dumps(spec('https://api' + str(index) + '.invalid')), Source.EXTERNAL)
        service = Service.create(Protocol.OPENAPI, 'https://api' + str(index) + '.invalid', item.id)
        manifest = Manifest.create((item,), (service,), created_at='2026-01-01T00:00:00Z')
        source = tmp_path / ('manifest-' + str(index) + '.json')
        write_manifest(manifest, source)
        sources.append(source)
    reports = [run(sources[0], tmp_path / ('output-' + str(index)), inputs=[sources[1]]) for index in range(2)]
    assert len(reports[0]['services']) == 2
    assert reports[0] == reports[1]
    for service in reports[0]['services']:
        first = tmp_path / 'output-0' / service['directory']
        second = tmp_path / 'output-1' / service['directory']
        for path in first.rglob('*'):
            if path.is_file():
                assert path.read_bytes() == (second / path.relative_to(first)).read_bytes()


def test_unknown_file_backed_har_upload_bodies_are_not_executable(tmp_path):
    entries = []
    for path, request in [('/missing-upload', {'postData': {'mimeType': 'multipart/form-data', 'params': [{'name': 'file', 'fileName': 'CANARY_FILE'}]}}),
                          ('/missing-binary', {'hasPostData': True, 'bodySize': 274}),
                          ('/bodyless', {'bodySize': 0})]:
        entries.append({'request': {'method': 'POST', 'url': 'https://neutral.invalid' + path, **request}, 'response': {'status': 200, 'content': {'mimeType': 'application/json', 'text': '{"value":7}'}}})
    source = tmp_path / 'capture.har'
    source.write_text(json.dumps({'log': {'version': '1.2', 'entries': entries}}))
    output = tmp_path / 'output'
    report = run(source, output)
    assert report['coverage'] == 'partial'
    assert len(report['services']) == 1
    assert len(report['services'][0]['operations']) == 1
    generated = output / report['services'][0]['directory'] / 'openapi.json'
    assert set(json.loads(generated.read_text())['paths']) == {'/bodyless'}
    assert sum(item.get('reason') == 'unknown_request_body' for item in report['exclusions']) == 2
    assert b'CANARY_FILE' not in (output / 'manifest.json').read_bytes()


def test_zero_executable_and_malformed_inputs_have_nonzero_machine_reasons(tmp_path, capsys):
    source = tmp_path / 'invalid.yaml'
    source.write_text('openapi: [ CANARY_SECRET: invalid')
    assert main(['run', str(source), '--output', str(tmp_path / 'output')]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result == {'coverage': 'unsupported', 'reason': 'invalid_or_unsupported_input'}
    source.write_text(json.dumps({'log': {'version': '1.2', 'entries': []}}))
    assert main(['run', str(source), '--output', str(tmp_path / 'empty')]) == 1
    result = capsys.readouterr()
    assert json.loads(result.out)['coverage'] == 'unsupported'


def test_workflow_json_literal_metadata_names_remain_unchanged(tmp_path):
    from website_client.privacy import safe_workflow
    workflow = {'arazzo': '1.0.0', 'info': {'title': 'Neutral', 'version': '1'}, 'sourceDescriptions': [],
                'workflows': [{'workflowId': 'simple', 'steps': [{'stepId': 'create', 'operationId': 'create', 'requestBody': {'payload': {'description': 'Neutral', 'default': True, 'example': 3}}}]}]}
    assert safe_workflow(workflow)['workflows'][0]['steps'][0]['requestBody']['payload'] == {'description': 'Neutral', 'default': True, 'example': 3}
    source = tmp_path / 'workflow.yaml'
    source.write_text(yaml.safe_dump(workflow))
    definition = tmp_path / 'definition.json'
    definition.write_text(json.dumps(spec()))
    result = run(source, tmp_path / 'output', inputs=[definition])
    assert len(result['services']) == 1
    assert result['workflows'][0]['status'] == 'retained_for_runner'


def test_real_url_traversal_collects_definitions_traffic_and_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send(self, body, content_type='text/html', status=200):
            if not isinstance(body, bytes):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if self.path == '/login':
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(303)
                self.send_header('Location', '/')
                self.send_header('Set-Cookie', 'sid=LOCAL_INITIAL; HttpOnly; Path=/')
                self.end_headers()
            else:
                server.writes += 1
                self.send('{}', 'application/json')
        def do_GET(self):
            server.calls.append(self.path)
            if self.path == '/login':
                return self.send('<form method="post"><input name="username"><button type="submit">Sign in</button></form>')
            if self.path == '/':
                return self.send('<link rel="service-desc" href="/definitions/main.yaml"><a href="/page-two">More</a><button role="tab" onclick="fetch(\'/observed\',{headers:{Authorization:\'Bearer LOCAL_BEARER\'}})">Records</button><button role="tab" onclick="fetch(\'/write\',{method:\'POST\'})">Actions</button>')
            if self.path == '/page-two':
                return self.send('<details><summary>Details</summary>Neutral</details>')
            if self.path == '/definitions/main.yaml':
                return self.send(yaml.safe_dump(spec(url)), 'application/yaml')
            if self.path in {'/read', '/observed'}:
                assert self.headers.get('Cookie') == 'sid=LOCAL_INITIAL'
                if self.path == '/observed':
                    assert self.headers.get('Authorization') == 'Bearer LOCAL_BEARER'
                return self.send('{"value":7}', 'application/json')
            self.send('{}', 'application/json', 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.calls, server.writes = [], 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    def initial_login(session):
        session._page.locator('input').fill('neutral')
        session._page.locator('button').click()
        session._page.wait_for_url(url + '/')
    try:
        output = tmp_path / 'output'
        report = run(url + '/login', output, login_handler=initial_login, headless=True, max_pages=3, max_actions=5, duration_seconds=10)
        assert report['coverage'] == 'partial'
        assert {item['provenance'] for item in report['services']} == {'definition', 'traffic-har2sdk'}
        exploration = report['discovery']['exploration']
        assert len(exploration['pages']) == 2
        assert len(exploration['actions']) >= 2
        assert exploration['blockedRequests']
        assert server.writes == 0
        service = next(item for item in report['services'] if item['provenance'] == 'definition')
        project = output / service['directory']
        package = service['package'].replace('-', '_')
        result = subprocess.run([sys.executable, '-m', package + '._cli', 'read'], cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'value': 7}
        # Saved evidence regenerates offline and retains the same non-secret profile association.
        calls_before = list(server.calls)
        monkeypatch.setattr('website_client.har_inference.backend_module', lambda: pytest.fail('converted saved manifest reinvoked HAR inference'))
        monkeypatch.setattr('website_client.autonomous._browser_input', lambda *_a, **_k: pytest.fail('saved manifest reopened browser'))
        regenerated_output = tmp_path / 'regenerated'
        regenerated = run(output / 'manifest.json', regenerated_output)
        assert server.calls == calls_before
        regenerated_service = next(item for item in regenerated['services'] if item['provenance'] == 'definition')
        regenerated_project = regenerated_output / regenerated_service['directory']
        result = subprocess.run([sys.executable, '-m', regenerated_service['package'].replace('-', '_') + '._cli', 'read'], cwd=regenerated_project, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'value': 7}
        profile_files = list((tmp_path / 'profiles').glob('*.json'))
        assert len(profile_files) == 1 and 'LOCAL_BEARER' in profile_files[0].read_text()
        for path in output.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                assert b'LOCAL_BEARER' not in path.read_bytes()
                assert b'LOCAL_INITIAL' not in path.read_bytes()
    finally:
        server.shutdown()
        server.server_close()


def test_metadata_filter_preserves_wire_fields_and_legacy_outputs_are_private(tmp_path, capsys):
    from website_client.admission import admit_openapi
    value = spec()
    value['paths']['/write'] = {'post': {'operationId': 'write', 'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['description'], 'properties': {'description': {'type': 'string', 'description': 'CANARY_PROSE'}, 'default': {'type': 'boolean'}, 'example': {'type': 'integer'}}}}}}, 'responses': {'204': {'description': 'CANARY_RESPONSE_DESCRIPTION'}}}}
    value['info']['description'] = 'CANARY_INFO'
    safe, executable, excluded = admit_openapi(value)
    properties = executable['paths']['/write']['post']['requestBody']['content']['application/json']['schema']['properties']
    assert set(properties) == {'description', 'default', 'example'}
    assert properties['description']['type'] == 'string'
    assert not excluded
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(value))
    imported = tmp_path / 'manifest.json'
    assert main(['import', '--openapi', str(source), '--output', str(imported)]) == 0
    output = tmp_path / 'legacy-client'
    assert main(['generate', '--openapi', str(source), '--name', 'legacy-client', '--output', str(output)]) == 0
    assert 'CANARY' not in imported.read_text()
    for path in output.rglob('*'):
        if path.is_file():
            assert b'CANARY' not in path.read_bytes()
    source.write_text('openapi: [CANARY_PARSER_SECRET: broken')
    assert main(['generate', '--openapi', str(source), '--output', str(tmp_path / 'broken')]) == 1
    assert 'CANARY' not in capsys.readouterr().err


def test_saved_manifest_sensitive_report_provenance_and_sha_privacy(tmp_path):
    from website_client.privacy import sanitized_manifest
    item = Artifact.create('CANARY_FILENAME.json', Kind.OPENAPI, json.dumps(spec()), Source.EXTERNAL,
                           locations=('https://neutral.invalid/spec?token=CANARY_LOCATION',))
    report = Artifact.create('evidence.json', Kind.REPORT, '{"private":"CANARY_PAYLOAD"}', Source.REPORT)
    target = Service.create(Protocol.OPENAPI, 'https://neutral.invalid', item.id, identifier='CANARY_SERVICE')
    supplied = Manifest.create((item, report), (target,))
    safe = sanitized_manifest(supplied)
    text = json.dumps(safe.to_dict())
    assert 'CANARY' not in text
    assert item.sha256 in text and report.sha256 in text
    source = tmp_path / 'manifest.json'
    write_manifest(supplied, source)
    output = tmp_path / 'output'
    result = run(source, output)
    assert len(result['services']) == 1
    assert b'CANARY' not in (output / 'manifest.json').read_bytes()


def test_missing_node_run_reports_dependency_reason(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'capture.har'
    source.write_text(json.dumps({'log': {'version': '1.2', 'entries': [{'request': {'method': 'GET', 'url': 'https://neutral.invalid/read'}, 'response': {'status': 200, 'content': {'mimeType': 'application/json', 'text': '{"value":7}'}}}]}}))
    monkeypatch.setattr('website_client.har_inference.shutil.which', lambda _: None)
    assert main(['run', str(source), '--output', str(tmp_path / 'output')]) == 1
    assert json.loads(capsys.readouterr().err) == {'coverage': 'unsupported', 'reason': 'har_dependency_required'}


def test_conflicting_artifact_identity_quarantines_affected_service_only(tmp_path):
    from dataclasses import replace
    first = Artifact.create('first.json', Kind.OPENAPI, json.dumps(spec('https://first.invalid')), Source.EXTERNAL)
    second_value = spec('https://second.invalid')
    second = replace(Artifact.create('second.json', Kind.OPENAPI, json.dumps(second_value), Source.EXTERNAL), id=first.id)
    unrelated = Artifact.create('unrelated.json', Kind.OPENAPI, json.dumps(spec('https://unrelated.invalid')), Source.EXTERNAL)
    manifests = [Manifest.create((first, unrelated), (Service.create(Protocol.OPENAPI, 'https://first.invalid', first.id), Service.create(Protocol.OPENAPI, 'https://unrelated.invalid', unrelated.id))),
                 Manifest.create((second,), (Service.create(Protocol.OPENAPI, 'https://second.invalid', second.id),))]
    sources = []
    for index, manifest in enumerate(manifests):
        path = tmp_path / ('manifest-' + str(index) + '.json')
        write_manifest(manifest, path)
        sources.append(path)
    result = run(sources[0], tmp_path / 'output', inputs=sources[1:])
    assert len(result['services']) == 1
    assert result['coverage'] == 'partial'
    assert sum(item['reason'] == 'conflicting_artifact_identity' for item in result['exclusions']) == 2
    project = tmp_path / 'output' / result['services'][0]['directory']
    assert json.loads((project / 'openapi.json').read_text())['servers'][0]['url'] == 'https://unrelated.invalid'


def test_historical_short_secret_path_and_sensitive_shapes_are_excluded(tmp_path):
    value = spec()
    value['paths']['/resource/SECRET123'] = {'get': {'operationId': 'capability_read', 'responses': {'200': {'description': 'ok'}}}}
    source = tmp_path / 'definition.json'
    source.write_text(json.dumps(value))
    output = tmp_path / 'output'
    result = run(source, output)
    assert result['coverage'] == 'partial'
    assert result['services'][0]['operations'] == ['read']
    assert any(item['reason'] == 'sensitive_structure' for item in result['exclusions'])
    for path in output.rglob('*'):
        if path.is_file():
            assert b'SECRET123' not in path.read_bytes()


@pytest.mark.parametrize('mode', ['traffic-only', 'html-only', 'same-origin-ref', 'declared-other-origin', 'declared-other-port'])
def test_url_route_coverage_origins_and_reference_bounds(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    class ApiHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', url)
            self.send_header('Access-Control-Allow-Headers', 'Authorization,X-Contract-Auth')
            self.send_header('Access-Control-Allow-Methods', 'GET,OPTIONS')
            self.end_headers()
        def do_GET(self):
            api.calls.append((self.path, self.headers.get('Cookie'), self.headers.get('Authorization'), self.headers.get('X-Contract-Auth')))
            assert self.headers.get('Cookie') is None
            assert self.headers.get('Authorization') == 'Bearer API_TOKEN'
            assert self.headers.get('X-Contract-Auth') == 'DECLARED_TOKEN'
            body = b'{"value":7}'
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', url)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    api = ThreadingHTTPServer(('127.0.0.1', 0), ApiHandler)
    api.calls = []
    threading.Thread(target=api.serve_forever, daemon=True).start()
    api_host = '127.0.0.1' if mode == 'declared-other-port' else 'localhost'
    api_url = f'http://{api_host}:{api.server_port}'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send(self, value, content_type='text/html', status=200):
            body = value.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            server.calls.append(self.path)
            if self.path == '/':
                link = '<link rel="service-desc" href="/definition/main.json">' if mode in {'same-origin-ref', 'declared-other-origin', 'declared-other-port'} else ''
                if mode == 'html-only':
                    data = ''
                else:
                    data_url = api_url + '/read' if mode in {'declared-other-origin', 'declared-other-port'} else url + '/read'
                    data = '<button role="tab" onclick="fetch(\'' + data_url + '\',{headers:{Authorization:\'Bearer API_TOKEN\',\'X-Contract-Auth\':\'DECLARED_TOKEN\'}})">Data</button>'
                return self.send(link + data + '<a href="/page-two">More</a><button role="tab" onclick="fetch(\'http://localhost:' + str(server.server_port) + '/offsite\')">Other</button>')
            if self.path == '/page-two':
                return self.send('<details><summary>Details</summary>Neutral</details>')
            if self.path == '/definition/main.json':
                document = spec(api_url if mode in {'declared-other-origin', 'declared-other-port'} else url)
                if mode == 'same-origin-ref':
                    document['paths']['/read']['get']['responses']['200']['content']['application/json']['schema'] = {'$ref': './schema.json'}
                else:
                    document['security'] = [{'Declared': []}]
                    document['components'] = {'securitySchemes': {'Declared': {'type': 'apiKey', 'in': 'header', 'name': 'X-Contract-Auth'}}}
                return self.send(json.dumps(document), 'application/json')
            if self.path == '/definition/schema.json':
                return self.send(json.dumps({'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'integer'}}}), 'application/json')
            if self.path == '/read':
                return self.send('{"value":7}', 'application/json')
            if self.path == '/offsite':
                server.offsite += 1
            return self.send('{}', 'application/json', 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.calls, server.offsite = [], 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    def initial_login(session):
        session._context.add_cookies([{'name': 'start_sid', 'value': 'START_COOKIE', 'url': url}])
    try:
        output = tmp_path / 'output'
        report = run(url, output, login_handler=initial_login, headless=True, max_pages=3, max_actions=5, duration_seconds=10)
        assert server.offsite == 0
        assert any(item['reason'] == 'undeclared_origin' for item in report['discovery']['exploration']['blockedRequests'])
        if mode == 'html-only':
            assert report['coverage'] == 'unsupported' and not report['services']
            return
        assert report['services']
        provenance = {item['provenance'] for item in report['services']}
        if mode == 'traffic-only':
            assert provenance == {'traffic-har2sdk'}
            service = report['services'][0]
            project = output / service['directory']
            result = subprocess.run([sys.executable, '-m', service['package'].replace('-', '_') + '._cli', service['operations'][0]], cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {'value': 7}
        else:
            assert 'definition' in provenance
            service = next(item for item in report['services'] if item['provenance'] == 'definition')
            project = output / service['directory']
            result = subprocess.run([sys.executable, '-m', service['package'].replace('-', '_') + '._cli', 'read'], cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {'value': 7}
            if mode == 'same-origin-ref':
                assert '/definition/schema.json' in server.calls
                assert '$ref' not in (project / 'openapi.json').read_text()
        profiles = [json.loads(path.read_text()) for path in (tmp_path / 'profiles').glob('*.json')]
        if mode in {'declared-other-origin', 'declared-other-port'}:
            assert len(profiles) == 2
            start = next(profile for profile in profiles if profile['origin'].startswith(url))
            selected = next(profile for profile in profiles if profile['origin'].startswith(api_url))
            assert 'authorization' not in start['headers']
            assert selected['headers'] == {'authorization': 'Bearer API_TOKEN', 'x-contract-auth': 'DECLARED_TOKEN'}
            assert selected['storage_state']['cookies'] == []
            assert start['storage_state']['cookies'][0]['value'] == 'START_COOKIE'
            if mode == 'declared-other-port':
                assert any(item['reason'] == 'cookie_origin_isolation' for item in report['exclusions'])
            assert len(api.calls) >= 2
        else:
            assert len(profiles) == 1
    finally:
        server.shutdown()
        server.server_close()
        api.shutdown()
        api.server_close()


def test_profile_output_overlap_is_rejected_before_login(tmp_path, monkeypatch):
    from website_client.browser import BrowserError
    output = tmp_path / 'output'
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(output / 'profiles'))
    monkeypatch.setattr('website_client.autonomous._browser_input', lambda *_a, **_k: pytest.fail('login opened before profile path validation'))
    with pytest.raises(BrowserError, match='profile_inside_output'):
        run('https://neutral.invalid', output)
    assert not output.exists()


@pytest.mark.parametrize('with_schema', [False, True])
def test_named_operations_exclude_subscription_without_switching_routes(tmp_path, with_schema):
    operations = tmp_path / 'operations.graphql'
    operations.write_text('query Status { status } subscription Updates { updates } query CANARY_PRIVATE { privateValue }')
    schema = tmp_path / 'schema.graphql'
    schema.write_text('type Query { status:String, privateValue:String } type Subscription { updates:String }')
    result = run(schema if with_schema else operations, tmp_path / 'output', endpoint='https://neutral.invalid/graphql', operations=operations if with_schema else None)
    assert result['coverage'] == 'partial'
    assert result['services'][0]['operations'] == ['Status']
    assert {item['reason'] for item in result['exclusions']} >= {'unsupported_subscription', 'sensitive_structure'}
    assert result['services'][0]['provenance'] == ('definition' if with_schema else 'schema-less-operations')
    project = tmp_path / 'output' / result['services'][0]['directory']
    assert (project / 'operations.graphql').exists() if with_schema else True
    for path in (tmp_path / 'output').rglob('*'):
        if path.is_file():
            assert b'CANARY' not in path.read_bytes()


def test_reused_service_id_with_different_endpoints_is_quarantined(tmp_path):
    from dataclasses import replace
    item = Artifact.create('schema.json', Kind.OPENAPI, json.dumps(spec()), Source.EXTERNAL)
    unrelated = Artifact.create('other.json', Kind.OPENAPI, json.dumps(spec('https://other.invalid')), Source.EXTERNAL)
    first = Manifest.create((item, unrelated), (Service.create(Protocol.OPENAPI, 'https://first.invalid', item.id, identifier='same-service'), Service.create(Protocol.OPENAPI, 'https://other.invalid', unrelated.id)))
    second = Manifest.create((item,), (Service.create(Protocol.OPENAPI, 'https://second.invalid', item.id, identifier='same-service'),))
    sources = []
    for index, manifest in enumerate((first, second)):
        source = tmp_path / ('manifest-' + str(index))
        write_manifest(manifest, source)
        sources.append(source)
    result = run(sources[0], tmp_path / 'output', inputs=sources[1:])
    assert result['coverage'] == 'partial'
    assert len(result['services']) == 1
    assert {'service': 'same-service', 'reason': 'conflicting_contracts'} in result['exclusions']


def test_saved_capture_manifest_uses_har_backend_and_graphql_own_route(tmp_path, monkeypatch):
    from website_client.discovery import Discovery
    from website_client.bundle_capture import capture_manifest
    discovery = Discovery()
    discovery.add({'request': {'method': 'GET', 'url': 'https://neutral.invalid/read?tag=a&tag=b'}, 'response': {'status': 200, 'content': {'mimeType': 'application/json', 'text': '{"value":7}'}}})
    manifest = capture_manifest(discovery, discovery.files('har'))
    source = tmp_path / 'captured-manifest.json'
    write_manifest(manifest, source)
    result = run(source, tmp_path / 'output')
    assert len(result['services']) == 1
    assert result['services'][0]['provenance'] == 'traffic-har2sdk'
    monkeypatch.setattr('website_client.har_inference.backend_module', lambda: pytest.fail('GraphQL-only traffic invoked HAR REST inference'))
    har = tmp_path / 'graphql.har'
    har.write_text(json.dumps({'log': {'version': '1.2', 'entries': [{'request': {'url': 'https://neutral.invalid/graphql', 'method': 'POST', 'postData': {'text': json.dumps({'query': 'query Status { status }', 'operationName': 'Status'})}}, 'response': {'status': 200, 'content': {'mimeType': 'application/json', 'text': '{"data":{"status":"ready"}}'}}}]}}))
    result = run(har, tmp_path / 'graphql-output')
    assert result['services'][0]['operations'] == ['Status']
    assert result['services'][0]['provenance'] == 'schema-less-operations'


def test_offline_reference_exclusion_never_opens_network(tmp_path, monkeypatch):
    import socket
    value = spec()
    value['paths']['/unresolved'] = {'get': {'operationId': 'unresolved', 'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'$ref': 'https://must-not-fetch.invalid/schema.json'}}}}}}}
    source = tmp_path / 'definition.yaml'
    source.write_text(yaml.safe_dump(value))
    monkeypatch.setattr(socket.socket, 'connect', lambda *_a, **_k: pytest.fail('offline generation opened a network connection'))
    result = run(source, tmp_path / 'output')
    assert result['coverage'] == 'partial'
    assert result['services'][0]['operations'] == ['read']
    assert any(item['reason'] == 'external_reference' for item in result['exclusions'])


def test_excluded_subscription_prunes_only_its_orphan_fragments(tmp_path):
    operations = tmp_path / 'operations.graphql'
    operations.write_text('query Status { ...StatusFields } subscription Updates { ...UpdateFields } fragment StatusFields on Query { status } fragment UpdateFields on Subscription { updates }')
    schema = tmp_path / 'schema.graphql'
    schema.write_text('type Query { status:String } type Subscription { updates:String }')
    result = run(schema, tmp_path / 'output', endpoint='https://neutral.invalid/graphql', operations=operations)
    assert result['coverage'] == 'partial'
    assert result['services'][0]['operations'] == ['Status']
    project = tmp_path / 'output' / result['services'][0]['directory']
    emitted = (project / 'operations.graphql').read_text()
    assert 'StatusFields' in emitted
    assert 'UpdateFields' not in emitted
    assert any(item['reason'] == 'unsupported_subscription' for item in result['exclusions'])
