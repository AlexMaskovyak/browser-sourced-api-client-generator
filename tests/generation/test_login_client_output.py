"""The login client's files are deterministic and contain only the website origin."""

import subprocess
import sys

import pytest

from website_client.cli import main
from website_client.browser import BrowserError
from website_client.login_generation import generate_login_client


def test_repeat_generation_is_identical_and_excludes_login_query(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        generate_login_client(
            "https://app.example.test/sign-in?token=PRIVATE_TEST_VALUE", directory
        )

    first_files = {p.name: p.read_bytes() for p in first.iterdir()}
    assert first_files == {p.name: p.read_bytes() for p in second.iterdir()}
    assert b"https://app.example.test" in first_files["client.py"]
    assert all(b"PRIVATE_TEST_VALUE" not in data for data in first_files.values())


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
    source = (directory / "client.py").read_text()
    assert "PRIVATE_TEST_VALUE" not in source
    compile(source, "client.py", "exec")
