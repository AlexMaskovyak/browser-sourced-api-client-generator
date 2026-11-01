# Turn discovery results into a Python client

Use this pipeline to discover a website once, save its API definitions, then
generate a client later without logging in again. It also accepts API definitions
from another tool, so browser discovery can be one step in a larger build.

The handoff is a **discovery manifest**: one JSON file containing definitions,
supporting reports, content hashes and the API each definition belongs to.
`discover` saves it as `manifest.json`. Copying this file is enough to move the
results to another machine with this generator installed. The client it generates
is a separate Python package with typed API methods and a command-line interface.

```text
Browser discovery or another producer
                -> discovery manifest
                -> normalize: validate and combine definitions
                -> normalized manifest with API status
                -> generate: select one API and create its Python client
```

## Prepare the environment

Run from the repository root, where `pyproject.toml` is located. Use the existing
Poetry installation and a supported Python version as described in the
[installation instructions](../README.md#start-here-website-to-client).

```sh
poetry sync --with dev
mkdir -p captures clients
```

Browser discovery needs Chrome or Chromium and an interactive terminal. You sign
in and complete verification codes in a temporary browser. Normalization and
generation use local inputs and send no service API requests. Dependency
installation may need network access.

All output names below must be new, and their parent directories must exist.
Replace example website addresses with the actual website before using discovery.

## Discover, inspect and generate

1. Read the website and save its discovery results:

   ```sh
   poetry run website-client discover --url https://app.example.com --explore --output ./captures/walk
   ```

   Sign in, open the page where exploration should start, then press Enter in the
   terminal. The bounded walk records requests and declared forms. It saves
   `./captures/walk/manifest.json` and readable reports, then closes the browser.

2. Validate the saved definitions and write their generation status:

   ```sh
   poetry run website-client normalize --input ./captures/walk --output ./captures/walk/normalized.json
   ```

   The terminal lists API IDs and status. In `normalized.json`, `services` contains
   each API's `endpoint`, `status` and `issues`. `ready` means its definitions are
   structurally usable, `review_required` means inferred OpenAPI needs review,
   and `blocked` means definitions or operations are missing or invalid.

3. Create a standalone client from the only ready API:

   ```sh
   poetry run website-client generate --manifest ./captures/walk/normalized.json --name site-client --output ./clients/site-client
   ```

   If several APIs are ready, add `--service` followed by the chosen ID from step
   2. Generation creates a Python project and `generation.json` with input hashes
   and producer history. Follow [client installation and usage](../README.md#run-a-generated-client)
   to install it. Calling its API commands requires fresh runtime authentication.

You can also pass the saved manifest directly to `generate`; it normalizes first.
The original loose files and browser session are never required. Old captures
without a manifest can still supply definition files to `import` below.

## Inspect and select an API

After normalization, run this command from the generator repository root. It
prints just the API IDs, endpoint addresses, statuses and validation issues:

```sh
poetry run python - <<'PYTHON'
import json
from pathlib import Path

manifest = json.loads(Path("captures/walk/normalized.json").read_text())
for api in manifest["services"]:
    print(f"{api['id']}  {api['protocol']}  {api['endpoint']}  {api['status']}")
    for issue in api["issues"]:
        print(f"  {issue}")
PYTHON
```

An API ID identifies one endpoint and its assigned definitions. Use the ID from
this output, not a document filename or operation name. If several APIs are
ready, this command prompts for your choice and generates only that API's client:

```sh
printf 'Paste the API ID, then press Enter: '
read -r api_id
poetry run website-client generate --manifest ./captures/walk/normalized.json --service "$api_id" --name selected-client --output ./clients/selected-client
```

A blocked API cannot generate. Missing GraphQL schemas require a complete schema
from the service or another tool; missing operations require operation documents.
[Import or combine those inputs](#connect-a-different-producer), then normalize
again using a new output filename. A form inventory alone cannot fill those gaps.

## Install the generated client

After generating `site-client` in the procedure above:

```sh
cd clients/site-client
poetry install
poetry run site-client --help
```

Help lists the actual endpoint commands and whether each reads or writes. Use
one listed name with `--help` to see its required JSON fields and URL options.
That operation name and its input fields depend on the API you discovered, so a
universal executable service command cannot be supplied here. Follow
[generated client login](../README.md#log-into-a-generated-client) when ready to
send a real request. Each browser-authenticated operation opens a fresh login;
the discovery session is not retained. Return to the generator root before
running the pipeline commands below.

## Chain commands through stdin and stdout

This example performs the same work without saving the intermediate normalized
manifest. It assumes one ready API; otherwise add `--service` to generation.

```sh
set -o pipefail
poetry run website-client discover --url https://app.example.com --explore --output ./captures/piped-walk --emit-manifest |
  poetry run website-client normalize --input - |
  poetry run website-client generate --manifest - --name piped-client --output ./clients/piped-client
```

Sign in and press Enter in the first command's terminal. Only discovery reads the
terminal; subsequent commands read the pipe. Progress goes to stderr. Each handoff
contains one complete JSON document, not JSON Lines or an incremental stream.
`generate` writes a generation report to stdout and the client to its output directory.
Browser discovery cannot accept piped stdin; offline HAR import accepts `--har -`.
`--bundle` and `--emit-bundle` remain aliases for the manifest flags.

## Connect a different producer

If another tool emits OpenAPI, pipe its output to `import --openapi -`. GraphQL
needs a complete schema, operation documents for the methods to generate, and the
API's HTTPS endpoint. These inputs come from that tool or the service's API
documentation, not from a form inventory.

This runnable offline example uses the repository's sample OpenAPI definition:

```sh
set -o pipefail
poetry run website-client import --openapi ./tests/fixtures/openapi.json --endpoint https://api.example.com |
  poetry run website-client normalize --input - |
  poetry run website-client generate --manifest - --name sample-client --output ./clients/sample-client
```

Another tool can emit the manifest format directly. The independent
[create_discovery_manifest.py](../examples/create_discovery_manifest.py) example
uses only Python's standard library. Run it without third-party packages:

```sh
set -o pipefail
python -S examples/create_discovery_manifest.py --protocol graphql --schema tests/fixtures/schema.graphql --operations tests/fixtures/operations.graphql --endpoint https://api.example.com/graphql |
  poetry run website-client normalize --input - |
  poetry run website-client generate --manifest - --name independent-client --output ./clients/independent-client
```

Expected result: a project containing `GetItem` and `CreateItem` methods and CLI
commands. To implement another producer, follow the [manifest format reference](discovery-manifest-format.md).

To combine separate captures or add a schema obtained elsewhere, repeat
`normalize --input` with their manifest paths. A schema-only GraphQL import can
omit `--operations`; merging fills missing schemas only when API IDs match.
Default IDs derive from protocol and endpoint. Conflicting schemas stop merging.
Unassigned operations stay unassigned until the producer identifies their API or
you explicitly assign them with `--bind-operations SERVICE_ID=DOCUMENT_ID_OR_NAME`.
Schema assignment uses `--bind-schema` with the same syntax. IDs and document
names are in the manifest's `services` and `artifacts` arrays.

## Handle failure and restart

| Result | Meaning and next step |
| --- | --- |
| `normalize` exits 0 | The transformation succeeded. Inspect API status; it may still contain blocked APIs. |
| `generate` exits 0 | The selected API produced a local client. This proves neither live compatibility nor write completion. |
| Invalid JSON, version, hash or reference | The command exits nonzero. Repair the upstream input; generation publishes no client from that invalid input. |
| API is blocked or ambiguous | Read `services[].issues`, obtain missing definitions or supply the intended `--service`. |
| API needs review | Inspect the inferred OpenAPI, then use `generate --allow-inferred` only if accepted. |
| Existing output or cancelled discovery | Inspect any new partial output and rerun with a new path. Prior output is not overwritten. |

`pipefail` reports a failed command, but a pipe is not a transaction. A producer
can emit valid JSON and then fail; downstream generation may finish before that
failure is known. Inspect outputs after a failed pipe. For builds that must wait
for producer success, save each step and use `&&` to run the next only on success:

```sh
poetry run website-client import --openapi ./tests/fixtures/openapi.json --endpoint https://api.example.com --output ./build-input.json &&
  poetry run website-client normalize --input ./build-input.json --output ./build-contracts.json &&
  poetry run website-client generate --manifest ./build-contracts.json --name build-client --output ./clients/build-client
```

Replace the first command with your actual producer, writing its stdout to a new
file if necessary. Do not reuse an old input after a failed producer command.
Discovery remains bounded. A form declaration or blocked mutation does not become
a callable API definition through normalization. Missing definitions require
another source or explicit developer work; the pipeline cannot infer them fully.
