# Product specification

**Input:** a website URL and an output directory. **Output:** an installable Python package whose methods call the server operations the website uses. Assume the website publishes no API description. The user supplies the initial login and MFA. Everything after confirmation runs programmatically.

## The installed Python interface

The first milestone builds an installable distribution named `login-client`. Its Python import is `login_client`. The example application loads a message after sign-in by requesting `GET /message?message_id=m1`. A developer who installs the generated wheel can ask the backend for a different message:

```python
from login_client import Client

client = Client()
message = client.get_message(message_id="m2")
print(message.subject)
```

`Client()` uses the website origin recorded at generation and the current user's private sign-in profile. `get_message` sends an authenticated GET with the caller's keyword argument. It returns a generated Python model with `id` and `subject` attributes. Changing the subject on the server after installation must change what the caller receives. Neither the sample message nor its subject belongs in the wheel.

The installed shell command is `login-client get-message --message-id m2`. It calls the same Python method and prints the returned model as JSON. Both interfaces work after the original browser exits, with standard input closed. A missing record reports HTTP 404. An expired session reports `authentication_required` and requires a new explicit login. The calling process never asks for a password or MFA code.

For this milestone the method is directly on `Client`. A dashboard link and a background GET have different contracts. The link returns HTML. The GET returns the inferred data model. The dashboard identifies where the request occurred. The HTTP verb, route and query names determine the method and its arguments. The [architecture](ARCHITECTURE.md#planned-public-operation-names-and-groups) specifies how later milestones group multiple services and resources.

### What the generator must do

1. **Observe the application.** Attach capture before the dashboard’s first data request. Pair requests with responses and retain sanitized evidence of method, target, arguments, status and data structure. Record which page and action produced the exchange when known. One screen can use several operations; several screens can share one operation.
2. **Describe the server operation.** For HTTP, pass sanitized traffic in HTTP Archive (HAR) format to har2sdk’s `generateFromHar()`. Validate and save the inferred OpenAPI document. Generate the Python request code and models with `openapi-python-client`. For GraphQL, use the captured operation document and the schema when available. Reuse Ariadne Codegen for schema-backed generation. Supplied definitions remain an optional input.
3. **Expose a deterministic interface.** Use protocol and endpoint to identify services. Derive methods from the HTTP verb and route or from a declared operation name. Use tags or evidenced collection/member paths for resource groups. Unambiguous methods also appear on the root client. Python and CLI share one operation mapping. Preserve aliases and resolve collisions predictably. The [naming and grouping rules](ARCHITECTURE.md#planned-public-operation-names-and-groups) define the algorithm. No agent chooses methods or names.
4. **Publish what the evidence supports.** Generate a usable login client before sign-in. Report coverage and a reason for each exclusion. Zero executable application operations leaves the login client available and produces a nonzero discovery status. Identical inputs and versions must generate identical output offline. Discovery never submits an upload or business write; explicit client invocation authorizes it. Require explicit mappings for multi-step workflows.

### How we prove it, one milestone at a time

**Use Docker for routine development and review.** Run [`./scripts/test-message-site.sh`](scripts/test-message-site.sh) from the repository root. The fixture owns its website, backend, credentials and expected records. The fixture issues a username, password and MFA code in memory for each test. The driver enters them through the real forms for both the generator and the installed generated client. Each must reach the protected dashboard in its own browser and receive the expected message from the backend. The backend must deny a message read after the password alone or an incorrect MFA code. Contributors only need Docker.

| Milestone | Pass condition |
| --- | --- |
| Login and page navigation | Already demonstrated in [PR #14](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/14) and [PR #15](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/15). Interrupted login and zero links leave a working client. One selected link yields a working HTML read. |
| Local site and backend | A real browser signs in, loads an HTML shell and fetches message `m1` as JSON. The browser response and rendered subject match the backend’s own request log and record. Report the generator’s emitted operations separately. |
| Capture and inference | Give the generator only the starting site URL and output location. Its own capture must contain the paired message exchange. Its inferred OpenAPI must contain the verb, route, query parameter and response structure. The fixture supplies no schema, endpoint hints or method names. |
| Installed application client | Install the wheel in a clean environment. Change the backend’s `m2` subject after generation. Run the Python and CLI examples above in separate processes. Verify both the received argument on the server and the updated subject in each result. Exercise missing IDs and expired sessions. A fresh reader must succeed from the package documentation without coaching. |
| Next capabilities | Add one proven behavior at a time: pagination, a GraphQL read, a settings change, a download or an upload. Compare downloaded bytes and verify the exact server effect of each explicitly invoked write. Never retry an uncertain write. |

## Acceptance and delivery order

Each stage has a named input and an observable output. The primary implementer may write unit tests for a component. A different author writes acceptance tests from these contracts before implementation. Keep the original failing result with the test commit. Change an expectation only when the contract changes or an independent review identifies an incorrect test.

| Stage | Input | Required output and evidence |
| --- | --- | --- |
| Sign-in | Starting URL and valid fixture credentials entered through both forms | The backend accepts the password and MFA and serves the protected dashboard. The dashboard makes its own authenticated read. |
| Capture | Browser events from the dashboard's first navigation onward | A paired same-origin GET and successful JSON response with the route, argument name and field types. The capture must exclude credential and sample values. |
| Inference | Sanitized HTTP exchanges with the browser closed | Valid OpenAPI with a GET on the observed path, its string query parameter and the response object's fields. |
| Generation | That operation definition | An installable wheel with a discoverable public method, result model, CLI command and instructions. Empty input still produces a package with login. |
| Installed use | The installed wheel, private profile and a caller-supplied argument | Both Python and CLI make one request each. Each returns the backend value set for that call. Missing data and expired authorization produce distinct errors. |

1. An independent adversary writes the installed-client test against this public interface. Run it against the current generator and commit the failing test before changing product code.
2. Attach the recorder before the dashboard loads, infer the operation from the dashboard's request and generate the wheel. Confirm the recorded operation, query parameter and response fields.
3. Install the wheel in a clean environment. Give it a private local profile from the initial sign-in. Invoke Python and the CLI in separate processes. For each, change the backend subject first and compare both the caller's result and the backend's request log.
4. Run the default Docker test, including missing-record and expired-session cases. A missing operation fails the default test. The local site's own sign-in and rendering checks diagnose failures separately. Run the existing regressions before pushing.

A zero-link dashboard with no observed API read still produces a login client. Add another capability only after the installed-client example passes. The total count of tests is never a milestone result.
