"""Cache locked runtime wheels for offline, independent generated-client tests."""

from pathlib import Path
import subprocess
import sys
import tomllib


packages = tomllib.loads(Path("poetry.lock").read_text())["package"]
versions = {item["name"]: item["version"] for item in packages}
constraints = Path("/tmp/client-runtime-constraints.txt")
constraints.write_text(
    "\n".join(f"{name}=={version}" for name, version in versions.items()) + "\n"
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--timeout",
        "120",
        "--only-binary",
        ":all:",
        "--dest",
        "/opt/client-wheels",
        "--constraint",
        str(constraints),
        *(
            f"{name}=={versions[name]}"
            for name in (
                "httpx",
                "attrs",
                "pydantic",
                "playwright",
                "graphql-core",
                "jsonschema",
                "platformdirs",
                "filelock",
                "requests",
                "jsonpointer",
                "openapi-schema-validator",
                "pytest",
                "poetry-core",
                "pdm-backend",
            )
        ),
    ],
    check=True,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--constraint",
        str(constraints),
        "--dest",
        "/opt/client-wheels",
        "src/website_client/native_dependencies/arazzo_runner-0.9.6+websiteclient.1-py3-none-any.whl",
    ],
    check=True,
)
