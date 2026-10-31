"""Drive the installed Python CLI through a real terminal and native browser."""

import http.server
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import ProxyHandler, build_opener
import uuid

from website_client.browser import Connection
import website_client
from graphql import build_schema, get_introspection_query, graphql_sync


def eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result: return result
        except Exception as error:
            last = type(error).__name__
        time.sleep(0.05)
    raise AssertionError(f"Condition did not complete within {timeout} seconds ({last})")


class Site:
    def __init__(self, root):
        def run(*args): subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.secrets = {"username": f"user-{uuid.uuid4()}@example.test", "password": str(uuid.uuid4()),
                        "cookie": str(uuid.uuid4()), "challenge": str(uuid.uuid4()), "code": "123456"}
        self.counts = {"password": 0, "mfa": 0, "app": 0}
        self.api_reads = 0
        self.api_writes = 0
        self.redirect_hits = 0
        fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures"
        self.schema = build_schema((fixtures / "schema.graphql").read_text())
        self.openapi = json.loads((fixtures / "openapi.json").read_text())
        ca, key, cert, csr, cakey = [root / name for name in ("ca.crt", "server.key", "server.crt", "server.csr", "ca.key")]
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(cakey), "-out", str(ca), "-subj", "/CN=Website Client Test CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(csr), "-subj", "/CN=localhost")
        extensions = root / "extensions.cnf"
        extensions.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n")
        run("openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(cakey), "-CAcreateserial", "-days", "1", "-out", str(cert), "-extfile", str(extensions))
        nss = Path.home() / ".pki/nssdb"
        nss.mkdir(parents=True, exist_ok=True)
        run("certutil", "-N", "--empty-password", "-d", f"sql:{nss}")
        run("certutil", "-A", "-d", f"sql:{nss}", "-n", "website-client-test", "-t", "C,,", "-i", str(ca))
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args): pass

            def redirect(self, path, cookie=None):
                self.send_response(303)
                self.send_header("Location", path)
                if cookie: self.send_header("Set-Cookie", cookie + "; Path=/; Secure; HttpOnly; SameSite=Lax")
                self.end_headers()

            def do_GET(self):
                cookie = self.headers.get("Cookie", "")
                target = urlsplit(self.path)
                if target.path == "/redirect-target":
                    site.redirect_hits += 1
                    self.send_error(500)
                    return
                if "session=" + site.secrets["cookie"] in cookie and target.path in {"/items/7", "/items/8", "/openapi.json", "/graphql"}:
                    site.api_reads += 1
                    if target.path == "/items/8": self.redirect("/redirect-target"); return
                    if target.path == "/items/7": body = {"id": 7, "name": "Example"}
                    elif target.path == "/openapi.json": body = site.openapi
                    else:
                        fields = parse_qs(target.query)
                        body = graphql_sync(site.schema, fields["query"][0],
                            root_value={"item": lambda _info, id: {"id": id, "name": "Example"}},
                            variable_values=json.loads(fields.get("variables", ["{}"])[0]),
                            operation_name=fields.get("operationName", [None])[0]).formatted
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(body).encode())
                    return
                if self.path == "/login":
                    body = '<form method="post" action="/login"><label>Username<input name="username"></label><label>Password<input name="password" type="password"></label><button>Sign in</button></form>'
                elif self.path == "/mfa":
                    body = '<form method="post" action="/mfa"><label>Verification code<input name="code"></label><button>Verify</button></form>'
                elif self.path == "/favicon.ico":
                    self.send_response(204); self.end_headers(); return
                elif "session=" + site.secrets["cookie"] in cookie:
                    site.counts["app"] += 1
                    body = "<h1>Signed in</h1>"
                else:
                    self.redirect("/login"); return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if size > 4096: self.send_error(413); return
                raw = self.rfile.read(size).decode()
                if self.path in {"/items", "/graphql"} and "session=" + site.secrets["cookie"] in self.headers.get("Cookie", ""):
                    assert self.headers.get("Origin") == site.url.rsplit("/", 1)[0]
                    data = json.loads(raw)
                    if self.path == "/graphql":
                        if "mutation" in data["query"]: site.api_writes += 1
                        else: site.api_reads += 1
                        result = graphql_sync(site.schema, data["query"], variable_values=data.get("variables"),
                            root_value={"item": lambda _info, id: {"id": id, "name": "Example"},
                                        "createItem": lambda _info, name: {"id": "2", "name": name}}).formatted
                        status = 200
                    else:
                        site.api_writes += 1
                        assert data == {"name": "Created"}
                        result, status = {"id": 2}, 201
                    assert "Chrome" in self.headers.get("User-Agent", ""), "Request did not use the native browser"
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result).encode())
                    return
                fields = parse_qs(raw)
                if self.path == "/login" and all(fields.get(key) == [site.secrets[key]] for key in ("username", "password")):
                    site.counts["password"] += 1
                    self.redirect("/mfa", "challenge=" + site.secrets["challenge"])
                elif (self.path == "/mfa" and fields.get("code") == [site.secrets["code"]]
                      and "challenge=" + site.secrets["challenge"] in self.headers.get("Cookie", "")):
                    site.counts["mfa"] += 1
                    self.redirect("/app", "session=" + site.secrets["cookie"])
                else: self.send_error(401)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"https://127.0.0.1:{self.server.server_port}/app"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Command:
    def __init__(self, site, *, discover_output=None, executable=None, args=None, env=None):
        self.baseline = set(Path(tempfile.gettempdir()).glob("website-client-browser-*"))
        executable = executable or shutil.which("website-client")
        self.pid, self.terminal = pty.fork()
        if self.pid == 0:
            if args is None:
                args = ["discover" if discover_output else "login", "--url", site.url, "--browser", "chromium", "--browser-path", "/work/docker/test-chromium.sh"]
                if discover_output: args += ["--output", str(discover_output)]
            os.execve(executable, [executable, *args], {**os.environ, **(env or {})})
        self.output = ""
        self.status = None
        self.connection = None
        self.port = None

    def pump(self):
        if select.select([self.terminal], [], [], 0)[0]:
            try: data = os.read(self.terminal, 65536)
            except OSError: data = b""
            self.output += data.decode(errors="replace")
            assert len(self.output) < 1024 * 1024
        if self.status is None:
            child, status = os.waitpid(self.pid, os.WNOHANG)
            if child: self.status = os.waitstatus_to_exitcode(status)

    def ready(self):
        self.pump()
        if self.status is not None: raise AssertionError("CLI exited before login prompt: " + self.output)
        return "press Enter here to finish" in self.output or "Press Enter here to start capture" in self.output or "press Enter here to run the endpoint" in self.output

    def connect(self):
        profiles = set(Path(tempfile.gettempdir()).glob("website-client-browser-*")) - self.baseline
        assert len(profiles) == 1
        self.profile = profiles.pop()
        self.port = int((self.profile / "DevToolsActivePort").read_text().splitlines()[0])
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{self.port}/json/list") as response:
            pages = json.load(response)
        self.connection = Connection(next(page["webSocketDebuggerUrl"] for page in pages if page["type"] == "page"), self.port)

    def node(self, selector):
        root = self.connection.call("DOM.getDocument")["root"]["nodeId"]
        return self.connection.call("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]

    def fill(self, selector, value):
        node = eventually(lambda: self.node(selector))
        self.connection.call("DOM.focus", {"nodeId": node})
        self.connection.call("Input.insertText", {"text": value})

    def click(self):
        node = eventually(lambda: self.node("button"))
        quad = self.connection.call("DOM.getBoxModel", {"nodeId": node})["model"]["content"]
        position = {"x": (quad[0] + quad[4]) / 2, "y": (quad[1] + quad[5]) / 2, "button": "left", "clickCount": 1}
        for kind in ("mousePressed", "mouseReleased"):
            self.connection.call("Input.dispatchMouseEvent", {"type": kind, **position})

    def wait(self):
        def done(): self.pump(); return self.status is not None
        eventually(done)
        return self.status

    def cleanup_verified(self):
        assert not self.profile.exists(), "Temporary browser profile remains"
        opener = build_opener(ProxyHandler({}))
        try: opener.open(f"http://127.0.0.1:{self.port}/json/list", timeout=1)
        except OSError: return
        raise AssertionError("Owned browser listener remains open")

    def stop(self):
        if self.status is None:
            os.kill(self.pid, signal.SIGTERM)
            self.wait()
        if self.connection:
            try: self.connection.close()
            except Exception: pass
        os.close(self.terminal)


def main():
    assert sys.platform == "linux" and os.getuid() != 0
    assert shutil.which("node") is None and shutil.which("npm") is None
    assert not Path("/var/run/docker.sock").exists()
    assert not str(Path(website_client.__file__)).startswith("/work/src")
    with tempfile.TemporaryDirectory(prefix="website-client-fixture-") as directory:
        site = Site(Path(directory))
        try:
            for scenario in ("success", "unfinished", "cancelled", "closed", "terminated"):
                site.counts = dict.fromkeys(site.counts, 0)
                command = Command(site)
                try:
                    eventually(command.ready)
                    command.connect()
                    eventually(lambda: command.node('input[name="username"]'))
                    if scenario == "success":
                        command.fill('input[name="username"]', site.secrets["username"])
                        command.fill('input[name="password"]', site.secrets["password"])
                        command.click()
                        command.fill('input[name="code"]', site.secrets["code"])
                        command.click()
                        eventually(lambda: site.counts["app"] == 1)
                        os.write(command.terminal, b"\n")
                    elif scenario == "unfinished": os.write(command.terminal, b"\n")
                    elif scenario == "cancelled": os.write(command.terminal, b"\x03")
                    elif scenario == "terminated": os.kill(command.pid, signal.SIGTERM)
                    else:
                        try: command.connection.call("Browser.close")
                        except Exception: pass
                    code = command.wait()
                    if scenario == "success":
                        assert code == 0, command.output
                        assert '"sessionReady": true' in command.output
                        assert '"apiVerified": false' in command.output
                        assert site.counts == {"password": 1, "mfa": 1, "app": 1}
                    else: assert code != 0, command.output
                    for secret in site.secrets.values(): assert secret not in command.output
                    command.cleanup_verified()
                    print(f"PASS installed Python CLI: {scenario}", flush=True)
                finally: command.stop()

            capture_output = Path(directory) / "capture"
            command = Command(site, discover_output=capture_output)
            try:
                eventually(command.ready)
                command.connect()
                command.fill('input[name="username"]', site.secrets["username"])
                command.fill('input[name="password"]', site.secrets["password"])
                command.click()
                command.fill('input[name="code"]', site.secrets["code"])
                command.click()
                eventually(lambda: command.node("h1"))
                os.write(command.terminal, b"\n")
                def capturing(): command.pump(); return "Capture started." in command.output
                eventually(capturing)
                initial_reads = site.api_reads
                base = site.url.rsplit("/", 1)[0]
                urls = [base + "/items/7?search=" + site.secrets["password"], base + "/openapi.json",
                    base + "/graphql?" + urlencode({"operationName": "GetItem", "query": "query GetItem($id: ID!) { item(id: $id) { id name } }", "variables": '{"id":"7"}'}),
                    base + "/graphql?" + urlencode({"operationName": "IntrospectionQuery", "query": get_introspection_query()})]
                for expected, url in enumerate(urls, initial_reads + 1):
                    command.connection.call("Page.navigate", {"url": url})
                    eventually(lambda: site.api_reads == expected)
                    time.sleep(0.2)
                os.write(command.terminal, b"\n")
                assert command.wait() == 0, command.output
                assert site.api_reads == initial_reads + len(urls), "Discovery replayed an API request"
                command.cleanup_verified()
                report = json.loads((capture_output / "discovery.json").read_text())
                assert report["source"] == "browser" and report["liveVerified"] is False
                assert report["harEntries"] >= 4, report
                assert list(capture_output.glob("openapi-downloaded-*.json"))
                assert list(capture_output.glob("graphql-schema-downloaded-*.graphql"))
                assert list(capture_output.glob("operations-*.graphql"))
                for path in capture_output.iterdir():
                    for secret in site.secrets.values(): assert secret not in path.read_text(), path.name
                offline = Path(directory) / "offline"
                subprocess.run([shutil.which("website-client"), "discover", "--har", str(capture_output / "capture.har"), "--output", str(offline)], check=True, capture_output=True)
                assert (offline / "discovery.json").exists()
                print("PASS installed Python CLI: authenticated capture and offline HAR import", flush=True)

                for kind in ("openapi", "graphql"):
                    output = Path(directory) / (kind + "-client")
                    name = "fixture-" + kind
                    args = [shutil.which("website-client"), "generate", "--name", name, "--output", str(output)]
                    if kind == "openapi": args += ["--openapi", str(next(capture_output.glob("openapi-downloaded-*.json")))]
                    else: args += ["--graphql-schema", str(next(capture_output.glob("graphql-schema-downloaded-*.graphql"))), "--operations", str(Path(__file__).resolve().parents[1] / "tests/fixtures/operations.graphql")]
                    subprocess.run(args, check=True, capture_output=True)
                    subprocess.run(["poetry", "--directory", str(output), "build", "--format", "wheel"], check=True, capture_output=True)
                    installed = Path(directory) / (kind + "-installed")
                    wheel = next((output / "dist").glob("*.whl"))
                    subprocess.run([sys.executable, "-m", "venv", str(installed)], check=True, capture_output=True)
                    runtime_python = installed / "bin/python"
                    subprocess.run([str(runtime_python), "-m", "pip", "install", "--no-index", "--find-links", "/opt/client-wheels", str(wheel)], check=True, capture_output=True)
                    subprocess.run([str(runtime_python), "-c", "import importlib.util; assert importlib.util.find_spec('website_client') is None; assert importlib.util.find_spec('ariadne_codegen') is None; assert importlib.util.find_spec('openapi_python_client') is None"], check=True, capture_output=True)
                    executable = installed / "bin" / name
                    help_result = subprocess.run([str(executable), "--help"], check=True, capture_output=True, text=True)
                    assert ("get_item" if kind == "openapi" else "GetItem") in help_result.stdout
                    print(f"PASS installed generated Python client: {kind} wheel and endpoint help", flush=True)
                    for scenario in ("read", "write", "cancel", "redirect") if kind == "openapi" else ("read", "write", "closed"):
                        inputs = Path(directory) / f"{kind}-{scenario}.json"
                        if scenario == "write":
                            inputs.write_text(json.dumps({"body": {"name": "Created"}} if kind == "openapi" else {"name": "Created"}))
                            operation = "create_item" if kind == "openapi" else "CreateItem"
                        else:
                            inputs.write_text(json.dumps({"item_id": 8 if scenario == "redirect" else 7} if kind == "openapi" else {"id": "7"}))
                            operation = "get_item" if kind == "openapi" else "GetItem"
                        arguments = [operation, "--url", base if kind == "openapi" else base + "/graphql", "--input", str(inputs),
                            "--login", "--login-url", site.url, "--browser", "chromium", "--browser-path", "/work/docker/test-chromium.sh"]
                        if scenario == "write":
                            denied = subprocess.run([str(executable), *arguments], capture_output=True, text=True)
                            assert denied.returncode and "requires --confirm-write" in denied.stderr
                            arguments += ["--confirm-write", operation]
                        before_reads, before_writes = site.api_reads, site.api_writes
                        generated = Command(site, executable=str(executable), args=arguments)
                        try:
                            eventually(generated.ready)
                            generated.connect()
                            if scenario == "cancel": os.write(generated.terminal, b"\x03")
                            elif scenario == "closed":
                                try: generated.connection.call("Browser.close")
                                except Exception: pass
                            else:
                                generated.fill('input[name="username"]', site.secrets["username"])
                                generated.fill('input[name="password"]', site.secrets["password"])
                                generated.click()
                                generated.fill('input[name="code"]', site.secrets["code"])
                                generated.click()
                                eventually(lambda: generated.node("h1"))
                                os.write(generated.terminal, b"\n")
                            result = generated.wait()
                            if scenario in {"read", "write"}:
                                assert result == 0, generated.output
                                assert '"id":' in generated.output
                                assert site.api_reads == before_reads + (scenario == "read")
                                assert site.api_writes == before_writes + (scenario == "write")
                            else:
                                assert result != 0, generated.output
                                assert site.api_reads == before_reads + (scenario == "redirect")
                                assert site.api_writes == before_writes
                                assert site.redirect_hits == 0
                            for secret in site.secrets.values(): assert secret not in generated.output
                            generated.cleanup_verified()
                            print(f"PASS generated browser login: {kind} {scenario}", flush=True)
                        finally: generated.stop()
            finally: command.stop()
        finally: site.close()


if __name__ == "__main__": main()
