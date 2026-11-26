"""Browser stage: generator and installed login command each complete MFA."""

import os
import subprocess
import sys

from .support import BROWSER, run_browser_sign_in


def test_generator_and_installed_client_complete_separate_browser_sign_ins(
    site, tmp_path, generated_application,
):
    """Keep the existing real standalone login proof, separate from API execution."""
    environment = tmp_path / "installed-login"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    runtime_python = environment / "bin/python"
    install_result = subprocess.run(
        [str(runtime_python), "-m", "pip", "install", "."],
        cwd=generated_application.project_directory,
        env={**os.environ, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "/opt/client-wheels"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    signed_in = run_browser_sign_in(site, [
        "login", "--url", site.url, *BROWSER,
    ], executable=str(environment / "bin/login-client"))
    assert signed_in["authentication"] == "user-confirmed"
    assert signed_in["sessionClosed"] is True
