"""Combine safe phase reports with actual Docker image identifiers."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def image_id(name: str) -> str:
    return subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True
    ).strip()


def source_state() -> dict:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], text=True
    ).splitlines()
    diff = subprocess.check_output(["git", "diff", "--no-ext-diff", "--binary", "HEAD"])
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode()
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        path = Path(name)
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return {
        "revision": revision,
        "dirty_files": status,
        "dirty_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "tree_sha256": digest.hexdigest(),
    }


def main() -> None:
    if sys.argv[1] == "--source":
        Path(sys.argv[2]).write_text(json.dumps(source_state(), indent=2))
        return
    root = Path(sys.argv[1])
    images = sys.argv[2:]
    assert len(images) == 2, "Expected generation and runtime image names."
    starting_source = json.loads((root / "source-state.json").read_text())
    unchanged = source_state()["tree_sha256"] == starting_source["tree_sha256"]
    if os.environ.get("WEBSITE_CLIENT_REQUIRE_FIXED_SOURCE") == "1":
        assert unchanged, (
            "Source files changed during acceptance; rerun against a fixed revision."
        )
    summary = {
        "source": starting_source,
        "source_unchanged_during_run": unchanged,
        "command": os.environ.get(
            "WEBSITE_CLIENT_ACCEPTANCE_COMMAND", "scripts/test-standalone.sh"
        ),
        "compatibility_tests_and_https": "passed"
        if os.environ.get("WEBSITE_CLIENT_COMPATIBILITY_PASSED") == "1"
        else "not_run",
        "generation": json.loads((root / "generation-summary.json").read_text()),
        "runtime": json.loads((root / "runtime-summary.json").read_text()),
        "images": {name: image_id(name) for name in images},
    }
    path = root / "acceptance-summary.json"
    path.write_text(json.dumps(summary, indent=2))
    print("PASS standalone acceptance. Machine-readable summary: " + str(path))


if __name__ == "__main__":
    main()
