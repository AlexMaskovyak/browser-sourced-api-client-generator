"""Generated source instructions and tests must also work against installed wheels."""

import json
from pathlib import Path
import subprocess
import sys

from website_client.autonomous import run
from website_client.generation import generate
from support.packaging import HELPERS, installed_target, installed_cli, supplied_bundle

FIXTURES = Path(__file__).parents[1] / "fixtures"


def run_installed_checks(project, target, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(HELPERS / "run_installed.py"),
            str(target),
            "module",
            "pytest",
            "-q",
            str(project / "tests"),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout


def test_sdk_wheel_ships_notices_and_useful_offline_contract_tests(tmp_path):
    project = tmp_path / "sdk"
    generate(
        project,
        "contract-sdk",
        openapi=FIXTURES / "openapi.json",
        runtime_config={"endpoint": "https://fixture.invalid"},
    )
    result = subprocess.run(
        [
            sys.executable,
            str(HELPERS / "build_wheel.py"),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    wheel = next((project / "dist").glob("*.whl"))
    target = tmp_path / "installed"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(target),
            str(wheel),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    run_installed_checks(project, target, tmp_path)
    assert (target / "contract_sdk/_notices/LICENSE.openapi-python-client").is_file()
    assert (project / "LICENSE").is_file()
    assert (project / "NOTICE.md").is_file()


def test_workflow_input_inventory_help_notices_and_installed_checks(tmp_path):
    source = supplied_bundle(tmp_path, "https://fixture.invalid")
    value = json.loads(source.read_text())
    workflow = value["workflows"][0]
    workflow["inputs"] = {
        "type": "object",
        "properties": {
            "ticketName": {"type": "string"},
            "optionalFlag": {"type": "boolean"},
        },
        "required": ["ticketName"],
    }
    workflow["steps"][1]["requestBody"]["payload"] = "$inputs.receipt"
    source.write_text(json.dumps(value))
    output = tmp_path / "output"
    report = run(source, output, name="inventory")
    project = output / report["workflowPackage"]["directory"]
    inputs = json.loads((project / "inventory_workflows/_inputs.json").read_text())[
        "upload"
    ]
    indexed = {item["name"]: item for item in inputs}

    assert indexed["receipt"] == {
        "name": "receipt",
        "required": True,
        "type": "unknown (no declared type)",
        "binary": True,
    }
    assert indexed["ticketName"]["required"]
    assert indexed["ticketName"]["type"] == "string"
    assert not indexed["optionalFlag"]["required"]
    assert indexed["optionalFlag"]["type"] == "boolean"
    readme = (project / "README.md").read_text()
    assert "--input inputs.json --file receipt=payload.bin" in readme
    assert "--file file=payload.bin" not in readme
    assert "playwright install" not in readme
    target = installed_target(project, tmp_path)
    result = installed_cli(target, "inventory-workflows", ["--help"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (
        "receipt: required; unknown (no declared type); binary file: --file receipt=PATH"
        in result.stdout
    )
    assert "optionalFlag: optional; boolean" in result.stdout
    assert "ticketName: required; string" in result.stdout
    run_installed_checks(project, target, tmp_path)
    assert (target / "inventory_workflows/_notices/NOTICE.arazzo-runner").is_file()
