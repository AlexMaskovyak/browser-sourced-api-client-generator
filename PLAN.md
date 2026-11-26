# Product specification

**Input:** a website URL and an output directory. **Output:** an installable Python package whose methods call the server operations the website uses. Assume the website publishes no API description. The user supplies the initial login and MFA. Everything after confirmation runs programmatically.

### What a developer should be able to do

For a message application observed fetching `GET /message?message_id=m1`, the target interface is:

```python
from mail_client import Client

c = Client()
message = c.get_message(message_id="m2")
print(message.subject)
```

```sh
mail-client get-message --message-id m2
```

The CLI parses the flag, calls the public Python method and prints its result. The client owns the server address, authentication, request construction and response parsing. Both interfaces must work with the initial browser closed and stdin closed. Reuse valid authorization from a private profile outside the package. On expiry, return `authentication_required`; explicit login starts recovery.

### What the generator must do

1. **Observe the application.** Attach capture before the dashboard’s first data request. Pair requests with responses and retain sanitized evidence of method, target, arguments, status and data structure. Record which page and action produced the exchange when known. One screen can use several operations; several screens can share one operation.
2. **Describe the server operation.** For HTTP, pass sanitized traffic in HTTP Archive (HAR) format to har2sdk’s `generateFromHar()`. Validate and save the inferred OpenAPI document. Generate the Python request code and models with `openapi-python-client`. For GraphQL, use the captured operation document and the schema when available. Reuse Ariadne Codegen for schema-backed generation. Supplied definitions remain an optional input.
3. **Expose a deterministic interface.** Use protocol and endpoint to identify services. Derive methods from the HTTP verb and route or from a declared operation name. Use tags or evidenced collection/member paths for resource groups. Unambiguous methods also appear on the root client. Python and CLI share one operation mapping. Preserve aliases and resolve collisions predictably. The [naming and grouping rules](ARCHITECTURE.md#planned-public-operation-names-and-groups) define the algorithm. No agent chooses methods or names.
4. **Publish what the evidence supports.** Generate a usable login client before sign-in. Report coverage and a reason for each exclusion. Zero executable application operations leaves the login client available and produces a nonzero discovery status. Identical inputs and versions must generate identical output offline. Discovery never submits an upload or business write; explicit client invocation authorizes it. Require explicit mappings for multi-step workflows.

### How we prove it, one milestone at a time

**Use Docker for routine development and review.** Run [`./scripts/test-message-site.sh`](scripts/test-message-site.sh) from the repository root. The fixture owns its website, backend, credentials and expected records. The driver automates only the initial sign-in on behalf of a user. No real account or personal data is needed.

| Milestone | Pass condition |
| --- | --- |
| Login and page navigation | Already demonstrated in [PR #14](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/14) and [PR #15](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/15). Interrupted login and zero links leave a working client. One selected link yields a working HTML read. |
| Local site and backend | A real browser signs in, loads an HTML shell and fetches message `m1` as JSON. The browser response and rendered subject match the backend’s own request log and record. Report the generator’s emitted operations separately. |
| Capture and inference | Give the generator only the starting site URL and output location. Its own capture must contain the paired message exchange. Its inferred OpenAPI must contain the verb, route, query parameter and response structure. The fixture supplies no schema, endpoint hints or method names. |
| Installed application client | Install the wheel in a clean environment. Change the backend’s `m2` subject after generation. Run the Python and CLI examples above in separate processes. Verify both the received argument on the server and the updated subject in each result. Exercise missing IDs and expired sessions. A fresh reader must succeed from the package documentation without coaching. |
| Next capabilities | Add one proven behavior at a time: pagination, a GraphQL read, a settings change, a download or an upload. Compare downloaded bytes and verify the exact server effect of each explicitly invoked write. Never retry an uncertain write. |

Keep implementation, adversarial review, generator execution and independent package use separate. The local site and Chromium agree on the authenticated JSON response for `m1`. The installed login command emits zero operations on that dashboard. Run `./scripts/test-message-site.sh --require-message-get` to reproduce the missing GET in its generated OpenAPI. The next implementation step is capturing the page’s natural request in the generator itself. Installed public-method execution and unattended reuse remain pending.
