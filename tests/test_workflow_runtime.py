"""Native Arazzo scheduling and serialization against independent local contracts."""

from copy import deepcopy
import email.parser
import email.policy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import socket
import threading

import pytest

from website_client.runtime_profile import ProfileStore
from website_client.workflow_runtime import execute_document, quiet_runner
from website_client.workflow_schema import validate_value

FIXTURES = Path(__file__).parent / 'fixtures' / 'workflow'
PAYLOAD = (FIXTURES / 'payload.bin').read_bytes()
DIGEST = 'e6b9f47e1fdba07810e19268cb55a8496558fbcc38f5b75f17bdf66300ef90e3'


def documents(url):
    document = json.loads((FIXTURES / 'workflow.json').read_text())
    spec = json.loads((FIXTURES / 'openapi.json').read_text())
    spec['servers'] = [{'url': url}]
    return document, {'api': spec}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send(self, value, status=200, cookie=None):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            self.process()
        def do_PUT(self):
            self.process()
        def do_GET(self):
            self.process()
        def process(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            server.calls.append((self.command, self.path, self.headers.get('Cookie'), body))
            if self.path == '/nullable':
                expected = b'null' if server.mode == 'whole-null' else b'' if server.mode == 'omitted' else b'{"value": null}'
                return self.send({'ok': body == expected}, 200 if body == expected else 422)
            if self.headers.get('Cookie') != 'sid=' + server.sid or self.headers.get('Authorization') != 'Bearer SYNTHETIC_TOKEN':
                return self.send({'error': 'expired'}, 401)
            if self.path == '/prepare':
                server.commits += 1
                if server.mode == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                assert json.loads(body) == {'filename': 'fixture.bin'}
                server.sid = 'ROTATED'
                value = {'ticket': 'neutral-ticket', 'ready': True}
                if server.mode == 'missing':
                    del value['ticket']
                if server.mode == 'null':
                    value['ticket'] = None
                if server.mode == 'malformed':
                    value = b'{broken'
                return self.send(value, cookie='sid=ROTATED; Path=/; HttpOnly')
            if self.path == '/binary':
                assert body == PAYLOAD
                server.stored = body
                return self.send({'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'note': 'binary'})
            if self.path == '/finish':
                value = json.loads(body)
                complete = value == {'ticket': 'neutral-ticket'} and server.stored == PAYLOAD
                assert complete
                return self.send({'complete': 1 if server.mode == 'integer' else False if server.mode == 'false' else True})
            if self.path == '/upload':
                message = email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body)
                parts = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
                assert parts['file'].get_filename() == 'payload.bin'
                assert parts['file'].get_content_type() == 'application/x-neutral'
                assert parts['file'].get_payload(decode=True) == PAYLOAD
                assert parts['note'].get_payload(decode=True) == b'neutral'
                return self.send({'size': len(PAYLOAD), 'sha256': DIGEST, 'note': 'neutral'})
            self.send({}, 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.calls, server.commits, server.sid, server.stored, server.mode = [], 0, 'INITIAL', b'', 'ok'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    with ProfileStore('neutral', url).locked() as store:
        store.save({'cookies': [{'name': 'sid', 'value': 'INITIAL', 'domain': '127.0.0.1', 'path': '/', 'expires': -1, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'}], 'origins': []}, {'authorization': 'Bearer SYNTHETIC_TOKEN'})
    yield url, server
    server.shutdown()
    server.server_close()


def test_native_guarded_sequence_exact_bytes_rotation_and_outputs(service):
    url, server = service
    document, sources = documents(url)
    result = execute_document(document, sources, 'upload', {'file': PAYLOAD}, {url: 'neutral'})
    assert result['status'] == 'success', result
    assert result['outputs'] == {'complete': True}
    assert result['requests'] == result['responses'] == 3
    assert [call[1] for call in server.calls] == ['/prepare', '/binary', '/finish']
    assert [call[2] for call in server.calls] == ['sid=INITIAL', 'sid=ROTATED', 'sid=ROTATED']
    assert server.commits == 1
    assert hashlib.sha256(PAYLOAD).hexdigest() == DIGEST and len(PAYLOAD) == 274
    with ProfileStore('neutral', url).locked() as store:
        assert store.load()['storage_state']['cookies'][0]['value'] == 'ROTATED'


@pytest.mark.parametrize('mode,reason,requests', [
    ('missing', 'missing_required_output', 1), ('null', 'null_required_output', 1),
    ('malformed', 'malformed_response_json', 1), ('integer', 'criterion_requires_boolean', 3),
    ('false', 'assertion_failed', 3), ('disconnect', 'transport_failure', 1),
])
def test_failures_never_dispatch_a_dependent_step_or_retry(service, mode, reason, requests):
    url, server = service
    server.mode = mode
    document, sources = documents(url)
    result = execute_document(document, sources, 'upload', {'file': PAYLOAD}, {url: 'neutral'})
    assert result['status'] == 'failure', result
    assert result['reason'] == reason
    assert len(server.calls) == requests
    assert server.commits == 1
    if mode == 'disconnect':
        assert result['effects'] == 'unknown'
        assert result['requests'] == 1 and result['responses'] == 0
    assert 'SYNTHETIC_TOKEN' not in json.dumps(result)


def test_unknown_file_objects_and_missing_inputs_rejected_before_dispatch(service):
    url, server = service
    document, sources = documents(url)
    for inputs, reason in [({'file': BytesIO(PAYLOAD)}, 'unsupported_input_object'), ({}, 'missing_input')]:
        result = execute_document(document, sources, 'upload', inputs, {url: 'neutral'})
        assert result['reason'] == reason
    assert not server.calls


def test_versioned_native_json_null_omitted_nested_and_schema_rejection(service):
    import requests
    import importlib.metadata
    from arazzo_runner import ArazzoRunner
    assert importlib.metadata.version('arazzo-runner') == '0.9.6+websiteclient.1'
    url, server = service
    server.mode = 'whole-null'
    source = {'openapi': '3.0.3', 'info': {'title': 'Nullable', 'version': '1'}, 'servers': [{'url': url}], 'paths': {'/nullable': {'post': {'operationId': 'nullable', 'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'string', 'nullable': True}}}}, 'responses': {'200': {'description': 'ok'}}}}}}
    document = {'arazzo': '1.0.0', 'info': {'title': 'Nullable', 'version': '1'}, 'sourceDescriptions': [{'name': 'api', 'type': 'openapi', 'url': 'openapi.json'}], 'workflows': [{'workflowId': 'nullable', 'steps': [{'stepId': 'send', 'operationId': 'nullable', 'requestBody': {'contentType': 'application/json', 'payload': None}, 'successCriteria': [{'condition': '$statusCode == 200'}]}]}]}
    validate_value(None, {'type': 'string', 'nullable': True}, dialect='3.0.3')
    with requests.Session() as session, quiet_runner():
        session.trust_env = False
        runner = ArazzoRunner(arazzo_doc=document, source_descriptions={'api': source}, http_client=session)
        execution = runner.start_workflow('nullable', {})
        event = runner.execute_next_step(execution)
    assert server.calls[-1][3] == b'null'
    assert event.get('success') is True
    server.calls.clear()
    result = execute_document(document, {'api': source}, 'nullable')
    assert result['status'] == 'success', result
    assert server.calls[-1][3] == b'null'
    source['paths']['/nullable']['post']['requestBody']['content']['application/json']['schema'] = {'type': 'string'}
    before = len(server.calls)
    result = execute_document(document, {'api': source}, 'nullable')
    assert result['reason'] == 'invalid_request' and len(server.calls) == before
    source['paths']['/nullable']['post']['requestBody']['required'] = False
    document['workflows'][0]['steps'][0]['requestBody'] = {'contentType': 'application/json'}
    server.mode = 'omitted'
    result = execute_document(document, {'api': source}, 'nullable')
    assert result['status'] == 'success', result
    assert server.calls[-1][3] == b''
    source['paths']['/nullable']['post']['requestBody']['required'] = True
    server.mode = 'nested-null'
    source['paths']['/nullable']['post']['requestBody']['content']['application/json']['schema'] = {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'string', 'nullable': True}}}
    document['workflows'][0]['steps'][0]['requestBody']['payload'] = {'value': None}
    result = execute_document(document, {'api': source}, 'nullable')
    assert result['status'] == 'success', result
    assert server.calls[-1][3] == b'{"value": null}'


def test_native_flat_multipart_retains_filename_media_type_and_bytes(service):
    url, server = service
    document, sources = documents(url)
    sources['api']['paths']['/upload']['post']['requestBody']['content']['multipart/form-data']['encoding'] = {'file': {'contentType': 'application/x-neutral'}}
    document['workflows'] = [{'workflowId': 'multipart', 'steps': [{'stepId': 'send', 'operationId': 'upload_file', 'requestBody': {'contentType': 'multipart/form-data', 'payload': {'file': {'content': '$inputs.file', 'file_name': 'payload.bin', 'contentType': 'application/x-neutral'}, 'note': 'neutral'}}, 'successCriteria': [{'condition': '$statusCode == 200'}]}]}]
    result = execute_document(document, sources, 'multipart', {'file': PAYLOAD}, {url: 'neutral'})
    assert result['status'] == 'success', result
    assert len(server.calls) == 1
