"""Independent website, CDN and GraphQL servers with request journals."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from typing import Iterator

from graphql import build_schema, graphql_sync


@dataclass
class Fixture:
    """Independent server journal and neutral response definitions."""

    url: str = ""
    cdn: str = ""
    api: str = ""
    website: str = ""
    introspection: bool = True
    mutations: int = 0
    cookies: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get(self, path: str) -> tuple[int, str, str]:
        if path == "/login":
            return 200, "text/html", "<p>Initial login</p>"
        if path == "/dashboard":
            return 200, "text/html", f'<script src="{self.cdn}/entry.js"></script>'
        if path == "/create-app.js":
            return 200, "application/javascript", dashboard_script(self.url, self.api)
        return 404, "application/json", "{}"


@contextmanager
def fixture_server() -> Iterator[Fixture]:
    fixture = Fixture()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            fixture.calls.append(("GET", self.path))
            if self.path == "/entry.js":
                self.send_response(302)
                self.send_header("Location", "/create-app.js")
                self.end_headers()
                return
            status, content_type, body = fixture.get(self.path)
            self.reply(status, content_type, body)

        def do_POST(self):
            fixture.calls.append(("POST", self.path))
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if (
                self.path != "/graphql"
                or self.headers.get("Authorization") != "Bearer CDN_TEST_BEARER"
                or self.headers.get("apollographql-client-name")
                != "SYNTHETIC_DASHBOARD"
                or self.headers.get("apollographql-client-version")
                != "SYNTHETIC_VERSION"
            ):
                self.reply(403, "application/json", "{}")
                return
            body = json.loads(raw)
            fixture.cookies.append(self.headers.get("Cookie", ""))
            if "mutation " in body["query"]:
                fixture.mutations += 1
            if (
                not fixture.introspection
                and body.get("operationName") == "IntrospectionQuery"
            ):
                self.reply(403, "application/json", "{}")
                return
            result = graphql_sync(
                build_schema(
                    "type Query { status: String! } type Mutation { save: String }"
                ),
                body["query"],
                root_value={"status": "ready"},
                variable_values=body.get("variables"),
                operation_name=body.get("operationName"),
            )
            self.reply(200, "application/json", json.dumps(result.formatted))

        def do_OPTIONS(self):
            self.send_response(204)
            self.cors()
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, apollographql-client-name, "
                "apollographql-client-version",
            )
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.end_headers()

        def cors(self):
            if fixture.website:
                self.send_header("Access-Control-Allow-Origin", fixture.website)
                self.send_header("Access-Control-Allow-Credentials", "true")

        def reply(self, status, content_type, body):
            content = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.cors()
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fixture.url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def dashboard_script(cdn_url: str, api_url: str = "") -> str:
    script = Path(__file__).parents[1] / "fixtures" / "browser" / "cdn_dashboard.js"
    return (
        script.read_text()
        .replace("API_ENDPOINT", json.dumps(api_url + "/graphql"))
        .replace("CDN_ORIGIN", json.dumps(cdn_url))
    )
