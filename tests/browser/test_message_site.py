"""Prove observed contracts and fresh installed calls against an independent site."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from uuid import uuid4

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    not Path("/work/docker/test-chromium.sh").is_file(),
    reason="Requires the installed-wheel Chromium test container",
)
BROWSER_PATH = "/work/docker/test-chromium.sh"
BROWSER = ["--browser", "chromium", "--browser-path", BROWSER_PATH]


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend("/work/docker")
    from message_site import MessageSite
    from test_login import assert_container_environment

    assert_container_environment()
    monkeypatch.setenv("HOME", str(tmp_path))
    message_site = MessageSite(tmp_path)
    try:
        yield message_site
    finally:
        message_site.close()


@pytest.fixture
def http_client(site, tmp_path):
    trust = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    with httpx.Client(base_url=site.url.rsplit("/", 1)[0], verify=trust) as client:
        yield client


@pytest.fixture
def signed_in_client(site, http_client):
    """Fixture controls authenticate separately from all browser acceptance work."""
    password_response = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password_response.status_code == 303
    assert password_response.headers["location"] == "/mfa"
    mfa_response = http_client.post("/mfa", data={"code": site.secrets["code"]})
    assert mfa_response.status_code == 303
    assert mfa_response.headers["location"] == "/app"
    return http_client


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
        eventually(command.ready)
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


@pytest.fixture
def generated_application(site, tmp_path):
    project = tmp_path / "message-client"
    profile = tmp_path / "private-runtime-profile"
    arguments = [
        "login", "--url", site.url, "--output", str(project), "--profile", str(profile),
    ]
    generated = run_browser_sign_in(
        site, arguments, environment={"WEBSITE_CLIENT_BROWSER_PATH": BROWSER_PATH},
    )
    assert generated["clientGenerated"] is True
    return GeneratedApplication(project, profile)


def resolve_local_schema(document, schema):
    """Allow normal OpenAPI local references without assuming generator internals."""
    while "$ref" in schema:
        reference = schema["$ref"]
        assert reference.startswith("#/"), reference
        schema = document
        for part in reference[2:].split("/"):
            schema = schema[part.replace("~1", "/").replace("~0", "~")]
    return schema


def test_capture_infers_the_observed_message_argument_and_response(generated_application):
    """Milestone: the generator discovers the operation without endpoint hints."""
    specification = json.loads((generated_application.project / "openapi.json").read_text())
    message_path = specification["paths"].get("/message", {})
    assert "get" in message_path, (
        "Browser and backend verified GET /message, but generated OpenAPI has no "
        "GET /message operation. Login success is not operation discovery."
    )
    operation = message_path["get"]
    parameters = message_path.get("parameters", []) + operation.get("parameters", [])
    query_parameters = [resolve_local_schema(specification, parameter) for parameter in parameters]
    message_id_parameter = next(
        (parameter for parameter in query_parameters
         if parameter.get("in") == "query" and parameter.get("name") == "message_id"),
        None,
    )
    assert message_id_parameter is not None, "Observed message_id argument was lost."
    assert resolve_local_schema(specification, message_id_parameter["schema"])["type"] == "string"

    response = resolve_local_schema(specification, operation["responses"]["200"])
    response_schema = resolve_local_schema(
        specification, response["content"]["application/json"]["schema"],
    )
    assert response_schema["type"] == "object"
    for field in ("id", "subject"):
        field_schema = resolve_local_schema(specification, response_schema["properties"][field])
        assert field_schema["type"] == "string", f"Observed {field} response field was lost."


def test_generator_and_installed_client_complete_separate_browser_sign_ins(
    site, tmp_path, generated_application,
):
    """Keep the existing real standalone login proof, separate from API execution."""
    from test_login import install_generated_wheel

    environment = tmp_path / "installed-login"
    install_generated_wheel(generated_application.project, environment)
    signed_in = run_browser_sign_in(site, [
        "login", "--url", site.url, *BROWSER,
    ], executable=str(environment / "bin/login-client"))
    assert signed_in["authentication"] == "user-confirmed"
    assert signed_in["sessionClosed"] is True


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


def test_installed_python_and_cli_each_return_independently_changed_server_data(
    site, tmp_path, generated_application,
):
    """Milestone: both public interfaces perform fresh, authenticated backend reads."""
    application = install_application(generated_application, tmp_path)
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        # A new unpredictable value for EACH call rejects canned or cached output.
        expected_subject = f"Fresh {interface} subject {uuid4()}"
        site.messages["m2"]["subject"] = expected_subject
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "m2")
        assert result.returncode == 0, result.stdout + result.stderr
        actual_subject = (
            result.stdout.strip() if interface == "python" else
            json.loads(result.stdout)["subject"]
        )
        assert actual_subject == expected_subject
        assert_single_message_request(site, requests_before, "m2", 200, True)
        assert site.counts == login_counts, "Installed read repeated browser login or dashboard navigation."


def test_installed_python_and_cli_report_missing_message_without_retry(
    site, tmp_path, generated_application,
):
    """Milestone: an actual backend 404 is an error, not an empty successful model."""
    application = install_application(generated_application, tmp_path)
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "unknown")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "404" in result.stdout + result.stderr
        assert_single_message_request(site, requests_before, "unknown", 404, True)
        assert site.counts == login_counts


def test_installed_python_and_cli_report_expired_auth_without_repeating_login(
    site, tmp_path, generated_application,
):
    """Milestone: expiry reports authentication_required with stdin already closed."""
    application = install_application(generated_application, tmp_path)
    site.sessions_active = False
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "m2")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "authentication_required" in result.stdout + result.stderr
        assert_single_message_request(site, requests_before, "m2", 401, False)
        assert site.counts == login_counts, "Expired authorization triggered a new sign-in."


def test_fixture_denies_messages_before_login_after_password_and_after_wrong_mfa(site, http_client):
    unauthenticated_response = http_client.get("/message?message_id=m1")
    assert unauthenticated_response.status_code == 401
    assert site.message_requests[-1]["authenticated"] is False

    password_response = http_client.post("/login", data={
        key: site.secrets[key] for key in ("username", "password")
    })
    assert password_response.status_code == 303
    assert password_response.headers["location"] == "/mfa"
    assert http_client.get("/message?message_id=m1").status_code == 401

    rejected_response = http_client.post("/mfa", data={"code": "incorrect"})
    assert rejected_response.status_code == 401
    assert http_client.get("/message?message_id=m1").status_code == 401
    assert site.counts == {"password": 1, "mfa": 0, "app": 0}


def test_fixture_controls_and_schema_absence(site, signed_in_client):
    client = signed_in_client
    original_first_message = dict(site.messages["m1"])
    site.messages["m2"]["subject"] = "Updated second fixture message"
    assert client.get("/message?message_id=m2").json() == site.messages["m2"]
    assert client.get("/message?message_id=m1").json() == original_first_message
    assert client.get("/message?message_id=unknown").status_code == 404
    for path in ("/openapi.json", "/swagger.json", "/api-docs", "/graphql", "/unknown"):
        missing_response = client.get(path)
        assert missing_response.status_code == 404
        assert missing_response.json() == {"error": "Not found"}
    assert client.post("/graphql", json={"query": "{ __typename }"}).status_code == 404
    assert client.post("/items", json={"name": "Unused"}).status_code == 404
    site.sessions_active = False
    assert client.get("/message?message_id=m2").status_code == 401
