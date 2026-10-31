# Browser-Sourced API Client Generator

Browser-Sourced API Client Generator discovers website endpoints from an
authenticated browser or an existing HAR, exports standard definitions, and uses
OpenAPI and GraphQL generators to create standalone Python clients with endpoint
commands and help. HAR is the HTTP Archive format used by browser network tools.

## Install and open a website

You need Python 3.11 or newer, Poetry 2.2 or newer, and installed Google Chrome or
Chromium. If Poetry is missing, install it using the [official instructions](https://python-poetry.org/docs/#installation).
From this repository, install the locked dependencies and Python command:

```sh
poetry sync --with dev
poetry run website-client --help
```

Replace `https://app.example.com` below with the website you want to use:

```sh
poetry run website-client login --url https://app.example.com
```

1. A separate temporary Chrome window opens.
2. Sign in normally, including any verification code. Enter credentials only into
   the website, never the terminal.
3. Return to the requested website if sign-in redirected to another service.
4. Press Enter in the terminal. The command checks the current page, closes the
   temporary browser and reports user-confirmed readiness.

This command does not save a login, generate a client, or verify an authenticated
API request. The login command performs no endpoint discovery or record collection. Website
scripts run normally while you log in. A subsequent invocation starts logged out.
Ctrl+C cancels and closes the browser. Closing the browser ends the command with
an error. The command requires an interactive terminal, not piped input.

Chromium users select `--browser chromium`. If your installation is in an unusual
location, supply `--browser-path /absolute/path/to/browser`. Failure to find a
browser requires installing Chrome or Chromium, or correcting that path. For an
incomplete login, sign in fully and return to the requested website before
confirming on a fresh run.

The implementation connects to the native browser directly from Python. It needs
no Node, npm, Playwright or JavaScript driver. Cookies and authentication stay in
the browser or process memory. The login command exports no saved browser state;
the discovery command exports the sanitized traffic described below.
Temporary profile files are removed on normal close; forced termination can leave
temporary browser files.

## Tests

```sh
poetry check
poetry run pytest -q
poetry build
sh scripts/test-docker.sh
```

Docker is needed only for the last command. It builds and installs the Python
wheel using Poetry and tests a real terminal and headed Chromium against a local HTTPS login
and verification-code fixture. The container has no host mounts or external
network during execution. It has no Node installation or production credentials.
These tests do not establish production service compatibility.

## Discover endpoints after login

Run from this repository's root, where `pyproject.toml` is located. Choose a new
output directory whose parent already exists. Replace the example URL below with
the website you want to capture:

```sh
mkdir -p captures
poetry run website-client discover --url https://app.example.com --output ./captures/example
```

1. Log into the temporary browser and complete verification codes.
2. Return to the requested website, then press Enter in the terminal to start capture.
3. Reload the page and open the workflows you want to discover. Capture covers
   only this owned tab and requests initiated from the requested website.
4. Press Enter again to save the files and close the browser.

Capture starts after login confirmation, so reloading matters. The tool observes
traffic created by your browsing; it never replays captured business operations.
You can cancel with Ctrl+C. Existing output is never overwritten. A failed export
can leave a partial new directory; inspect it and choose a new path before retrying.

## Use an existing HAR instead

Export a HAR from your browser's Network panel, then supply its file path:

```sh
poetry run website-client discover --har ./session.har --output ./captures/imported-session
```

Create `captures/` first as shown above; it is ignored by Git. HAR import is
offline, opens no browser and leaves the input file unchanged. Both
sources use the same analysis and write the following files, when available:

| File | What it contains |
| --- | --- |
| `capture.har` | Sanitized HAR with headers, cookies, payload values and URL query values removed or redacted. |
| `discovery.json` | Observed routes, methods, named GraphQL operations, omitted data, conflicts and coverage limits. |
| `openapi-inferred-*.json` | Valid OpenAPI 3.1 descriptions inferred from observed HTTP routes and sample shapes. These require review and are not complete service specifications. |
| `operations.graphql` | Combined GraphQL operation documents when their definitions do not conflict. |
| `operations-*.graphql` | Individual captured GraphQL documents, kept separately for review. |
| `openapi-downloaded-*.json` | Actual OpenAPI 3.0/3.1 documents encountered in JSON or YAML responses, validated separately from inference. |
| `graphql-schema-downloaded-*.graphql` | Actual complete introspection responses converted into GraphQL schema definition language, or validated SDL responses. |

To capture an actual definition, open the website's known schema or specification
endpoint while recording, or import a HAR containing that response. The tool does
not guess schema URLs or manufacture a GraphQL schema from examples. Operations
with inline literal values are omitted from exported GraphQL documents; comments
and runtime variables are excluded. Authentication-related routes are omitted.

The default maximum is 1,000 entries; use `--max-entries N` up to 10,000. HAR input
is limited to 32 MiB, individual request/response bodies to 1 MiB, and browser
buffers are bounded. In-flight, unavailable or oversized data is reported as a
coverage gap. A successful capture never claims whole-service completeness.
Outputs use private permissions. Downloaded formal specifications are kept as
definitions; the sanitized HAR is not a lossless backup of the original traffic.

## Generate a Python client from a definition

For OpenAPI, use a validated local JSON or YAML file, either a downloaded service
definition or an inferred definition you have reviewed:

```sh
poetry run website-client generate --openapi ./openapi.json --name example-client --output ./example-client
```

For GraphQL, supply both a complete schema and the operations you want as methods:

```sh
poetry run website-client generate --graphql-schema ./schema.graphql --operations ./operations.graphql --name example-graphql-client --output ./example-graphql-client
```

The schema input can also be complete introspection JSON. Use the filenames listed
in `discovery.json`, or definitions obtained directly from the service. Operation
documents alone cannot supply a full schema. Conflicting operation definitions
need review before combining them. Bundle external references into OpenAPI input
first; the tool does not fetch external references implicitly.

OpenAPI generation calls [openapi-python-client](https://github.com/openapi-generators/openapi-python-client).
GraphQL generation calls [Ariadne Codegen](https://github.com/mirumee/ariadne-codegen).
Both run locally from the Poetry environment. The result is a standalone Python
package, its upstream typed methods, an installed endpoint command, input help,
the source definition and a generation report. It does not depend on this generator.
OpenAPI generator warnings are treated as failures. GraphQL operations are
validated against the supplied schema before generation. Existing output
directories are refused.

## Run a generated client

For the OpenAPI example above:

```sh
cd example-client
poetry install
poetry run example-client --help
```

Top-level help lists the generated operation names and READ/WRITE behavior. Choose
an actual listed name and run `poetry run example-client OPERATION --help` for its
JSON input fields. Replace `OPERATION` with that listed name; it is not a literal
command. Supply the service base URL with `--url` for OpenAPI, or the exact GraphQL
endpoint URL for GraphQL. JSON field names match the upstream Python arguments.

Commands call the upstream generated methods, including their typed input models.
Reads send remote requests. Writes require `--confirm-write` followed by the exact
operation name. POST HTTP operations are conservatively treated as writes. No
request is retried automatically, and redirects are not followed.

Authentication is provided through `--headers-stdin` as a JSON object of temporary
header values. No browser-login or credential-capture helper is bundled into the
generated client yet. Do not put credentials in command arguments or source files.
Use `--output NEW_FILE` to save private JSON results. If a request fails, reconcile
the service before retrying because a write may already have succeeded. Complex
input types, custom GraphQL scalars and multi-step service workflows may require
the generated Python API or service-specific extensions.

## Dependency management

Poetry creates an ignored `.venv/` inside this repository. `poetry sync` reads
the committed `poetry.lock`; it installs the locked dependencies and the command,
and removes packages that are no longer in the selected dependency groups, without
opening a browser. `poetry run` uses that environment without requiring activation.
`poetry build` creates the wheel and source archive in `dist/`. If Poetry reports
that the lock is stale after an intentional dependency edit, run `poetry lock`
and review both `pyproject.toml` and `poetry.lock` before committing. To change
dependencies, use `poetry add PACKAGE` or `poetry add --group dev PACKAGE`.
