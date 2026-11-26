"""The login client's files are deterministic and contain only the website origin."""

import json
import subprocess
import sys

import pytest

from website_client.cli import main
from website_client.browser import BrowserError
from website_client.login_generation import generate_login_client
from website_client.page_link import PageLink


def test_repeat_generation_is_identical_and_excludes_login_query(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        generate_login_client(
            "https://app.example.test/sign-in?token=PRIVATE_TEST_VALUE", directory
        )

    first_files = project_files(first)
    assert first_files == project_files(second)
    assert b"https://app.example.test" in first_files["login_client/_login.py"]
    assert all(b"PRIVATE_TEST_VALUE" not in data for data in first_files.values())


def project_files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize(
    ("website", "shell_website"),
    [
        ("https://portal.example.test:8443", "https://portal.example.test:8443"),
        ("https://[::1]:8443", "'https://[::1]:8443'"),
    ],
)
def test_linked_page_readme_has_one_concrete_request_and_isolated_install(
    tmp_path, website, shell_website
):
    output = tmp_path / "linked-client"
    generate_login_client(
        website + "/private-sign-in?token=PRIVATE_TEST_VALUE",
        output,
        link=PageLink("/team/guide"),
    )

    readme = (output / "README.md").read_text()
    [operation] = json.loads((output / "generation.json").read_text())["operations"]
    assert "`GET /team/guide`" in readme
    command = (
        f'.venv/bin/login-client {operation} '
        f'--url {shell_website} --login '
        '--login-url "$login_url" --output page.json'
    )
    assert command in readme
    assert readme.count(f"{operation} --url") == 1
    assert website + "/team/guide" not in readme
    assert 'read -r login_url' in readme
    assert (
        'python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt'
        in readme
    )
    assert "Python 3.11 or newer" in readme
    assert "JSON string" in readme and "raw HTML" in readme
    assert "HTML source can differ from browser-rendered content" in readme
    assert "poetry run" not in readme and "OPERATION" not in readme
    assert "from login_client" not in readme
    assert all(
        b"PRIVATE_TEST_VALUE" not in data and b"private-sign-in" not in data
        for data in project_files(output).values()
    )
    definition = json.loads((output / "openapi.json").read_text())
    assert definition["paths"]["/team/guide"]["get"]["operationId"] == "read_linked_page"


def test_login_generates_an_openapi_package_with_no_endpoints(tmp_path):
    output = tmp_path / "client"
    generate_login_client("https://example.test", output)

    definition = json.loads((output / "openapi.json").read_text())
    report = json.loads((output / "generation.json").read_text())
    assert definition["paths"] == {}
    assert report["generator"] == "openapi-python-client"
    assert report["operations"] == []
    assert "zero endpoints" in (output / "README.md").read_text()

    # Import the upstream SDK from its published directory in a new interpreter.
    subprocess.run(
        [sys.executable, "-c", "from login_client import Client; Client(base_url='https://example.test')"],
        cwd=output,
        check=True,
    )


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [(KeyboardInterrupt(), 130), (BrowserError("Browser unavailable"), 1)],
    ids=["cancelled", "browser-unavailable"],
)
def test_interrupted_login_leaves_runnable_client(
    tmp_path, monkeypatch, error, expected_exit
):
    output = tmp_path / "retry-client"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def fail_to_open(*args):
        assert (output / "README.md").is_file()
        raise error

    monkeypatch.setattr("website_client.cli.BrowserSession", fail_to_open)
    assert (
        main(["login", "--url", "https://app.example.test", "--output", str(output)])
        == expected_exit
    )
    subprocess.run([sys.executable, str(output / "client.py"), "--help"], check=True)


def test_invalid_url_still_creates_client_in_default_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["login", "--url", "bad-url-PRIVATE_TEST_VALUE"]) == 1
    [directory] = (tmp_path / "website-clients").iterdir()
    assert (directory / "README.md").is_file()
    assert all(b"PRIVATE_TEST_VALUE" not in data for data in project_files(directory).values())
    subprocess.run([sys.executable, str(directory / "client.py"), "--help"], check=True)
