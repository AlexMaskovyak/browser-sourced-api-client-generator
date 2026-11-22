"""Neutral websites used to test discovery without production accounts."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import yaml
from support.definitions import read_definition as spec


@contextmanager
def observed_website():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, content_type="text/html", status=200):
            if not isinstance(body, bytes):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/login":
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "sid=LOCAL_INITIAL; HttpOnly; Path=/")
                self.end_headers()
            else:
                server.writes += 1
                self.send("{}", "application/json")

        def do_GET(self):
            server.calls.append(self.path)
            if self.path == "/login":
                return self.send(
                    '<form method="post"><input name="username"><button type="submit">Sign in</button></form>'
                )
            if self.path == "/":
                return self.send(
                    '<link rel="service-desc" href="/definitions/main.yaml"><a href="/page-two">More</a><button role="tab" onclick="fetch(\'/observed\',{headers:{Authorization:\'Bearer LOCAL_BEARER\'}})">Records</button><button role="tab" onclick="fetch(\'/write\',{method:\'POST\'})">Actions</button>'
                )
            if self.path == "/page-two":
                return self.send("<details><summary>Details</summary>Neutral</details>")
            if self.path == "/definitions/main.yaml":
                return self.send(yaml.safe_dump(spec(url)), "application/yaml")
            if self.path in {"/read", "/observed"}:
                assert self.headers.get("Cookie") == "sid=LOCAL_INITIAL"
                if self.path == "/observed":
                    assert self.headers.get("Authorization") == "Bearer LOCAL_BEARER"
                return self.send('{"value":7}', "application/json")
            self.send("{}", "application/json", 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls, server.writes = [], 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def initial_login(session):
        session._page.locator("input").fill("neutral")
        session._page.locator("button").click()
        session._page.wait_for_url(url + "/")

    try:
        yield url, server, initial_login
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def reference_website(mode):
    class ApiHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", url)
            self.send_header(
                "Access-Control-Allow-Headers", "Authorization,X-Contract-Auth"
            )
            self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
            self.end_headers()

        def do_GET(self):
            api.calls.append(
                (
                    self.path,
                    self.headers.get("Cookie"),
                    self.headers.get("Authorization"),
                    self.headers.get("X-Contract-Auth"),
                )
            )
            assert self.headers.get("Cookie") is None
            assert self.headers.get("Authorization") == "Bearer API_TOKEN"
            assert self.headers.get("X-Contract-Auth") == "DECLARED_TOKEN"
            body = b'{"value":7}'
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", url)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    api = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    api.calls = []
    threading.Thread(target=api.serve_forever, daemon=True).start()
    api_host = "127.0.0.1" if mode == "declared-other-port" else "localhost"
    api_url = f"http://{api_host}:{api.server_port}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, value, content_type="text/html", status=200):
            body = value.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            server.calls.append(self.path)
            if self.path == "/":
                link = (
                    '<link rel="service-desc" href="/definition/main.json">'
                    if mode
                    in {
                        "same-origin-ref",
                        "declared-other-origin",
                        "declared-other-port",
                    }
                    else ""
                )
                if mode == "html-only":
                    data = ""
                else:
                    data_url = (
                        api_url + "/read"
                        if mode in {"declared-other-origin", "declared-other-port"}
                        else url + "/read"
                    )
                    data = (
                        '<button role="tab" onclick="fetch(\''
                        + data_url
                        + "',{headers:{Authorization:'Bearer API_TOKEN','X-Contract-Auth':'DECLARED_TOKEN'}})\">Data</button>"
                    )
                return self.send(
                    link
                    + data
                    + '<a href="/page-two">More</a><button role="tab" onclick="fetch(\'http://localhost:'
                    + str(server.server_port)
                    + "/offsite')\">Other</button>"
                )
            if self.path == "/page-two":
                return self.send("<details><summary>Details</summary>Neutral</details>")
            if self.path == "/definition/main.json":
                document = spec(
                    api_url
                    if mode in {"declared-other-origin", "declared-other-port"}
                    else url
                )
                if mode == "same-origin-ref":
                    document["paths"]["/read"]["get"]["responses"]["200"]["content"][
                        "application/json"
                    ]["schema"] = {"$ref": "./schema.json"}
                else:
                    document["security"] = [{"Declared": []}]
                    document["components"] = {
                        "securitySchemes": {
                            "Declared": {
                                "type": "apiKey",
                                "in": "header",
                                "name": "X-Contract-Auth",
                            }
                        }
                    }
                return self.send(json.dumps(document), "application/json")
            if self.path == "/definition/schema.json":
                return self.send(
                    json.dumps(
                        {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                        }
                    ),
                    "application/json",
                )
            if self.path == "/read":
                return self.send('{"value":7}', "application/json")
            if self.path == "/offsite":
                server.offsite += 1
            return self.send("{}", "application/json", 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls, server.offsite = [], 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    def initial_login(session):
        session._context.add_cookies(
            [{"name": "start_sid", "value": "START_COOKIE", "url": url}]
        )

    try:
        yield url, server, api, api_url, initial_login
    finally:
        server.shutdown()
        server.server_close()
        api.shutdown()
        api.server_close()
