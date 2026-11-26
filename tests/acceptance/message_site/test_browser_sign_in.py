"""Browser stage: generator and installed login command each complete MFA."""

from .support import BROWSER, run_browser_sign_in


def test_generator_and_installed_client_complete_separate_browser_sign_ins(
    site, tmp_path, generated_application,
):
    """Keep the existing real standalone login proof, separate from API execution."""
    from test_login import install_generated_wheel

    environment = tmp_path / "installed-login"
    install_generated_wheel(generated_application.project_directory, environment)
    signed_in = run_browser_sign_in(site, [
        "login", "--url", site.url, *BROWSER,
    ], executable=str(environment / "bin/login-client"))
    assert signed_in["authentication"] == "user-confirmed"
    assert signed_in["sessionClosed"] is True
