# Discovery manifest format

Use this reference to supply saved discovery results to the generator. A manifest
is a JSON document containing API definitions, source hashes and explicit service
associations. For ordinary URL or file usage, start with the [pipeline guide](discovery-pipeline.md).

## Version 1 fields

The [packaged structural schema](../src/website_client/schemas/discovery-bundle-v1.schema.json)
is authoritative. Consumers also check content hashes, references, dependency cycles,
byte limits and standard definition validity. Unknown versions and extra fields are
rejected. The standard-library [producer example](../examples/create_discovery_manifest.py)
shows how to create a complete manifest; the table below is a field reference.

| Field | Meaning |
| --- | --- |
| `format` | Exactly `website-client-discovery`. |
| `version` | Integer `1`. |
| `stage` | `discovery` or `normalized`; generation revalidates either. |
| `producer` | Producer name/version, recording a claim of origin rather than authentication. |
| `createdAt` | Timestamp including timezone; capture time is not collection-completeness evidence. |
| `artifacts` | Stored UTF-8 documents with kind, ID, content, hash and provenance. |
| `services` | Endpoint/protocol and explicit definition/operation document references. |
| `sourceBundles` | Parent manifest digests, producer identities and capture times. |
| `normalization` | Policy version, unassigned contract documents and limitations. |

Each stored document is called an `artifact` in the exchange format. Its fields are
`id`, `name`, `kind`, `content`, `sha256` and `provenance`. Kinds are `report`, `har`,
`openapi`, `graphql-schema`, `graphql-operations` and `arazzo`. The hash covers the
exact stored UTF-8 content. IDs must be unique; document names need not be unique
across producers. Provenance records source, safe locations, `derivedFrom` IDs and
producer identities. Refer to a document by ID when its name is ambiguous.

A service contains `id`, `protocol` (`openapi` or `graphql`), `endpoint`,
`schemaArtifact`, `operationArtifacts` and `confidence`. GraphQL can have a complete
schema with no named operations, or named operations with no complete schema. Those
are distinct generation routes. Documents belong to their declared endpoint, not
to a global pool inferred from similar fields or neighboring source code.

Default service IDs use the first 16 hexadecimal characters of SHA-256 over
`protocol + "\0" + endpoint`, prefixed with `service-`. Endpoints are canonical
HTTPS URLs; HTTP is accepted only on explicit loopback addresses for local services.
Query values, embedded credentials and fragments are excluded. Authority casing and
default ports are normalized and an empty path becomes `/`. Generated clients save
the endpoint and need no repeated `--url` for ordinary calls.

Confidence is `published`, `external`, `observed`, `declared` or `inferred`. It records
contract evidence, not successful execution or permission to perform a write.

## Normalization and automatic generation

Normalization parses named operations and fragments, checks variable declarations,
and validates against a complete schema when supplied. Schema-only GraphQL uses
Ariadne's native root builders and bounded default leaf selections; schema-less
operations keep narrower typing and validation. Unsupported subscriptions, ambiguous
symbols and essential sensitive structures receive exclusions.

The ordinary `run` command admits supported operations automatically and packages
all unambiguous usable services with separate namespaces. It records excluded
operations and conflicts in `coverage.json`. It neither asks for inferred-schema
review nor requires choosing among several usable services. Compatibility stage
commands still expose `ready`, `review_required` and `blocked` status and the legacy
`--service`/`--allow-inferred` controls. Those flags are not default-path prerequisites.

Standard merge requires a repeated service ID to retain the same protocol, endpoint
and compatible definitions. It rejects conflicting ownership. The autonomous path
quarantines affected services and retains independent ones, with explicit reasons.
An artifact ID cannot silently acquire a different contract.

## Reports and private runtime association

Reports preserve only supported safe structure and provenance. Unknown report
payloads can be omitted with an audit reason. Original and sanitized source hashes
record privacy transformations without retaining the raw content. Existing captured
GraphQL request metadata keeps safe method and query-name conventions; it contains
no copied request headers or query values.

A generated `runtime-associations.json` report maps explicit origins to stable
non-secret profile identifiers under `runtimeProfiles`. This lets a saved manifest
regenerate a client offline and retain its profile association. It contains no
cookies, tokens or profile file paths. Private credential state stays outside output
and installed package directories; another machine needs its own protected profile.

Arazzo documents keep their standard format. Restricted workflow admission/execution
and explicit source associations are handled by the workflow owner, not inferred from
manifest document proximity. Form declarations and blocked request shapes remain
evidence; they do not manufacture executable dependencies.

## Integrity and limits

Manifest digests use `website-client-canonical-json-v1`: sorted keys, two-space
indentation, unescaped Unicode, standard JSON values and one final newline in UTF-8.
Array order is preserved. Artifact hashes cover the stored safe content, while source
hash reports preserve transformation provenance. Generation reports record backend
versions, used definitions and operation coverage. Neither a hash nor successful
generation establishes live compatibility.

Limits are 64 MiB per manifest, 32 MiB per artifact, 8 MiB per definition/operation
document, 500 artifacts and 100 services. Merge accepts at most 20 inputs sharing the
64 MiB budget. Credentials are excluded from the exchange contract. All import and
output routes apply automatic privacy filtering; external producers should also avoid
supplying secrets. These heuristics cannot perfectly anonymize arbitrary unknown data.
