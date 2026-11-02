# Current work

Build complete Python client projects for every supported API backend through the
same command: REST/OpenAPI, GraphQL with a schema, and captured GraphQL operations.

1. Dispatch complete builds through the existing generation pipeline. Preserve
   selected definitions, inferred-contract review permission and runtime defaults
   in portable rebuild inputs. Keep protocol-specific request code in its backend.
2. Generate accurate README examples and synthetic tests for each backend, without
   inventing response/input models or treating writes as reads.
3. Verify complete output, exact rebuilding, CLI defaults and generated checks for
   all three backend paths. Run shared tests and clean-container scenarios, then
   update the PR around the complete behavior.

Complete means one build command owns each installable project. It does not imply
complete website coverage or permission to execute live writes. Failure preserves
existing output and publishes no partially enriched project.

## Verification

The shared dispatcher builds all three supported backend paths. Full suite:
215 tests. Clean Docker: 215 tests and 28 installed-command scenarios. REST,
schema-backed GraphQL and operation-document projects install without edits,
pass their generated tests and reproduce exactly from shipped inputs. CLI request
tests exercise actual backend conversion/models with a synthetic HTTP transport.
Inferred HTTP definitions retain explicit review permission across rebuilding.
No live business operation was executed.
