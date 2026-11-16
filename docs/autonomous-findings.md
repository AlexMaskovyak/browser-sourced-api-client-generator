# Integration findings

Source-inspection design findings are distinct from demonstrated execution failures.
Each material finding records impact, reproduction/basis, disposition and evidence.

| ID | Location and basis | Impact | Disposition | Verification |
| --- | --- | --- | --- | --- |
| D1 | self_discovery observer, source inspection | Headers can cross origin on capture. | Explicit selected origins before persistence. | Pending M1. |
| D2 | browser/session_http, source inspection | Persistent browser and one-call transport cannot run unattended jobs. | Non-persistent login and locked profile APIRequestContext. | Pending M1. |
| D3 | imports/definitions, supplied canaries and source | Sensitive structural values escape scalar-only filtering. | Privacy checks before every persistence route; exclude essential sensitive structures. | Pending M2. |
| D4 | normalization/pipeline, source inspection | Manual review, single service and operations-required blockers. | Automatic admission, all ready services and Ariadne builders. | Pending M2. |
| D5 | definitions, source inspection | Whole-document remote-ref failure loses usable methods. | Bounded same-origin discovery resolution, offline per-operation exclusion. | Pending M2. |

Design review and implementation evidence will be appended at fixed revisions.

## Design review of 2067028

- D6, `client_cli.enter_generated_client`: current injection is CLI-only. A saved
  profile/default endpoint must also be available through a documented generated
  Python context/factory, with native upstream return values. This is an integration
  requirement, not evidence of a completed runtime. Add installed public-API tests.
- D7, `generate_openapi_project`: upstream `--fail-on-warning` may discard usable
  methods along with one unsupported operation. Automatic admission must exclude
  unsupported operations, preserve diagnostics and generate remaining usable methods;
  do not suppress warnings and claim complete coverage. Test mixed supported and
  unsupported definitions against installed output.

Coordinator disposition: D1-D7 are required milestone criteria, not optional risks.
Public Python profile support is M1 and automatic mixed-operation admission is M2.
The architecture remains the supplied upstream architecture.

## Baseline verification

- Worktree base: `fb218f4`, clean main and preserved feature worktree at `682d28c`.
- Installed worktree-only Poetry 2.5.1 under ignored `work/poetry-env`; selected
  Python 3.12.14. Neither existing environment was changed.
- `work/poetry-env/bin/poetry install --with dev`: succeeded from existing lock.
- `work/poetry-env/bin/poetry run pytest -q`: **205 passed in 10.89 seconds**.
- No project-configured lint or type-check command exists at baseline. Ruff is an
  upstream dependency, which does not establish a project lint policy.
- Docker engine 27.4.0 is available. Existing cached images are not clean acceptance
  evidence for this branch.

## Independent design review closure

Adversary reviewed `2067028` and the coordinator recorded D6/D7 in `032072a`.
No additional material design blockers were reported. Planning/adversarial design
review is complete before application changes. D1-D7 remain implementation and test
obligations. Required probes include two-process profile rotation, no replay on
request/profile-save failure, public Python default-profile use and mixed-operation
partial coverage. No product acceptance has yet been claimed.

## Milestone 1 review at 35dc531

Implementation commits: `ba5d94a` (profiles/dispatch) and `35dc531` (native files and
standalone Python/CLI integration). Author focused checks: profile/browser/session
43 passed in 0.46s; native/generated checks 27 passed in 18.20s. Full
`work/poetry-env/bin/poetry run pytest -q`: 221 passed in 21.39s.
Independent reviewer reran the six focused files: 70 passed in 17.85s.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F1 | `authenticated_http.AuthenticatedDispatch.send` | Independent local disconnect-after-commit probe exposed cookie and bearer canaries in raw Playwright exception string and formatted traceback. Journal still showed one request/effect, no replay. | Sanitize driver failures at shared dispatch boundary, raise bounded unknown-effect error without exception chaining. | Pending correction and canary probe. |
| F2 | `client_cli.show_failure` | `ProfileError('authentication_required')` before call enters ValueError branch and emits generic input error rather than reason JSON. Reviewer direct reproduction. | Handle bounded profile errors before generic input validation. | Pending missing-profile CLI regression. |
| F3 | `native_files.convert` | Ordinary generated JSON model named File is mistaken for native upload; convert of its valid name field fails. Same class-name hazard applies to Upload. Reviewer generated OpenAPI reproduction. | Match actual upstream file type identity, retain native JSON model handling. | Pending both collision regressions plus real uploads. |

Independent adversary verified full two-request job serialization across processes,
profile/origin rejection, single committed effect after disconnect, and no replay on
profile-save failure. A generated wheel installed into an isolated target executed
public `client_context()` twice through fresh closed-stdin `python -I` processes,
renewing cookies and importing no generator module. This shared dependency environment
is useful installed-wheel evidence, not milestone 4 clean-container acceptance.

Scratch probe results remain under the chat workspace's `work/adversary-m1`:
`results-35dc531.json` and `public-results-35dc531.json`. Required installed console
commands, autonomous URL wiring, workflow guards and clean-container acceptance
remain incomplete. Milestone 1 is not closed while F1-F3 remain unresolved.

## Milestone 1 correction closure at 7d90c49

F1-F3 resolved. Author focused five-file check: 52 passed in 20.34s; independent
reviewer rerun: 52 passed in 20.31s. Full suite: 227 passed in 24.11s.
`git diff --check` passed. Reviewer also independently injected credential-bearing
fetch errors and verified safe exception/traceback, one call and no replay, and
verified exact pre-call authentication reason JSON. All five independent adversary
cases passed against the corrected revision; credential canaries were absent from
disconnect exceptions/tracebacks while the server retained exactly one effect.

M1 complete within its runtime integration scope. M4 installed console/container
acceptance remains required. D1 automatic discovery capture, D3-D5 and D7 admission
remain M2 obligations. D2 private runtime and D6 public Python factory are implemented
and independently reviewed; complete autonomous URL integration is still pending.

## Milestone 2 intermediate review at 1ba7924

First slice implements HAR/admission seams and selectively applies `0775d8c` with
its regressions. Author and independent reviewer each ran
`poetry run pytest tests/test_admission.py tests/test_generation.py tests/test_self_discovery.py tests/test_discovery.py -q`:
55 passed in 5.65s. Reviewer used an isolated fixed-revision archive. This is not
acceptance of the still-incomplete autonomous orchestration or all privacy surfaces.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F4 | `admission.prune_components` | A response references `#/components/schemas/Outer/properties/safe`, but unrelated `Outer.unrelated` has a remote ref. Retaining the whole component makes final validation reject the otherwise usable method. | Preserve only required reference dependencies, without silently changing executable wire semantics. | Pending correction. |
| F5 | `admission` security handling | Unused sensitive security-scheme name/header excludes an independent unauthenticated read. | Check actual effective security requirements per operation; remove unused sensitive structures from saved/executable definitions. | Pending correction. |
| F6 | `admission.admit_openapi` | A malformed `post: null` raises AttributeError and prevents usable independent reads from generating. | Bounded invalid-contract exclusion for malformed operation entries. | Pending correction. |

All findings reproduced locally without target fetches. Implementer notified; final
M2 schema-only, orchestration, privacy and route acceptance remain pending.

## Milestone 2 schema-only slice review at 7e5ac73

Independent reviewer ran admission/schema-only/pipeline/generation checks in an
isolated archive: 59 passed in 12.74s. F4-F6 corrections passed their reproductions.
Native wire capture also verified missing required arguments reject and omitted
optional arguments stay absent, preserving the server default. A custom GraphQL root
type correctly uses Ariadne's generated Query builder.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F7 | `graphql_admission.schema_operations` | Roots or leaves named `class`/`from` become native `class_`/`from_`; snake-case-only metadata breaks eager CLI loading, including other commands and help. | Use upstream naming behavior and reject ambiguous generated symbols automatically. | Pending correction and actual generated command checks. |
| F8 | `graphql_admission.safe_schema` | Removing sensitive interface `CANARY_PRIVATE` leaves `Record implements CANARY_PRIVATE`, invalidating the schema and losing independent `Query.status`. | Close privacy exclusions across interface and inheritance references, preserving independent roots. | Pending correction. |

SDL/introspection happy cases and abstract/leafless exclusions passed the author's
slice checks. Final autonomous discovery, complete route and persistence checks are
still pending; no complete M2 delivery is claimed by these intermediate reviews.

## Integrated M2 review at 3343dff

Author's seven-file focused run: 116 passed in 14.54s. Independent adversary's five
finite offline cases passed: mixed local/inherited serialization and remote-ref
exclusions without target fetches; output-wide definition canaries; two ready
services; missing Node terminal reason; and schema-only scalar/object native execution.
Supplied files were unchanged. This is not clean-container acceptance.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F9 | `admission.scrub_document`, `privacy.safe_workflow` | Coordinator reproduction: valid wire properties named description/default are rewritten or deleted as metadata; workflow literal payload values change too. | Context-aware metadata filtering, preserving non-sensitive wire property names and explicit JSON values. | Author regression added; independent final check pending. |
| F10 | `autonomous.run` merge recovery | Same service ID and schema but different endpoints makes standard merge reject; fallback ignores endpoint/protocol, picks second endpoint and reports complete. Reviewer fixed-revision reproduction. | Quarantine conflicting ownership while preserving independent services; never silently select an endpoint. | Pending exact regression and correction. |

Final M2 reference bundling, cross-origin profile wiring, HTML-only/traffic-only
separation and complete regression review remain pending. No material finding is
closed merely because other focused tests passed.

Independent reviewer reran the integrated seven-file suite at `3343dff`: 116 passed
in 15.14s. F7/F8 corrections passed. Two related named-operation admission failures
were also reproduced and sent to the implementer:

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F11 | `privacy.safe_operations`, normalization and autonomous generation | Mixed named query/subscription loses the supported query without a schema, or silently switches to schema-only generation with a schema. A privacy-excluded named operation also disappears without a partial-coverage reason. | Admit supported named operations independently, preserve typed named route and fragment dependencies, and report every destructive exclusion. | Pending correction. |

The adversary completed six offline cases plus one bounded browser probe at the same
fixed revision. Signed provenance and producer canaries passed. Browser traversal
visited the second page, fetched the API, blocked the attempted POST, and persisted no
known-sensitive tab/form names. Initial-login callback ran once with stdin closed.
No further broad probe extensions are required before the final corrected M2 pass.

## Final M2 correction findings at d67e393

Independent focused suite: 179 passed in 31.06s. Adversary's original six offline,
browser and three correction cases passed, but a narrower reviewer fragment case
still demonstrated F11: a fragment used only by an excluded subscription remains
orphaned and causes NoUnusedFragments to reject the supported typed query.

Full author regression at this revision: **263 passed, 2 failed in 48.70s**. The
failure is not waived by the focused results.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F12 | privacy report filtering and captured GraphQL owner | Dropping discovery.json metadata changes captured GET and operationName-in-URL conventions; two full-suite regressions fail. | Preserve only the owning compiler's safe method/endpoint/query-name metadata, excluding values and headers. | Affected check passed with orphan-fragment correction; full recheck pending. |
| F13 | initial multi-origin profile seeding | Same-host, different-port API receives no cookie in browser fetch, but domain-only storage-state filtering copies start_sid into its saved profile and generated CLI sends it. Reviewer changed only the API host in the declared-origin test; one test failed in 4.51s. | Require origin ownership when seeding initial cookies; exclude/report ambiguity and preserve native renewal in isolated runtime contexts. | Exact browser regression and independent correction probe pending. |

M2 remains open until F11-F13 and the full relevant suite pass. Workflow source-file
association is an explicit M3 integration obligation; retained workflow documents
have not been advertised as executable before that work.

## M2 correction re-review at ee385ea

Author affected checks: 79 passed in 36.85s. Independent reviewer ran the affected
fixed-archive suite: 51 passed in 33.20s. F11 orphan fragments and F12 captured GET
conventions are corrected; exact two-port cookie-isolation case also passes.

F13 is still open because the correction introduced a required cookie-renewal
failure. Coordinator's direct state probe dropped the renewed cookie. Independent
reviewer reproduced it with real Playwright: page issues sid=OLD; same-origin fetch
sends OLD and response sets sid=NEW; browser state has NEW, but ownership filter
still recognizes only OLD. Saved profile has zero cookies. After browser closure,
the generated native client CLI with closed stdin sends no cookie and fails with
`authentication_required`. Valid final-response cookie renewal must survive initial
profile handoff without another outgoing read. The writer is correcting this exact
case while preserving cross-port isolation; no broad probe extension is pending.

## Milestone 2 closure at d9d4d07

F13 resolved through selected-origin native Set-Cookie issuance evidence, including
the final response. Shared-jar transmission does not transfer issuance ownership;
multiple issuers stay ambiguous and excluded. The runtime still uses native cookie
renewal in isolated request contexts.

- Author affected browser/profile check: 74 passed in 28.90s.
- Full `work/poetry-env/bin/poetry run pytest -q`: **270 passed in 58.80s**.
- Independent ownership/cross-origin/handoff check: 11 passed in 11.62s.
- Reviewer's original real-browser OLD-to-NEW reproduction now saves NEW and the
  generated closed-stdin CLI succeeds after browser closure.
- Adversary's bounded browser probe passed at the same revision: positive traversal,
  blocked writes, output canaries and single initial-login callback.
- `git diff --check` passed. No configured project lint/type command exists.
- Coordinator executed the actual README OpenAPI and schema-only SDL commands against
  committed fixtures with new scratch outputs; both returned complete reports and
  exited 0. Actual `run --help` matches the documented syntax.

F4-F13 are resolved within M2. M2 is complete. Its coherent application commits are
`1ba7924`, `7e5ac73`, `3343dff`, `d67e393`, `ee385ea`, and `d9d4d07`.
M3 must preserve explicit supplied/discovered workflow source associations, implement
the restricted runner and validate actual responses before dependent requests.
M4 installed console/wheel and clean-container acceptance remain incomplete.

## M3 admission and required native fix

At `a99cfeb`, author workflow/admission/generation checks passed 41 tests in 5.33s;
independent fixed-archive check passed 41 in 5.64s. Execution/binding integration is
still in progress.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F14 | workflow input-reference admission | Original supplied workflow has no inputs schema and is wrongly excluded as unknown_input_reference; the native evidence executed it. | Restore original fixture, JSON Schema permissive defaults and runtime input-presence checks. | Working correction pending commit/review. |
| F15 | native Arazzo HTTPExecutor | Schema-valid whole JSON null sends empty bytes and receives 422; nested null sends correct JSON. Reproduced on actual wire. | Minimal explicitly versioned upstream dependency fix; no completion waiver or custom transport serializer. | Patch/build/installed-wheel acceptance pending. |
| F16 | workflow source closure | Unused remote component rejects an independent workflow. Reviewer fixed-slice reproduction. | Validate selected operation dependency closure; preserve unrelated executable workflows. | Working correction pending final review. |

The adversary confirmed the native dependency patch design is authorized by the
reproduced-incompatibility exception. Actual metadata and clean sibling-wheel
installation are required checks. No PR existed before the user's latest request;
that request now authorizes a draft PR and branch pushes. Overall implementation
remains incomplete through M3/M4.

## Pushed checkpoint and quality review

The latest user request authorized branch pushes and a draft PR. Draft PR #12 is
[open](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/12).
The pushed guard/runtime checkpoint `d4d9bd1` passed a coordinator-run full test from
an isolated fixed archive: 303 passed in 62.47s. Later dependency patch installation
changes the old native-null counterexample expectation; it does not invalidate that
recorded checkpoint result. No GitHub checks are configured/reported for this branch.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F17 | workflow quiet_runner | Overlapping thread contexts restore logger handlers early and leak a credential canary; later restoration also corrupts global stream/handler state. Independent reproduction. | Serialize suppression or use equivalent scoped native logging with correct restoration. | Correction pending. |
| F18 | multipart request guard | A dict supplied to a string note field is stringified natively, then accepted by wire-only validation and sent as a POST. | Validate resolved native values before coercion without implementing another expression evaluator. | Correction pending. |

The new explicit quality requirement also triggers a bounded refactor audit. The
independent reviewer identified long/mixed orchestration, anonymous operation/source
records, duplicated profile lifecycle, dense security predicates and mixed model
conversion. The durable plan records reusable helpers, validated dataclasses,
secret-safe repr, conventional spacing and focused regression checks. These quality
criteria must pass independent review before completion.
