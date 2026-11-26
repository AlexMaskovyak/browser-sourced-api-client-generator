# What each test proves

For the message-client milestone, start Docker and run `./scripts/test-message-site.sh` from the repository root. Docker builds the generator, starts a private test website and backend, and runs the independent acceptance suite. It uses disposable accounts and performs no external website operations. The first image build needs network access. The test run uses only container loopback.

## Acceptance tests

Read the result for the stage you are changing. A failed setup means later assertions in that test did not run. A skipped browser test outside Docker provides no sign-in evidence.

| Stage | Source | What must happen |
| --- | --- | --- |
| Test website and backend | `acceptance/message_site/test_fixture_controls.py` | Password and MFA control access. A changed record, unknown ID and expired session produce distinguishable results. The site supplies no API definition. |
| Sign-in | `acceptance/message_site/test_browser_sign_in.py` | The generator and installed client each open a fresh browser, complete both forms and receive the record fetched by the dashboard. |
| Capture | `acceptance/message_site/test_capture_contract.py` | The saved HTTP archive pairs the dashboard’s GET with its response. It retains argument names and field types while removing credentials and message values. |
| Inference | `acceptance/message_site/test_inference_contract.py` | The generator’s OpenAPI describes the GET, its string query argument and the JSON fields the backend returned. |
| Installed application | `acceptance/message_site/test_installed_client.py` | Python and CLI each request a different record from the one observed. The server log identifies the argument and each caller receives the subject set immediately before its call. Missing records and expired authorization fail with the expected errors. |

The [product plan](../PLAN.md) defines the public interface before implementation. For a new capability, an independent author writes the acceptance test and records the failure against the preceding revision. Implementers may add unit tests to narrow the cause. During review, name the first failing stage and the evidence used to identify it.

## Component and integration tests

Use these directories to locate regressions or run a smaller set while developing. From the repository root with its Poetry development dependencies installed, for example, `poetry run pytest tests/capture` exercises the recorder. Run `./scripts/test-docker.sh` for the complete suite, including tests that need a real browser or installed wheel.

| Directory | Input and output checked |
| --- | --- |
| `authentication/` | Browser startup, URL validation, confirmation and cleanup. |
| `capture/` | Network events become bounded request and response observations. |
| `discovery/` | Page links, navigation decisions and API definition candidates. |
| `inference/` | Recorded traffic and GraphQL documents become sanitized operation descriptions. |
| `contracts/` | Saved manifests preserve evidence and reject malformed or conflicting records. |
| `generation/` | The generator writes package files and keeps a login-capable client on interruption. |
| `runtime/` | Generated operation arguments, transports, redirects and error handling. |
| `integration/` | Multiple components together, including manifest-to-package commands, generated interfaces, zero-link login and one protected HTML read. |

Older component and integration tests retain their original coverage. Their presence does not establish that an independent author wrote them in advance. Use the independent installed-application test to judge the message-client milestone.
