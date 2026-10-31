# Delivery plan

## Review readability

- [ ] Name transport limits and time units, deriving the base64 limit from the
  decoded response limit. Separate request, response and cleanup responsibilities.
- [ ] Separate the HTTPS fixture, terminal driver and container scenarios. Expand
  compact statements and explain lifecycle and isolation decisions in comments.
- [ ] Run focused transport tests and the complete installed-client container flow,
  then push the review changes to the existing PR.

## Generated client browser login

1. [x] Include standalone Python browser-session support in generated packages.
   Add operation-level login options and keep help local and writes explicitly confirmed.
2. [x] Pass requests from upstream typed methods through the authenticated native
   browser connection. Keep credentials in memory, restrict the API origin, block
   redirects, prevent response scripts from running and perform no automatic retry.
3. [x] Generate and install both client formats, then test manual login, verification
   codes, authenticated reads, confirmed writes, cancellation and cleanup in Docker.
   Document the complete install-to-endpoint procedure and open a separate PR.

Verification covers 60 Python tests and 15 container scenarios. Generated clients
are installed in separate fresh environments with only their declared runtime
dependencies. Both formats complete browser login, verification codes, authenticated
reads and confirmed writes. Cancellation, browser closure and redirect rejection
preserve request counts and remove owned browser profiles.

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

Service-specific workflows belong in separate client extensions. Generated packages
own their browser-login support. See ARCHITECTURE.md for ownership and recovery.
