# Delivery plan

1. [x] Capture browser traffic after login and import existing HAR files through
   the same bounded analysis. Export a sanitized HAR and endpoint evidence.
2. [x] Validate observed OpenAPI documents and GraphQL schemas. Keep inferred
   OpenAPI and captured GraphQL operations separate from complete service schemas.
3. [x] Generate standalone Python packages through openapi-python-client and
   Ariadne Codegen, with typed methods, endpoint commands and input help.
4. [x] Verify authenticated capture, offline import, real code generation and
   installed clients in a clean container.

## Boundaries and completion

Python owns browser capture, analysis and orchestration. Upstream generators own
typed endpoint source. Generated clients are independent packages. Authentication
stays transient; capture never replays requests. Existing output is preserved.

Run focused tests before each implementation commit and the full suite before
handoff. Completion requires Poetry validation, unit tests, package builds and
the container scenarios to pass. Container tests exercise a local HTTPS service
and establish installation and behavior within that environment. Compatibility
with another service requires its own authenticated verification.

Service-specific workflows and authentication helpers belong in separate client
extensions. See ARCHITECTURE.md for component ownership and recovery behavior.
