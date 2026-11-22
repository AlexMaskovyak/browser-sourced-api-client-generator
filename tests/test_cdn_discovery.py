"""CDN assets render the app without admitting the CDN as an API origin."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace
from typing import Iterator

from graphql import build_schema, graphql_sync
import pytest

from website_client.autonomous import run
from website_client.browser import origin
from website_client.exploration_policy import request_allowed


@pytest.mark.parametrize(
    "resource_type", ["script", "stylesheet", "image", "font", "media"]
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_strict_discovery_allows_page_asset_reads(resource_type, method):
    request = browser_request(method=method, resource_type=resource_type)
    assert request_allowed(request, origin("https://app.example.test"), True) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_type": "fetch"},
        {"resource_type": "xhr"},
        {"resource_type": "document", "navigation": True},
        {"resource_type": "script", "navigation": True},
        {"resource_type": "other"},
        {"method": "POST"},
        {"method": "PUT"},
        {"method": "DELETE"},
        {"method": "OPTIONS"},
        {"url": "https://cdn.example.test/app.js?action=delete"},
        {"url": "https://cdn.example.test/app.js?query=mutation%20Save%20%7Bsave%7D"},
        {"url": "http://cdn.example.test/app.js"},
        {"url": "https://user:secret@cdn.example.test/app.js"},
    ],
)
def test_page_assets_do_not_exempt_api_calls_writes_or_unsafe_urls(overrides):
    request = browser_request(**overrides)
    assert request_allowed(request, origin("https://app.example.test"), True)


def browser_request(
    *,
    method="GET",
    resource_type="script",
    navigation=False,
    url="https://cdn.example.test/create-form.js",
):
    return SimpleNamespace(
        url=url,
        method=method,
        resource_type=resource_type,
        post_data=None,
        is_navigation_request=lambda: navigation,
    )


def test_cdn_driven_app_generates_callable_client_without_admitting_cdn(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WEBSITE_CLIENT_PROFILE_DIR", str(tmp_path / "profiles"))
    with fixture_server() as cdn, fixture_server() as app:
        app.cdn = cdn.url

        def initial_login(session):
            # This substitutes only for initial login and dashboard selection.
            session._page.goto(app.url + "/dashboard")
            session._page.get_by_role("tab", name="Records").wait_for()
            app.calls.clear()
            cdn.calls.clear()

        output = tmp_path / "generated"
        report = run(
            app.url + "/login",
            output,
            login_handler=initial_login,
            headless=True,
            max_pages=1,
            max_actions=2,
            duration_seconds=10,
        )

        # Before the fix the fresh guarded tab cannot load the script, so this
        # assertion fails with unsupported/no_executable_operations.
        assert report["services"], report["reason"]
        service = next(
            item for item in report["services"] if "Status" in item["operations"]
        )
        exploration = report["discovery"]["exploration"]
        assert exploration["actions"]
        assert {"/entry.js", "/create-app.js"} <= {path for _, path in cdn.calls}
        assert all(path in {"/entry.js", "/create-app.js"} for _, path in cdn.calls)
        assert ("POST", "/write") not in app.calls
        blocked = exploration["blockedRequests"]
        assert any(item["reason"] == "undeclared_origin" for item in blocked)
        assert any(item["method"] == "POST" for item in blocked)

        # A CDN response does not declare an API or create a credential profile.
        profiles = list((tmp_path / "profiles").glob("*.json"))
        assert len(profiles) == 1
        assert cdn.url not in profiles[0].read_text()
        project = output / service["directory"]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                service["package"].replace("-", "_") + "._cli",
                "Status",
            ],
            cwd=project,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"status": "ready"}
        assert sum(path == "/graphql" for _, path in app.calls) >= 3
        for path in output.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                assert b"CDN_TEST_BEARER" not in path.read_bytes()


@dataclass
class Fixture:
    """Independent server journal and neutral response definitions."""

    url: str = ""
    cdn: str = ""
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get(self, path: str) -> tuple[int, str, str]:
        if path == "/login":
            return 200, "text/html", "<p>Initial login</p>"
        if path == "/dashboard":
            return 200, "text/html", f'<script src="{self.cdn}/entry.js"></script>'
        if path == "/create-app.js":
            return 200, "application/javascript", dashboard_script(self.url)
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
            ):
                self.reply(403, "application/json", "{}")
                return
            body = json.loads(raw)
            result = graphql_sync(
                build_schema("type Query { status: String! }"),
                body["query"],
                root_value={"status": "ready"},
                variable_values=body.get("variables"),
                operation_name=body.get("operationName"),
            )
            self.reply(200, "application/json", json.dumps(result.formatted))

        def reply(self, status, content_type, body):
            content = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
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


def dashboard_script(cdn_url: str) -> str:
    return f"""
document.addEventListener('DOMContentLoaded', () => {{
  const button = document.createElement('button');
  button.setAttribute('role', 'tab');
  button.textContent = 'Records';
  button.onclick = () => {{
    fetch('/graphql', {{
      method: 'POST', headers: {{'Content-Type': 'application/json', Authorization: 'Bearer CDN_TEST_BEARER'}},
      body: JSON.stringify({{query: 'query Status {{ status }}', operationName: 'Status'}})
    }});
    fetch('/write', {{method: 'POST'}}).catch(() => {{}});
    fetch({json.dumps(cdn_url)} + '/api/private.js').catch(() => {{}});
    fetch({json.dumps(cdn_url)} + '/write', {{method: 'POST'}}).catch(() => {{}});
  }};
  document.body.appendChild(button);
}});
"""
