# Delivery plan

## Portable discovery and normalization

1. [x] Define a versioned, self-contained JSON bundle and public JSON Schema. Preserve
   every discovery file with its hash, producer and provenance. Record endpoint
   associations when evidence is collected, not from numbered filenames later.
2. [x] Normalize and merge bundles into endpoint-specific contracts. Validate schema
   and operation documents, preserve unresolved declarations and reject conflicts.
   Let an external schema producer complete a capture with a missing schema.
3. [x] Add file/directory/stdin inputs and stdout outputs: discovery export, standard
   definition import, normalization and generation from bundles. Preserve legacy
   file-based generation. Record bundle, schema and operation hashes in clients.
4. [x] Test malformed versions, hashes, ambiguous APIs, conflicting operations,
   inferred definitions and file/pipe equivalence. In Docker, copy only a walk's
   manifest and generate/install clients from it. Exercise an independent producer
   and a browser-to-normalize-to-generate pipe. Document the executable pipeline.

Completion requires the focused tests, full suite, Poetry checks, builds and clean
container scenarios to pass, followed by a separate PR based on exploration PR #7.
Normalization must not invent a backend contract from an unverified form report.

Verified: 154 Python tests pass locally and in Docker, with Poetry lock validation
and source/wheel builds. All 21 installed-command container scenarios pass. Both
client formats are generated, installed and exercised using only a copied manifest
after deleting the original walk directory. A standard-library producer runs with
`python -S` through normalize/generate pipes. A real temporary browser login and
full exploration walk pipe through normalization into generation. These are local
HTTPS fixture checks, not production compatibility or whole-service coverage.

## Algorithmic site exploration

1. [x] Traverse same-origin pages, navigation menus, tabs and pagination in a stable
   order with page, depth, action and time limits. Use Playwright only, no model.
2. [x] Inventory forms and their declared fields/methods. Extract GraphQL documents,
   including mutations, from loaded frontend modules. Distinguish declarations,
   observed requests and blocked writes. Do not submit forms to learn contracts.
3. [x] Capture traffic throughout exploration, then run existing schema discovery.
   Block API mutations, unknown POSTs, WebSockets and off-site document navigation
   during exploration. Disable service workers and replace the login tab first.
4. [x] Verify navigation, menus, pagination, contracts, bounds and blocked writes in
   unit tests and the installed CLI in Docker. Generate clients from results,
   update usage/limitations and open a separate PR.

Complete when an installed command discovers endpoints and write declarations
that are absent from the starting page, reports its coverage and omissions, and
passes the full regression suite. The algorithm does not prove all UI is covered
or that declared forms map directly to the application's actual API calls.

Verification: 121 Python tests pass locally and in Docker. All 19 installed-command
container scenarios pass. The exploration fixture reveals pages behind a menu,
tabs and pagination, inventories upload fields, extracts a declared mutation and
blocks page-script and worker writes. Both generated client formats run in fresh
environments. The GraphQL client uses the schema and operation documents emitted
by exploration, including the discovered mutation; confirmed writes are tested
only against the local fixture. Poetry lock validation and source/wheel builds pass.

## Automatic schema discovery (second PR)

1. [x] Observe authenticated page reload traffic, inspect schema links and check a
   bounded list of common definition paths. Introspect only observed GraphQL
   endpoints or an explicitly supplied endpoint. Reuse the Playwright session.
2. [x] Keep credentials transient and scoped by API origin. Only schema GETs and the
   tool's own read-only introspection query are sent. Report every probe outcome,
   limits and missing coverage. Preserve manual capture and offline HAR import.
3. [x] Validate downloaded definitions with the existing standards validators and
   use them with the existing client generators. Test scope, omissions, disabled
   introspection and the installed end-to-end flow in Docker.

Complete when an installed CLI can log in, discover real OpenAPI and GraphQL
schemas without a second terminal confirmation, and generate working clients
from them. This PR depends on the Playwright migration in PR #5.

Verified: 84 Python tests pass locally and in Docker. All 17 installed-command
container scenarios pass, including automatic OpenAPI/GraphQL discovery and
disabled introspection. Both generated client formats use automatically discovered
definitions and run in fresh runtime environments. Poetry lock validation and
source/wheel builds pass. The fixture counts GraphQL writes from the parsed
operation kind, so introspection's `mutationType` field cannot count as a write.

## Playwright migration (separate PR)

1. [x] Replace the custom browser process and WebSocket protocol implementation with
   Playwright's Python API. Preserve temporary profiles, manual login, scoped
   capture and browser-native endpoint requests.
2. [x] Keep Chrome-specific request interception behind Playwright CDP sessions.
   Playwright owns transport, event dispatch, tabs and process shutdown. Python
   owns request scope, credential handling and the one-request policy.
3. [x] Update standalone generated dependencies and installation instructions. Run
   focused lifecycle and transport tests, then all tests, builds and Docker flows.

Complete when installed root and generated CLIs pass the existing login, capture,
read/write, redirect and cleanup scenarios. Self-discovery is a second PR and
does not change behavior in this migration.

Verified with Poetry's locked dependency check, source and wheel builds, 63 Python
tests locally and in Docker, and all 15 installed-command container scenarios.
Standalone OpenAPI and GraphQL clients run in fresh environments with only their
declared runtime dependencies. Navigation timing regressions now have tests.
The container wheel cache is built before copying project source so code edits
do not trigger another download of Playwright's bundled driver.

## Code readability

- [x] Separate generation by backend and move generated package text into templates.
  Give the CLI named steps for parsing, validation, model conversion, execution
  and output, with explicit limits for JSON files and runtime headers.
- [x] Verify the refactor with generated-client tests and the full container flow.

- [x] Name transport limits and time units, deriving the base64 limit from the
  decoded response limit. Separate request, response and cleanup responsibilities.
- [x] Separate the HTTPS fixture, terminal driver and container scenarios. Expand
  compact statements and explain lifecycle and isolation decisions in comments.
- [x] Run focused transport tests and the complete installed-client container flow,
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

The previous implementation covered 65 Python tests and 15 container scenarios. Generated clients
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
