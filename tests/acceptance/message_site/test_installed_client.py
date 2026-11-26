"""Consumer stage: installed public Python and CLI interfaces call the server."""

import json
from uuid import uuid4

from .support import install_application, run_installed_consumer, assert_single_message_request


def test_installed_python_and_cli_each_return_independently_changed_server_data(
    site, tmp_path, generated_application,
):
    """Milestone: both public interfaces perform fresh, authenticated backend reads."""
    application = install_application(generated_application, tmp_path)
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        # A new unpredictable value for EACH call rejects canned or cached output.
        expected_subject = f"Fresh {interface} subject {uuid4()}"
        site.messages["m2"]["subject"] = expected_subject
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "m2")
        assert result.returncode == 0, result.stdout + result.stderr
        actual_subject = (
            result.stdout.strip() if interface == "python" else
            json.loads(result.stdout)["subject"]
        )
        assert actual_subject == expected_subject
        assert_single_message_request(site, requests_before, "m2", 200, True)
        assert site.counts == login_counts, "Installed read repeated browser login or dashboard navigation."



def test_installed_python_and_cli_report_missing_message_without_retry(
    site, tmp_path, generated_application,
):
    """Milestone: an actual backend 404 is an error, not an empty successful model."""
    application = install_application(generated_application, tmp_path)
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "unknown")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "404" in result.stdout + result.stderr
        assert_single_message_request(site, requests_before, "unknown", 404, True)
        assert site.counts == login_counts



def test_installed_python_and_cli_report_expired_auth_without_repeating_login(
    site, tmp_path, generated_application,
):
    """Milestone: expiry reports authentication_required with stdin already closed."""
    application = install_application(generated_application, tmp_path)
    site.sessions_active = False
    login_counts = site.counts.copy()
    for interface in ("python", "cli"):
        requests_before = len(site.message_requests)
        result = run_installed_consumer(application, interface, "m2")
        assert result.returncode != 0, result.stdout + result.stderr
        assert "authentication_required" in result.stdout + result.stderr
        assert_single_message_request(site, requests_before, "m2", 401, False)
        assert site.counts == login_counts, "Expired authorization triggered a new sign-in."
