# Browser-Sourced API Client Generator

Generate standalone Python clients from a website or saved API definitions. For a
website, sign in once in the browser. The tool then explores within fixed limits,
selects supported operations and packages every usable service without more prompts.
Installed clients run from Python, their console commands or a scheduler. No agent
or model API is required.

This branch is under active implementation in [draft PR #12](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/12).
The authenticated runtime, autonomous service generation and packaged workflow
execution pass local tests and independent review. Clean-container distribution
acceptance remains in progress.

## Install

Use Python 3.11 or newer and Poetry 2.2 or newer. Run from the repository root:

```sh
poetry sync --with dev
poetry run website-client --help
```

Installation downloads locked Python dependencies. URL discovery also needs Chrome
or Chromium. HAR inference needs Node 20 or newer and npm at generation time; the pinned
har2sdk dependency is installed automatically into a local cache. Generation from
supplied definitions does not need Node. Installed clients need HTTPX and Playwright's
packaged request driver, but no browser executable or separately installed Node.

If installation fails, correct the reported dependency or Python version, then rerun
installation. Neither command signs into a website or calls its business APIs.

## Generate from a website

The following is an example. Replace the URL with the website you intend to inspect
and choose a new output directory:

```sh
poetry run website-client run https://app.example.com --output ./site-clients
```

Sign in in the temporary browser, including MFA, then confirm login once in the
terminal. The remaining discovery and generation need no direction. The browser
closes after capture. Discovery follows bounded read conventions and does not submit
forms, upload files or execute discovered mutations. A server can still attach side
effects to a read; a bounded walk does not prove a website is free of side effects.

The output contains a sanitized manifest, `coverage.json`, and separate projects
under `services/`. The report maps service identifiers to their package paths and
records automatic exclusions. `complete` means the supplied or discovered evidence
was processed, not that every website operation was found. `partial` preserves usable
clients and explains missing coverage. `unsupported` means nothing executable was
produced and exits nonzero. Existing outputs are never overwritten.

Credentials are saved separately in a protected local profile. Generated projects
contain an endpoint and profile identifier, never that profile's credential file.
Do not copy credentials into a generated project. Another machine needs its own
protected profile and login.

## Generate from saved inputs

Saved-input generation runs without opening the website. This runnable example uses
the repository's OpenAPI fixture and writes a new local output:

```sh
poetry run website-client run ./tests/fixtures/openapi.json --endpoint https://api.example.com --output ./sample-clients
```

No request is sent to `api.example.com`. The endpoint is configuration for later
execution. Use the service's actual endpoint when generating a client you intend
to call. An initial `--endpoint` is needed only when the input lacks that fact.

| Input | Processing |
| --- | --- |
| HAR 1.2 JSON | Safe traffic structure through har2sdk, then native OpenAPI Python Client. |
| OpenAPI 3.0/3.1 JSON or YAML | Native REST methods and models; converted upstream OpenAPI uses the same path. |
| GraphQL SDL or complete introspection | Ariadne named-operation generation when `--operations FILE` is supplied; otherwise native builders with bounded default leaf selections. |
| Named GraphQL operations without a schema | Existing operation compiler with declared-variable validation and narrower response typing. |
| Saved discovery manifests | Existing readers and merge rules; all unambiguous usable services are packaged. |
| Arazzo plus bundled OpenAPI | Eligible individual methods and explicitly mapped linear workflows receive standalone projects. |

Repeat `--input FILE` for additional initial manifests or bundled definitions.
Unsupported references or encodings exclude affected operations automatically.
Saved inputs never cause remote-reference fetches. Definitions with no executable
operations finish with an unsupported report, rather than asking for setup repair.

## Install and call a generated client

The coverage report identifies each generated project. Install the desired project
with `python -m pip install /path/to/project`, then run its named console command
with `--help`. These steps install local code and display local help; they send no
business request. Help gives the actual operation names and inputs.

For a call, supply ordinary input arguments or a JSON input file. File inputs use a
path plus optional filename and content type. Writes retain the existing
`--confirm-write OPERATION` flag; there is no interactive confirmation. The saved
endpoint/profile defaults are loaded automatically. A scheduler uses the same
command with stdin closed. See the generated README for its exact package imports,
file example and public `client_context()` API.

The file and complete encoded request bound is 8 MiB. Uploads use upstream native
File/Upload types and serializers; streaming uploads are not advertised. Cookies
renew automatically and a per-profile lock covers a complete call or Python job.
Authentication rejection ends with `authentication_required` without prompting.
To recover, rerun the original URL command with a new output directory and sign in
explicitly. Its stable origin profile renews credentials used by previously installed
clients. Application-specific token refresh or new MFA cannot be inferred.

Failures never trigger an automatic retry. A disconnect or output-file failure can
follow a successful remote write. Inspect the service before deciding to repeat an
uncertain write; the tool neither rolls it back nor claims it failed remotely.

## Install a generated workflow package

When explicit supported workflows are present, `coverage.json` includes
`workflowPackage.directory`. That directory contains its own README, standalone
project and companion native dependency wheel. Install both together from that
directory using its documented `python -m pip install ./arazzo_runner-0.9.6+websiteclient.1-py3-none-any.whl .`
command. The companion fixes a reproduced upstream JSON-null serialization defect;
its patch, source hash and license notices are included.

The workflow console command lists actual workflow IDs in `--help`. It accepts an
ordinary JSON input file and repeated `--file NAME=PATH` binary inputs. The generated
Python package exports `execute_workflow(workflow_id, inputs=None)`. Both use fixed
bundled destinations and private profile defaults without prompts. Invoking a
workflow authorizes its declared steps, which may write remote data. No automatic
retry occurs. The generated README explains the result fields and exit codes.

## Advanced commands and implementation

`discover`, `import`, `normalize` and `generate` remain available for existing
pipelines. Their staged interface is optional. Start with [the pipeline guide](docs/discovery-pipeline.md)
for command-specific inputs and compatibility flags. The [manifest format](docs/discovery-manifest-format.md)
is a producer reference, separate from everyday usage. [Architecture](ARCHITECTURE.md)
records component ownership, credential boundaries and failure behavior.

Privacy filtering removes credentials, raw samples and known sensitive examples or
structure before persistence. It cannot perfectly anonymize arbitrary unknown input.
Destructive filtering is reported, and essential ambiguity excludes an operation.
Runtime results intentionally returned to callers are separate from saved discovery.

## Verify changes

```sh
poetry check
poetry run pytest -q
poetry build
```

These commands validate the project, run local tests and build distributions. The clean-container acceptance command is still being implemented. Unit or fixture success does not establish compatibility with an
arbitrary production website.
