# Browser-Sourced API Client Generator

Browser-Sourced API Client Generator records requests and operations from an
authenticated website or an existing HAR, then generates standalone Python clients
with endpoint commands and help. HAR is the HTTP Archive format used by browser
network tools. Published schemas are optional enrichment.

For REST/HTTP APIs, captured routes and sample shapes produce inferred OpenAPI
for review; a published OpenAPI definition can replace those samples. For GraphQL,
named operation documents generate callable methods directly. If a complete schema
is supplied or discovered, Ariadne Codegen adds schema-based models and validation.
Other API contract formats do not yet have dedicated generation support.

## Start here: website to client

Run these steps from the repository root, where `pyproject.toml` is located.
You need Python 3.11 or newer, an existing Poetry 2.2 or newer installation, and
Google Chrome or Chromium. Use new output paths each time; existing files and
client directories are not replaced.

```text
discover: sign in and explore the website
   -> captures/walk/manifest.json
normalize: validate definitions and report each API's status
   -> captures/walk/normalized.json
generate: select a ready API and create its Python package and CLI
   -> clients/site-client/
```

### 1. Install the generator

```sh
poetry sync --with dev
poetry run website-client --help
mkdir -p captures clients
```

Installation reads the committed dependency lock and installs the generator into
this repository's environment. It does not open a browser. If Poetry is missing,
use its [installation instructions](https://python-poetry.org/docs/#installation).

### 2. Sign in and discover the website

Replace the example URL with the application you want to inspect:

```sh
poetry run website-client discover --url https://app.example.com --explore --output ./captures/walk
```

A temporary Chrome window opens. Sign in there, complete any verification code,
and open the application page where exploration should start. Return to the
terminal and press Enter once. The command explores pages and looks for API
definitions, saves its results, and closes the browser. Chromium users add
`--browser chromium`.

This step reads the website and records declared forms and blocked write requests.
It does not submit those forms. The complete handoff is
`./captures/walk/manifest.json`, a JSON file containing the definitions and reports.
**Start with `discover`; a separate `login` command is not needed.** Login sessions
are not saved between commands.

### 3. Validate the results and choose an API

```sh
poetry run website-client normalize --input ./captures/walk --output ./captures/walk/normalized.json
```

This runs offline. The terminal prints API IDs, endpoint addresses and status:

| Status | What to do |
| --- | --- |
| `ready` | Generate this API's client. |
| `review_required` | Review the inferred OpenAPI definition before using `--allow-inferred`. |
| `blocked` | Read this API's `issues` in `normalized.json`; obtain missing definitions or resolve conflicts before generating. |

If there is exactly one ready API, the next command selects it automatically.
If several are ready, choose one of the IDs printed by normalization and add
`--service` followed by that ID. The [pipeline guide](docs/discovery-pipeline.md#inspect-and-select-an-api)
includes a command to print the status and issues without opening the full JSON.
Normalization can succeed even when no API is ready; it preserves incomplete
results so they can be completed later.

### 4. Generate the standalone client

```sh
poetry run website-client generate --manifest ./captures/walk/normalized.json --name site-client --output ./clients/site-client
```

This also runs offline. It creates a standalone Python project, API methods,
endpoint commands and `generation.json`, which records which definitions were
used. Generation does not call the service's business operations. GraphQL operation
clients validate declared variables and return JSON dictionaries. Unknown custom
input fields and response shapes remain untyped without an optional schema;
`generation.json` records whether schema validation was available.

### 5. Install the client and see its commands

```sh
cd clients/site-client
poetry install
poetry run site-client --help
```

You are now in the generated project, using its own dependencies. Help lists the
actual operation names. To call an operation, first inspect that operation's help
for its inputs, API URL and authentication options. See
[run a generated client](#run-a-generated-client) and
[log into a generated client](#log-into-a-generated-client). A service request is a
separate action; successful generation does not establish live compatibility.

## Continue from another starting point

| What you already have | Start with |
| --- | --- |
| A saved discovery directory or copied `manifest.json` | Step 3, passing that directory or file to `normalize`. No browser login is needed. |
| OpenAPI or GraphQL definitions from another tool | [`import`, then normalize and generate](docs/discovery-pipeline.md#connect-a-different-producer). |
| A browser network HAR file | [`discover --har`](#use-an-existing-har-instead), then step 3. HAR is the HTTP Archive format. |
| Another tool that emits a discovery manifest | [Pipe it to normalization and generation](docs/discovery-pipeline.md#chain-commands-through-stdin-and-stdout). |
| A generated client | [Install and run it](#run-a-generated-client); do not repeat discovery merely to execute a command. |

For the full command pipeline, input merging, missing schemas, pipes and failure
recovery, use [the pipeline guide](docs/discovery-pipeline.md). Its staged commands
wait for each previous command to succeed. Pipe examples use `pipefail`, which
reports failures but cannot undo downstream output after a late producer failure.
For implementing another producer, see the [manifest format](docs/discovery-manifest-format.md)
and [create_discovery_manifest.py](examples/create_discovery_manifest.py).

## Check browser login only

Replace `https://app.example.com` below with the website you want to use. This optional command checks
only the login browser; it is not a prerequisite for discovery:

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

The implementation uses Playwright's Python API with your installed browser.
Playwright includes its own Node driver; you do not need to install Node or npm.
Project code and generated clients remain Python. Cookies and authentication stay in
the browser or process memory. The login command exports no saved browser state;
the discovery command exports the sanitized traffic described below.
Temporary profile files are removed on normal close; forced termination can leave
temporary browser files.

## Explore pages and discover read and write contracts

Use exploration when the API calls you need appear on other pages, inside menus
or in forms. It runs a deterministic browser traversal, with no agent, model
account or API key. From the repository root, replace the example website address:

```sh
mkdir -p captures
poetry run website-client discover --url https://app.example.com --explore --output ./captures/example-walk
```

1. Sign in in the temporary browser and open the application page where exploration
   should start. Complete verification codes normally.
2. Press Enter once. The command walks same-origin links in breadth-first order,
   expands navigation menus, opens tabs and form dialogs, and follows pagination.
   It records API traffic during the walk, then runs schema discovery.
3. Read `exploration.json` for visited pages, attempted controls, queued pages,
   blocked requests and limits. Use `discovery.json` for observed endpoints and
   `self-discovery.json` for schema probe results. Generate clients from validated
   definitions and captured or declared GraphQL documents as described below.

The command inventories form actions, methods, upload types, named fields,
constraints and submitter overrides. It also extracts GraphQL query and mutation
documents from supported source literals in loaded same-origin frontend modules.
Forms and frontend documents are **declarations**, not verified backend contracts.
They are marked as unexecuted. Mutations can be included in the generated operation
documents without being called. Each declaration records whether its document
could be exported; inline literals are excluded from exported documents.

During exploration, parsed GraphQL queries and GET/HEAD requests are permitted.
API mutations, unknown POSTs and other write methods are blocked before sending;
their inferred input shapes are recorded without payload values. Forms are never
submitted. The tool may open an Add, New, Edit, Create or Upload dialog, but it
does not activate its submit controls. Input contents, selected values and hidden
tokens are not collected. Off-site document navigation is blocked and routed
WebSockets are not connected to their servers,
service workers are disabled, and the login tab is replaced before exploration.

Defaults are 20 pages, 60 control actions, link depth 3 and 120 seconds of
exploration. Change them with `--max-pages`, `--max-actions`, `--max-depth` and
`--explore-seconds`. Schema probes have their separate `--max-probes` limit.
The starting-page scan's `--observe-seconds` option cannot be combined with exploration.
Exploration implies `--auto`; the simpler starting-page scan remains available
below. Output directories must be new. Ctrl+C cancels and closes the browser.

HTTP/REST traffic is collected as route and shape evidence for OpenAPI. GraphQL
has additional operation parsing, which lets the explorer recognize queries
carried by POST. Other POSTs are blocked when their read/write behavior is unknown,
so a REST search implemented with POST may be missed. Discovery does not currently
use an OpenAPI definition to authorize those requests. This limitation applies to
the automatic walk; it does not prevent generation from an externally supplied
OpenAPI definition. See [API coverage and architecture](ARCHITECTURE.md#http-discovery-and-supported-api-definitions).

This is a bounded traversal, not proof of whole-site coverage. Custom widgets,
popups, cross-site pages, persisted GraphQL operations and unknown read-via-POST
APIs may be skipped or blocked. Only loaded same-origin scripts and literal
GraphQL tags or `body` strings are inspected; minified/computed operation formats
may not be understood. GET endpoints can have server-side effects despite their
method. A page visit or form declaration does not establish payment, submission
or approval. Review declared contracts before using them for writes.

Inspection also has fixed limits: 200 links or controls per state, 100 retained
forms with up to 100 fields each, and 30 loaded frontend modules of up to 1 MiB
each with an 8 MiB combined source budget. Truncation and pending work are reported.

## Find service definitions on the current page

From the repository root, create `captures/` and choose a new output directory.
The following example opens the website, waits for you to sign in, then discovers
definitions. Replace the example address with the actual website:

```sh
mkdir -p captures
poetry run website-client discover --url https://app.example.com --auto --output ./captures/example-auto
```

1. Sign in in the temporary browser, including verification codes, and return to
   the application page you normally use.
2. Press Enter once in the terminal. The command reloads that page, observes its
   traffic for five seconds, inspects up to 200 schema links and checks common
   definition paths. It tries GraphQL introspection, a read-only request for type
   and field definitions, on observed GraphQL endpoints.
3. The command saves the results and closes the browser automatically. Open
   `self-discovery.json` to see which probes found definitions, were rejected,
   redirected, failed or returned no usable schema. Use the validated downloaded
   files with the generation commands below.

This mode actively sends schema GET requests and its own introspection queries.
It does not replay captured business operations or click application controls.
Reloading still runs the website's normal scripts. Schema links and common paths
are restricted to the website's origin. Observed GraphQL API origins can differ;
selected authentication headers stay in memory and are scoped to that API origin.
Redirects are not followed. HAR import remains offline and cannot use `--auto`.

By default there are at most 12 probes, each with a 30-second request timeout.
Use `--max-probes 8` or `--observe-seconds 10` to change the limits, each from 1 to
30. If the page does not issue a GraphQL request during observation, supply the
known HTTPS endpoint with `--graphql-endpoint https://api.example.com/graphql`.
That is an example endpoint; obtain the real address from the website's API docs
or a prior capture. Query values and authentication paths are rejected.

Common paths include `/openapi.json`, `/openapi.yaml`, `/api/openapi.json`,
`/v3/api-docs`, `/.well-known/openapi.json` and `/swagger.json`. Only OpenAPI 3.0/3.1
and complete GraphQL schemas pass validation. A server can hide its definitions or
disable introspection. The report records that gap without inventing a schema or
claiming full API coverage. Use manual capture below for workflows that were not
loaded during observation. GraphQL generation still requires operation documents
in addition to the schema. Service-specific authentication headers or schema
formats may require an extension.

Existing output is never replaced. If login or discovery fails, check the website
address and browser state, then retry with a new output path. If an export leaves
a partial directory, inspect it before retrying. Cancel with Ctrl+C.

## Capture endpoints manually after login

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
| `manifest.json` | Versioned, self-contained bundle of all exported evidence and definitions, with API associations and hashes. Use this file for normalization and client generation. |
| `capture.har` | Sanitized HAR with headers, cookies, payload values and URL query values removed or redacted. |
| `discovery.json` | Observed routes, methods, named GraphQL operations, omitted data, conflicts and coverage limits. |
| `self-discovery.json` | Automatic mode only: schema probe outcomes, request limits, unprobed candidates and observation limits. |
| `openapi-inferred-*.json` | Valid OpenAPI 3.1 descriptions inferred from observed HTTP routes and sample shapes. These require review and are not complete service specifications. |
| `operations.graphql` | Combined GraphQL operation documents when their definitions do not conflict. |
| `operations-*.graphql` | Individual captured GraphQL documents, kept separately for review. |
| `openapi-downloaded-*.json` | Actual OpenAPI 3.0/3.1 documents encountered in JSON or YAML responses, validated separately from inference. |
| `graphql-schema-downloaded-*.graphql` | Actual complete introspection responses converted into GraphQL schema definition language, or validated SDL responses. |

Automatic discovery checks the definition locations described above. For manual
capture, open the website's known schema or specification endpoint while recording,
or import a HAR containing that response. Manual capture does not probe additional
URLs. Neither mode manufactures a GraphQL schema from examples. Operations
with inline literal values are omitted from exported GraphQL documents; comments
and runtime variables are excluded. Authentication-related routes are omitted.

The default maximum is 1,000 entries; use `--max-entries N` up to 10,000. HAR input
is limited to 32 MiB, captured request/response bodies to 1 MiB, and browser
buffers are bounded. Automatic schema probes can validate definitions up to 8 MiB;
their HAR body remains subject to the capture limit. In-flight, unavailable or oversized data is reported as a
coverage gap. A successful capture never claims whole-service completeness.
Outputs use private permissions. Downloaded formal specifications are kept as
definitions; the sanitized HAR is not a lossless backup of the original traffic.

## Generate a Python client from a definition

For OpenAPI, use a validated local JSON or YAML file, either a downloaded service
definition or an inferred definition you have reviewed:

```sh
poetry run website-client generate --openapi ./openapi.json --name example-client --output ./example-client
```

For GraphQL with an available schema, these file inputs enable stronger models:

```sh
poetry run website-client generate --graphql-schema ./schema.graphql --operations ./operations.graphql --name example-graphql-client --output ./example-graphql-client
```

The schema input can also be complete introspection JSON. Use the filenames listed
in `discovery.json`, or definitions obtained directly from the service. Operation
documents also generate directly through `import --operations` and the manifest
pipeline above, without a schema. Conflicting definitions need review before
combining them. Bundle external references into OpenAPI input
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

## Log into a generated client

The generated package includes its own Python browser support. It needs installed
Chrome or Chromium and an interactive terminal. From the generated package's
directory, run `poetry install`, then `poetry run example-client --help`.

Choose an operation listed by that help and inspect it with
`poetry run example-client OPERATION --help`. Replace `OPERATION` with its actual
name and create `input.json` with the required fields shown there. In the example
below, also replace both URLs with the service's API and application addresses:

```sh
poetry run example-client OPERATION --url https://api.example.com --input input.json --login --login-url https://app.example.com
```

1. A fresh Chrome window opens at the application address. Sign in and complete
   verification codes there. Enter credentials only into the website.
2. Open the application page you normally use, then return to the terminal and
   press Enter. The generated typed method sends its request through that native
   browser session and prints the parsed result.
3. The temporary browser and profile close after the operation. Ctrl+C cancels.
   Each command starts a fresh session; authentication state is not saved.

`--url` is the OpenAPI base URL or exact GraphQL endpoint. `--login-url` defaults
to `--url`; supply it when the sign-in website differs from the API. Select
Chromium with `--browser chromium`, or supply an installed executable with
`--browser-path`. No generator installation is needed by the generated package.

Cookies stay in the native browser. Authorization and common CSRF headers observed
for the selected API origin are held in memory. If the application uses another
session header, add `--session-header HEADER_NAME` and visit the relevant page
before pressing Enter; the value is observed from that session. No token copying
or browser debugging setup is required.

Inputs and write confirmation are checked before opening the browser. For a write,
add `--confirm-write OPERATION`, using the same operation name. Each session permits
one request to the selected API origin and method. Redirects are blocked, responses
are read without running their scripts, and no automatic retry occurs. Browser
requests and responses are limited to 8 MiB. Compatibility with a specific service
still requires authenticated verification.

For environments that already supply credentials, `--headers-stdin` accepts a JSON
object of temporary headers. Use it as an alternative to `--login`. Do not put
credentials in command arguments or source files. Use `--output NEW_FILE` for private
JSON results. If a request fails, check the service before retrying because a write
may already have succeeded. Complex inputs, custom scalars and multi-step workflows
may require the generated Python API or service-specific extensions.

## Dependency management

Poetry creates an ignored `.venv/` inside this repository. `poetry sync` reads
the committed `poetry.lock`; it installs the locked dependencies and the command,
and removes packages that are no longer in the selected dependency groups, without
opening a browser. `poetry run` uses that environment without requiring activation.
`poetry build` creates the wheel and source archive in `dist/`. If Poetry reports
that the lock is stale after an intentional dependency edit, run `poetry lock`
and review both `pyproject.toml` and `poetry.lock` before committing. To change
dependencies, use `poetry add PACKAGE` or `poetry add --group dev PACKAGE`.

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
network during execution. Playwright's bundled Node driver runs inside the
container. No production credentials are used.
These tests do not establish production service compatibility.
