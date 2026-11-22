"""Neutral loopback HTTP fixture. No production TLS behavior is asserted here."""

from email.parser import BytesParser
from email.policy import default
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit
import uuid

from graphql import build_schema, graphql_sync

ROOT = Path("/work/tests/fixtures")
BASE = "http://127.0.0.1:8765"
CDN = "http://127.0.0.1:8766"
SCHEMA = build_schema((ROOT / "native_upload/schema.graphql").read_text())
STATE = {
    "cookie": str(uuid.uuid4()),
    "expired": False,
    "journal": [],
    "logins": 0,
    "requests": 0,
    "api_attempts": 0,
    "cdn_assets": 0,
}
SECRETS = {
    "username": "fixture-person-" + str(uuid.uuid4()),
    "password": str(uuid.uuid4()),
    "code": "649285",
    "bearer": "fixture-bearer-" + str(uuid.uuid4()),
}


def contracts() -> tuple[dict, dict]:
    source = json.loads((ROOT / "workflow/openapi.json").read_text())
    source["servers"] = [{"url": BASE}]
    source["paths"]["/excluded"] = {
        "get": {
            "operationId": "excluded_form",
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "style": "form",
                    "explode": False,
                    "schema": {"type": "array", "items": {"type": "string"}},
                }
            ],
            "responses": {"200": {"description": "ok"}},
        }
    }
    for name, schema in [
        ("nullable", {"type": "string", "nullable": True}),
        (
            "nested",
            {
                "type": "object",
                "properties": {"value": {"type": "string", "nullable": True}},
            },
        ),
        ("nonnull", {"type": "string"}),
    ]:
        source["paths"]["/" + name] = {
            "post": {
                "operationId": name,
                "requestBody": {
                    "required": name != "nullable",
                    "content": {"application/json": {"schema": schema}},
                },
                "responses": {"200": {"description": "ok"}},
            }
        }
    workflow = json.loads((ROOT / "workflow/workflow.json").read_text())
    for name, operation, body in [
        ("null", "nullable", {"contentType": "application/json", "payload": None}),
        ("omitted", "nullable", None),
        (
            "nested",
            "nested",
            {"contentType": "application/json", "payload": {"value": None}},
        ),
        (
            "disallowed",
            "nonnull",
            {"contentType": "application/json", "payload": "$inputs.value"},
        ),
    ]:
        step = {
            "stepId": "send",
            "operationId": operation,
            "successCriteria": [{"condition": "$statusCode == 200"}],
        }
        if body is not None:
            step["requestBody"] = body
        workflow["workflows"].append({"workflowId": name, "steps": [step]})
    return source, workflow


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_) -> None:
        pass

    def send(self, value, status=200, kind="application/json", cookie=False):
        body = (
            json.dumps(value).encode() if kind == "application/json" else value.encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header(
                "Set-Cookie",
                "session=" + STATE["cookie"] + "; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()
        self.wfile.write(body)

    def allowed(self) -> bool:
        return not STATE["expired"] and (
            "session=" + STATE["cookie"]
        ) in self.headers.get("Cookie", "")

    def api_allowed(self) -> bool:
        return (
            self.allowed()
            and self.headers.get("Authorization") == "Bearer " + SECRETS["bearer"]
        )

    def do_GET(self) -> None:
        target = urlsplit(self.path)
        if not target.path.startswith("/__"):
            STATE["requests"] += 1
        if target.path == "/__state":
            return self.send({**STATE, "secrets": SECRETS})
        if target.path == "/assets/app.js":
            STATE["cdn_assets"] += 1
            return self.send(
                'fetch("/graphql", {method:"POST",headers:{"Content-Type":"application/json",'
                '"Authorization":window.fixtureBearer},body:JSON.stringify({query:"{status}"})})',
                kind="application/javascript",
            )
        if target.path == "/empty":
            return self.send("<h1>No discoverable operations</h1>", kind="text/html")
        if target.path == "/login":
            return self.send(
                '<form method="post" action="/login"><input name="username"><input name="password" type="password"><button>Sign in</button></form>',
                kind="text/html",
            )
        if target.path == "/mfa":
            return self.send(
                '<form method="post" action="/mfa"><input name="code"><button>Verify</button></form>',
                kind="text/html",
            )
        if not self.allowed():
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        source, workflow = contracts()
        if target.path == "/openapi.json":
            return self.send(source)
        if target.path == "/workflow.json":
            return self.send(workflow)
        if target.path == "/graphql":
            if not self.api_allowed():
                return self.send({}, 401)
            values = parse_qs(target.query)
            result = graphql_sync(
                SCHEMA,
                values.get("query", ["{ status }"])[0],
                root_value={"status": "ready"},
            )
            return self.send(result.formatted)
        if target.path == "/app":
            return self.send(
                '<h1>Signed in</h1><link rel="service-desc" href="/workflow.json"><link rel="service-desc" href="/openapi.json">'
                + "<script>window.fixtureBearer="
                + json.dumps("Bearer " + SECRETS["bearer"])
                + '</script><script src="'
                + CDN
                + '/assets/app.js"></script>',
                kind="text/html",
            )
        self.send({}, 404)

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/__"):
            STATE["requests"] += 1
        if self.path == "/__expire":
            STATE["expired"] = True
            return self.send({})
        if self.path == "/__guard_fail":
            STATE["guard_fail"] = True
            return self.send({})
        if self.path == "/__rotate":
            STATE["rotate"] = True
            return self.send({})
        if self.path in {"/login", "/mfa"}:
            values = parse_qs(body.decode())
            expected = ("username", "password") if self.path == "/login" else ("code",)
            if not all(values.get(key) == [SECRETS[key]] for key in expected):
                return self.send({}, 401)
            self.send_response(303)
            self.send_header("Location", "/mfa" if self.path == "/login" else "/app")
            if self.path == "/mfa":
                STATE["logins"] += 1
                self.send_header(
                    "Set-Cookie",
                    "session=" + STATE["cookie"] + "; Path=/; HttpOnly; SameSite=Lax",
                )
            self.end_headers()
            return
        self.api(body)

    def do_PUT(self) -> None:
        self.api(self.rfile.read(int(self.headers.get("Content-Length", "0"))))

    def api(self, body: bytes) -> None:
        STATE["api_attempts"] += 1
        if not self.api_allowed():
            return self.send({"error": "expired"}, 401)
        kind = self.headers.get("Content-Type", "")
        entry = {
            "path": self.path,
            "method": self.command,
            "size": len(body),
            "body_hex": body.hex() if "multipart" not in kind else None,
        }
        STATE["journal"].append(entry)
        payloads = []
        note = ""
        if "multipart/form-data" in kind:
            parts = list(
                BytesParser(policy=default)
                .parsebytes(("Content-Type: " + kind + "\r\n\r\n").encode() + body)
                .iter_parts()
            )
            fields = {
                part.get_param("name", header="content-disposition"): part
                for part in parts
            }
            payloads = [
                part.get_payload(decode=True) for part in parts if part.get_filename()
            ]
            if self.path == "/graphql":
                operation = json.loads(fields["operations"].get_payload(decode=True))
                variables = operation["variables"]
                note = next(
                    (
                        value.get("note", "")
                        for value in variables.values()
                        if isinstance(value, dict)
                    ),
                    "",
                ) or next(
                    (
                        value
                        for key, value in variables.items()
                        if key.startswith("note")
                    ),
                    "",
                )
            else:
                note = fields["note"].get_payload(decode=True).decode()
        elif self.path == "/binary":
            payloads = [body]
        elif self.path == "/graphql":
            data = json.loads(body)
            result = graphql_sync(
                SCHEMA,
                data["query"],
                variable_values=data.get("variables"),
                root_value={"status": "ready"},
            )
            return self.send(result.formatted)
        if payloads:
            entry["files"] = [
                {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                for value in payloads
            ]
            if "multipart/form-data" in kind:
                entry["multipart"] = [
                    {
                        "filename": part.get_filename(),
                        "content_type": part.get_content_type(),
                    }
                    for part in parts
                    if part.get_filename()
                ]
            receipt = {
                "sha256": entry["files"][0]["sha256"],
                "size": len(payloads[0]),
                "note": note,
            }
            if self.path == "/graphql":
                field = (
                    "submit"
                    if "submit(" in operation["query"].replace(" ", "")
                    else "upload"
                )
                response = {"data": {field: receipt}}
            else:
                response = receipt
        else:
            response = (
                {"ticket": "fixture-ticket"}
                if self.path == "/prepare"
                else {"complete": True}
            )
        if self.path == "/prepare" and STATE.pop("guard_fail", False):
            response = {}
        rotate = STATE.pop("rotate", False)
        if rotate:
            STATE["cookie"] = str(uuid.uuid4())
        self.send(response, cookie=rotate)


if __name__ == "__main__":
    cdn_server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    Thread(target=cdn_server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
