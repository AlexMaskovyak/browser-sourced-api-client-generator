"""Promoted native-upload contracts and private runtime tests, using local services."""

from contextlib import ExitStack
import email.parser
import email.policy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import httpx
import pytest

from website_client.authenticated_http import AuthenticatedDispatch, AuthenticationRequired, SDKTransport, profile_dispatch, workflow_session
from website_client.generation import generate
from website_client.native_files import convert
from website_client.runtime_profile import HeaderCapture, ProfileError, ProfileStore, save_browser_profile

FIXTURES = Path(__file__).parent / 'fixtures' / 'native_upload'
PAYLOAD = (FIXTURES / 'payload.bin').read_bytes()
DIGEST = 'e6b9f47e1fdba07810e19268cb55a8496558fbcc38f5b75f17bdf66300ef90e3'


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, value, status=200, cookie=None):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def do_PUT(self):
            self.dispatch()

        def dispatch(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            server.calls.append((self.path, self.headers.get('Cookie'), self.headers.get('Authorization'), body))
            if self.path == '/browser-login':
                raw = b'''<script>fetch('/read', {headers: {Authorization: 'Bearer SYNTHETIC_TOKEN'}}).then(r => {window.finished = r.ok})</script>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Set-Cookie', 'sid=SYNTHETIC_INITIAL; Path=/; HttpOnly')
                self.end_headers()
                self.wfile.write(raw)
                return
            if server.expired or self.headers.get('Cookie') != 'sid=' + server.sid or self.headers.get('Authorization') != 'Bearer SYNTHETIC_TOKEN':
                return self.send({'reason': 'expired'}, 401)
            if self.path.startswith('/rotate'):
                server.rotations += 1
                server.sid = 'SYNTHETIC_ROTATED' + (str(server.rotations) if server.rotations > 1 else '')
                return self.send({'rotated': True}, cookie='sid=' + server.sid + '; Path=/; HttpOnly')
            if self.path == '/read':
                return self.send({'ok': True})
            if self.path == '/binary':
                files, note = [body], 'binary'
            else:
                message = email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body)
                parts = {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}
                fields = {name: part.get_payload(decode=True) for name, part in parts.items()}
                if self.path == '/upload':
                    files, note = [fields['file']], fields['note'].decode()
                    assert parts['file'].get_filename() == 'payload.bin'
                    assert parts['file'].get_content_type() == 'application/octet-stream'
                else:
                    operation = json.loads(fields['operations'])
                    mapping = json.loads(fields['map'])
                    files, note = [fields[key] for key in mapping], 'graphql'
                    if operation['operationName'] == 'NestedUpload':
                        assert len(files) == 2
                        assert operation['variables']['input']['note'] == 'graphql'
                        assert set(path for paths in mapping.values() for path in paths) == {'variables.input.files.0', 'variables.input.files.1'}
            if not files or any(item != PAYLOAD for item in files):
                return self.send({'reason': 'wrong bytes'}, 422)
            receipt = {'sha256': hashlib.sha256(files[0]).hexdigest(), 'size': len(files[0]), 'note': note}
            server.uploads += 1
            if self.path == '/graphql':
                receipt = {'data': {'submit' if operation['operationName'] == 'NestedUpload' else 'upload': receipt}}
            self.send(receipt)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.calls, server.expired, server.sid, server.uploads = [], False, 'SYNTHETIC_INITIAL', 0
    server.rotations = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', server
    server.shutdown()
    server.server_close()


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / 'private'
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(root))
    return root


def provision(url, identifier='neutral'):
    with ProfileStore(identifier, url).locked() as store:
        store.save({'cookies': [{'name': 'sid', 'value': 'SYNTHETIC_INITIAL', 'domain': '127.0.0.1', 'path': '/', 'expires': -1, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'}], 'origins': []}, {'authorization': 'Bearer SYNTHETIC_TOKEN'})


@pytest.fixture
def native_projects(tmp_path, service, profiles, monkeypatch):
    url, server = service
    provision(url)
    rest, gql = tmp_path / 'rest', tmp_path / 'graphql'
    generate(rest, 'wire-rest', openapi=FIXTURES / 'openapi.json', runtime_config={'endpoint': url, 'profileId': 'neutral'})
    generate(gql, 'wire-graphql', schema=FIXTURES / 'schema.graphql', operations=FIXTURES / 'operations.graphql', runtime_config={'endpoint': url + '/graphql', 'profileId': 'neutral'})
    monkeypatch.syspath_prepend(str(rest))
    monkeypatch.syspath_prepend(str(gql))
    yield rest, gql
    for name in list(sys.modules):
        if name == 'wire_rest' or name.startswith('wire_rest.') or name == 'wire_graphql' or name.startswith('wire_graphql.'):
            del sys.modules[name]


def test_four_native_uploads_and_generated_public_defaults(native_projects, service):
    assert len(PAYLOAD) == 274 and hashlib.sha256(PAYLOAD).hexdigest() == DIGEST
    rest = importlib.import_module('wire_rest')
    gql = importlib.import_module('wire_graphql')
    upload = importlib.import_module('wire_rest.api.default.upload_file')
    binary = importlib.import_module('wire_rest.api.default.put_binary')
    body_type = importlib.import_module('wire_rest.models.upload_file_body').UploadFileBody
    file_type = importlib.import_module('wire_rest.types').File
    path = str(FIXTURES / 'payload.bin')
    with ExitStack() as stack:
        body = convert({'file': {'path': path}, 'note': 'rest'}, body_type, stack)
        raw = convert({'path': path}, file_type, stack)
        with rest.client_context() as client:
            receipt = upload.sync(client=client, body=body)
            assert receipt.sha256 == DIGEST
            assert binary.sync(client=client, body=raw).size == 274
        assert not body.file.payload.closed
    assert body.file.payload.closed and raw.payload.closed
    upload_type = importlib.import_module('wire_graphql.base_model').Upload
    input_type = importlib.import_module('wire_graphql.input_types').UploadInput
    with ExitStack() as stack:
        file = convert({'path': path}, upload_type, stack)
        nested = convert({'files': [{'path': path}, {'path': path}], 'note': 'graphql'}, input_type, stack)
        with gql.client_context() as client:
            assert client.upload_file(file=file, note='graphql').upload.sha_256 == DIGEST
            assert client.nested_upload(input=nested).submit.size == 274
    assert file.content.closed and all(item.content.closed for item in nested.files)
    assert service[1].uploads == 4
    for project in native_projects:
        for path in project.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                assert b'SYNTHETIC_TOKEN' not in path.read_bytes()
                assert b'SYNTHETIC_INITIAL' not in path.read_bytes()


def test_generated_cli_subprocess_uses_defaults_and_closed_stdin(native_projects, service, tmp_path):
    rest, gql = native_projects
    input_path = tmp_path / 'input.json'
    environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    descriptor = {'path': str((FIXTURES / 'payload.bin').resolve())}
    cases = [
        (rest, 'wire_rest', 'upload_file', {'body': {'file': descriptor, 'note': 'rest'}}),
        (rest, 'wire_rest', 'put_binary', {'body': descriptor}),
        (gql, 'wire_graphql', 'UploadFile', {'file': descriptor, 'note': 'graphql'}),
        (gql, 'wire_graphql', 'NestedUpload', {'input': {'files': [descriptor, descriptor], 'note': 'graphql'}}),
    ]
    for project, package, operation, arguments in cases:
        input_path.write_text(json.dumps(arguments))
        result = subprocess.run([sys.executable, '-m', package + '._cli', operation, '--confirm-write', operation, '--input', str(input_path)], cwd=project, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert DIGEST in result.stdout
    assert service[1].uploads == 4
    input_path.write_text(json.dumps({'body': descriptor}))
    service[1].expired = True
    result = subprocess.run([sys.executable, '-m', 'wire_rest._cli', 'put_binary', '--confirm-write', 'put_binary', '--input', str(input_path)], cwd=rest, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert json.loads(result.stderr)['reason'] == 'authentication_required'
    assert service[1].uploads == 4


def test_rotation_saved_on_error_and_reused_without_browser(service, profiles):
    url, server = service
    provision(url)
    with pytest.raises(RuntimeError, match='job failed'):
        with profile_dispatch('neutral', url) as dispatch:
            with httpx.Client(transport=SDKTransport(dispatch), trust_env=False) as client:
                assert client.get(url + '/rotate').status_code == 200
            raise RuntimeError('job failed')
    with profile_dispatch('neutral', url) as dispatch:
        with workflow_session(dispatch) as session:
            assert not session.trust_env
            assert session.get(url + '/read').json() == {'ok': True}
    assert server.calls[-1][1] == 'sid=SYNTHETIC_ROTATED'
    assert (profiles / 'neutral.json').stat().st_mode & 0o777 == 0o600


def test_origin_permissions_capture_and_no_credentials_in_errors(service, profiles):
    url, server = service
    provision(url)
    capture = HeaderCapture(url)
    capture.observe('Network.requestWillBeSent', {'request': {'url': 'https://other.invalid', 'headers': {'Authorization': 'OTHER'}}})
    capture.observe('Network.requestWillBeSent', {'request': {'url': url + '/read', 'headers': {'Authorization': 'Bearer SYNTHETIC_TOKEN', 'X-Unrelated': 'PRIVATE'}}})
    assert capture.headers == {'authorization': 'Bearer SYNTHETIC_TOKEN'}
    with pytest.raises(ProfileError, match='profile_origin_mismatch'):
        with ProfileStore('neutral', 'https://other.invalid').locked() as store:
            store.load()
    with profile_dispatch('neutral', url) as dispatch:
        with pytest.raises(ProfileError, match='undeclared_destination'):
            dispatch.send('GET', 'https://other.invalid/read', {}, b'')
        with pytest.raises(ProfileError, match='invalid_service_origin'):
            dispatch.send('GET', url.replace('http://', 'http://u:p@') + '/read', {}, b'')
    assert not server.calls
    (profiles / 'neutral.json').chmod(0o644)
    with pytest.raises(ProfileError, match='unsafe_profile_permissions'):
        with ProfileStore('neutral', url).locked() as store:
            store.load()


def test_profile_rejects_symlinks_and_filters_browser_state(service, profiles, tmp_path):
    url, server = service
    context = Mock()
    context.storage_state.return_value = {'cookies': [{'domain': 'other.invalid', 'name': 'private', 'value': 'OTHER'}], 'origins': [{'origin': 'https://other.invalid', 'localStorage': []}]}
    save_browser_profile(context, 'neutral', url)
    with ProfileStore('neutral', url).locked() as store:
        assert store.load()['storage_state'] == {'cookies': [], 'origins': []}
    (profiles / 'neutral.json').unlink()
    other = tmp_path / 'unrelated'
    other.write_text('do not change')
    other.chmod(0o600)
    (profiles / 'neutral.json').symlink_to(other)
    with pytest.raises(ProfileError, match='unsafe_profile_permissions'):
        with ProfileStore('neutral', url).locked() as store:
            store.save({}, {})
    assert other.read_text() == 'do not change'


def test_dispatch_disposes_redirect_auth_and_oversized_responses():
    api, response = Mock(), Mock()
    api.fetch.return_value = response
    dispatch = AuthenticatedDispatch(api, 'https://neutral.invalid')
    for status, error in [(302, ProfileError), (401, AuthenticationRequired), (403, AuthenticationRequired)]:
        response.status = status
        with pytest.raises(error):
            dispatch.send('POST', 'https://neutral.invalid/upload', {}, PAYLOAD)
        response.dispose.assert_called()
    response.status = 200
    response.body.return_value = b'x' * (8 * 1024 * 1024 + 1)
    with pytest.raises(ProfileError, match='response_exceeds'):
        dispatch.send('GET', 'https://neutral.invalid/read', {}, b'')
    assert api.fetch.call_args.kwargs['max_retries'] == 0
    assert api.fetch.call_args.kwargs['max_redirects'] == 0
    assert api.fetch.call_args.kwargs['timeout'] == 30000


def test_unknown_objects_bounds_and_file_cleanup(native_projects, tmp_path):
    body_type = importlib.import_module('wire_rest.models.upload_file_body').UploadFileBody
    with ExitStack() as stack:
        with pytest.raises(ValueError):
            convert({'file': {'path': str(FIXTURES / 'payload.bin')}, 'note': object()}, body_type, stack)
    with pytest.raises(ValueError):
        convert(object(), __import__('typing').Any)
    file_type = importlib.import_module('wire_rest.types').File
    oversized = tmp_path / 'large.bin'
    with oversized.open('wb') as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with ExitStack() as stack, pytest.raises(ValueError, match='exceeds 8 MiB'):
        convert({'path': str(oversized)}, file_type, stack)


def test_profile_lock_covers_two_request_jobs_in_separate_processes(service, profiles):
    url, server = service
    provision(url)
    program = '''
import sys, time, httpx
from website_client.authenticated_http import profile_dispatch, SDKTransport
with profile_dispatch('neutral', sys.argv[1]) as dispatch:
    with httpx.Client(transport=SDKTransport(dispatch), trust_env=False) as client:
        for i in range(2):
            response = client.get(sys.argv[1] + '/rotate?worker=' + sys.argv[2])
            assert response.status_code == 200
            time.sleep(0.15)
'''
    processes = [subprocess.Popen([sys.executable, '-c', program, url, worker], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for worker in ('A', 'B')]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()
    workers = [call[0].split('=')[-1] for call in server.calls]
    assert workers in [['A', 'A', 'B', 'B'], ['B', 'B', 'A', 'A']]
    assert [call[1] for call in server.calls] == ['sid=SYNTHETIC_INITIAL', 'sid=SYNTHETIC_ROTATED', 'sid=SYNTHETIC_ROTATED2', 'sid=SYNTHETIC_ROTATED3']
    with ProfileStore('neutral', url).locked() as store:
        assert store.load()['storage_state']['cookies'][0]['value'] == 'SYNTHETIC_ROTATED4'


def test_native_model_aliases_omission_requiredness_and_unknown_objects(tmp_path, monkeypatch):
    # Independently add wire names that differ from native Python attributes.
    source = json.loads((FIXTURES / 'openapi.json').read_text())
    properties = source['paths']['/upload']['post']['requestBody']['content']['multipart/form-data']['schema']['properties']
    properties['client-note'] = {'type': 'string'}
    spec = tmp_path / 'aliases.json'
    spec.write_text(json.dumps(source))
    output = tmp_path / 'aliases'
    generate(output, 'alias-rest', openapi=spec)
    monkeypatch.syspath_prepend(str(output))
    model = importlib.import_module('alias_rest.models.upload_file_body').UploadFileBody
    descriptor = {'path': str(FIXTURES / 'payload.bin')}
    with ExitStack() as stack:
        value = convert({'file': descriptor, 'note': 'normal', 'client-note': 'alias'}, model, stack)
        assert value.client_note == 'alias'
        assert dict(value.to_multipart())['client-note'][1] == b'alias'
        omitted = convert({'file': descriptor, 'note': 'normal'}, model, stack)
        assert 'client-note' not in dict(omitted.to_multipart())
        with pytest.raises(TypeError):
            convert({'file': descriptor}, model, stack)
        with pytest.raises(ValueError):
            convert({'file': descriptor, 'note': {'unknown': object()}}, model, stack)
    from pydantic import BaseModel, ConfigDict, Field
    class GraphInput(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        native_name: str = Field(alias='nativeName')
        optional_name: str | None = Field(default=None, alias='optionalName')
    value = convert({'nativeName': 'name'}, GraphInput)
    assert value.model_dump(by_alias=True, exclude_unset=True) == {'nativeName': 'name'}
    with pytest.raises(ValueError):
        convert({}, GraphInput)
    with pytest.raises(ValueError):
        convert({'nativeName': object()}, GraphInput)


def test_anonymous_profile_dispatch_is_origin_scoped_and_creates_no_profile(profiles):
    from http.server import SimpleHTTPRequestHandler
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        with profile_dispatch(None, url) as dispatch:
            assert dispatch.send('GET', url, {}, b'')[2] == b'ok'
            with pytest.raises(ProfileError, match='undeclared_destination'):
                dispatch.send('GET', 'https://other.invalid', {}, b'')
        assert not profiles.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_real_browser_cookie_and_bearer_capture_survives_browser_close(service, profiles):
    from playwright.sync_api import sync_playwright
    from website_client.browser import find_browser
    url, server = service
    capture = HeaderCapture(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=find_browser('chrome'), headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.on('request', lambda request: capture.observe('Network.requestWillBeSent', {'request': {'url': request.url, 'headers': request.all_headers()}}))
        page.goto(url + '/browser-login')
        page.wait_for_function('window.finished === true')
        save_browser_profile(context, 'neutral', url, capture.headers)
        context.close()
        browser.close()
    assert capture.headers == {'authorization': 'Bearer SYNTHETIC_TOKEN'}
    with profile_dispatch('neutral', url) as dispatch:
        assert json.loads(dispatch.send('GET', url + '/read', {}, b'')[2]) == {'ok': True}
    assert server.calls[-1][1:3] == ('sid=SYNTHETIC_INITIAL', 'Bearer SYNTHETIC_TOKEN')


def test_encoded_request_limit_stops_native_multipart_before_dispatch():
    from io import BytesIO
    api = Mock()
    dispatch = AuthenticatedDispatch(api, 'https://neutral.invalid')
    with httpx.Client(transport=SDKTransport(dispatch), trust_env=False) as client:
        with pytest.raises(ProfileError, match='request_exceeds_8_mib'):
            client.post('https://neutral.invalid/upload', files={'file': ('full.bin', BytesIO(b'x' * (8 * 1024 * 1024)), 'application/octet-stream')})
    api.fetch.assert_not_called()
