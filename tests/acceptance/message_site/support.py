"""Drive real browser login and independent installed consumers without API hints."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess


BROWSER_PATH = "/work/docker/test-chromium.sh"
BROWSER = ["--browser", "chromium", "--browser-path", BROWSER_PATH]


def observe_natural_message(command, site):
    """Observe the page's own fetch and rendering without injecting a request."""
    from terminal_command import eventually
    from test_login import complete_login

    page = command.connection.page
    origin = site.url.rsplit("/", 1)[0]
    expected_message = dict(site.messages["m1"])
    with page.expect_response(site.url) as dashboard_response, page.expect_response(
        origin + "/message?message_id=m1"
    ) as message_response:
        complete_login(command, site)

    assert dashboard_response.value.headers["content-type"] == "text/html"
    assert expected_message["subject"] not in dashboard_response.value.text()
    response = message_response.value
    assert response.request.method == "GET"
    assert response.status == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == expected_message
    eventually(lambda: page.locator("#subject").inner_text() == expected_message["subject"])
    return {
        "method": "GET", "path": "/message?message_id=m1",
        "authenticated": True, "status": 200, "body": expected_message,
    }



def run_browser_sign_in(site, arguments, *, executable=None, environment=None):
    from terminal_command import Command, eventually
    from test_login import assert_no_credentials

    counts_before = site.counts.copy()
    requests_before = len(site.message_requests)
    command = Command(site, args=arguments, executable=executable, env=environment)
    try:
        try:
            eventually(command.ready)
        except AssertionError as error:
            raise AssertionError("Browser login did not become ready: " + command.output) from error
        command.connect()
        expected_natural_request = observe_natural_message(command, site)
        os.write(command.terminal, b"\n")
        assert command.wait() == 0, command.output
        # This checks the original browser listener is gone before any consumer runs.
        command.cleanup_verified()
        assert_no_credentials(command.output, site)
        assert site.counts == {step: count + 1 for step, count in counts_before.items()}
        assert site.message_requests[requests_before:] == [expected_natural_request], (
            "Generation or login replayed the dashboard request."
        )
        return json.loads(command.output.splitlines()[-1])
    finally:
        command.stop()



@dataclass
class GeneratedApplication:
    project: Path
    profile: Path



def resolve_local_schema(document, schema):
    """Allow normal OpenAPI local references without assuming generator internals."""
    while "$ref" in schema:
        reference = schema["$ref"]
        assert reference.startswith("#/"), reference
        schema = document
        for part in reference[2:].split("/"):
            schema = schema[part.replace("~1", "/").replace("~0", "~")]
    return schema



@dataclass
class InstalledApplication:
    python: Path
    executable: Path
    consumer: Path
    working_directory: Path
    environment: dict



def install_application(generated_application, tmp_path):
    from test_login import install_generated_wheel

    environment_directory = tmp_path / "installed-application"
    install_generated_wheel(generated_application.project, environment_directory)
    clean_directory = tmp_path / "independent-consumer"
    clean_directory.mkdir()
    consumer = clean_directory / "consume_message.py"
    shutil.copyfile(Path(__file__).with_name("consume_message.py"), consumer)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("WEBSITE_CLIENT_")
    }
    environment.update({
        "PATH": str(environment_directory / "bin") + ":/usr/bin:/bin",
        "WEBSITE_CLIENT_PROFILE": str(generated_application.profile),
        # API execution must not start another browser or prompt for credentials.
        "WEBSITE_CLIENT_BROWSER_PATH": str(tmp_path / "no-browser-executable"),
        "NODE_EXTRA_CA_CERTS": str(tmp_path / "ca.crt"),
    })
    return InstalledApplication(
        environment_directory / "bin/python", environment_directory / "bin/login-client",
        consumer, clean_directory, environment,
    )



def run_installed_consumer(application, interface, message_id):
    if interface == "python":
        arguments = [str(application.python), "-I", str(application.consumer), message_id]
    else:
        arguments = [str(application.executable), "get-message", "--message-id", message_id]
    return subprocess.run(
        arguments, cwd=application.working_directory, env=application.environment,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )



def assert_single_message_request(site, requests_before, message_id, status, authenticated):
    expected_body = (
        dict(site.messages[message_id]) if status == 200 else
        {"error": "Unknown message"} if status == 404 else {"error": "Sign-in required"}
    )
    assert site.message_requests[requests_before:] == [{
        "method": "GET", "path": f"/message?message_id={message_id}",
        "authenticated": authenticated, "status": status, "body": expected_body,
    }], "Installed call must make exactly one backend request with the caller's argument."
