"""Local workflow server, request journal and source documents shared by tests."""

import email.parser
import email.policy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading

import pytest
from website_client.runtime_profile import ProfileStore

FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'workflow'
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
                if server.mode == 'object':
                    value['ticket'] = {'nested': 'object'}
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
            if self.path.startswith('/resource/'):
                return self.send({'value': self.path.rsplit('/', 1)[-1]})
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

