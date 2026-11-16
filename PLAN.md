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
remains incomplete. No PR, merge, external write or message is authorized.

## Verification and boundaries

Use existing Poetry project/lock and isolated worktree environment. Shell Poetry
1.5.1 cannot read this project; provision Poetry 2.2 separately. Baseline has pytest
but no configured lint/type checks. Record exact commands/counts and absence of
checks, never unsupported passing claims. Use `poetry run pytest -q`, focused files,
`poetry build` and milestone 4 acceptance command. Dependency registry downloads are
allowed; saved-definition generation cannot access targets. All mutation probes use
neutral local services. Findings live in `docs/autonomous-findings.md`.

## Status

Planning and independent adversarial design review completed at `032072a`.
Milestone 1 assigned to the implementer. Implementation review and acceptance pending.

Design clarifications: generated public Python calls need a default-profile factory
or context as well as CLI injection (D6). Automatic admission must preserve usable
operations when another operation cannot be generated upstream (D7), with explicit
coverage reasons rather than suppressed diagnostics. These are required tests.
