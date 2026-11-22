# Autonomous browser-sourced client integration

The tool will accept a URL or saved supported input and produce installable Python
clients for all unambiguous eligible services. Initial login is the only interactive
phase. This work implements the supplied November 15 specification; its architecture,
upstream owners and demonstrated capabilities are settled inputs.

## Baseline and ownership

Start at `fb218f4` in the separate `feature/autonomous-integration` worktree. Preserve
both reviewed worktrees. Do not merge the upload branch. Consume supplied evidence,
without repeating upstream comparison. Coordinator owns plan, architecture and
findings. One implementer owns application/test files per milestone. Independent
reviewer reviews fixed commits; adversary reviews design and runs isolated probes.

## Milestones and executable completion criteria

1. Private profiles, native files and authenticated dispatch. Non-persistent initial
   browser; origin-scoped credential capture; protected atomic profiles outside all
   outputs; maintained lock library covering load, entire call/job and save. HTTPX
   and requests adapters send upstream-built bytes through APIRequestContext.fetch.
   Enable Ariadne multipart; scoped native File/Upload conversion, 8 MiB bound.
   Verify four exact-byte uploads, aliases/omission/unknown objects, bearer/cookies,
   rotation, concurrency, origin isolation, expiry and closed-stdin subprocess calls.
2. Autonomous discovery/generation. Extend existing pipeline/traversal/packaging;
   defaults and guards before exploration; advertised OpenAPI/Arazzo; structured
   har2sdk 0.6.15 adapter; all ready services; schema-only Ariadne custom builders.
   Selectively apply `0775d8c` and regressions. Verify every specified input route,
   deterministic output, real positive coverage and blocked writes, privacy canaries,
   dependency errors, conflicts, offline no-target access and automatic exclusions.
3. Restricted workflows. Use Arazzo 0.9.6 public step API, supplied admission profile,
   dialect validators and immediate output/response guard. Verify happy sequence,
   missing/null output, malformed JSON, integer boolean, false criteria, bad pointers,
   file objects, serialization/path/origin rejection and committed-write disconnect.
   Journals must prove one request/effect, no retry and no dependent dispatch.
4. Standalone distribution. Build wheels, generated tests/notices/help/docs and
   checked-in container recipe plus one acceptance command. Fixture driver does only
   initial login; close stdin; finish discovery/browser closure/generation/install;
   run public Python methods and installed CLI in separate runtime container with
   mounted profile, no checkout/research PYTHONPATH/Node/browser executable. Verify
   expiration, artifact privacy and deterministic source/configuration.

Implement one milestone, run focused checks, commit coherently, independently review
and probe fixed revision, resolve confirmed findings and re-review affected changes.
Then run full relevant suite and clean acceptance. Unrun/failed required acceptance
remains incomplete. The latest user request authorizes pushing this branch and opening a draft PR.
Merges, real external service writes and messages to others remain unauthorized.

## Verification and boundaries

Use existing Poetry project/lock and isolated worktree environment. Shell Poetry
1.5.1 cannot read this project; use the isolated Poetry 2.5.1 installation. Baseline has pytest
but no configured lint/type checks. Record exact commands/counts and absence of
checks, never unsupported passing claims. Use `poetry run pytest -q`, focused files,
`poetry build` and milestone 4 acceptance command. Dependency registry downloads are
allowed; saved-definition generation cannot access targets. All mutation probes use
neutral local services. Findings live in `docs/autonomous-findings.md`.

## Status

### November 22: CDN-dependent page loading

The example run at delivery revision `6896b63` produced no client. Its saved report
contains 411 blocked cross-origin GET requests, including dashboard JavaScript,
one visited page, no controls, and six failed definition probes. These observations
do not establish that blocking scripts was the only cause.

1. Reproduce strict discovery blocking a page's external JavaScript with a neutral
   two-origin browser fixture. Require a generated client whose operation comes
   from the script-driven page, with no manual post-login navigation.
2. Permit browser-classified page assets through the read-only request policy.
   Keep undeclared API requests, writes, external navigation, credential capture,
   and frontend contract attribution within their existing boundaries.
3. Test redirects and negative request cases, run the complete host suite, update
   the usage/architecture explanation, and commit and push a separate correction PR
   based on `feature/autonomous-integration`. PR 12 remains the base implementation.
   A fresh authenticated example run remains required before claiming example support.
4. For unsupported discovery and failures after accepting a new output directory,
   also write a root README and a standalone Python
   client stub. Explain the evidence and recovery steps; the stub sends no requests,
   returns a nonzero unsupported result, and does not count as a generated service.
   Verify its help, ordinary invocation, privacy, and offline behavior.
5. Make the standalone local fixture depend on a second-origin script for GraphQL
   discovery. Exercise empty-site and invalid-input output through the installed
   generator, and assert README/Python assets for every supported input route.

Completion requires the regression to fail before the fix and pass afterwards,
actual generated-client execution, and server journals proving blocked requests
were not sent. The prior container result remains tied to `2b9c062`.

Completed in [PR #13](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/13),
based on PR 12. CDN correction: `73066a1`; failure output: `5e40b10`; local acceptance:
`9744024` and fixture-profile isolation `fdc6cea`. Host suite at `436bac9`: 389 passed
in 106.13s. Complete `scripts/test-docker.sh` at clean, unchanged `fdc6cea`: exit 0,
389 tests in 135.29s, HTTPS compatibility, CDN-dependent generation, empty-site and
invalid-input client assets, and separate installed runtime all passed. The compact
[verification record](docs/verification/cdn-client-assets.json) records exact revisions
and image IDs. These results do not establish live example compatibility.

Planning and independent adversarial design review completed at `032072a`.
Milestone 1 implemented at `35dc531`, corrected at `7d90c49` and independently
reviewed/probed with no remaining material M1 findings. Full suite: 227 passed.
Milestone 2 independently reviewed and completed at `d9d4d07`; full suite: 270
passed. Milestone 3 native guarded execution and the versioned JSON-null fix pass independent
probes. Typed source association and workflow distribution are implemented at
`f6072a1` and independently accepted after corrections at `a2037ab`. Fixed-archive
full verification: 347 passed in 87.57s. Independent reviewer correction checks:
30 passed; adversary correction checks: 5 passed. No material M3 findings remain.
The explicit quality refactor is independently accepted at `32ad5ea`; full suite:
323 passed. Milestone 4 completed and independently accepted at `2b9c062`.
`scripts/test-docker.sh` exited 0 with clean, unchanged source: 351 tests in
114.73s, the original HTTPS compatibility program, and standalone runtime
acceptance. Nine generated installed-package checks passed, 75 source/configuration
files matched, and 44 wheel hashes were verified. Host full suite at `d27827c`:
351 passed in 93.98s. No material findings remain unresolved.

Design clarifications: generated public Python calls need a default-profile factory
or context as well as CLI injection (D6). Automatic admission must preserve usable
operations when another operation cannot be generated upstream (D7), with explicit
coverage reasons rather than suppressed diagnostics. These are required tests.

## Reproduced native dependency incompatibility

Arazzo 0.9.6 sends an empty body for explicit JSON payload null. A neutral wire
test returned 422 where the schema allows null and the required bytes are `null`.
Nested null works. This remains a required case; an automatic exclusion is only
temporary protection, not completion. The permitted exception for reproduced
upstream incompatibility applies.

Build an explicitly versioned upstream dependency `0.9.6+websiteclient.1` with the
minimal native HTTPExecutor fix. Preserve omitted-body behavior. Store the small
patch, hash-pinned source/build provenance, rebuild procedure and Apache notices.
Use a prebuilt companion wheel and lock it in Poetry; no runtime source patching.
Ship the companion beside generator/workflow wheels and as a generator resource
for future packaging. Normal pip installation must offer both wheel paths. Inspect
actual METADATA for exact version requirements, with no workstation file URLs.
Fresh-container tests must verify installed version, native null/omitted/nested JSON
wire bytes, schema-disallowed null rejection and existing workflow regressions.
The independent adversary reviewed this dependency/distribution design and found
no material blocker subject to those packaging checks. The patch is applied at `b54e3e6` and its native wire behavior and portable generator
metadata are independently verified. Fresh sibling-wheel installation and actual
native version/wire checks passed in milestone 4 at `2b9c062`.

## Developer quality acceptance

The user's latest instruction makes reusable, understandable, open-sourceable code
an explicit completion criterion. Apply it to all integration code already added as
well as remaining work. Preserve the selected architecture and native API owners.

Before further implementation handoff:

1. Split autonomous orchestration into input, merge/quarantine, association,
   per-service generation, coverage and persistence responsibilities. Keep the
   public run API unchanged.
2. Add validated dataclasses at integration boundaries: selected operation metadata,
   source associations, file descriptors, execution/configuration summaries and
   registries where useful. Keep upstream documents as their standard mappings;
   do not duplicate OpenAPI, GraphQL, Arazzo or cookie domain models. Mark sensitive
   documents, headers, cookies, payloads and file paths as excluded from repr.
3. Share SDK/workflow profile session lifecycle code while preserving one driver,
   sorted whole-job locks, cookie persistence and cleanup on failures.
4. Separate workflow document/source/input/step/output admission, destination,
   parameter/body checks and native execution/result classification into focused
   helpers. Extract Pydantic/attrs conversion adapters from scalar dispatch.
5. Format introduced/touched code with conventional spacing, public type hints and
   concise rationale comments. Avoid long functions and dense one-line statements.
   Do not turn this into an unrelated repository-wide rewrite.
6. Commit coherent refactors after focused seam regressions, run the full relevant
   suite before push, then have the independent reviewer assess maintainability as
   well as correctness. Verify secret-safe repr, existing corrected cases and actual
   documented usage. No author approves their own work.

The reviewer identified concrete hotspots in autonomous.run, workflow admission and
execution, duplicated profile lifecycle, native file conversion and source report
parsing. These are bounded component refactors, not new architecture.

## Clean distribution acceptance procedure

The checked-in acceptance command must build separate generation and runtime
images. Build-time installation may use declared package registries. Actual
acceptance runs have no external network access and use only a neutral fixture
service. The fixture driver completes initial login and closes stdin; it must not
navigate, select contracts or repair inputs afterward.

The generation image includes Chromium, Python generation dependencies and the
pinned har2sdk generation cache. It must copy the local native companion wheel
before Poetry resolves dependencies. It produces source projects and wheels,
including a sibling native companion wheel for workflow installations. Preserve
the existing browser and compatibility acceptance checks.

The runtime image contains only Python, declared runtime dependencies, generated
wheels and the independent fixture assertions. It must have no generator checkout,
generator package, code generators, browser executable, separate Node/npm or
research PYTHONPATH. Use a separate writable private-profile directory mount with
matching non-root ownership. Cookie renewal needs atomic directory writes. Mount
portable output separately and prove it contains no credentials.

Acceptance must exercise actual installed public methods and console commands,
four native upload forms, the guarded workflow, literal null versus omitted JSON,
cookie renewal after browser closure, expired credentials with no prompt, and an
excluded serialization command with no dispatch. Compare fixture request/effect
counts and the committed 274-byte file hash. Run generated tests, inspect notices
and installed dependency versions, and compare deterministic source/configuration
apart from explicitly volatile metadata. Record image identifiers, commands,
results and wheel hashes in a machine-readable summary. Any failed assertion must
make the single acceptance command return nonzero.
