# Portable discovery bundles

A producer emits one UTF-8 JSON document. The consumer can read it from a file,
stdin or a directory containing `manifest.json`. It contains the evidence and
definitions needed for later processing; artifact names are labels, not paths to
load. No browser session is required for normalization or generation.

## Produce an independent bundle

The example producer uses only Python's standard library. `python -S` disables
site packages, demonstrating that the producer does not import this generator.
Run from the repository root after installing the tool with Poetry. These sample
definitions are included in the repository and make no service requests:

```sh
mkdir -p clients
set -o pipefail
python -S examples/produce_bundle.py --protocol graphql --schema tests/fixtures/schema.graphql --operations tests/fixtures/operations.graphql --endpoint https://api.example.com/graphql |
  poetry run website-client normalize --input - |
  poetry run website-client generate --bundle - --name independent-client --output ./clients/independent-client
```

Expected result: a standalone Python package and CLI with `GetItem` and
`CreateItem`, plus `generation.json` recording the producer and input hashes.
Use a new output directory for another run. If normalization rejects the input,
inspect the reported service's issues and source documents before retrying.

## Version 1 fields

The normative structural schema ships with the package at
[`discovery-bundle-v1.schema.json`](../src/website_client/schemas/discovery-bundle-v1.schema.json).
Consumers also check hashes, references, dependency cycles, byte limits and API
definition validity. Unknown versions and additional fields are rejected.

| Field | Meaning |
| --- | --- |
| `format` | Exactly `website-client-discovery`. |
| `version` | Integer `1`. |
| `stage` | `discovery` or `normalized`. Generation revalidates either stage. |
| `producer` | Producer `name` and `version`; a claim of origin, not authentication. |
| `createdAt` | ISO 8601 timestamp including timezone. |
| `artifacts` | Complete UTF-8 content, kind, identity, hash and provenance. |
| `services` | API endpoint, protocol and explicitly associated artifact IDs. |
| `sourceBundles` | Parent bundle digests, producer identities and capture times. |
| `normalization` | Policy version, unassigned contract artifacts and limitations. |

An artifact has `id`, `name`, `kind`, `content`, `sha256` and `provenance`.
Kinds are `report`, `har`, `openapi`, `graphql-schema` and `graphql-operations`.
The hash is lowercase SHA-256 of the exact UTF-8 `content` bytes. Artifact IDs must
be unique; names need not be unique across producers. Ambiguous name selection
requires the artifact ID. Provenance records `source`, `locations`, `derivedFrom`
artifact IDs and `producers`. Preserve originals when adding derived documents.

A service has `id`, `protocol` (`openapi` or `graphql`), `endpoint`,
`schemaArtifact`, `operationArtifacts` and `confidence`. `schemaArtifact` may be
null in incomplete discovery. GraphQL operation documents belong to a particular
endpoint, not a global pool. The default service ID is `service-` followed by the
first 16 hexadecimal characters of SHA-256 of `protocol + "\0" + endpoint`.
The endpoint is canonical HTTPS, with a lowercase authority and `/` for an empty
path; the default `:443` port, query values, embedded credentials and fragments
are excluded. Captured
numeric or identity path segments may be templates. Runtime client commands still
require their actual `--url`.

Confidence is `published`, `external`, `observed`, `declared` or `inferred`.
It does not establish live behavior or successful execution. A mutation may be
fully described yet remain unexecuted. Reports retain form declarations and
blocked request shapes without manufacturing a server schema from them.

## Normalization and selection

Normalization validates schemas, combines equivalent operation definitions and
validates GraphQL operations against their assigned schema. Conflicting operation
definitions block that service. Similar schemas are not evidence that an unbound
document belongs to an endpoint. Use explicit binding flags or have the upstream
producer supply the association.

Services become `ready`, `review_required` or `blocked`, with issues and operation
names. Inferred OpenAPI needs `--allow-inferred` at generation. Published schemas
supersede inferred samples for the same service; superseded evidence is retained.
Generation automatically selects a single ready service and reports other
services/unassigned artifacts in lineage. Multiple ready services require
`--service`. Readiness is structural, not a live compatibility claim.

Merge at most 20 inputs with repeated `normalize --input` arguments. Matching
service IDs must describe the same protocol and endpoint. Missing schemas can be
filled; conflicting published schemas or colliding IDs are rejected. Normalized
bundles can be normalized again without accumulating new transformations.

## Integrity and limits

Bundle digests use `website-client-canonical-json-v1`: sorted object keys,
two-space indentation, unescaped Unicode, standard JSON values and one final
newline, encoded as UTF-8. Array order is preserved. Artifact hashes always cover
their original exact content. `generation.json` records the selected service,
parent bundle hashes, raw and normalized schema hashes, operation artifact hashes
and the operation document actually passed to the code generator. It does not
include private walk reports or authentication state.

Bundles are limited to 64 MiB, individual artifacts to 32 MiB, definitions and
operation documents to 8 MiB, artifacts to 500 and services to 100. Merge inputs
share the 64 MiB budget. Credentials are not part of this exchange contract.
Browser capture sanitizes its evidence; external producers are responsible for
the content they supply. Import preserves that content rather than pretending to
sanitize arbitrary third-party data. A sanitized HAR alone cannot reconstruct
schemas or frontend declarations that the full bundle preserves.
