"""Structured invocation of the pinned generation-only har2sdk dependency."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from filelock import FileLock
from platformdirs import user_cache_path

from .browser import BrowserError

BACKEND = Path(__file__).with_name("har_backend")
VERSION = "0.6.15"


def _require_node(*, installing=False) -> None:
    if not shutil.which("node") or installing and not shutil.which("npm"):
        raise BrowserError(
            "har_dependency_required: Node >=20 and npm are required for HAR inference."
        )
    version = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, timeout=10
    )
    try:
        major = int(version.stdout.strip().lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    if version.returncode or major < 20:
        raise BrowserError(
            "har_dependency_required: Node >=20 is required for HAR inference."
        )


def backend_module() -> Path:
    """Locate the pinned converter, installing only its locked generation dependencies."""
    configured = os.environ.get("WEBSITE_CLIENT_HAR_BACKEND")
    _require_node(installing=not configured)
    if configured:
        root = Path(configured)
        module = root / "node_modules" / "har2sdk" / "dist" / "index.js"
        try:
            package = json.loads((module.parents[1] / "package.json").read_text())
        except (OSError, ValueError):
            raise BrowserError("har_dependency_required: configured har2sdk is unavailable.") from None
        if package.get("version") != VERSION or not module.is_file():
            raise BrowserError("har_dependency_required: configured har2sdk version is incorrect.")
        return module
    lock = (BACKEND / "package-lock.json").read_bytes()
    cache = user_cache_path("website-client") / (
        "har2sdk-" + VERSION + "-" + hashlib.sha256(lock).hexdigest()[:12]
    )
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(cache / "install.lock", timeout=120):
        module = cache / "node_modules" / "har2sdk" / "dist" / "index.js"
        if not (cache / ".installed").exists():
            for name in ("package.json", "package-lock.json"):
                shutil.copyfile(BACKEND / name, cache / name)
            result = subprocess.run(
                ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=cache,
                capture_output=True,
                timeout=120,
            )
            if result.returncode or not module.is_file():
                raise BrowserError("har_dependency_install_failed")
            (cache / ".installed").write_text(VERSION)
        return module


def infer_har(safe_har: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Accept sanitized HAR text only; raw input never becomes an intermediate file."""
    module = backend_module()
    with tempfile.TemporaryDirectory(prefix="website-client-har-") as directory:
        root = Path(directory)
        source, target = root / "safe.har", root / "result.json"
        source.write_text(safe_har)
        result = subprocess.run(
            [
                "node",
                str(BACKEND / "convert.mjs"),
                str(module),
                str(source),
                str(target),
            ],
            capture_output=True,
            timeout=120,
        )
        if (
            result.returncode
            or not target.exists()
            or target.stat().st_size > 32 * 1024 * 1024
        ):
            raise BrowserError("har_inference_failed")
        value = json.loads(target.read_text())
        if (
            not isinstance(value.get("openapi"), dict)
            or not isinstance(value.get("warnings"), list)
            or not isinstance(value.get("leaks"), list)
        ):
            raise BrowserError("har_inference_invalid_result")
        # Diagnostics are structured but may contain data-derived snippets. Keep counts,
        # never tainted samples or masked previews, in the portable report.
        return value["openapi"], {
            "backend": "har2sdk",
            "version": VERSION,
            "warnings": [
                {"reason": "upstream_inference_warning"} for _ in value["warnings"]
            ],
            "leaks": [{"reason": "upstream_leak_detected"} for _ in value["leaks"]],
        }
