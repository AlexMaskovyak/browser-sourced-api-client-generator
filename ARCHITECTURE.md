# How the generator is built

The tool collects a website's requests and operation documents and turns them into a standalone
Python client. For commands, start with the [README](README.md). This document
explains where the work happens in the code.

## Data flow

```text
Temporary browser session ─┐
Existing HAR file ─────────┼─> discovery manifest JSON
External API definitions ─┘             |
                                   normalize
                                       |
                              select one API
                                       |
                                    generate
                                       |
                            standalone Python client
```

The manifest is the saved handoff between discovery and generation. It contains
operation documents, optional definitions, supporting reports, endpoint associations
and content hashes.
Later steps can run from that file alone, without the browser or original capture
directory. Its [format reference](docs/discovery-manifest-format.md) defines the
versioned JSON contract.

## Components

Paths below are relative to `src/website_client/`.

| Component | Responsibility |
| --- | --- |
| `cli.py`, `pipeline.py` | Parse commands and connect discovery, import, normalization and generation. |
| `browser.py` | Open a temporary browser through Playwright's Python API; close its session and profile. Playwright supplies the underlying browser driver. |
| `capture.py`, `discovery.py` | Collect browser requests or import HAR traffic; extract routes, sample shapes and encountered definitions. |
| `exploration.py`, `exploration_policy.py`, `contracts.py` | Walk pages and controls within explicit limits; classify requests and record declared forms and supported frontend operations. |
| `self_discovery.py` | Look for published API definitions and request GraphQL schema introspection where applicable. |
| `bundle_capture.py`, `discovery_manifest/` | Build the manifest; validate its version, hashes and references; read/write files and streams; combine producer inputs. |
| `normalization.py`, `definitions.py` | Normalize validated, immutable dataclasses; check API definitions and assigned operations, preserve unresolved evidence, and calculate which APIs can generate. |
| `operation_contracts.py`, `operation_requests.py` | Parse operation/variable contracts and preserve captured request methods and URL parameter conventions. |
| `operation_generation.py`, `operation_runtime.py` | Generate callable methods without a schema; send bounded requests and enforce variable constraints and write permission. |
| `generation.py`, `templates/` | Run the upstream code generators and package their output with endpoint commands and authentication support. |
| `client_cli.py`, `session_http.py` | Supply the generated client's command interface and browser-authenticated HTTP transport. |

Project code is Python. Poetry manages dependencies and builds. Generated packages
have their own dependencies and can run independently of this repository.

`discovery_manifest/records.py` supplies immutable dataclasses throughout capture,
import, merging, normalization and generation. JSON is parsed or serialized at
input/output; standard OpenAPI and HAR documents keep their native formats.
Construction and replacement check field types and schema rules. Document hashes
and manifest references are also validated. Nested collections are tuples;
changes create new records rather than mutating producer data.

## HTTP discovery and supported API definitions

| API | Generation input | Upstream generator |
| --- | --- | --- |
| REST/HTTP | Published OpenAPI 3.0/3.1, or inferred OpenAPI accepted after review. | `openapi-python-client` |
| GraphQL | Named query/mutation documents; complete schema optional. | Operation-document client, or Ariadne Codegen when a schema is available. |

Both use the same manifest and commands. Other contract formats need additional
readers and generators. Observed routes and form fields alone may be insufficient
to generate a usable client.

During automatic exploration, request checks permit ordinary GET/HEAD, OPTIONS
and recognized GraphQL queries, subject to navigation and action checks. Unknown
POSTs and write methods are blocked; forms are inventoried without submission.
Consequently a REST search using POST can be missed. OpenAPI does not currently
control request authorization. This policy belongs to `exploration_policy.py`.

## Authentication and service changes

The website remains authoritative for authentication and business records. A
human signs in through the temporary browser. Credentials remain in that browser
or process memory; discovery exports sanitized evidence, not a reusable login.

Discovery and client execution are separate sessions. A generated client's
`--login` operation opens a fresh browser. Its HTTPX adapter sends the typed
method's request through that session, scoped to the selected API origin and
method. Writes require explicit command confirmation. Redirects are not followed
and failed requests are not retried automatically. Additional session header names
are explicit command inputs. The adapter observes their values only for the API
origin and holds them in memory. A caller sharing an existing browser session can
supply the same bounded header selection without transferring browser ownership.
HTTP rejection diagnostics report status codes without response bodies or tokens.

Generated operation methods document the captured operation name, declared variable
types, omission behavior and mutation permission. The generator supplies these
from compiled contracts; it does not invent business descriptions from names.
Shared runtime docstrings explain HTTP client ownership and failure behavior.
Identical saved manifests and generator versions must produce identical output.

## Failure and recovery

A valid manifest can contain blocked APIs. Normalization records their issues;
generation revalidates and requires one selected, usable API. Conflicting inputs
are rejected or leave that API blocked rather than silently choosing a contract.

Commands preserve existing output. On failure, inspect any newly created partial
output and retry with a new path. Browser cleanup runs on normal exit and
cancellation; forced termination can leave temporary profile files. For an
uncertain service write, check the service before retrying.

The shell sequences commands. Pipes carry complete JSON documents, so an upstream
failure after emitting valid JSON cannot undo downstream generation. Use staged
files and `&&` when each command must succeed before the next starts. Runnable
examples and recovery steps belong in the [pipeline guide](docs/discovery-pipeline.md).

Unit tests exercise parsing and policies. Docker installs the built wheel and
checks browser login, discovery, command chaining and both generated client types
against a local HTTPS fixture. Compatibility with a real service requires its
own verification.

## Complete project ownership

`project_generation.py` owns the version 1 project definition and complete build.
It reads and merges bounded portable manifests, normalizes them, selects one
API and invokes the existing generation dispatcher in staging. REST/OpenAPI,
schema-backed GraphQL and operation-document GraphQL keep their existing request
builders and model ownership. An inferred HTTP contract requires explicit review
permission, which is retained in the versioned project definition.
It then generates documentation, synthetic tests, runtime defaults and portable
rebuild inputs. An explicit Poetry lock is verified before the tree is published.
Failure leaves the requested output absent; existing output is never replaced.

`client_cli.py` applies packaged endpoint, login URL and header-name defaults only
at that endpoint. Explicitly selecting a different endpoint does not reuse those
login defaults. Credential values are observed during future login, never stored
in the project file. Rebuild inputs preserve operation/request contracts rather
than unrelated capture context. No service-specific packaging or post-build
source edits are required.

## First-page data discovery

`SiteExplorer` retains the authenticated tab so tab-scoped state survives setup.
It resets script execution, waits for hydration and request activity, and exercises
empty read-only search controls before global navigation. This exposes requests
for lists whose initial rows were embedded in HTML. Source inspection includes
HTML and scripts actually loaded for the owned page across asset origins; unrelated
scripts remain excluded. Per-source, aggregate and source-count limits are fixed.

`Discovery` preserves typed Boolean defaults and bounded pagination literals while
withholding opaque data literals. Withheld documents carry an explicit reason.
Captured request/response evidence, rather than a declared frontend operation alone,
assigns a document to its API endpoint. Client generation remains a separate stage.

The page-data container scenario starts with rendered records, a tab-scoped view
selection and a script on an asset origin. Only exercising search produces the
list request. The test generates and independently installs a client from that
capture, calls the generated list method and checks the returned record/status.
