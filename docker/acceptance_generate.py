"""Exercise the installed public command; browser driver stops after initial login."""

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile
from urllib.request import urlopen

from terminal_command import Command, eventually


OUTPUT = Path("/artifacts")
COMMAND_RESULTS: list[dict] = []


def execute(
    arguments: list[str], *, expected_exit: int = 0
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300
    )
    COMMAND_RESULTS.append({"command": arguments, "exit_code": result.returncode})
    assert result.returncode == expected_exit, result.stdout + result.stderr
    return result


@dataclass(frozen=True)
class Fixture:
    url: str = "http://127.0.0.1:8765/app"

    def __post_init__(self) -> None:
        if self.url != "http://127.0.0.1:8765/app":
            raise ValueError("The acceptance fixture must remain on loopback.")


def verify_generator_console(site: Fixture, state: dict) -> None:
    command = Command(
        site,
        args=[
            "run",
            site.url,
            "--name",
            "neutral-cli",
            "--output",
            str(OUTPUT / "cli-generated"),
            "--browser",
            "chromium",
            "--browser-path",
            "/work/docker/test-chromium.sh",
            "--explore-seconds",
            "8",
            "--max-pages",
            "3",
            "--max-actions",
            "6",
        ],
    )
    try:
        eventually(
            lambda: (command.pump(), "Press Enter to finish login" in command.output)[
                1
            ],
            timeout_seconds=60,
        )
        command.connect()
        command.fill('input[name="username"]', state["secrets"]["username"])
        command.fill('input[name="password"]', state["secrets"]["password"])
        command.click()
        command.fill('input[name="code"]', state["secrets"]["code"])
        command.click()
        eventually(lambda: command.node("h1"))
        os.write(command.terminal, b"\n\x04")
        # The queued terminal EOF is the final input. The API proof below also
        # closes the stdin descriptor itself, rather than claiming this does.
        eventually(
            lambda: (command.pump(), command.status is not None)[1], timeout_seconds=300
        )
        assert command.status == 0, command.output
        command.cleanup_verified()
        (OUTPUT / "generator-console.log").write_text(command.output)
    finally:
        command.stop()


def initial_login_and_generate(site: Fixture, state: dict, output: Path) -> None:
    from website_client.autonomous import run

    def login(session) -> None:
        # The callback acts only as the person completing initial browser login.
        page = session._page
        page.locator('input[name="username"]').fill(state["secrets"]["username"])
        page.locator('input[name="password"]').fill(state["secrets"]["password"])
        page.locator("button").click()
        page.locator('input[name="code"]').fill(state["secrets"]["code"])
        page.locator("button").click()
        page.locator("h1").wait_for()
        sys.stdin.close()
        os.close(0)

    result = run(
        site.url,
        output,
        name="neutral-client",
        browser="chromium",
        browser_path="/work/docker/test-chromium.sh",
        login_handler=login,
        headless=True,
        duration_seconds=8,
        max_pages=3,
        max_actions=6,
    )
    (OUTPUT / "generation.log").write_text(json.dumps(result, indent=2))
    assert not list(Path("/tmp").glob("playwright_chromiumdev_profile-*")), (
        "Browser profile remains after public run returns"
    )


def verify_unsupported_assets() -> dict:
    """The actual installed CLI must leave runnable output even on failure."""
    from website_client.autonomous import run

    empty_output = OUTPUT / "empty-site"
    result = run(
        # Use a different origin so this intentionally unauthenticated run does
        # not replace the successful fixture's private login profile.
        "http://127.0.0.1:8766/empty",
        empty_output,
        browser="chromium",
        browser_path="/work/docker/test-chromium.sh",
        login_handler=lambda _session: None,
        headless=True,
        duration_seconds=3,
        max_pages=1,
        max_actions=1,
    )
    assert result["coverage"] == "unsupported" and not result["services"]
    invalid = OUTPUT / "invalid.yaml"
    invalid.write_text("openapi: [ invalid")
    invalid_output = OUTPUT / "invalid-input"
    execute(
        ["website-client", "run", str(invalid), "--output", str(invalid_output)],
        expected_exit=1,
    )
    for output in (empty_output, invalid_output):
        assert (output / "README.md").is_file()
        assert (output / "coverage.json").is_file()
        execute([sys.executable, "-I", str(output / "client.py"), "--help"])
        result = execute(
            [sys.executable, "-I", str(output / "client.py")], expected_exit=1
        )
        assert json.loads(result.stdout)["requestsSent"] == 0
    return {"empty_site": "passed", "invalid_input": "passed", "stub_exit_code": 1}


def regenerate(output: Path, after: dict) -> list[Path]:
    execute(
        [
            "website-client",
            "run",
            str(output / "manifest.json"),
            "--name",
            "neutral-client",
            "--output",
            str(OUTPUT / "regenerated"),
        ]
    )
    assert (
        json.load(urlopen("http://127.0.0.1:8765/__state"))["requests"]
        == after["requests"]
    ), "saved regeneration accessed target"
    projects = sorted(
        path
        for parent in (output / "services", output / "workflows")
        for path in parent.iterdir()
        if path.is_dir() and (path / "pyproject.toml").is_file()
    )
    assert len(projects) >= 3, list(projects)
    return projects


def compare_sources(output: Path, projects: list[Path]) -> list[str]:
    deterministic = []
    for project in projects:
        counterpart = OUTPUT / "regenerated" / project.relative_to(output)
        assert counterpart.is_dir(), counterpart
        for file in project.rglob("*"):
            if (
                not file.is_file()
                or file.suffix
                not in {".py", ".toml", ".json", ".graphql", ".yaml", ".yml"}
                or file.name == "generation.json"
                or "__pycache__" in file.parts
            ):
                continue
            other = counterpart / file.relative_to(project)
            assert other.is_file() and file.read_bytes() == other.read_bytes(), str(
                file
            )
            deterministic.append(str(file.relative_to(output)))
    assert deterministic
    return deterministic


def build_projects(projects: list[Path]) -> tuple[list[dict], Path]:
    wheelhouse = OUTPUT / "wheels"
    wheelhouse.mkdir()
    catalog = []
    for project in projects:
        assert (project / "README.md").is_file(), project
        assert list(project.rglob("*.py")), project
        execute(["poetry", "--directory", str(project), "build", "--format", "wheel"])
        data = tomllib.loads((project / "pyproject.toml").read_text())
        scripts = data.get("project", {}).get(
            "scripts", data.get("tool", {}).get("poetry", {}).get("scripts", {})
        )
        console = next(iter(scripts))
        package = console.replace("-", "_")
        catalog.append(
            {
                "project": str(project.relative_to(OUTPUT)),
                "package": package,
                "console": console,
            }
        )
        for wheel in project.rglob("*.whl"):
            shutil.copyfile(wheel, wheelhouse / wheel.name)
    for wheel in Path("/opt/client-wheels").glob("*.whl"):
        shutil.copyfile(wheel, wheelhouse / wheel.name)
    shutil.copyfile(
        "/work/tests/fixtures/native_upload/payload.bin", OUTPUT / "payload.bin"
    )
    (OUTPUT / "catalog.json").write_text(json.dumps(catalog, indent=2))
    return catalog, wheelhouse


def verify_private_outputs(state: dict) -> None:
    secrets = list(state["secrets"].values()) + [state["cookie"]]
    for file in OUTPUT.rglob("*"):
        if file.is_file():
            value = file.read_bytes()
            assert all(secret.encode() not in value for secret in secrets), str(file)
            assert "profile" not in file.name.lower() or file.suffix == ".py", str(file)
            if file.suffix == ".whl":
                with zipfile.ZipFile(file) as archive:
                    for member in archive.namelist():
                        assert all(
                            secret.encode() not in archive.read(member)
                            for secret in secrets
                        ), (str(file), member)
    profile = Path(os.environ["WEBSITE_CLIENT_PROFILE_DIR"])
    assert profile.stat().st_mode & 0o777 == 0o700
    for file in profile.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600, file


def verify_generator_metadata() -> None:
    assert importlib.metadata.version("arazzo-runner") == "0.9.6+websiteclient.1"
    wheel = next(Path("/work/dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(
            next(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            )
        ).decode()
        assert "arazzo-runner (==0.9.6+websiteclient.1)" in metadata
        assert "file://" not in metadata
        assert "/work/" not in metadata


def main() -> None:
    assert os.getuid() == 1000
    verify_generator_metadata()
    # Exercise the installed generator with independent website/CDN/API hosts,
    # denied introspection, scoped authentication and an actual generated call.
    execute(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "/work/tests/test_cdn_discovery.py::test_cdn_driven_app_generates_callable_client_without_admitting_cdn[True]",
        ]
    )
    state = json.load(urlopen("http://127.0.0.1:8765/__state"))
    output = OUTPUT / "generated"
    verify_generator_console(Fixture(), state)
    initial_login_and_generate(Fixture(), state, output)
    after = json.load(urlopen("http://127.0.0.1:8765/__state"))
    assert not [entry for entry in after["journal"] if entry["path"] != "/graphql"], (
        after["journal"]
    )
    assert after["logins"] == 2
    # Each login and guarded reload must load the CDN script. GraphQL cannot be
    # discovered or packaged without it; the runtime phase also calls that client.
    assert after["cdn_assets"] >= 4, after["cdn_assets"]
    projects = regenerate(output, after)
    deterministic = compare_sources(output, projects)
    catalog, wheelhouse = build_projects(projects)
    unsupported_assets = verify_unsupported_assets()
    verify_private_outputs(state)
    hashes = {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in wheelhouse.glob("*.whl")
    }
    (OUTPUT / "generation-summary.json").write_text(
        json.dumps(
            {
                "projects": catalog,
                "commands": COMMAND_RESULTS,
                "versions": {
                    name: importlib.metadata.version(name)
                    for name in (
                        "browser-sourced-api-client-generator",
                        "arazzo-runner",
                        "openapi-python-client",
                        "ariadne-codegen",
                        "playwright",
                    )
                },
                "node_version": execute(["node", "--version"]).stdout.strip(),
                "har2sdk_version": "0.6.15",
                "stdin_physically_closed_after_login": True,
                "ordinary_generator_console": True,
                "cdn_dependent_graphql": True,
                "separate_graphql_host_without_schema": "passed",
                "unsupported_client_assets": unsupported_assets,
                "wheel_sha256": hashes,
                "login_count": after["logins"],
                "deterministic_files": deterministic,
                "volatile_exclusions": [
                    "generation.json lineage/report metadata",
                    "build/dist archives",
                    "__pycache__",
                ],
                "tls": "HTTP loopback fixture; TLS trust is covered only by compatibility checks",
            },
            indent=2,
        )
    )
    print(
        "PASS autonomous initial-login-only generation, offline regeneration, wheel build and privacy"
    )


if __name__ == "__main__":
    main()
