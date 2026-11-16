# Generate clients from URLs and saved inputs

Use `website-client run SOURCE --output DIRECTORY` for the ordinary workflow. It
connects the existing discovery, import, normalization and generation components
and processes all unambiguous usable services. The individual stage commands remain
available for existing producers and diagnostic work; they are not prerequisites.

## Prerequisites and inputs

Install the project through Poetry as described in the [README](../README.md#install).
URL discovery needs a browser and one initial sign-in. HAR inference needs Node 20+
and npm. Saved inputs need no browser. Supplied standard definitions and manifests
with converted executable contracts need no Node. A saved capture manifest that
still needs HAR inference requires Node/npm. Output directories must be new. Source
files are read without alteration.

An endpoint comes from a definition's server URL, an existing manifest, or the initial
`--endpoint URL` argument when the input cannot supply it. GraphQL operations come
from capture or an initial `--operations FILE`; a complete schema can generate through
native builders without an operation file. No mid-run schema edits are required.

## Run a saved definition

The following commands run from the repository root and use committed fixtures:

```sh
poetry run website-client run tests/fixtures/openapi.json --endpoint https://api.example.com --output ./rest-example
poetry run website-client run tests/fixtures/schema.graphql --endpoint https://api.example.com/graphql --output ./graphql-example
```

Both commands write local projects and reports, without contacting the configured
endpoint. The first uses OpenAPI. The second uses schema-only GraphQL builders.
Use new output paths if rerunning. If no supported operation remains, the command
returns a nonzero result and an unsupported report. An unsupported operation is
excluded rather than converted into a manual setup task.

To use named GraphQL operations instead of schema-only defaults, supply
`--operations tests/fixtures/operations.graphql` as an initial input. For a HAR,
pass its filename as SOURCE. A saved discovery directory supplies its manifest.
Repeat `--input FILE` when combining manifests or supplying bundled workflow sources.
Declared local workflow sources within the supplied bundle are loaded automatically.
Exact advertised source URLs enter the existing bounded discovery queue. Missing or
conflicting sources exclude the affected workflow without selecting a nearby file.

This additional offline example uses the committed three-step workflow and its
declared sibling OpenAPI file:

```sh
poetry run website-client run tests/fixtures/workflow/workflow.json --name workflow-example --output ./workflow-example
```

Expected result: a service project under `services/` and a workflow project under
`workflows/workflow-example-workflows/`. Generation sends no request to the fixture's
declared destination. This is a packaging example; executing it requires a matching
service and private login profile, which the offline command does not create.

## Read coverage and install packages

`coverage.json` records generated projects, source provenance and automatic exclusion
reasons. Separate service projects are under `services/`; their names are deterministic.
Install each project you need with `python -m pip install PROJECT_DIRECTORY`, then use
that project's console command with `--help`. Installation may download declared
runtime dependencies. Help sends no service request.

Workflow projects also need the included native companion wheel. Their README gives
the command that supplies both that wheel and the project to pip. When distributing
built wheels, supply both sibling wheel paths. A nested wheel resource alone cannot
satisfy pip's dependency resolution. `coverage.json` identifies the workflow package
under `workflowPackage`; its console help lists executable workflow IDs.

Coverage reports describe available evidence. A successful generation is not live API
verification, and a complete report is not a claim that bounded browser traversal
found every operation. Conflicts block only unresolved services or methods; usable
independent services remain packaged. Installed clients read endpoint/profile defaults
and run with stdin closed. Their generated READMEs show exact imports and file inputs.

## Keep credentials separate

URL discovery persists selected origin-scoped login state in a private local profile,
outside every output directory. Generated configuration contains only the endpoint,
origin and profile identifier. Cookie renewal is automatic. The full load, call/job
and save sequence is locked so concurrent processes cannot overwrite rotated cookies.

A saved manifest or wheel is portable code and contract evidence, not portable
credentials. Another machine must provision its own protected profile through login.
Authentication rejection terminates with `authentication_required`, without asking
for headers, MFA or agent help. To recover, rerun the original URL command with a new output directory and sign in.
The stable origin profile refreshes credentials for existing installed clients.

## Use individual stages when integrating another producer

These are advanced compatibility commands. They read saved inputs and write local
outputs. They do not make business API calls. For example, create and normalize a
manifest from the committed OpenAPI fixture:

```sh
mkdir -p generated
poetry run website-client import --openapi tests/fixtures/openapi.json --endpoint https://api.example.com --output generated/imported.json
poetry run website-client normalize --input generated/imported.json --output generated/normalized.json
poetry run website-client generate --manifest generated/normalized.json --name sample-client --output generated/sample-client
```

The first command creates the parent directory for these local outputs. Expected result: a manifest, normalized manifest and
one standalone project. An already existing destination is an error and is preserved.
For multiple services, the compatibility `generate` command accepts `--service ID`;
use `run` to package all usable services automatically. Inferred-review flags on the
legacy stage interface are not required by `run`.

`discover --har FILE` imports an existing HAR. `discover --url URL --explore` retains
the staged browser-discovery interface. `import --graphql-schema FILE --endpoint URL`
accepts complete SDL/introspection; `import --operations FILE --endpoint URL` imports
named schema-less operations. `normalize --input FILE` can be repeated for manifests.
Each command's `--help` lists its exact arguments and limits.

For shell pipelines, use `set -o pipefail` and remember that a downstream command can
publish output before a producer fails late. Staged files make failure boundaries
clearer. Manifest/stdin interfaces exchange complete JSON documents, not JSON Lines.
Only the initial browser login reads terminal input; saved-input stages do not.

## Handle exclusions and failures

| Result | Meaning and next action |
| --- | --- |
| `complete` | All eligible supplied/discovered evidence was processed; install generated projects and invoke only intended operations. |
| `partial` | Usable projects remain; inspect recorded automatic exclusions to understand coverage. No setup dialogue follows. |
| `unsupported` | No executable operation remains; command exits nonzero and explains why. |
| HAR dependency failure | Install the stated Node/npm prerequisite, then rerun with a new output. Definitions can still generate without HAR conversion. |
| `authentication_required` | Execution stopped without retry; explicitly log in again before a later attempt. |
| Uncertain request failure | A write may have succeeded. Check the service before deciding to retry. |
| Local output failure | Publication or saving failed; do not infer that a preceding remote request failed. Inspect partial local output before reusing a path. |

Non-exploded form array queries, unsupported complex encodings, unresolved remote
references, ambiguous contracts and essential sensitive structure are excluded
without schema rewriting. Workflows require explicit supported documents and value
mappings. No dependency is inferred from source proximity, request order or matching
values. Privacy filtering is conservative and cannot detect every unknown secret.

For producer fields, use the [manifest reference](discovery-manifest-format.md).
For component and credential ownership, use [architecture](../ARCHITECTURE.md).
