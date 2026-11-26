# Generate a Python client from the website’s server operations

A developer installs the generated package, constructs `Client()`, and calls a method with ordinary Python arguments. The CLI exposes the same operations and calls those public methods. The generator gets its description of each operation from browser-observed requests and responses or a service definition. After the initial sign-in, discovery and generation run without human direction or an agent.

## What the developer gets

For example, suppose a local message application fetches a message by ID and its server returns a message object. These are proposed interfaces for that fixture:

```python
from mail_client import Client

c = Client()
message = c.get_message(message_id="m2")
print(message.subject)
```

```sh
mail-client get-message --message-id m2
```

The CLI parses the ID, calls `c.get_message(message_id="m2")`, and prints the returned message as JSON. Python returns the native result directly. Both use the same authenticated session handling, generated request builder, response parsing and server-error handling. Preserve existing CLI spellings as aliases when needed.

The installed client owns its default server address and authentication setup. Initial login and MFA are explicit. Subsequent calls, including scheduled CLI runs, reuse valid authorized state without prompting. If the server requires sign-in again, surface `authentication_required` so the caller can recover through explicit login. Keep credentials outside the distributable package.

## What works today

- [PR #14](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/14) generates an installable login client before opening the browser. An unfinished login leaves a working client.
- [PR #15](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/15) selects one dashboard link and gives its literal GET path to the OpenAPI generator. Its installed CLI can sign in again and return that page’s HTML. With zero eligible links, it still produces a working login client. Preserve both cases and deterministic generation from saved inputs.
- The one-link test proves authenticated page retrieval. It leaves three product requirements open: capturing the application request behind a page, exposing it as a method on `Client`, and invoking that same public method from the CLI using an existing login. The current Python caller has to supply browser transport and call a generated module function.

## Next milestone: one captured application read, end to end

Use a synthetic message application. Its dashboard returns HTML and makes one authenticated request for message data. The request path, verb, argument and response must come from the website’s exchange. Keep fixture-specific names and paths out of generator logic.

1. **Capture the exchange.** Start at the fixture’s login URL. After login, record the application request and its corresponding response through the existing capture module. Save sanitized evidence of the method, request target, inputs, status and response structure. Verify against the fixture server’s request log. If one sample cannot distinguish a literal value from a parameter, use another observed call of the same operation or an advertised definition. Verify that discovery sends zero writes. Prove this step before changing generation.
2. **Generate one Python method.** Feed the saved exchange through the existing HTTP inference and OpenAPI generation path. The generated public method delegates to the upstream request function and returns its native model. Put authentication and transport setup behind `Client`. Build the wheel, install it in a clean environment, and call the public method using a different valid message ID from the one captured. The server must see that ID and the caller must receive its current subject.
3. **Run the CLI through that method.** Verify that the CLI passes the parsed argument to the public method. Then run the installed executable in a separate process with stdin closed. Check the same request and application result as in Python, including the ID and subject. Exercise a rejected request through both interfaces. The CLI must preserve the error meaning in its exit status and diagnostic.
4. **Stop at one proven operation.** Keep the zero-link and one-link tests passing. Generate twice from the same saved evidence and compare the packages. A fresh user of the installed package must be able to run the documented Python and CLI examples without coaching, access to the generator checkout, or knowledge of the server route. Use separate people or agents for implementation, adversarial review, running the generator, and consuming the package. Agents are development roles only.

## Expand one operation at a time

Reuse existing generation backends. For REST, OpenAPI Python Client owns serialization, HTTP calls and models. For GraphQL, reuse Ariadne Codegen for schema-backed operations and the existing operation-document path for captured named operations without a schema. Each backend feeds the same public Python and CLI contract. Do not hand-code a parallel request implementation for either interface.

Apply the same acceptance test to each later capability: a list with pagination, a named GraphQL read, a settings change, a file download or an upload. For a download, verify the actual bytes. For a write or upload, verify the server received the intended arguments or file and performed the expected change on an isolated fixture. Discovery never executes a write to learn what it does. Explicit invocation by the caller is the execution boundary. Never retry a write with an uncertain outcome.

Generate only operations whose inputs and results the evidence supports. Record a reason for unsupported operations. A zero-operation run still produces the login client and reports its coverage. Reaching a page, finding an anchor, generating files, or receiving HTTP 200 alone cannot establish that a business operation works.
