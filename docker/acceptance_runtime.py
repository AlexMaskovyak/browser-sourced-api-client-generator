"""Independent assertions against installed generated APIs with closed stdin."""

import hashlib
import importlib
import importlib.metadata
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
import zipfile

ROOT = Path("/acceptance")
ARTIFACTS = Path("/artifacts")
BASE = "http://127.0.0.1:8765"
COMMAND_RESULTS: list[dict] = []
DIGEST = "e6b9f47e1fdba07810e19268cb55a8496558fbcc38f5b75f17bdf66300ef90e3"


def state() -> dict:
    return json.load(urlopen(BASE + "/__state"))


def control(action: str) -> None:
    urlopen(Request(BASE + "/__" + action, data=b"")).close()


def program(arguments: list[str], expected: int = 0) -> subprocess.CompletedProcess:
    result = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=60
    )
    assert result.returncode == expected, {
        "command": arguments,
        "code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    for secret in list(state()["secrets"].values()) + [state()["cookie"]]:
        assert secret not in result.stdout + result.stderr
    COMMAND_RESULTS.append(
        {"command": arguments, "exit_code": result.returncode, "stdout": result.stdout}
    )
    return result


def module(package: str, name: str = ""):
    return importlib.import_module(package + ("." + name if name else ""))


def upload_python(rest: str, gql: str, payload: bytes) -> None:
    file_type = module(rest, "types").File
    body_type = module(rest, "models.upload_file_body").UploadFileBody
    with module(rest).client_context() as client:
        receipt = module(rest, "api.default.upload_file").sync(
            client=client,
            body=body_type(
                file=file_type(
                    payload=BytesIO(payload),
                    file_name="fixture.bin",
                    mime_type="application/octet-stream",
                ),
                note="rest",
            ),
        )
        assert receipt.sha256 == DIGEST
        assert (
            module(rest, "api.default.put_binary")
            .sync(
                client=client,
                body=file_type(
                    payload=BytesIO(payload),
                    file_name="fixture.bin",
                    mime_type="application/octet-stream",
                ),
            )
            .size
            == 274
        )
    Upload = module(gql, "base_model").Upload
    UploadInput = module(gql, "input_types").UploadInput
    Mutation = module(gql, "custom_mutations").Mutation
    fields = module(gql, "custom_fields").ReceiptFields
    selected = (fields.sha_256, fields.size, fields.note)
    with module(gql).client_context(allow_writes=True) as client:
        result = client.mutation(
            Mutation.upload(
                file=Upload(
                    filename="fixture.bin",
                    content=BytesIO(payload),
                    content_type="application/octet-stream",
                ),
                note="graphql",
            ).fields(*selected),
            operation_name="UploadFile",
        )
        assert result["upload"]["sha256"] == DIGEST, result
        result = client.mutation(
            Mutation.submit(
                input=UploadInput(
                    files=[
                        Upload(
                            filename="fixture.bin",
                            content=BytesIO(payload),
                            content_type="application/octet-stream",
                        )
                        for _ in range(2)
                    ],
                    note="graphql",
                )
            ).fields(*selected),
            operation_name="NestedUpload",
        )
        assert result["submit"]["size"] == 274, result


def verify_environment() -> None:
    assert os.getuid() == 1000
    assert not os.environ.get("PYTHONPATH")
    for name in ("website_client", "ariadne_codegen", "openapi_python_client"):
        assert importlib.util.find_spec(name) is None, name
    for name in ("node", "npm", "chromium", "chrome", "google-chrome"):
        assert shutil.which(name) is None, name
    assert not Path("/work").exists()
    assert importlib.metadata.version("arazzo-runner") == "0.9.6+websiteclient.1"


def upload_console(rest: dict, gql: dict, before: int) -> None:
    descriptor = {
        "path": str(ROOT / "payload.bin"),
        "filename": "fixture.bin",
        "content_type": "application/octet-stream",
    }
    cases = [
        (rest, "upload_file", {"body": {"file": descriptor, "note": "rest"}}),
        (rest, "put_binary", {"body": descriptor}),
        (gql, "mutation_upload", {"file": descriptor, "note": "graphql"}),
        (
            gql,
            "mutation_submit",
            {"input": {"files": [descriptor, descriptor], "note": "graphql"}},
        ),
    ]
    for item, operation, inputs in cases:
        path = Path("/tmp") / (operation + ".json")
        path.write_text(json.dumps(inputs))
        result = program(
            [
                item["console"],
                operation,
                "--input",
                str(path),
                "--confirm-write",
                operation,
            ]
        )
        assert DIGEST in result.stdout, result.stdout
    assert len(state()["journal"]) == before + 4
    batch = state()["journal"][before:]
    assert [item["path"] for item in batch] == [
        "/upload",
        "/binary",
        "/graphql",
        "/graphql",
    ]
    assert [len(item["files"]) for item in batch] == [1, 1, 1, 2]


def verify_workflows(workflow: dict, rest: dict, payload: bytes) -> int:
    workflows = module(workflow["package"])
    result = workflows.execute_workflow("upload", {"file": payload})
    assert result["status"] == "success", result
    program(
        [workflow["console"], "upload", "--file", "file=" + str(ROOT / "payload.bin")]
    )
    count = len(state()["journal"])
    control("guard_fail")
    guarded = workflows.execute_workflow("upload", {"file": payload})
    assert guarded["reason"] == "missing_required_output", guarded
    assert len(state()["journal"]) == count + 1
    assert state()["journal"][-1]["path"] == "/prepare"
    for name, expected in [
        ("null", b"null"),
        ("omitted", b""),
        ("nested", b'{"value": null}'),
    ]:
        result = workflows.execute_workflow(name, {})
        assert result["status"] == "success", result
        assert bytes.fromhex(state()["journal"][-1]["body_hex"]) == expected
        program([workflow["console"], name])
        assert bytes.fromhex(state()["journal"][-1]["body_hex"]) == expected
    count = len(state()["journal"])
    result = workflows.execute_workflow("disallowed", {"value": None})
    assert result["reason"] == "invalid_request", result
    assert len(state()["journal"]) == count
    invalid_input = Path("/tmp/disallowed.json")
    invalid_input.write_text(json.dumps({"value": None}))
    program(
        [workflow["console"], "disallowed", "--input", str(invalid_input)], expected=2
    )
    assert len(state()["journal"]) == count
    program([rest["console"], "excluded_form"], expected=2)
    assert len(state()["journal"]) == count
    return count


def verify_authentication(rest: dict, count: int) -> None:
    control("rotate")
    program(
        [
            rest["console"],
            "put_binary",
            "--input",
            "/tmp/put_binary.json",
            "--confirm-write",
            "put_binary",
        ]
    )
    program(
        [
            rest["console"],
            "put_binary",
            "--input",
            "/tmp/put_binary.json",
            "--confirm-write",
            "put_binary",
        ]
    )
    assert len(state()["journal"]) == count + 2
    control("expire")
    attempts = state()["api_attempts"]
    expired = program(
        [
            rest["console"],
            "put_binary",
            "--input",
            "/tmp/put_binary.json",
            "--confirm-write",
            "put_binary",
        ],
        expected=1,
    )
    assert json.loads(expired.stderr)["reason"] == "authentication_required"
    assert state()["api_attempts"] == attempts + 1, "Expired request was retried"
    assert len(state()["journal"]) == count + 2


def verify_journal() -> None:
    for entry in state()["journal"]:
        for metadata in entry.get("multipart", []):
            assert metadata == {
                "filename": "fixture.bin",
                "content_type": "application/octet-stream",
            }, metadata
        for file in entry.get("files", []):
            assert file == {"sha256": DIGEST, "size": 274}, entry


def verify_distribution(catalog: list[dict]) -> list[str]:
    generated_tests = []
    for item in catalog:
        assert str(module(item["package"]).__file__).startswith(
            "/usr/local/lib/python3.12/site-packages/"
        ), module(item["package"]).__file__
        project = ARTIFACTS / item["project"]
        assert list(project.glob("*NOTICE*")) or list(project.rglob("THIRD_PARTY*")), (
            str(project)
        )
        tests = project / "tests"
        if tests.exists():
            program(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    str(tests),
                ]
            )
            generated_tests.append(item["console"])
    assert len(generated_tests) == len(catalog), generated_tests
    for wheel in (ARTIFACTS / "wheels").glob("*workflows*.whl"):
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
    profile = Path(os.environ["WEBSITE_CLIENT_PROFILE_DIR"])
    assert profile.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in profile.iterdir())
    return generated_tests


def verify_final_privacy() -> None:
    current = state()
    secrets = list(current["secrets"].values()) + [current["cookie"]]
    for path in ARTIFACTS.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        value = path.read_bytes()
        assert all(secret.encode() not in value for secret in secrets), str(path)
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    assert all(
                        secret.encode() not in archive.read(member)
                        for secret in secrets
                    ), (str(path), member)


def main() -> None:
    verify_environment()
    catalog = json.loads((ROOT / "catalog.json").read_text())
    rest = next(
        item
        for item in catalog
        if importlib.util.find_spec(item["package"] + ".types") is not None
    )
    gql = next(
        item
        for item in catalog
        if importlib.util.find_spec(item["package"] + ".custom_mutations") is not None
    )
    workflow = next(item for item in catalog if item["console"].endswith("-workflows"))
    payload = (ROOT / "payload.bin").read_bytes()
    assert len(payload) == 274 and hashlib.sha256(payload).hexdigest() == DIGEST
    before = len(state()["journal"])
    upload_python(rest["package"], gql["package"], payload)
    assert len(state()["journal"]) == before + 4
    batch = state()["journal"][before:]
    assert [item["path"] for item in batch] == [
        "/upload",
        "/binary",
        "/graphql",
        "/graphql",
    ]
    assert [len(item["files"]) for item in batch] == [1, 1, 1, 2]
    upload_console(rest, gql, before + 4)
    count = verify_workflows(workflow, rest, payload)
    verify_authentication(rest, count)
    verify_journal()
    generated_tests = verify_distribution(catalog)
    verify_final_privacy()
    versions = {
        name: importlib.metadata.version(name)
        for name in ("arazzo-runner", "playwright", "httpx", "requests")
    }
    summary = {
        "status": "passed",
        "commands": COMMAND_RESULTS,
        "versions": versions,
        "native_upload_forms": 4,
        "python_uploads": 4,
        "console_uploads": 4,
        "workflow_python_and_console": True,
        "guarded_missing_output_no_dependent_dispatch": True,
        "captured_bearer_and_cookie": True,
        "native_multipart_metadata": True,
        "native_null_omitted_nested": True,
        "rotation": True,
        "expiration_closed_stdin": True,
        "excluded_no_dispatch": True,
        "journal_count": len(state()["journal"]),
        "generated_tests": generated_tests,
        "no_generator_browser_node": True,
    }
    (ARTIFACTS / "runtime-summary.json").write_text(json.dumps(summary, indent=2))
    print(
        "PASS installed standalone Python methods, console commands, guarded workflows, credentials and native bytes"
    )


if __name__ == "__main__":
    main()
