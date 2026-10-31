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
from urllib.parse import parse_qs
from urllib.request import ProxyHandler, build_opener
import uuid

from website_client.browser import Connection
import website_client


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
                fields = parse_qs(self.rfile.read(size).decode())
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
    def __init__(self, site):
        self.baseline = set(Path(tempfile.gettempdir()).glob("website-client-browser-*"))
        executable = shutil.which("website-client")
        self.pid, self.terminal = pty.fork()
        if self.pid == 0:
            os.execv(executable, [executable, "login", "--url", site.url, "--browser", "chromium", "--browser-path", "/work/docker/test-chromium.sh"])
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
        return "press Enter here to finish" in self.output

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
        finally: site.close()


if __name__ == "__main__": main()
