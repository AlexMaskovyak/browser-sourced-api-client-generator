"""Drive an installed command through its real terminal and owned browser."""

import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

from website_client.browser import Connection

# Local fixture operations should finish promptly; a hang must fail the test.
DEFAULT_WAIT_TIMEOUT_SECONDS = 15
POLL_INTERVAL_SECONDS = 0.05
CONTROL_CONNECT_TIMEOUT_SECONDS = 1
TERMINAL_READ_BYTES = 64 * 1024
MAX_TERMINAL_OUTPUT_BYTES = 1024 * 1024


def eventually(check, timeout_seconds=DEFAULT_WAIT_TIMEOUT_SECONDS):
    """Poll a real process or browser condition without replacing its behavior."""
    deadline_seconds = time.monotonic() + timeout_seconds
    last_error_name = None
    while time.monotonic() < deadline_seconds:
        try:
            result = check()
            if result:
                return result
        except Exception as error:
            last_error_name = type(error).__name__
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Condition did not complete within {timeout_seconds} seconds "
        f"({last_error_name})"
    )


class Command:
    def __init__(
        self, site, *, discover_output=None, executable=None, args=None, env=None
    ):
        self.baseline = set(
            Path(tempfile.gettempdir()).glob("website-client-browser-*")
        )
        executable = executable or shutil.which("website-client")
        self.pid, self.terminal = pty.fork()
        if self.pid == 0:
            if args is None:
                args = [
                    "discover" if discover_output else "login",
                    "--url",
                    site.url,
                    "--browser",
                    "chromium",
                    "--browser-path",
                    "/work/docker/test-chromium.sh",
                ]
                if discover_output:
                    args += ["--output", str(discover_output)]
            os.execve(executable, [executable, *args], {**os.environ, **(env or {})})
        self.output = ""
        self.status = None
        self.connection = None
        self.port = None

    def pump(self):
        if select.select([self.terminal], [], [], 0)[0]:
            try:
                data = os.read(self.terminal, TERMINAL_READ_BYTES)
            except OSError:
                data = b""
            self.output += data.decode(errors="replace")
            assert len(self.output.encode()) < MAX_TERMINAL_OUTPUT_BYTES
        if self.status is None:
            child, status = os.waitpid(self.pid, os.WNOHANG)
            if child:
                self.status = os.waitstatus_to_exitcode(status)

    def ready(self):
        self.pump()
        if self.status is not None:
            raise AssertionError("CLI exited before login prompt: " + self.output)
        return (
            "press Enter here to finish" in self.output
            or "Press Enter here to start capture" in self.output
            or "press Enter here to run the endpoint" in self.output
        )

    def connect(self):
        profiles = (
            set(Path(tempfile.gettempdir()).glob("website-client-browser-*"))
            - self.baseline
        )
        assert len(profiles) == 1
        self.profile = profiles.pop()
        self.port = int(
            (self.profile / "DevToolsActivePort").read_text().splitlines()[0]
        )
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{self.port}/json/list") as response:
            pages = json.load(response)
        self.connection = Connection(
            next(
                page["webSocketDebuggerUrl"] for page in pages if page["type"] == "page"
            ),
            self.port,
        )

    def node(self, selector):
        root = self.connection.call("DOM.getDocument")["root"]["nodeId"]
        return self.connection.call(
            "DOM.querySelector", {"nodeId": root, "selector": selector}
        )["nodeId"]

    def fill(self, selector, value):
        node = eventually(lambda: self.node(selector))
        self.connection.call("DOM.focus", {"nodeId": node})
        self.connection.call("Input.insertText", {"text": value})

    def click(self):
        node = eventually(lambda: self.node("button"))
        quad = self.connection.call("DOM.getBoxModel", {"nodeId": node})["model"][
            "content"
        ]
        position = {
            "x": (quad[0] + quad[4]) / 2,
            "y": (quad[1] + quad[5]) / 2,
            "button": "left",
            "clickCount": 1,
        }
        for kind in ("mousePressed", "mouseReleased"):
            self.connection.call("Input.dispatchMouseEvent", {"type": kind, **position})

    def wait(self):
        def done():
            self.pump()
            return self.status is not None

        eventually(done)
        return self.status

    def cleanup_verified(self):
        assert not self.profile.exists(), "Temporary browser profile remains"
        opener = build_opener(ProxyHandler({}))
        try:
            opener.open(
                f"http://127.0.0.1:{self.port}/json/list",
                timeout=CONTROL_CONNECT_TIMEOUT_SECONDS,
            )
        except OSError:
            return
        raise AssertionError("Owned browser listener remains open")

    def stop(self):
        if self.status is None:
            os.kill(self.pid, signal.SIGTERM)
            self.wait()
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
        os.close(self.terminal)
