"""Local HTTPS service for browser login and generated endpoint tests."""

import http.server
import json
from pathlib import Path
import ssl
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit, urlencode
import uuid

from graphql import build_schema, graphql_sync, get_operation_ast, parse
from exploration_site import response as exploration_response

# The fixture accepts only small login forms and example JSON requests.
MAX_FIXTURE_REQUEST_BODY_BYTES = 4 * 1024
FIXTURES_DIRECTORY = Path(__file__).resolve().parents[1] / "tests/fixtures"

LOGIN_FORM = (
    '<form method="post" action="/login">'
    '<label>Username<input name="username"></label>'
    '<label>Password<input name="password" type="password"></label>'
    "<button>Sign in</button></form>"
)
VERIFICATION_FORM = (
    '<form method="post" action="/mfa">'
    '<label>Verification code<input name="code"></label>'
    "<button>Verify</button></form>"
)


def run_certificate_command(*args):
    subprocess.run(
        args,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare_tls(root):
    """Trust a fixture CA inside the container instead of bypassing TLS checks."""
    ca, key, cert, csr, cakey = [
        root / name
        for name in ("ca.crt", "server.key", "server.crt", "server.csr", "ca.key")
    ]
    run_certificate_command(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-keyout",
        str(cakey),
        "-out",
        str(ca),
        "-subj",
        "/CN=Website Client Test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    run_certificate_command(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(csr),
        "-subj",
        "/CN=localhost",
    )
    extensions = root / "extensions.cnf"
    extensions.write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
    )
    run_certificate_command(
        "openssl",
        "x509",
        "-req",
        "-in",
        str(csr),
        "-CA",
        str(ca),
        "-CAkey",
        str(cakey),
        "-CAcreateserial",
        "-days",
        "1",
        "-out",
        str(cert),
        "-extfile",
        str(extensions),
    )
    nss = Path.home() / ".pki/nssdb"
    nss.mkdir(parents=True, exist_ok=True)
    run_certificate_command("certutil", "-N", "--empty-password", "-d", f"sql:{nss}")
    run_certificate_command(
        "certutil",
        "-A",
        "-d",
        f"sql:{nss}",
        "-n",
        "website-client-test",
        "-t",
        "C,,",
        "-i",
        str(ca),
    )
    return cert, key


class SiteRequestHandler(http.server.BaseHTTPRequestHandler):
    @property
    def site(self):
        return self.server.site

    @property
    def authenticated(self):
        session_cookie = "session=" + self.site.secrets["cookie"]
        return session_cookie in self.headers.get("Cookie", "")

    def log_message(self, *_args):
        # Default HTTP logs can include query values used in redaction tests.
        pass

    def redirect(self, path, cookie=None):
        self.send_response(303)
        self.send_header("Location", path)
        if cookie:
            self.send_header(
                "Set-Cookie",
                cookie + "; Path=/; Secure; HttpOnly; SameSite=Lax",
            )
        self.end_headers()

    def send_json(self, value, status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def send_html(self, value):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(value.encode())

    def api_get(self, target):
        """Return fixture API data, recording every actual server request."""
        self.site.api_reads += 1
        if target.path == "/items/8":
            self.redirect("/redirect-target")
            return
        if target.path == "/items/7":
            self.send_json({"id": 7, "name": "Example"})
            return
        if target.path == "/openapi.json":
            self.send_json(self.site.openapi)
            return

        fields = parse_qs(target.query)
        result = graphql_sync(
            self.site.schema,
            fields["query"][0],
            root_value={"item": lambda _info, id: {"id": id, "name": "Example"}},
            variable_values=json.loads(fields.get("variables", ["{}"])[0]),
            operation_name=fields.get("operationName", [None])[0],
        )
        self.send_json(result.formatted)

    def do_GET(self):
        target = urlsplit(self.path)
        if self.authenticated and self.site.exploration:
            if target.path == "/logout":
                self.site.logout_hits += 1
            if target.path == "/outside":
                self.redirect("https://outside.invalid/exit")
                return
            content = exploration_response(self.path)
            if content:
                self.send_response(200)
                self.send_header("Content-Type", content[0])
                self.end_headers()
                self.wfile.write(content[1].encode())
                return
        if target.path == "/redirect-target":
            self.site.redirect_hits += 1
            self.send_error(500)
            return

        api_paths = {"/items/7", "/items/8", "/openapi.json", "/graphql"}
        if self.authenticated and target.path in api_paths:
            self.api_get(target)
        elif self.path == "/login":
            self.send_html(LOGIN_FORM)
        elif self.path == "/mfa":
            self.send_html(VERIFICATION_FORM)
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.authenticated:
            self.site.counts["app"] += 1
            content = "<h1>Signed in</h1>"
            if self.site.self_discovery:
                # The fixture page makes an ordinary GraphQL read without
                # project JavaScript. Discovery must observe it, not replay it.
                query = urlencode({"query": "query AutomaticRead { __typename }"})
                content += '<link rel="service-desc" href="/openapi.json">'
                content += '<iframe hidden src="/graphql?' + query + '"></iframe>'
            self.send_html(content)
        else:
            self.redirect("/login")

    def api_post(self, payload_text):
        # These checks prove that endpoint commands use the browser session,
        # including its cookie, application origin and native browser identity.
        assert self.headers.get("Origin") == self.site.url.rsplit("/", 1)[0]
        assert "Chrome" in self.headers.get("User-Agent", "")
        data = json.loads(payload_text)

        if self.path == "/graphql":
            if data.get("operationName") == "IntrospectionQuery":
                self.site.introspection_reads += 1
                if self.site.introspection_disabled:
                    self.site.api_reads += 1
                    self.send_json({"errors": [{"message": "Introspection disabled"}]})
                    return
            operation = get_operation_ast(
                parse(data["query"]), data.get("operationName")
            )
            if operation is not None and operation.operation.value == "mutation":
                self.site.api_writes += 1
            else:
                self.site.api_reads += 1
            result = graphql_sync(
                self.site.schema,
                data["query"],
                variable_values=data.get("variables"),
                root_value={
                    "item": lambda _info, id: {"id": id, "name": "Example"},
                    "createItem": lambda _info, name: {"id": "2", "name": name},
                },
            )
            self.send_json(result.formatted)
        else:
            self.site.api_writes += 1
            assert data == {"name": "Created"}
            self.send_json({"id": 2}, status_code=201)

    def do_POST(self):
        content_length_bytes = int(self.headers.get("Content-Length", "0"))
        if content_length_bytes > MAX_FIXTURE_REQUEST_BODY_BYTES:
            self.send_error(413)
            return

        payload_text = self.rfile.read(content_length_bytes).decode()
        if self.path in {"/items", "/graphql"} and self.authenticated:
            self.api_post(payload_text)
            return

        fields = parse_qs(payload_text)
        password_matches = all(
            fields.get(key) == [self.site.secrets[key]]
            for key in ("username", "password")
        )
        challenge_cookie = "challenge=" + self.site.secrets["challenge"]
        verification_matches = fields.get("code") == [
            self.site.secrets["code"]
        ] and challenge_cookie in self.headers.get("Cookie", "")

        if self.path == "/login" and password_matches:
            self.site.counts["password"] += 1
            self.redirect("/mfa", challenge_cookie)
        elif self.path == "/mfa" and verification_matches:
            self.site.counts["mfa"] += 1
            self.redirect("/app", "session=" + self.site.secrets["cookie"])
        else:
            self.send_error(401)


class Site:
    """Own the local service, fixture credentials and request counters."""

    def __init__(self, root):
        self.secrets = {
            "username": f"user-{uuid.uuid4()}@example.test",
            "password": str(uuid.uuid4()),
            "cookie": str(uuid.uuid4()),
            "challenge": str(uuid.uuid4()),
            "code": "123456",
        }
        self.counts = {"password": 0, "mfa": 0, "app": 0}
        self.api_reads = 0
        self.api_writes = 0
        self.redirect_hits = 0
        self.self_discovery = False
        self.introspection_disabled = False
        self.introspection_reads = 0
        self.exploration = False
        self.logout_hits = 0
        self.schema = build_schema((FIXTURES_DIRECTORY / "schema.graphql").read_text())
        self.openapi = json.loads((FIXTURES_DIRECTORY / "openapi.json").read_text())

        certificate_path, key_path = prepare_tls(root)
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            SiteRequestHandler,
        )
        self.server.site = self
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_path, key_path)
        self.server.socket = context.wrap_socket(
            self.server.socket,
            server_side=True,
        )
        threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        ).start()
        self.url = f"https://127.0.0.1:{self.server.server_port}/app"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
